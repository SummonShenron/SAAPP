"""The generic ReAct (Reason -> Act -> Observe -> Decide) loop shared by every iterative
tool_agent_node-style caller in backend/services/agent_workflow.py.

Moved out as part of the incremental agent_workflow.py split (docs/coding-agent-roadmap.md,
Section 13) — unlike the plain helper functions already moved to backend/utils/agent_utils.py, this is a genuine standalone engine (parameterized
entirely via the `act`/`is_unsafe` callbacks, not a closure over any node's local state), so it gets its
own service module rather than a "utils" file.
"""
import asyncio
import json
import logging

from backend.models.models import lite_llm
from backend.utils.agent_utils import (
    _CAPABILITY_DENIAL_RE,
    _extract_declared_names,
    _find_unseen_existing_declarations,
    _ClarificationNeeded,
    _UnsafeActionRequested,
    _READ_FILE_DEFAULT_LINE_WINDOW,
    _check_final_answer_grounding,
    _check_idiom_grounding,
    _final_answer_has_code_block,
    _has_successful_code_precedent_research,
    _format_react_attempts,
    _final_diff_disagrees_with_fetched_content,
    _is_empty_observation,
    _mentions_unresolved_truncation,
    _parse_agent_json,
    _truncate_observation,
    find_mismatched_start_line_note,
    format_steering_notes,
    parse_definition_index_from_observation,
    safe_emit_event,
)

logger = logging.getLogger("SASS Logger")

# Only used inside run_react_loop's own body (not passed in by any caller), so they move with it.
_DEFAULT_STUCK_ACTION_THRESHOLD = 3
_DEFAULT_STUCK_ACTION_MESSAGE = (
    "You have called {tool} multiple times in a row without making real progress. Stop calling "
    "{tool} again — try a genuinely different tool_action or a materially different approach on "
    "your next step instead."
)
_MAX_BATCH_SIZE = 5


async def run_react_loop(
    *,
    question: str,
    schema: str,
    prompt_template: str,
    act,
    is_unsafe=lambda decision: False,
    max_iterations: int,
    node_name: str,
    initial_attempts: list | None = None,
    max_retry_nudges: int = 1,
    llm=lite_llm,
    stuck_action_redirects: dict | None = None,
    capability_denial_watchlist: list | None = None,
    architecture_map: str = "",
    batchable_actions: frozenset | None = None,
    declaration_lookup=None,
    partial_read_windows_ok: bool = False,
    required_action: dict | None = None,
    steering_source=None,
    on_steering_applied=None,
    steering_extra_steps: int = 2,
    steering_max_extra_steps: int = 8,
) -> dict:
    """Generic Reason -> Act -> Observe -> Decide loop shared by every iterative tool
    (MongoDB, GitHub search, ...). Each step asks the model for the next action given
    everything tried so far; the model decides for itself when it has enough to answer,
    or is honest that it doesn't. Returns {"final_answer": str, "attempts": list[dict],
    "show_work": bool} — show_work is the model's own call on whether its step-by-step trace
    is worth repeating in the chat message itself, not just the live trace panel (see
    show_work's schema entry in TOOL_AGENT_PROMPT); defaults true when a "final" doesn't set it.
    Raises _UnsafeActionRequested if is_unsafe() ever flags a proposed action, and
    _ClarificationNeeded if the model reports genuine uncertainty instead of guessing.
    `initial_attempts`, when given, seeds the loop with attempts already made in an earlier
    call — the resume side of a clarification pause, so the loop continues instead of
    starting from zero. `llm` defaults to lite_llm; deep thinking mode passes lite_llm_deep
    instead, so a wider step/nudge budget also comes with more carefully reasoned individual
    step decisions rather than just more of them.

    `steering_source` (a zero-arg callable returning newly arrived user messages) lets the user
    redirect the work while it runs: it is polled at the start of every step and again right after
    each model decision. Messages stay in every later step's prompt, each one grants a couple of
    extra steps (bounded by `steering_max_extra_steps`) since a redirect usually needs new work, and
    a decision made while one was arriving is discarded and re-made with it. `on_steering_applied`
    (async, called with the new messages and the step number) lets the caller announce it.

    A prompt-level instruction alone isn't enough to stop the model from giving up right after
    a failed OR empty action even when steps remain — it already has evidence of that ("never
    claim something exists without verifying it" was in the prompt and got broken anyway, in
    the exact scenario this guards). retry_nudge_count enforces it mechanically instead of just
    asking nicely: whenever the loop sees an outstanding failed-or-empty action with steps
    still available, it tells the model so and, if the model tries to conclude anyway, rejects
    that "final" and forces one more real step — up to max_retry_nudges times per call (default
    1). It stops rejecting after that budget is spent, so a genuinely doomed action (a real
    404, a real rate limit, a search that's empty no matter how it's phrased) still gets an
    honest "final" rather than looping forever. Deep thinking mode raises this budget alongside
    max_iterations, since a higher step cap alone doesn't help if the loop only gets to
    second-guess itself once.

    "Outstanding failed-or-empty action" is tracked per (tool_action name, args) pair, not just
    "did the last step fail" — a real failure mode this caught: read_repo_file 404s, list_repo_tree
    (a different action, taken to diagnose the 404) then succeeds, and the model concludes right
    there without ever actually retrying the read. Checking only the last observation would
    see the successful list and never nudge, even though the thing the user actually asked for
    was never retrieved. Empty results are tracked the same way as outright errors (see
    _is_empty_observation) — an empty search result is very often a sign of looking in the
    wrong place (wrong repo, wrong collection, wrong query), not proof nothing exists.
    unretried_inconclusive_tools maps every tool_action that has failed or come back empty to the
    exact args it was called with, until it's attempted again with either a real success or
    genuinely different args — calling the same failed action with the identical args again is
    not a retry (it's the same query bouncing off the same wall) and does not clear the flag, so
    it keeps nudging until the model actually changes something. Tracked regardless of what ran
    in between.

    A real production trace showed the above still isn't enough for one specific pattern: the
    model called search_code with several genuinely DIFFERENT (differently-worded) queries in a
    row, ~15 steps total, and never once switched to find_file — each retry was "genuine" by the
    args-signature check above (different wording each time), so it kept clearing the nudge, even
    though the actual problem (an exact-token search tool being used for a colloquial name) never
    changes no matter how the query is reworded. A prose rule in the prompt telling it to use
    find_file after an empty search_code already existed and was already live in production when
    this happened — advisory text alone wasn't enough once it was several steps into a losing
    strategy. `stuck_action_redirects` (optional: {tool_action_name: (consecutive_miss_threshold,
    redirect_message)}) adds a mechanical escalation on top: it tracks a CONSECUTIVE-misses streak
    per tool_action_name regardless of args (reset by any success or any different tool_action),
    and once a listed tool's streak reaches its threshold, injects `redirect_message` into the
    prompt AND actively rejects one further call to that same stuck tool_action (not executed, not
    recorded as an attempt) — forcing a real switch to a different action, the same mechanical
    escalation already used for a premature "final".

    A second real production trace showed the same "prose alone isn't enough" lesson applies even
    to a rule this loop's own prompt already states plainly: told to check its own action menu
    before denying a capability, the model still produced a confident, detailed "final" claiming
    it had no browser tool at all, while browser_navigate sat in that exact turn's menu.
    `capability_denial_watchlist` (optional: a list of (capability_keyword_regex,
    tool_action_menu_substring) pairs) is the mechanical backstop: when a "final" answer matches
    both a generic denial-shaped phrase (_CAPABILITY_DENIAL_RE — "I don't have...", "I can't...",
    etc.) AND one of the watchlist's capability keywords, AND that pair's tool_action_menu_substring
    is actually present in this turn's prompt_template (proving the capability really is
    available), the "final" is rejected once (not executed as a real step, no state to retry — just
    a corrective notice injected into the next step) instead of trusting the model to have caught
    its own contradiction.

    A self-drive experiment (docs/coding-agent-roadmap.md, Section 7) surfaced a third fabrication
    shape unrelated to any tool_action, budget, or capability check above: a confidently-formatted
    diff editing a data structure (a whole dict) that was never verified to exist anywhere in the
    real codebase — presented alongside quotes from OTHER, genuinely-read parts of the same file,
    so it read as thoroughly researched. `_final_diff_disagrees_with_fetched_content` is the
    mechanical backstop: when a "final" answer contains a `diff --git` block against an EXISTING
    file (a new file has nothing pre-existing to verify and is skipped), at least one of that
    hunk's own claimed pre-existing lines (context or removed, never a `+` line) must actually
    appear in a real read_repo_file observation for that same path recorded THIS turn — otherwise
    the "final" is rejected once, the same budget-limited shape as the capability-denial check
    above, so a real diff grounded in an earlier part of the conversation (outside this loop's own
    attempts) still gets through eventually rather than looping forever on a false positive.

    The diff check above only catches one specific, mechanically-checkable fabrication shape. The
    general case — an arbitrary prose claim (a function name, a specific value, a "such-and-such
    exists/doesn't exist" assertion) that never actually came from any real observation — has no
    reliable string/regex check and structurally cannot be caught downstream either: this app's
    reward evaluator (reward_evaluator.py) only ever sees this final_answer AFTER it has already
    been folded into "the DATA" a second LLM rewrites into the user-facing response, and is told
    to judge that rewrite's faithfulness to the DATA it was handed — by the time it runs, a
    fabrication made HERE has already become the ground truth everything downstream is judged
    against, and the check structurally cannot look further upstream than the data it was given.
    `_check_final_answer_grounding` runs a second, independent LLM call comparing the proposed
    final_answer against the real `attempts` gathered this turn, right here where they still exist
    as separate things — the only place this can actually be checked. Same budget-limited-rejection
    shape as every other mechanical gate above (reject once, force a correction, let a second
    attempt through even if this was a false positive), and skipped entirely when there are no real
    attempts yet (nothing to ground a claim in), so a purely conversational "final" never pays this
    extra LLM-call's cost.

    `_check_final_answer_grounding` catches FALSE claims — something stated as fact that never
    appeared in attempts. Its own prompt explicitly says not to flag a reasonable synthesis or a
    plainly-labeled inference. That leaves a real, separate gap: generic, exampleish proposed code
    that isn't factually wrong about anything (it doesn't claim a function exists that doesn't,
    doesn't misquote a value) but also isn't actually derived from anything real this turn — it's
    boilerplate that would look identical whether the model had read the repo or read nothing at
    all. No amount of tuning the existing check catches this, since by design it's only allowed to
    object to false statements — "ungrounded but not false" is exactly the gap it's told to leave
    alone. Two more gates close it, same budget-limited-rejection shape as everything above, wired
    in as their own independent checks rather than folded into the existing one (conflating "is
    this true" with "is this tailored" would make a single check worse at both):
    a cheap, mechanical gate first — `_has_successful_code_precedent_research` requires at least
    one real, non-error, non-empty read_repo_file/search_code/search_literal/find_file result in
    `attempts` before a "final" containing a code block (`_final_answer_has_code_block`) is
    accepted; reject once, forcing an actual look at real code before proposing an implementation,
    with no LLM call needed. Then `_check_idiom_grounding` — a second, independent LLM judge
    asking a genuinely different question than `_check_final_answer_grounding`'s ("would this code
    look the same if the model had never seen these real files?", not "is any claim in it false")
    — classifies WHY ungrounded code is ungrounded, since the two causes need different corrective
    nudges: `no_real_example_found` (a discovery problem — nothing comparable ever turned up this
    turn, so the notice should point toward searching more specifically) versus
    `real_example_ignored` (a compliance problem — a real precedent WAS read and the proposed code
    didn't use it, so the notice names that specific file/function as the template to actually
    follow, not just repeats "be more grounded" in the abstract). The mechanical gate matters even
    with the LLM judge in place: it's what keeps `_check_idiom_grounding` from ever running against
    an empty `attempts` list with nothing real to compare against.

    An extensive diagnostic loop (docs/coding-agent-roadmap.md, Sections 4b-4j) showed every one
    of those real traces burn most of a turn's step budget reading files or searching one at a
    time, well before ever reasoning about them — a genuinely multi-file investigation needs N
    reads that don't depend on each other, but the loop only ever let it spend one action per
    step. `batchable_actions` (optional: a frozenset of tool_action names safe to run
    concurrently) lets a single "query" step submit `{"queries": [{"tool_action", "args",
    "purpose"}, ...]}` instead of one `tool_action`/`args` pair — each item runs concurrently via
    asyncio.gather, and every one of them still goes through the exact same real execution
    (is_unsafe, trace emission, error handling) and mechanical bookkeeping (retry-nudge tracking,
    truncation tracking, the stuck-action streak) that a real sequential step would have gotten,
    applied once per item in the order given — a batch never gets WEAKER scrutiny than the
    equivalent sequential steps would have, just fewer round trips to get there. A stuck tool
    slipped into a batch rejects the entire batch (none of it runs) rather than silently dropping
    just that one item, and any tool_action not in `batchable_actions` is rejected individually
    with a synthetic error instead of executed — deliberately excludes writes
    (`run_mongo_query`), real slow CI dispatches (`run_repo_tests`/`run_snippet`), and every
    `browser_*` action (inherently stateful/sequential), regardless of what the caller passes.

    A real trace showed batching alone doesn't stop a different kind of waste: the model re-ran
    the exact same tool_action + args it had already gotten a real, successful result for several
    steps earlier — reading the same file/interface twice for zero new information, on a turn that
    then ran out of its entire step budget without ever producing an answer
    (docs/coding-agent-roadmap.md, Section 9). `succeeded_action_signatures` tracks every
    (tool_action_name, args_signature) pair that has genuinely succeeded (not an ERROR, not empty,
    not an unresolved truncation) at any point THIS turn. Unlike every other mechanical check in
    this loop, this one is unconditional with no budget limit at all — a byte-identical repeat of
    an already-succeeded call can only ever return the same answer again within one turn, so there
    is no genuine case where actually re-running it is the right call. A repeat is skipped before
    ever reaching `act()` (no wasted network/GitHub API call either) and recorded with a message
    pointing back at the matching earlier attempt, for both a lone action and any item inside a
    batch."""
    attempts: list = list(initial_attempts or [])
    final_answer = None
    # Defaults true (show the receipt) whenever a "final" doesn't explicitly say otherwise —
    # a missing/malformed field is more likely a parsing hiccup than a deliberate "hide this",
    # so err toward the more transparent option rather than silently dropping useful context.
    show_work = True
    retry_nudge_count = 0
    unretried_inconclusive_tools: dict = {}  # tool_action_name -> args signature of the failing call
    # A real production trace showed even 3 rejections (deep thinking's full retry_nudge budget)
    # isn't always enough: the model just kept re-submitting a "final" without ever taking the
    # corrective action, and once the budget ran out it walked straight through with 3 different
    # files still truncated and never re-read. Unlike an ERROR/empty result — which might be a
    # genuinely unfixable dead end, so a bounded budget before accepting an honest "I couldn't"
    # is the right call — a truncated file is never actually a dead end: the rest of it is right
    # there. Tracked separately so a "final" is rejected UNCONDITIONALLY (no budget) while any
    # tool_action_name in this set has an unresolved truncation, instead of sharing
    # retry_nudge_count's limited budget with genuinely-unfixable failures.
    truncated_unresolved_tools: set = set()
    # A real production trace (docs/coding-agent-roadmap.md, Section 9) showed the model re-run
    # the exact same tool_action + args it had already gotten a real, successful result for
    # earlier in the SAME turn — re-reading a file/interface it had already read minutes (and
    # several steps) before, burning step budget on zero new information. Unlike every other
    # tracking dict here, this isn't about a failure — it's the opposite: (tool_action_name,
    # args_signature) pairs are added here only on a genuine SUCCESS (see _record_action_result),
    # so a later identical call can be recognized as pure redundant repeat and skipped without
    # ever hitting the network/GitHub API a second time for it.
    succeeded_action_signatures: set = set()
    # A real trace (Section 10) showed a subtler waste than an exact repeat: after read_repo_file
    # truncates a file (no start_line given), its own response already lists every top-level
    # def/class and its real line number — but the model ran two MORE search tools trying to
    # relocate a symbol it had already been told the line number for, then still guessed the
    # wrong start_line anyway. Maps path -> {symbol_name: line_number} from every truncated
    # read_repo_file result seen this turn, so a later start_line guess for a named symbol can be
    # checked against real ground truth instead of trusted blindly (see find_mismatched_start_line_note).
    definition_indexes_by_path: dict = {}
    stuck_action_streak = {"tool": None, "count": 0}  # consecutive misses on ONE tool, any args
    stuck_action_reject_count = 0
    MAX_STUCK_ACTION_REJECTIONS = 1
    capability_denial_reject_count = 0
    MAX_CAPABILITY_DENIAL_REJECTIONS = 1
    pending_capability_denial_notice: str | None = None
    # `required_action` ({"tool_action", "nudge", "final_notice", optional "nudge_after_step" /
    # "max_final_rejections"}): for a turn where the user asked for something that can ONLY be
    # satisfied by one specific action (a connected-folder edit request needs propose_local_edits —
    # reading, searching and explaining never make the change). Real traces ended with the model
    # re-reading files, pasting a snippet, or announcing it "couldn't" write, with the tool sitting
    # in its menu the whole time. Prose telling it to use the tool kept losing to the model's own
    # momentum, so this is mechanical: once it has read something and still hasn't acted, every step
    # is told to act; and a "final" that never used the action is rejected (bounded, so a genuinely
    # unanswerable request still gets an honest final eventually).
    required_action_final_rejects = 0
    pending_required_action_notice: str | None = None

    def _required_action_done() -> bool:
        if not required_action:
            return True
        prefix = f"{required_action['tool_action']}("
        return any(
            str(a.get("action_desc", "")).startswith(prefix) and not str(a.get("observation", "")).startswith("ERROR")
            for a in attempts
        )

    ungrounded_diff_reject_count = 0
    MAX_UNGROUNDED_DIFF_REJECTIONS = 1
    pending_ungrounded_diff_notice: str | None = None
    ungrounded_claim_reject_count = 0
    MAX_UNGROUNDED_CLAIM_REJECTIONS = 1
    pending_ungrounded_claim_notice: str | None = None
    no_precedent_research_reject_count = 0
    MAX_NO_PRECEDENT_RESEARCH_REJECTIONS = 1
    pending_no_precedent_research_notice: str | None = None
    idiom_grounding_reject_count = 0
    MAX_IDIOM_GROUNDING_REJECTIONS = 1
    pending_idiom_grounding_notice: str | None = None
    duplicate_declaration_reject_count = 0
    MAX_DUPLICATE_DECLARATION_REJECTIONS = 1
    pending_duplicate_declaration_notice: str | None = None

    steering_notes: list = []
    steering_extra_used = 0
    budget = max_iterations

    async def _absorb_steering(step_number: int) -> bool:
        """Pulls any newly arrived steering messages into the run. True when there were some."""
        nonlocal budget, steering_extra_used
        if steering_source is None:
            return False
        new_messages = [str(m) for m in (steering_source() or []) if str(m).strip()]
        if not new_messages:
            return False
        steering_notes.extend(new_messages)
        grant = max(min(steering_extra_steps * len(new_messages), steering_max_extra_steps - steering_extra_used), 0)
        budget += grant
        steering_extra_used += grant
        logger.info(
            "[%s] step %s: %d steering message(s) received (+%d step(s), budget now %d).",
            node_name, step_number, len(new_messages), grant, budget,
        )
        if on_steering_applied is not None:
            try:
                await on_steering_applied(new_messages, step_number)
            except Exception:
                logger.exception("[%s] on_steering_applied callback failed.", node_name)
        return True

    step = -1
    while step + 1 < budget:
        step += 1
        # Before forced_final is computed: a steer arriving now can extend the budget.
        await _absorb_steering(step + 1)
        forced_final = step == budget - 1
        # Shown on every step with an outstanding failure or empty result, not just once —
        # only the actual rejection of a premature "final" below is budget-limited (via
        # retry_nudge_count), so the model still sees the reminder if it takes an unrelated
        # detour (like listing the repo tree) before eventually trying to conclude.
        needs_retry_nudge = not forced_final and bool(unretried_inconclusive_tools)

        stuck_redirect_entry = None
        if stuck_action_streak["tool"]:
            # A tool-specific entry (like search_code's) always wins when listed — it can give
            # more targeted advice ("call find_file instead") than the generic fallback below can.
            # Every OTHER tool still gets the generic circuit breaker instead of no backstop at
            # all — see _DEFAULT_STUCK_ACTION_MESSAGE's comment for why this is no longer
            # search_code-specific.
            stuck_redirect_entry = (stuck_action_redirects or {}).get(stuck_action_streak["tool"]) or (
                _DEFAULT_STUCK_ACTION_THRESHOLD,
                _DEFAULT_STUCK_ACTION_MESSAGE.format(tool=stuck_action_streak["tool"]),
            )
        stuck_redirect_active = (
            not forced_final
            and stuck_redirect_entry is not None
            and stuck_action_streak["count"] >= stuck_redirect_entry[0]
        )

        question_for_step = question + format_steering_notes(steering_notes)
        if forced_final:
            question_for_step += (
                "\n\n(You have used all your steps. You MUST return "
                "action=\"final\" now, honestly summarizing what you tried and found.)"
            )
        if needs_retry_nudge:
            failed_tools = ", ".join(sorted(unretried_inconclusive_tools))
            question_for_step += (
                f"\n\n(One or more of your actions failed or came back empty and was never "
                f"successfully retried ({failed_tools}), and you still have steps remaining — "
                "an empty result often means the query, path, or scope was wrong, not that "
                "nothing exists. Actually retry it with corrected information — a DIFFERENT "
                "query/path/args than the one that just failed (calling it again with the exact "
                "same args does not count as retrying it, and neither does a different "
                "diagnostic action, like listing the repo tree) — before concluding. Only choose "
                "action=\"final\" now if you are certain nothing else could help.)"
            )
        if truncated_unresolved_tools:
            # More insistent than the generic nudge above, and names the actual path when it can
            # — a real trace showed the generic reminder alone wasn't enough to change behavior
            # across 3 rejections in a row. This one keeps firing with NO budget limit (see
            # truncated_unresolved_tools' own comment) because more content is always available
            # here, unlike a genuinely-failed action that might really be a dead end.
            stuck_path = None
            for stuck_tool in truncated_unresolved_tools:
                try:
                    stuck_path = json.loads(unretried_inconclusive_tools.get(stuck_tool, "{}")).get("path")
                except (TypeError, ValueError):
                    stuck_path = None
                if stuck_path:
                    break
            path_hint = f" (the one you left unfinished was {stuck_path})" if stuck_path else ""
            question_for_step += (
                f"\n\n(You have not finished reading a file you started{path_hint} — it was "
                "truncated and you never called read_repo_file again with a start_line to see "
                "the rest. This is NOT a failed or empty result — the rest of the file is right "
                "there waiting to be read. You MUST call read_repo_file with a start_line on that "
                "same path as your very next action. This will keep being rejected, with no "
                "limit, until you actually do this — proposing code changes to a file you have "
                "not fully read is not acceptable.)"
            )
        if stuck_redirect_active:
            question_for_step += f"\n\n({stuck_redirect_entry[1]})"
        if pending_capability_denial_notice:
            question_for_step += f"\n\n({pending_capability_denial_notice})"
            pending_capability_denial_notice = None
        if required_action and not forced_final and not _required_action_done():
            has_read_something = any(
                str(a.get("action_desc", "")).startswith("read_repo_file(")
                and not str(a.get("observation", "")).startswith("ERROR")
                for a in attempts
            )
            if has_read_something and step >= required_action.get("nudge_after_step", 3):
                question_for_step += f"\n\n({required_action['nudge']})"
        if pending_required_action_notice:
            question_for_step += f"\n\n({pending_required_action_notice})"
            pending_required_action_notice = None
        if pending_ungrounded_diff_notice:
            question_for_step += f"\n\n({pending_ungrounded_diff_notice})"
            pending_ungrounded_diff_notice = None
        if pending_ungrounded_claim_notice:
            question_for_step += f"\n\n({pending_ungrounded_claim_notice})"
            pending_ungrounded_claim_notice = None
        if pending_no_precedent_research_notice:
            question_for_step += f"\n\n({pending_no_precedent_research_notice})"
            pending_no_precedent_research_notice = None
        if pending_idiom_grounding_notice:
            question_for_step += f"\n\n({pending_idiom_grounding_notice})"
            pending_idiom_grounding_notice = None
        if pending_duplicate_declaration_notice:
            question_for_step += f"\n\n({pending_duplicate_declaration_notice})"
            pending_duplicate_declaration_notice = None

        prompt = prompt_template.format(
            question=question_for_step,
            schema=schema,
            attempts=_format_react_attempts(attempts),
            architecture_map=architecture_map,
        )

        try:
            response = await llm.ainvoke(prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content]) if isinstance(resp_content, list) else str(resp_content)
            decision = _parse_agent_json(raw_text)
        except Exception:
            logger.exception("[%s] step %s failed to produce a usable decision.", node_name, step + 1)
            break

        if await _absorb_steering(step + 1):
            # The user redirected the work while this decision was being made, so it was made
            # without that guidance. Discard it rather than act on (or conclude from) a plan they
            # just changed; the next step re-decides with the steering in the prompt.
            logger.info("[%s] step %s: decision discarded — steering arrived while it was being made.", node_name, step + 1)
            continue

        action = decision.get("action")
        # A model sometimes labels a step "final" while naming the tool it wants to run — observed
        # with propose_local_edits, sent as {"action": "final", "tool_action": ..., "args": ...} and
        # no answer text. A "final" with no answer is meaningless, and honoring it threw the tool
        # call away and ended the turn with "I wasn't able to find a conclusive answer". The intent
        # is plainly a query, so run it as one.
        if (
            action == "final" and not forced_final
            and not str(decision.get("answer") or "").strip()
            and (decision.get("tool_action") or decision.get("queries"))
        ):
            logger.info("[%s] step %s: 'final' with no answer but a tool call — treating it as a query.", node_name, step + 1)
            decision = {**decision, "action": "query"}
            action = "query"
        if action == "final" and not forced_final and truncated_unresolved_tools:
            # Unconditional — no budget check, unlike the ERROR/empty case below. A real
            # production trace showed the model exhaust the ENTIRE deep-thinking retry budget
            # (3 rejections) re-submitting "final" without ever actually re-reading any of the
            # 3 files it had left truncated, then walk straight through once the budget ran out.
            # A truncated file is never a genuine dead end, so there's no principled reason to
            # ever let this go until it's actually resolved or the loop is forced to conclude.
            continue
        if action == "final" and needs_retry_nudge and retry_nudge_count < max_retry_nudges:
            # Told to retry and it tried to conclude anyway — force one more real step instead
            # of accepting a premature answer. retry_nudge_count only increments here (at the
            # actual rejection), not just when the nudge was shown, so a detour in between
            # (e.g. it lists the repo tree first) doesn't spend the budget for free.
            retry_nudge_count += 1
            continue
        if (
            action == "final"
            and not forced_final
            and required_action
            and not _required_action_done()
            and required_action_final_rejects < required_action.get("max_final_rejections", 2)
        ):
            required_action_final_rejects += 1
            pending_required_action_notice = required_action["final_notice"]
            logger.info(
                "[%s] step %s: 'final' without %s on a turn that requires it — rejected (%s/%s).",
                node_name, step + 1, required_action["tool_action"], required_action_final_rejects,
                required_action.get("max_final_rejections", 2),
            )
            continue
        if action == "final" and not forced_final and capability_denial_watchlist and capability_denial_reject_count < MAX_CAPABILITY_DENIAL_REJECTIONS:
            answer_text = decision.get("answer") or ""
            denied_tool_marker = None
            if _CAPABILITY_DENIAL_RE.search(answer_text):
                for capability_re, tool_menu_substring in capability_denial_watchlist:
                    if capability_re.search(answer_text) and tool_menu_substring in prompt_template:
                        denied_tool_marker = tool_menu_substring
                        break
            if denied_tool_marker:
                # A confident, detailed denial isn't more trustworthy than a short one if the
                # capability is sitting right there in the menu — reject once (not executed,
                # not recorded as an attempt) instead of trusting the model caught its own
                # contradiction. Budget-limited for the same reason as every other mechanical
                # rejection here: a genuinely correct "I don't have that" (a capability that
                # really isn't in the menu) must still get through eventually.
                capability_denial_reject_count += 1
                pending_capability_denial_notice = (
                    f"Your last answer denied having a capability, but '{denied_tool_marker}' is "
                    "listed in AVAILABLE ACTIONS THIS TURN above — you do have it right now. Do "
                    "not deny having it; if you haven't actually used it yet this turn, use it "
                    "before answering."
                )
                continue
        if action == "final" and not forced_final and ungrounded_diff_reject_count < MAX_UNGROUNDED_DIFF_REJECTIONS:
            answer_text = decision.get("answer") or ""
            ungrounded_path = _final_diff_disagrees_with_fetched_content(answer_text, attempts)
            if ungrounded_path:
                # Same shape as the capability-denial rejection above, one step later in the same
                # real trace that motivated it: a confidently-formatted diff isn't more trustworthy
                # than a rough one if none of what it claims already exists in the file ever
                # actually came back from a real read this turn. Budget-limited for the same reason
                # as every other mechanical rejection here — a real diff against a file genuinely
                # read earlier in the conversation (outside this loop's own attempts) must still be
                # allowed through eventually rather than looping forever on a false positive.
                ungrounded_diff_reject_count += 1
                pending_ungrounded_diff_notice = (
                    f"Your proposed diff edits {ungrounded_path}, but none of the lines it claims "
                    f"already exist there ever appeared in a real read_repo_file result for that "
                    f"exact path this turn. Call read_repo_file({ungrounded_path}) for real, quote "
                    "the actual current lines you're changing, and rebuild the diff from what's "
                    "really there before answering again — do not guess at the file's structure."
                )
                continue
        if action == "final" and not forced_final and ungrounded_claim_reject_count < MAX_UNGROUNDED_CLAIM_REJECTIONS:
            answer_text = decision.get("answer") or ""
            unsupported_claims = await _check_final_answer_grounding(answer_text, attempts, llm)
            if unsupported_claims:
                # The general case the diff check above can't cover — an arbitrary prose claim
                # (a function name, a specific value, an existence assertion) with no reliable
                # mechanical check, so a second LLM call judges it against the real attempts
                # instead. Same budget-limited shape as every rejection above: force one real
                # correction, then let a second attempt through even if this was a false positive.
                ungrounded_claim_reject_count += 1
                claims_list = "; ".join(unsupported_claims)
                pending_ungrounded_claim_notice = (
                    "Your last answer made at least one claim that doesn't actually appear in your "
                    f"real observations this turn: {claims_list}. Either verify each of these with a "
                    "real action before answering again, or remove/qualify them as unverified — do "
                    "not restate them as fact without real evidence from this turn."
                )
                continue
        if action == "final" and not forced_final and no_precedent_research_reject_count < MAX_NO_PRECEDENT_RESEARCH_REJECTIONS:
            answer_text = decision.get("answer") or ""
            if _final_answer_has_code_block(answer_text) and not _has_successful_code_precedent_research(attempts):
                # Cheap, mechanical, no LLM call — a "final" proposing code with zero real,
                # non-error, non-empty read_repo_file/search_code/search_literal/find_file result
                # this turn means nothing real was ever actually looked at to model the code on.
                # Also what keeps _check_idiom_grounding below from ever running against an empty
                # attempts list with nothing real to compare against.
                no_precedent_research_reject_count += 1
                pending_no_precedent_research_notice = (
                    "Your answer includes code, but you haven't actually looked at any real code "
                    "in this repo this turn (no successful read_repo_file/search_code/"
                    "search_literal/find_file result yet). Go find and read something comparable "
                    "in the real repo before proposing an implementation — do not write code from "
                    "general knowledge of how a project 'like this' is usually structured."
                )
                continue
        if (
            action == "final" and not forced_final and declaration_lookup is not None
            and duplicate_declaration_reject_count < MAX_DUPLICATE_DECLARATION_REJECTIONS
        ):
            answer_text = decision.get("answer") or ""
            declared_names = _extract_declared_names(answer_text)
            if declared_names:
                # Cheap and mechanical (no LLM call), so it runs before the LLM-judged idiom check
                # below — a rejection here skips that call for this pass. Catches the observed
                # failure of proposing `const overflowItems = [...]` as if new when one already
                # existed in a region of the file the model never read: whole-repo lookup (via the
                # caller's declaration_lookup), but only objects when the model hasn't actually
                # seen the existing declaration, so a legitimate "here's the updated X" passes.
                try:
                    existing = await declaration_lookup(declared_names)
                    unseen = _find_unseen_existing_declarations(existing, attempts)
                except Exception:
                    logger.exception("[%s] declaration lookup failed — skipping the check (fail-open).", node_name)
                    unseen = []
                if unseen:
                    duplicate_declaration_reject_count += 1
                    listing = "; ".join(f"`{u['name']}` at {u['path']}:{u['line']}" for u in unseen)
                    pending_duplicate_declaration_notice = (
                        f"Your code declares something that already exists in this repo, and you "
                        f"haven't looked at the existing version this turn: {listing}. Read that "
                        "code (read_repo_file around that line) before answering again, then make "
                        "your change build on its real current contents — if you're replacing it, "
                        "say so explicitly and keep whatever it contains that should stay, rather "
                        "than presenting the declaration as new."
                    )
                    continue
        if action == "final" and not forced_final and idiom_grounding_reject_count < MAX_IDIOM_GROUNDING_REJECTIONS:
            answer_text = decision.get("answer") or ""
            idiom_issue = await _check_idiom_grounding(answer_text, attempts, llm)
            if idiom_issue:
                # The distinct axis _check_final_answer_grounding is structurally forbidden from
                # covering: not "is any claim false" but "is this code actually derived from what
                # you found, or generic/exampleish boilerplate that would look the same either
                # way." The two reason_categories need genuinely different corrective nudges — a
                # discovery problem (nothing comparable was ever found) versus a compliance
                # problem (a real precedent WAS read and got ignored) — so the notice itself
                # differs, not just the fact of rejection.
                idiom_grounding_reject_count += 1
                if idiom_issue["reason_category"] == "real_example_ignored":
                    pending_idiom_grounding_notice = (
                        "Your proposed code doesn't actually follow a real pattern you already "
                        f"read this turn: {idiom_issue['reason']} Rewrite it using that as your "
                        "actual template — not generic inspiration — matching its real "
                        "structure, naming, and error handling."
                    )
                else:
                    pending_idiom_grounding_notice = (
                        "Your proposed code looks generic/exampleish rather than genuinely "
                        f"derived from this repo's real code: {idiom_issue['reason']} Search for "
                        "and read a real, comparable implementation in this repo before "
                        "proposing code again — don't invent a plausible-looking equivalent from "
                        "scratch."
                    )
                continue
        if action == "final":
            final_answer = decision.get("answer") or "I wasn't able to find a conclusive answer."
            show_work = decision.get("show_work")
            show_work = show_work if isinstance(show_work, bool) else True
            logger.info(
                "[%s] Step %s: accepted final answer after %s real action(s) — %r",
                node_name, step + 1, len(attempts), final_answer[:200],
            )
            break

        if action == "clarify":
            question_text = decision.get("question") or "I need a bit more information to continue — could you clarify?"
            raise _ClarificationNeeded(question_text, attempts)

        if action != "query":
            attempts.append({
                "purpose": decision.get("purpose", "(unclear)"),
                "action_desc": "(no valid action returned)",
                "observation": "ERROR: model did not return a recognized action",
            })
            continue

        queries = decision.get("queries")
        # >= 1, not > 1 — a "queries" list with exactly one item still has to go through the
        # batch path below, since it puts the real tool_action/args inside that one list item
        # rather than at the top level; the batch machinery already handles any size >= 1
        # correctly (asyncio.gather over a single task works fine), so there's no need for a
        # separate single-item normalization path.
        is_batch = isinstance(queries, list) and len(queries) >= 1
        batch_tool_names = [q.get("tool_action") for q in queries if isinstance(q, dict)] if is_batch else []
        stuck_tool_in_this_step = (
            stuck_action_streak["tool"] in batch_tool_names if is_batch
            else decision.get("tool_action") == stuck_action_streak["tool"]
        )
        if (
            stuck_redirect_active
            and stuck_tool_in_this_step
            and stuck_action_reject_count < MAX_STUCK_ACTION_REJECTIONS
        ):
            # Told to switch away from this tool_action and it tried it again anyway — whether
            # alone or slipped into a batch alongside other actions — reject the WHOLE step
            # outright (nothing in it executes, nothing recorded as an attempt) instead of just
            # hoping the redirect message alone changes its mind, the same mechanical escalation
            # already used for a premature "final". Budget-limited for the same reason: a
            # genuinely doomed switch shouldn't force a second forced rejection on top of the first.
            stuck_action_reject_count += 1
            continue

        async def _execute_one_action(
            tool_action_name: str, args: dict, purpose: str,
            batch_index: int | None = None, batch_size: int | None = None,
        ) -> str:
            # Shared by the single-action and batch paths below so a batched action gets the
            # exact same real execution (trace emission, is_unsafe, error handling) a sequential
            # step would have — no weaker scrutiny just because it ran alongside others.
            # batch_index/batch_size (only passed by the batch path, and only when the batch has
            # more than one item) let the frontend trace panel actually show when concurrent
            # batching happened, instead of the only way to confirm it being to read the backend
            # log and notice several attempts sharing one step number.
            sub_decision = {"tool_action": tool_action_name, "args": args, "purpose": purpose}
            trace_payload = {"node": node_name, "title": "Working...", "detail": purpose}
            if batch_size and batch_size > 1:
                trace_payload["batch_index"] = batch_index
                trace_payload["batch_size"] = batch_size
            await safe_emit_event("trace_detail", trace_payload)
            if is_unsafe(sub_decision):
                raise _UnsafeActionRequested(sub_decision, attempts)
            try:
                observation = act(sub_decision)
                if asyncio.iscoroutine(observation):
                    observation = await observation
            except Exception as e:
                observation = f"ERROR: {e}"
            return _truncate_observation(observation)

        def _record_action_result(tool_action_name: str, args: dict, purpose: str, observation: str) -> None:
            # Exactly the bookkeeping a real sequential step already did — extracted so it can be
            # applied once per item in a batch, in order, instead of only ever seeing one
            # tool_action per step.
            nonlocal stuck_action_streak
            if tool_action_name:
                args_signature = json.dumps(args or {}, sort_keys=True, default=str)
                prior_args_signature = unretried_inconclusive_tools.get(tool_action_name)
                is_unresolved_truncation = _mentions_unresolved_truncation(observation)
                if (
                    is_unresolved_truncation
                    and partial_read_windows_ok
                    and (args or {}).get("start_line")
                    and "truncated — this file has" not in observation
                ):
                    # A deliberate mid-file window ("lines 1905-1919 of 2444") always ends with
                    # "N more lines below", so under the strict rule it could never count as
                    # finished short of reading to EOF — a real trace had the model re-read Chat.tsx
                    # for 13 steps and never reach propose_local_edits. Only callers whose proposed
                    # changes are validated mechanically against the real file (a connected local
                    # folder) opt in; a read with NO start_line that got cut off still counts.
                    is_unresolved_truncation = False
                still_failing = (
                    observation.startswith("ERROR")
                    or _is_empty_observation(observation)
                    or is_unresolved_truncation
                )
                if is_unresolved_truncation:
                    unretried_inconclusive_tools[tool_action_name] = args_signature
                    truncated_unresolved_tools.add(tool_action_name)
                else:
                    truncated_unresolved_tools.discard(tool_action_name)
                    if prior_args_signature is not None:
                        if not still_failing or args_signature != prior_args_signature:
                            del unretried_inconclusive_tools[tool_action_name]
                    elif still_failing:
                        unretried_inconclusive_tools[tool_action_name] = args_signature

                if not still_failing:
                    succeeded_action_signatures.add((tool_action_name, args_signature))

                if tool_action_name == "read_repo_file":
                    path = (args or {}).get("path")
                    start_line = (args or {}).get("start_line")
                    if path and not start_line:
                        index = parse_definition_index_from_observation(observation)
                        if index:
                            definition_indexes_by_path[path] = index
                    elif path and start_line:
                        note = find_mismatched_start_line_note(
                            purpose, path, start_line, (args or {}).get("line_count"),
                            _READ_FILE_DEFAULT_LINE_WINDOW, definition_indexes_by_path.get(path, {}),
                        )
                        if note:
                            observation = f"{observation}\n\n{note}"

                is_stuck_worthy_miss = still_failing and not is_unresolved_truncation
                if is_stuck_worthy_miss and stuck_action_streak["tool"] == tool_action_name:
                    stuck_action_streak["count"] += 1
                elif is_stuck_worthy_miss:
                    stuck_action_streak = {"tool": tool_action_name, "count": 1}
                else:
                    stuck_action_streak = {"tool": None, "count": 0}
            args_summary = ", ".join(f"{k}={v}" for k, v in (args or {}).items())
            action_desc = f"{tool_action_name}({args_summary})" if tool_action_name else (args_summary or "")
            logger.info(
                "[%s] Step %s (%s) — action=%s | observation=%r",
                node_name, step + 1, purpose, action_desc or "(none)", observation[:200],
            )
            attempts.append({"purpose": purpose, "action_desc": action_desc, "observation": observation})

        def _is_redundant_repeat(tool_action_name: str, args: dict) -> bool:
            if not tool_action_name:
                return False
            args_signature = json.dumps(args or {}, sort_keys=True, default=str)
            return (tool_action_name, args_signature) in succeeded_action_signatures

        _REDUNDANT_REPEAT_MESSAGE = (
            "(Skipped — you already ran this exact action with these exact args earlier this "
            "turn and it succeeded. Re-use that real result from the matching attempt above "
            "instead of running it again.)"
        )

        if is_batch:
            batch_purpose = decision.get("purpose") or "Working..."
            valid_items: list[dict] = []
            for item in queries:
                item = item if isinstance(item, dict) else {}
                tool_name = item.get("tool_action")
                item_purpose = item.get("purpose") or batch_purpose
                item_args = item.get("args") or {}
                if tool_name and batchable_actions and tool_name in batchable_actions:
                    if _is_redundant_repeat(tool_name, item_args):
                        _record_action_result(tool_name, item_args, item_purpose, _REDUNDANT_REPEAT_MESSAGE)
                    elif len(valid_items) < _MAX_BATCH_SIZE:
                        valid_items.append(item)
                    else:
                        _record_action_result(
                            tool_name, item_args, item_purpose,
                            f"ERROR: too many actions in one batch (max {_MAX_BATCH_SIZE}) — "
                            "this one was dropped; retry it in a later step.",
                        )
                elif tool_name:
                    _record_action_result(
                        tool_name, item_args, item_purpose,
                        f"ERROR: '{tool_name}' cannot be batched with other actions this way — "
                        "call it in its own step instead.",
                    )
                else:
                    _record_action_result(
                        "", item_args, item_purpose,
                        "ERROR: a batched item was missing a valid tool_action and was not executed.",
                    )
            if valid_items:
                observations = await asyncio.gather(*(
                    _execute_one_action(
                        item["tool_action"], item.get("args") or {}, item.get("purpose") or batch_purpose,
                        batch_index=i + 1, batch_size=len(valid_items),
                    )
                    for i, item in enumerate(valid_items)
                ))
                for item, observation in zip(valid_items, observations):
                    _record_action_result(
                        item["tool_action"], item.get("args") or {}, item.get("purpose") or batch_purpose, observation,
                    )
            continue

        purpose = decision.get("purpose", "Working...")
        tool_action_name = decision.get("tool_action") or ""
        args = decision.get("args") or {}
        if _is_redundant_repeat(tool_action_name, args):
            observation = _REDUNDANT_REPEAT_MESSAGE
        else:
            observation = await _execute_one_action(tool_action_name, args, purpose)
        _record_action_result(tool_action_name, args, purpose, observation)

    if final_answer is None:
        # Loop ran out of steps without an explicit final action — force one last honest
        # synthesis instead of silently returning the last raw observation.
        await _absorb_steering(len(attempts))
        try:
            prompt = prompt_template.format(
                question=(
                    f"{question}{format_steering_notes(steering_notes)}\n\n(You are out of steps. You MUST return action=\"final\" now, "
                    "honestly summarizing what you tried and found — never invent an answer "
                    "beyond what the attempts above actually show.)"
                ),
                schema=schema,
                attempts=_format_react_attempts(attempts),
                architecture_map=architecture_map,
            )
            response = await llm.ainvoke(prompt)
            resp_content = response.content if hasattr(response, "content") else str(response)
            raw_text = "".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content]) if isinstance(resp_content, list) else str(resp_content)
            decision = _parse_agent_json(raw_text)
            final_answer = decision.get("answer") or "I wasn't able to find a conclusive answer after several attempts."
        except Exception:
            logger.exception("[%s] final synthesis step failed.", node_name)
            final_answer = "I wasn't able to find a conclusive answer after several attempts."

    return {"final_answer": final_answer, "attempts": attempts, "show_work": show_work}
