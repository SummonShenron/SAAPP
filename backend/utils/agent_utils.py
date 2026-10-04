import ast
import difflib
import json
import logging
import re
from datetime import datetime

from langchain_core.callbacks.manager import adispatch_custom_event
from langchain_core.messages import AIMessage

from backend.components.constraints import GROUNDING_CHECK_PROMPT, IDIOM_GROUNDING_CHECK_PROMPT

logger = logging.getLogger("SASS Logger")


async def safe_emit_event(name: str, data: dict):
    """Safely emit a custom event, ignoring errors if called outside an active run context."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # Safely ignored when called from fallback utilities or standalone scripts
        pass


def format_error_payload(error_message: str) -> str:
    """Serializes an error message into an SSE-compatible JSON format."""
    error_data = {
        "status": "error",
        "message": error_message,
        "timestamp": str(import_datetime().now()) # or your preferred timestamp
    }
    return f"data: {json.dumps(error_data)}\n\n"

# Helper for the timestamp inside the utility
def import_datetime():
    return datetime

def format_final_payload(data: dict) -> str:
    """Serializes data to a server-sent event (SSE) format."""
    import json
    return f"data: {json.dumps(data)}\n\n"

def update_chat_history(history: str, role: str, message: str) -> str:
    """Appends new messages to the history transcript string."""
    entry = f"{role.upper()}: {message}\n"
    return (history or "") + entry


def resolve_recent_mention(messages: list, extractor, skip_predicate=None):
    """Scans messages most-recent-first, applying `extractor` to each message's content and
    returning the first (i.e. most recent) truthy result. `skip_predicate`, if given, skips
    a message's content entirely without trying to extract from it (e.g. bare approval
    replies like "yes"/"ok" that never carry the real topic). Returns None if nothing in
    history matches — callers apply their own final fallback."""
    for m in reversed(messages or []):
        content = getattr(m, "content", "") if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else str(m))
        if skip_predicate and skip_predicate(content):
            continue
        result = extractor(content)
        if result:
            return result
    return None


# --- run_react_loop helpers (backend/services/agent_workflow.py's tool_agent_node loop) ---
# New standalone logic for the ReAct loop goes here rather than as another closure inside
# agent_workflow.py — agent_workflow.py stays the graph nodes, plain functions accumulate here,
# working toward eventually splitting them the way app.py/app_utils.py already are.

_DEFINITION_INDEX_BLOCK_RE = re.compile(
    r"Top-level definitions found in it:\n(.*?)\nCall read_repo_file", re.DOTALL
)
_DEFINITION_INDEX_LINE_RE = re.compile(r"^\s*line (\d+): (?:def|class) (\w+)", re.MULTILINE)


def parse_definition_index_from_observation(observation: str) -> dict:
    """A read_repo_file truncation response (agent_workflow._build_definition_index) embeds a
    real, line-numbered table of every top-level def/class in the file — a genuine answer to
    "which line is X on," not a guess. Extracts it into {symbol_name: line_number} so a later
    start_line choice for a named symbol can be checked against that ground truth. Returns {}
    when the observation isn't a truncated-without-start_line read_repo_file result at all."""
    block_match = _DEFINITION_INDEX_BLOCK_RE.search(observation or "")
    if not block_match:
        return {}
    return {
        m.group(2): int(m.group(1))
        for m in _DEFINITION_INDEX_LINE_RE.finditer(block_match.group(1))
    }


def find_mismatched_start_line_note(
    purpose: str, path: str, start_line, line_count, default_window: int, index_for_path: dict
) -> str | None:
    """Real, evidenced gap (docs/coding-agent-roadmap.md, Section 10): a production trace ran
    two extra search tools trying to relocate a symbol it had already read the definition index
    for a few steps earlier, then still guessed the wrong start_line — the two search tools it
    reached for (search_code, trace_symbol) don't even return line numbers, so the guess was
    inevitable once it stopped consulting the index it already had.

    Returns a corrective note when `purpose` names a symbol this same path's own earlier
    definition index already placed at a real line number, but the requested start_line/
    line_count window won't actually reach it. None when there's nothing to flag — including
    when index_for_path is empty (no earlier index seen for this path this turn) or start_line
    wasn't given at all (a fresh, unwindowed read has nothing to compare against yet)."""
    if not index_for_path or not start_line:
        return None
    try:
        start_line = int(start_line)
    except (TypeError, ValueError):
        return None
    try:
        window = int(line_count) if line_count else default_window
    except (TypeError, ValueError):
        window = default_window
    for symbol, real_line in index_for_path.items():
        if symbol in (purpose or "") and not (start_line <= real_line < start_line + window):
            return (
                f"(Note: your purpose mentions '{symbol}', which an earlier read of {path} "
                f"already placed at line {real_line} — this read's window (lines "
                f"{start_line}-{start_line + window - 1}) doesn't reach it. Re-call "
                f"read_repo_file with start_line={real_line} to actually see it, instead of "
                "guessing a location.)"
            )
    return None


# --- read_repo_file's truncation/paging knobs (moved from agent_workflow.py) ---
# A flat character-count cap on a large file (this repo has several 2000+ line files) silently
# cuts off before ever reaching a function defined further down — a real fabrication risk, since
# a model can mistake "I was given the start of the file" for "I read the file" and fill the gap
# it never saw with something plausible instead of real code. start_line lets a caller jump
# straight to a specific line once it knows where to look (from _build_definition_index below, or
# from search_code), the same way a normal editor would.
_READ_FILE_CHAR_CAP = 3500
_READ_FILE_DEFAULT_LINE_WINDOW = 150
# Always show at least this much raw content even when a large file's definition index needs
# most of the observation's budget (docs/coding-agent-roadmap.md, Section 12) — the index takes
# priority, but the snippet never shrinks to nothing.
_READ_FILE_MIN_SNIPPET_CHARS = 500
_DEFINITION_INDEX_WRAPPER = (
    "\n\n... [truncated — this file has {line_count} lines total, too long to show in full. "
    "Top-level definitions found in it:\n{index}\nCall read_repo_file again with start_line set "
    "to the one you actually need — do not assume the file's contents past this point from "
    "general knowledge of what a file like this usually contains.]"
)
_TOP_LEVEL_DEF_RE = re.compile(r'^(?:async\s+)?(def|class)\s+(\w+)')


def _build_definition_index(lines: list) -> str:
    """A lightweight table of contents for a large file: every top-level (module-level, not
    nested inside a class/function) def/class and the line it starts on. Lets the model jump
    straight to the real function it needs via start_line instead of guessing or reading from
    the top of a multi-thousand-line file and hoping the truncated slice happens to reach it."""
    entries = [
        f"  line {i}: {m.group(1)} {m.group(2)}"
        for i, line in enumerate(lines, start=1)
        if (m := _TOP_LEVEL_DEF_RE.match(line))
    ]
    return "\n".join(entries[:150])


# --- run_react_loop's own bookkeeping helpers (moved from agent_workflow.py) ---

def _parse_agent_json(raw_text: str) -> dict:
    """Defensive JSON extraction shared by every ReAct-loop tool: tries direct parsing,
    then a regex-located JSON object, then gives up and returns {} (the loop treats a
    decision with no recognizable "action" as a failed step, not a crash)."""
    clean_text = raw_text.strip()
    clean_text = re.sub(r"^```(?:json|python)?\s*", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"\s*```$", "", clean_text)

    try:
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        json_match = re.search(r"(\{.*\})", clean_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


_EMPTY_OBSERVATION_VALUES = {
    "", "[]", "{}", "none", "null", "no results", "no results found",
    "no matches.", "no matches", "no diff context available.", "no similar file paths found.",
}


def _is_empty_observation(observation: str) -> bool:
    """An empty result (no matches, an empty list, an empty file listing) is not the same as
    "nothing exists" — it's very often a sign the query, path, repo, or collection was wrong,
    not proof of absence (this is exactly what happened with the repo-misresolution bug
    earlier: a wrong repo name didn't error, it just came back empty). Treated the same way
    an outright "ERROR: ..." is by the retry-nudge tracking in run_react_loop, since both are
    "this step didn't actually get you anywhere" — the model just can't tell that from the
    text alone without this check."""
    return observation.strip().lower() in _EMPTY_OBSERVATION_VALUES


def _mentions_unresolved_truncation(observation: str) -> bool:
    """A real production trace showed the model treat a truncated read_repo_file result as if
    it were the whole file: it read agent_workflow.py once (truncated at _READ_FILE_CHAR_CAP,
    nowhere near run_react_loop's actual body), never re-called with start_line despite the
    truncation note explicitly saying to, and confidently proposed a fully fabricated
    reimplementation instead — with 3 full steps of budget still unused, so this wasn't even
    budget pressure. The truncation note already tells it not to guess; this makes that
    mechanical instead of relying on it to comply. Reuses the exact same marker text _read_file
    emits (both no-start_line truncation variants share "truncated — this file has"), so a
    follow-up read_repo_file call with a genuinely different start_line — a different
    args_signature — clears it via the same unretried_inconclusive_tools machinery an ERROR or
    empty result already does, no new tracking dict needed.

    A second real trace immediately exposed a gap in this same fix: forced to retry, the model
    correctly called read_repo_file WITH a start_line — but that's a THIRD, different message
    shape ("N more lines below — re-call with a higher start_line"), not the first two, so it
    wasn't covered and the model declared "final" from lines 400-549 of a 4552-line file with
    the real code (line ~2350) still unread. Same fix, same reasoning, just the missing variant."""
    return "truncated — this file has" in observation or "more lines below" in observation


_MAX_OBSERVATION_CHARS = 4000


def _truncate_observation(observation) -> str:
    """Every other action in this loop self-limits its own output (_read_file caps at 3500
    chars, _list_tree caps at 400 paths) — an unbounded PyMongo query was the one path with no
    cap at all, and a raw find() over a collection that stores embedding vectors is easily
    hundreds of documents with long float arrays each. That observation gets JSON-dumped
    straight into both the next reasoning prompt (_format_react_attempts) and the final
    footer (_format_observation_for_footer), so one huge result set is enough to blow past
    Gemini's 1,048,576-token input limit on the final Voice Composer call. Truncating once,
    right where every tool's observation is captured, protects all of them generically instead
    of special-casing Mongo."""
    text = observation if isinstance(observation, str) else json.dumps(observation, default=str, indent=2)
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    return text[:_MAX_OBSERVATION_CHARS] + f"\n... [truncated — {len(text)} total characters]"


class _UnsafeActionRequested(Exception):
    """Raised by run_react_loop when is_unsafe() flags a step, so the calling node can
    build its own tool-specific approval-required response instead of the loop guessing.

    attempts carries whatever the loop had accumulated before this step, the same way
    _ClarificationNeeded does below — needed by any unsafe action whose approval must resume the
    SAME investigation thread (e.g. propose_code_plan, approved then continuing on to draft the
    real implementation) rather than dispatch a one-shot external action whose content was
    already fully composed before the approval card (propose_append_target_doc/
    propose_send_email don't read this field, and don't need to)."""
    def __init__(self, decision: dict, attempts: list | None = None):
        self.decision = decision
        self.attempts = attempts or []


class _ClarificationNeeded(Exception):
    """Raised by run_react_loop when the model reports genuine uncertainty (action="clarify")
    instead of guessing — carries the question to ask and the attempts made so far, so the
    calling node can pause for a real answer and resume the loop from where it left off
    instead of starting over."""
    def __init__(self, question: str, attempts: list):
        self.question = question
        self.attempts = attempts


# Catches the exact shape of a real production failure: a confident, detailed "final" answer
# denying a capability that was sitting in that same turn's own action menu the whole time
# (browser_navigate — see docs/coding-agent-roadmap.md, "tool_agent_node had zero conversation
# history"). A prose rule already tells the model to check its own menu before denying a
# capability; this is the mechanical backstop for when it doesn't, checking the actual "final"
# TEXT against the actual menu rather than trusting the model caught its own contradiction. Only
# the denial-shaped phrase is generic/repo-agnostic here — WHICH capabilities to watch for is
# domain knowledge the caller (tool_agent_node) supplies via capability_denial_watchlist, the
# same config-driven shape as stuck_action_redirects.
_CAPABILITY_DENIAL_RE = re.compile(
    r"\b(i (?:don'?t|do not) (?:actually |currently )?have\b|i can'?t\b|i cannot\b|i'?m not able to\b|"
    r"i (?:am|'m) (?:not able|unable) to\b|i (?:am|'m) only (?:able|allowed) to (?:read|view|search)\b|"
    r"read-only\b|i lack\b|no access to\b|i (?:don'?t|do not) have access\b)",
    re.IGNORECASE,
)


# A different failure shape from the same production trace as the capability-denial backstop
# above (docs/coding-agent-roadmap.md, Section 7): three self-drive attempts in a row each
# fabricated a different kind of ground truth (invented code, a false "this doesn't exist" claim,
# and — the one this catches — a confidently-presented diff editing a data structure that was
# never actually verified to exist). Before a "final" containing a diff against an EXISTING file
# is accepted, at least one of that diff's own claimed pre-existing lines (context or removed,
# never a `+` line) must actually appear in a real read_repo_file observation for that same path
# recorded THIS turn — otherwise the diff was composed from a plausible guess, not derived from
# what was actually fetched. String/regex based, not a real diff parser — same accepted
# soft-failure-mode tradeoff already used by trace_symbol/find_file in this file.
_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.MULTILINE)
_DIFF_NEW_FILE_RE = re.compile(r"^new file mode")


def _extract_diff_file_grounding_lines(final_answer: str) -> dict:
    """Maps each existing-file path named in a `diff --git` block inside final_answer to the
    non-added lines (context or removed) inside its hunks — the lines the diff claims already
    existed in that file before this change. A brand-new file (a `new file mode` line before the
    next file header) is skipped entirely, since there's nothing pre-existing to verify."""
    files: dict = {}
    current_path = None
    skip_current = False
    for line in final_answer.splitlines():
        header_match = _DIFF_FILE_HEADER_RE.match(line)
        if header_match:
            current_path = header_match.group(1)
            skip_current = False
            files.setdefault(current_path, [])
            continue
        if current_path is None:
            continue
        if _DIFF_NEW_FILE_RE.match(line):
            skip_current = True
            files.pop(current_path, None)
            continue
        if skip_current or line.startswith(("+++", "---", "index ", "@@")) or line.startswith("+"):
            continue
        if line.startswith("-"):
            files[current_path].append(line[1:].strip())
        elif line.startswith(" "):
            files[current_path].append(line[1:].strip())
    return {path: [l for l in lines if l] for path, lines in files.items()}


def _final_diff_disagrees_with_fetched_content(final_answer: str, attempts: list) -> str | None:
    """Returns the file path of the first diff hunk whose claimed pre-existing lines never
    actually appeared in a real read_repo_file result for that path this turn — a strong signal
    the diff was composed from a guess instead of derived from real fetched content. None if every
    diffed file either has real corroborating evidence or the answer contains no diff at all."""
    for path, grounding_lines in _extract_diff_file_grounding_lines(final_answer).items():
        if not grounding_lines:
            continue
        fetched_text = "\n".join(
            a["observation"] for a in attempts
            if a.get("action_desc", "").startswith("read_repo_file(") and f"path={path}" in a["action_desc"]
        )
        if not fetched_text or not any(line in fetched_text for line in grounding_lines):
            return path
    return None


async def _check_final_answer_grounding(final_answer: str, attempts: list, llm) -> list[str]:
    """Runs a lightweight LLM-based grounding check comparing a ReAct loop's proposed final
    answer against the REAL tool observations gathered this turn — the check the reward
    evaluator structurally cannot do, since by the time evaluate_response runs, this final_answer
    has already been folded into "the DATA" the Voice Composer rewrites and the evaluator is told
    to trust; a fabrication made here becomes the ground truth everything downstream judges
    against. This has to run right here, while final_answer and attempts still exist as separate
    things. Returns a list of specific unsupported-claim strings (empty if grounded, OR if the
    check itself failed to run — fail-open, same as every other soft-failure in this module,
    since a broken checker should never be able to block every future answer).
    _final_diff_disagrees_with_fetched_content above catches one specific, mechanically-checkable
    shape of this (a fabricated diff); this is the general case for arbitrary prose claims, which
    has no reliable regex/string check and genuinely needs a second model's judgment instead.
    """
    if not attempts:
        return []
    try:
        prompt = GROUNDING_CHECK_PROMPT.format(
            final_answer=final_answer,
            attempts=_format_react_attempts(attempts),
        )
        response = await llm.ainvoke(prompt)
        resp_content = response.content if hasattr(response, "content") else str(response)
        raw_text = (
            "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content)
            if isinstance(resp_content, list) else str(resp_content)
        )
        decision = _parse_agent_json(raw_text)
    except Exception:
        logger.exception("[grounding_check] check failed to run — treating as grounded (fail-open).")
        return []
    if decision.get("grounded"):
        return []
    claims = decision.get("unsupported_claims")
    return [c.strip() for c in claims if isinstance(c, str) and c.strip()] if isinstance(claims, list) else []


# The grounding check above catches FALSE claims — something stated as fact that never appeared
# in attempts. Its own prompt explicitly says not to flag a reasonable synthesis or a plainly-
# labeled inference. Generic, exampleish proposed code usually isn't factually wrong about
# anything — it doesn't claim a function exists that doesn't, it doesn't misquote a value. It's
# boilerplate that would look identical whether the model had read the repo or read nothing at
# all. That's a different axis entirely — "is this actually derived from what you found" versus
# "is this true" — and no amount of tuning the existing check catches it, since by design it's
# only allowed to object to false statements. These two checks target that separate axis.
_CODE_BLOCK_RE = re.compile(r"```[\w]*\n.*?```", re.DOTALL)
# Kept intentionally narrow to the tools that actually produce real code/content from this repo —
# a successful list_repo_tree or list_commits proves the model looked around, but not that it
# actually read anything resembling an implementation to model new code on.
_CODE_PRECEDENT_RESEARCH_ACTIONS = ("read_repo_file(", "search_code(", "search_literal(", "find_file(")


def _final_answer_has_code_block(final_answer: str) -> bool:
    return bool(_CODE_BLOCK_RE.search(final_answer or ""))


def _has_successful_code_precedent_research(attempts: list) -> bool:
    """True if at least one read_repo_file/search_code/search_literal/find_file attempt this turn
    came back with a real, non-empty, non-error result — not just that one of these was CALLED,
    which says nothing about whether it actually returned anything to model new code on."""
    for attempt in attempts:
        action_desc = attempt.get("action_desc", "") or ""
        if not action_desc.startswith(_CODE_PRECEDENT_RESEARCH_ACTIONS):
            continue
        observation = attempt.get("observation", "") or ""
        if not observation.startswith("ERROR") and not _is_empty_observation(observation):
            return True
    return False


# Declarations in a proposed code block that might collide with something already in the repo.
# Names this generic are declared in dozens of unrelated places, so a hit on one says nothing
# about "the" existing thing being redeclared — excluded outright rather than judged case by case.
_DECLARED_NAME_RE = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:const|let|var|function|class|interface|type|enum|def)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
_GENERIC_DECLARED_NAMES = frozenset({
    "result", "results", "response", "request", "data", "value", "values", "props", "state",
    "index", "items", "item", "error", "event", "handler", "callback", "options", "config",
    "params", "args", "kwargs", "default", "main", "test", "setup", "helper", "wrapper",
    "render", "update", "create", "delete", "submit", "change", "click",
})
_MIN_DECLARED_NAME_LENGTH = 6
_MAX_DECLARED_NAMES_CHECKED = 12
# A name already declared in more places than this is a common idiom, not "the" existing thing.
_MAX_EXISTING_LOCATIONS = 3


def _extract_declared_names(final_answer: str) -> list:
    """Distinct, non-generic identifiers the answer's code blocks DECLARE (const/function/class/
    def/...), in first-seen order, capped. Only looks inside fenced code blocks."""
    names: list = []
    for block in _CODE_BLOCK_RE.findall(final_answer or ""):
        for match in _DECLARED_NAME_RE.finditer(block):
            name = match.group(1)
            if (
                len(name) >= _MIN_DECLARED_NAME_LENGTH
                and name.lower() not in _GENERIC_DECLARED_NAMES
                and name not in names
            ):
                names.append(name)
                if len(names) >= _MAX_DECLARED_NAMES_CHECKED:
                    return names
    return names


def _find_unseen_existing_declarations(existing: dict, attempts: list) -> list:
    """From {name: [{"path","line","text"}, ...]} (declarations that already exist in the repo),
    returns the ones the model has NOT actually looked at this turn: [{"name","path","line"}]. A
    declaration counts as seen if its exact line appears in any attempt's observation — so a
    legitimate "here is the updated X" answer passes once the model read the original X, and only
    a redeclaration made without ever having seen what's already there is flagged."""
    observations = [(a.get("observation") or "") for a in attempts]
    unseen = []
    for name, locations in (existing or {}).items():
        if not locations or len(locations) > _MAX_EXISTING_LOCATIONS:
            continue
        if any(loc["text"] and any(loc["text"] in obs for obs in observations) for loc in locations):
            continue
        first = locations[0]
        unseen.append({"name": name, "path": first["path"], "line": first["line"]})
    return unseen


async def _check_idiom_grounding(final_answer: str, attempts: list, llm) -> dict | None:
    """The second, distinct check "tailored vs exampleish" needs, since _check_final_answer_
    grounding is structurally forbidden from flagging this (its own prompt tells it not to flag
    a reasonable synthesis or inference, and generic code is exactly that shape — not false,
    just not derived from anything real). Returns None when the proposed code is judged
    genuinely grounded, when final_answer has no code block at all (nothing to judge), when
    attempts is empty (nothing to compare against — _has_successful_code_precedent_research
    above is what actually enforces there being something here before this ever runs for real),
    or if the check itself fails to run (fail-open, same convention as every other soft-failure
    in this module). Otherwise returns {"reason_category": "no_real_example_found" |
    "real_example_ignored", "reason": "..."} — the category distinguishes a discovery problem
    (nothing comparable was ever found) from a compliance problem (a real precedent WAS read and
    the proposed code didn't use it), since those need genuinely different corrective nudges."""
    if not attempts or not _final_answer_has_code_block(final_answer):
        return None
    try:
        prompt = IDIOM_GROUNDING_CHECK_PROMPT.format(
            final_answer=final_answer,
            attempts=_format_react_attempts(attempts),
        )
        response = await llm.ainvoke(prompt)
        resp_content = response.content if hasattr(response, "content") else str(response)
        raw_text = (
            "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in resp_content)
            if isinstance(resp_content, list) else str(resp_content)
        )
        decision = _parse_agent_json(raw_text)
    except Exception:
        logger.exception("[idiom_grounding_check] check failed to run — treating as grounded (fail-open).")
        return None
    if decision.get("grounded", True):
        return None
    reason = (decision.get("reason") or "").strip()
    if not reason:
        # Flagged as ungrounded but gave nothing to act on — fail-open rather than reject with a
        # notice that has nothing concrete to tell the model to actually do differently.
        return None
    reason_category = decision.get("reason_category")
    if reason_category not in {"no_real_example_found", "real_example_ignored"}:
        reason_category = "no_real_example_found"
    return {"reason_category": reason_category, "reason": reason}


def _format_observation_for_footer(observation) -> str:
    if isinstance(observation, str):
        return observation
    return json.dumps(observation, default=str, indent=2)


def _format_react_attempts(attempts: list) -> str:
    if not attempts:
        return "(none yet — this is the first step)"
    return "\n\n".join(
        f"Attempt {i} — Purpose: {a['purpose']}\nAction: {a['action_desc']}\nObservation: {a['observation']}"
        for i, a in enumerate(attempts, 1)
    )


def _format_attempts_steps(attempts: list) -> str:
    """Renders attempts as 'Step N — purpose / Result' blocks for display — used both in a
    completed answer's footer and in a clarification pause message. Purely a display renderer:
    resuming a paused clarification reads attempts back from real checkpointed state
    (state["paused_clarification"]), not by re-parsing this rendered text."""
    return "\n\n".join(
        f"**Step {i} — {a['purpose']}:**\n```\n{a['action_desc']}\n```\n"
        f"**Result:**\n```\n{_format_observation_for_footer(a['observation'])}\n```"
        for i, a in enumerate(attempts, 1)
    )


# --- find_file's fuzzy matching + trace_symbol's write/read classification (moved) ---

_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")

# find_file's fuzzy matching, so a colloquial name ("navbar") can still surface a differently
# named real file (menu-navigator.tsx) even though they share no exact token — search_code only
# matches GitHub's literal keyword index, which finds nothing at all in that case. Splits both on
# non-alphanumeric characters AND camelCase boundaries so "MenuNavigator" and "menu-navigator"
# tokenize to the same {"menu", "navigator"} regardless of naming convention.
_PATH_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FUZZY_MATCH_CUTOFF = 0.5
_FUZZY_MATCH_LIMIT = 15


def _tokenize_for_fuzzy_match(text: str) -> set:
    tokens = set()
    for fragment in _PATH_TOKEN_SPLIT_RE.split(text):
        if not fragment:
            continue
        for sub in _CAMEL_BOUNDARY_RE.split(fragment):
            if sub:
                tokens.add(sub.lower())
    return tokens


def fuzzy_query_tokens(query: str) -> set:
    """Tokens for a find_file query. A trailing file extension is dropped first (a query of
    "local_workspace.py" means the file local_workspace, and "py" would otherwise be a token no
    path ever matches, since paths are scored with their own extension already stripped)."""
    return _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", query.strip()))


def _fuzzy_path_score(query_tokens: set, path: str) -> float:
    """How well `path` (folder names and filename, extension stripped) covers the query: each
    query token is matched to its best path token — an exact match is 1.0, otherwise difflib's
    character-level ratio so near-misses (navbar/navigator, singular vs. plural) still score
    usefully — and the score is the average across ALL query tokens. Averaging matters: scoring
    by the single best token gave 1.0 to every file sharing one word with the query, so a search
    for "local_workspace.py" ranked local_start.ps1 and every other "local*" file level with the
    real local_workspace.py, burying it."""
    path_tokens = _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", path))
    if not path_tokens or not query_tokens:
        return 0.0
    per_token = []
    for query_token in query_tokens:
        best = 0.0
        for path_token in path_tokens:
            if query_token == path_token:
                best = 1.0
                break
            best = max(best, difflib.SequenceMatcher(None, query_token, path_token).ratio())
        per_token.append(best)
    return sum(per_token) / len(per_token)


# trace_symbol's write-vs-read classification — regex-based (not a real parser) by design, to
# stay consistent with find_file's own heuristic rather than pull in a per-language AST
# dependency for one tool. Covers both this repo's Python and TypeScript/React conventions:
# a plain assignment, an attribute/dict-style assignment, a def/class that IS the symbol, a
# React state setter call (setSymbol(...)), a useState/useReducer/useRef/useMemo destructuring
# that defines the symbol, and a function returning it — matching exactly the "write/decide
# site" categories described in docs/coding-agent-roadmap.md. Everything else that mentions the
# symbol (a read, a prop being consumed, a log line, a re-export) falls through to "read".
def _classify_symbol_line(symbol: str, line: str) -> str:
    stripped = line.strip()
    escaped = re.escape(symbol)

    if re.match(rf'^(export\s+)?(async\s+)?(def|function|class)\s+{escaped}\b', stripped):
        return "write"

    setter_name = f"set{symbol[0].upper()}{symbol[1:]}" if symbol else ""
    if setter_name and re.search(rf'\b{re.escape(setter_name)}\s*\(', stripped):
        return "write"

    if re.search(rf'\b{escaped}\b[^=]*=\s*use(State|Reducer|Ref|Memo|Context)\s*\(', stripped):
        return "write"

    if re.search(rf'(?<![=!<>]){escaped}\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'\.{escaped}\s*=(?!=)', stripped) or re.search(rf'\[["\']{escaped}["\']\]\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'^return\b.*\b{escaped}\b', stripped):
        return "write"

    return "read"


# --- task-shape detectors (moved) ---

# A real production trace showed a static system-prompt paragraph telling the model to reach
# for browser_navigate on "a real page's current, real content or appearance" wasn't enough —
# asked to "inspect the actual page" to fix a z-index conflict, it did 15 steps of pure repo
# reading and never opened a browser once (see docs/coding-agent-roadmap.md, "Failure B").
# Rather than trust a general system-prompt rule to out-compete many turns of code-reading
# momentum, this detects the specific category of question (layout/rendering/visual state that
# literally cannot be confirmed from source alone) and injects a directive into THIS turn's own
# question text — much harder to deprioritize than a rule buried among many others.
_VISUAL_INSPECTION_RE = re.compile(
    r"\b(inspect|actual page|actual site|how (?:it|this|that) (?:looks|renders|appears)|"
    r"z-?index|stacking|overlap(?:ping)?|visually|on[- ]?screen|in the browser|"
    r"css (?:issue|bug|problem)|layout (?:issue|bug|problem)|rendering (?:issue|bug|problem))\b",
    re.IGNORECASE,
)


def _mentions_visual_inspection(text: str) -> bool:
    return bool(_VISUAL_INSPECTION_RE.search(text or ""))


# A real production trace showed the default step budget forced a premature, fabricated answer
# on a "scan the repo and plan this refactor" ask: it needs one search plus a confirmatory
# trace_symbol per candidate call site to actually be exhaustive, which the flat 7-step (or even
# 14-step deep) budget doesn't leave room for — so it pattern-completed the rest instead of
# admitting it ran out of steps. This detects that task shape from the user's own wording and
# grants it the same higher budget deep_thinking gets, regardless of whether deep_thinking is on.
_AUDIT_TASK_RE = re.compile(
    r"\b(scan the (?:whole |entire )?repo|every (?:file|place|call ?site|usage|occurrence)s?|"
    r"across the (?:whole |entire )?(?:repo|codebase)|cross-cutting|\baudit\b|"
    r"(?:refactor|migration|architecture) plan|which files (?:do we|need to|touch|are)|"
    r"how many files|plan (?:the|this|a) (?:refactor|migration|architecture))\b",
    re.IGNORECASE,
)


def _is_audit_style_task(text: str) -> bool:
    return bool(_AUDIT_TASK_RE.search(text or ""))


# search_literal exists specifically because search_code rides GitHub's hosted search index
# (capped at ~20 results, subject to indexing lag) and can silently miss real matches — but it's
# opt-in, so an audit-style task ("find every place X is used") can still reach for search_code
# out of habit and come back with a plausible-looking but incomplete answer (docs/coding-agent-
# roadmap.md, Section 4c). Prose guidance for this already existed in TOOL_AGENT_PROMPT and wasn't
# enough on its own — same lesson this whole file keeps re-learning — so this rides the same
# mechanical injection already proven for the architecture map below instead of adding a new one.
_AUDIT_TASK_SEARCH_NUDGE = (
    "AUDIT TASK DETECTED: prefer search_literal over search_code for exhaustive results this turn "
    "— search_code rides GitHub's hosted search index (capped, subject to indexing lag) and can "
    "miss real matches; search_literal greps the actual repo tree directly.\n"
)


# --- README-first context for audit-style tasks ---

# A cross-file refactor/audit question benefits from the project's own documented purpose and
# conventions BEFORE diving into individual files — the same reasoning that motivated the
# architecture map below: a capability/context the model must remember to seek out on its own
# (here, thinking to read README.md before investigating) gets skipped under pressure, so this is
# injected automatically into the prompt rather than left as something it has to think to do.
# Root-level only, deliberately — a repo's own top-level README is the one doc almost guaranteed
# to describe what the whole project actually is; a docs/ subfolder's structure varies too much
# project to project to guess at without real signal, and this is meant to be a cheap, safe
# default, not an attempt to discover every doc in the repo.
_README_CANDIDATE_PATHS = ("README.md", "Readme.md", "README.rst", "README.txt", "README")
_README_MAX_CHARS = 6000


def _find_readme_path(tree_items: list) -> str | None:
    paths = {item.get("path", "") for item in tree_items}
    for candidate in _README_CANDIDATE_PATHS:
        if candidate in paths:
            return candidate
    return None


def _build_readme_context(tree_items: list, fetch_content) -> str:
    """Fetches the repo's own top-level README (if any) and returns a prompt-ready block —
    fetch_content(path) must return (content, error) like _fetch_file_content does. Empty string
    if there's no README at the root, the fetch fails, or it's blank — a missing/unreachable
    README isn't an error worth surfacing to the model, just nothing extra to add this turn."""
    readme_path = _find_readme_path(tree_items)
    if not readme_path:
        return ""
    content, error = fetch_content(readme_path)
    if error or not isinstance(content, str) or not content.strip():
        return ""
    truncated = content[:_README_MAX_CHARS]
    if len(content) > _README_MAX_CHARS:
        truncated += f"\n... [truncated — {readme_path} continues past this point]"
    return (
        f"PROJECT README ({readme_path}) — real documented context; read this before individual "
        "files so you understand what this project actually is/does before investigating "
        f"specifics:\n{truncated}\n\n"
    )


# --- architecture map (moved) ---

# An auto-injected, repo-wide internal-import graph for audit-style tasks (docs/coding-agent-
# roadmap.md, Section 4c). search_literal/trace_symbol find where a SYMBOL is referenced; this
# shows which FILES depend on which other files, which is what actually answers "what else does
# this touch" for a cross-file refactor — the exact gap that produced the fabricated per-user-
# token plan in Section 4b. Injected straight into the prompt (like `schema` already is) rather
# than offered as a new opt-in tool_action, because this project has now independently shown
# three times (Sections 0, 0b, 4b) that a capability the model must remember to reach for gets
# skipped under pressure.
_ARCH_MAP_MAX_FILES_SCANNED = 200
_ARCH_MAP_MAX_FILE_BYTES = 200_000
_ARCH_MAP_MAX_ENTRIES = 150
_ARCH_MAP_PY_EXTENSION = ".py"
_ARCH_MAP_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

_JS_IMPORT_RE = re.compile(
    r"""(?:^\s*import\s+['"]([^'"]+)['"])|(?:\bfrom\s+['"]([^'"]+)['"])|(?:\brequire\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)


def _extract_python_imports(content: str) -> list[str]:
    # Best-effort, same spirit as _classify_symbol_line's regex heuristic — a file with a real
    # (rare) syntax error just contributes no edges to the map instead of failing the whole thing.
    # Relative imports are kept as their literal dotted form (e.g. ".utils") rather than resolved
    # to a file path — the model can trivially map that back to a real path itself, and it avoids
    # building a second, error-prone module-resolution layer for comparatively little benefit.
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * (node.level or 0)
            if node.module:
                imports.append(f"{dots}{node.module}")
            else:
                # `from . import utils` — module is None, the actual reference (a sibling
                # module) lives in the imported names instead.
                imports.extend(f"{dots}{alias.name}" for alias in node.names)
    return imports


def _extract_js_imports(content: str) -> list[str]:
    # Regex, not a real parser — matches this codebase's own precedent (_classify_symbol_line)
    # for TS/JS specifically. Covers `import x from '...'`, bare `import '...'`, and
    # `require('...')`; does not cover dynamic `import(variable)` or path-aliased imports
    # (e.g. tsconfig `@/`) — an acceptable, documented gap rather than a bundler-grade resolver.
    imports = []
    for match in _JS_IMPORT_RE.finditer(content):
        imports.append(next(g for g in match.groups() if g))
    return imports


def _is_internal_python_import(import_str: str, top_level_segments: set) -> bool:
    if import_str.startswith("."):
        return True
    return import_str.split(".")[0] in top_level_segments


def _import_scope_key(importing_path: str, import_str: str) -> str:
    """A RELATIVE import string alone (Python's '.utils'/'..models', or JS/TS's './api') is
    ambiguous repo-wide — it resolves relative to the IMPORTING file's own directory, so the
    identical string written in two different directories names two completely different real
    modules. Without this, _build_architecture_map's reverse map (imported_by) would key on the
    bare string alone and silently merge unrelated files from different directories into one
    entry — e.g. every file anywhere in the repo that happens to write `from .utils import x`
    bundled together, even though `.utils` inside backend/services/ and `.utils` inside
    frontend/components/ are unrelated. An ABSOLUTE import (a real top-level package name) has no
    such ambiguity — it names the same real module no matter which file imports it — so it's
    returned unprefixed, keeping the map's normal (non-relative) case exactly as readable as
    before. Deliberately NOT a real module resolver (matches _extract_python_imports' own
    documented tradeoff) — just enough disambiguation to stop two unrelated modules from
    colliding into one entry; scoping by directory, not resolving to the actual target file."""
    if not import_str.startswith("."):
        return import_str
    importer_dir = importing_path.rsplit("/", 1)[0] if "/" in importing_path else "(repo root)"
    return f"{importer_dir}::{import_str}"


def _repo_top_level_segments(tree_items: list) -> set:
    # Derived from the real tree every time (never hardcoded) so this works identically on any
    # repo, not just this one — matches this session's own multi-repo/multi-user design goal.
    segments = set()
    for item in tree_items:
        path = item.get("path", "")
        parts = path.split("/")
        segments.add(parts[0])
        if len(parts) == 1 and parts[0].endswith(_ARCH_MAP_PY_EXTENSION):
            segments.add(parts[0][: -len(_ARCH_MAP_PY_EXTENSION)])
    return segments


def _build_architecture_map(tree_items: list, fetch_content) -> str:
    """Builds a compact FILE -> internal imports (and its inverse) map from real file content —
    fetch_content(path) must return (content, error) like _fetch_file_content does. Only internal
    (own-repo) imports are kept; a bare package import (os, react, requests) would otherwise
    dominate the reverse map with useless high fan-in and drown out the edges that actually matter
    for scoping a refactor's blast radius."""
    top_level_segments = _repo_top_level_segments(tree_items)
    candidates = [
        item for item in tree_items
        if item.get("path", "").endswith((_ARCH_MAP_PY_EXTENSION,) + _ARCH_MAP_JS_EXTENSIONS)
        and item.get("size", 0) <= _ARCH_MAP_MAX_FILE_BYTES
    ][:_ARCH_MAP_MAX_FILES_SCANNED]

    imports_by_file: dict = {}
    imported_by: dict = {}
    for item in candidates:
        path = item.get("path", "")
        content, error = fetch_content(path)
        if error or not isinstance(content, str):
            continue
        if path.endswith(_ARCH_MAP_PY_EXTENSION):
            internal = [i for i in _extract_python_imports(content) if _is_internal_python_import(i, top_level_segments)]
        else:
            internal = [i for i in _extract_js_imports(content) if i.startswith(".")]
        if not internal:
            continue
        imports_by_file[path] = sorted(set(internal))
        for imp in internal:
            # See _import_scope_key's own docstring — a relative import must be scoped by the
            # importing file's directory here (the reverse map), even though the forward map
            # above keeps the raw string exactly as written in the file.
            imported_by.setdefault(_import_scope_key(path, imp), set()).add(path)

    if not imports_by_file:
        return ""

    forward_lines = [
        f"  {path} -> {', '.join(imports_by_file[path])}"
        for path in sorted(imports_by_file)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    reverse_lines = [
        f"  {imp} <- imported by: {', '.join(sorted(imported_by[imp]))}"
        for imp in sorted(imported_by)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    return (
        "PRE-COMPUTED ARCHITECTURE MAP (real internal-import graph — exhaustive for the files "
        "scanned, not a guess; use this to find every file a change would touch instead of "
        "relying on search_code alone):\n"
        "FILE -> ITS INTERNAL IMPORTS:\n" + "\n".join(forward_lines) +
        "\n\nINTERNAL MODULE -> IMPORTED BY:\n" + "\n".join(reverse_lines)
    )


# ---------------------------------------------------------------------------
# Local-folder editing: recognising an edit request, and keeping stale "I can't write" replies
# from teaching the model that it can't. Everything here is mechanical on purpose — a prompt line
# saying "you can edit files" kept losing to many turns of the model's own earlier refusals.
# ---------------------------------------------------------------------------

# The longer verbs have no trailing \b on purpose: people type "updatethe trace-sidebar" (a real
# message) and still mean it. The short ones keep it so "add" doesn't match "address".
_EDIT_VERB_RE = re.compile(
    r"\b(?:change|modify|update|replace|rename|remove|delete|insert|refactor|rewrite|implement|"
    r"create|adjust|apply|tweak)|\b(?:edit|fix|add|move|swap|make|write)\b",
    re.IGNORECASE,
)
# Something code-shaped to act on. A bare verb ("make your replies shorter", "update my memory")
# isn't an edit request; a verb plus a file name / UI element / code term is.
_CODE_TARGET_RE = re.compile(
    r"\b[\w./-]+\.(?:py|tsx?|jsx?|css|json|md|html|ya?ml|toml|sh|ps1)\b|"
    r"\b(?:file|function|component|class|css|stylesheet|line|lines|code|button|header|sidebar|panel|"
    r"menu|navbar|banner|endpoint|route|test|tests|variable|import|prop|style|color|colour|label|"
    r"icon|tooltip|modal|loader|spinner|readme|hero)\b",
    re.IGNORECASE,
)
# "do it", "go ahead", "yes please apply that", "ugh just make the change" — a confirmation or
# command with no target of its own, which only means "edit" given what came just before.
_EDIT_CONFIRMATION_RE = re.compile(
    r"^\W*(?:(?:yes|yep|yeah|ok|okay|sure|please|pls|ugh|come on|just|now)\W+)*"
    r"(?:do it|go ahead|apply(?: it| that| this| the change| the changes)?|"
    r"make (?:the|that|those|this) (?:change|edit|update|fix)s?|make it so|proceed|ship it|"
    r"fix it|change it|edit it|update it|why (?:won'?t|wont|don'?t|dont) you (?:just )?(?:do|make|apply|change|edit))",
    re.IGNORECASE,
)
# A previous assistant reply that offered to make a change, or said it couldn't.
_OFFERED_OR_DENIED_EDIT_RE = re.compile(
    r"(?:would you like me to|want me to|shall i|should i|do you want me to)[^.?!]{0,80}"
    r"\b(?:make|apply|update|change|edit|modify|implement|go ahead)\b|"
    r"\b(?:copy (?:and|&) paste|paste (?:this|the|it)|replace (?:those|these|the) (?:two )?lines)\b|"
    r"\b(?:i (?:can'?t|cannot|don'?t have|do not have|am unable to|'m unable to|lack)|"
    r"i (?:am|'m) only able to)\b[^.]{0,200}\b(?:write|edit|apply|modify|change|read and search)\b",
    re.IGNORECASE,
)
_STALE_DENIAL_RE = re.compile(
    r"\bi (?:can'?t|cannot|don'?t have|do not have|am unable to|'m unable to|lack|am not able to|'m not able to)\b"
    r"[^.]{0,200}\b(?:write|edit|apply|modify|writing|physically|file-writing|write/edit)\b|"
    r"\bi (?:am|'m) only able to (?:read|view|search)\b|"
    r"\bonly (?:have )?the ability to read and search\b",
    re.IGNORECASE,
)


def _message_text(message) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content or "")


def _is_human(message) -> bool:
    return getattr(message, "type", "") == "human"


def looks_like_edit_request(messages) -> bool:
    """True when the user's latest message is asking for a code/file change — either directly
    (an edit verb plus a code-shaped target: "change the header text in Chat.tsx") or as a
    confirmation of one ("do it", "yes apply that", "why won't you just make the change") that
    follows an earlier edit request or an assistant reply that offered/refused one. Deliberately
    conservative on bare verbs: "make your replies shorter" and "update my memory" are not edits."""
    if not messages:
        return False
    humans = [m for m in messages if _is_human(m)]
    if not humans:
        return False
    last = _message_text(humans[-1]).strip()
    if not last:
        return False

    def explicit(text: str) -> bool:
        return bool(_EDIT_VERB_RE.search(text) and _CODE_TARGET_RE.search(text))

    if explicit(last):
        return True
    previous_ai = next((_message_text(m) for m in reversed(messages) if not _is_human(m)), "")
    words = len(last.split())
    if words > 40:
        return False
    # The whole window, not just the last few messages: after several "make the change" /
    # "APPLY THE CHANGE" in a row (the real thread) the original, explicit request is further back.
    recent_explicit = any(explicit(_message_text(m)) for m in humans[:-1])
    is_confirmation = bool(_EDIT_CONFIRMATION_RE.search(last))
    # A scrubbed refusal (see scrub_stale_capability_denials) still counts as "the assistant
    # refused" — the scrub runs before this check, so the placeholder is all that's left of it.
    ai_offered_or_denied = (
        bool(_OFFERED_OR_DENIED_EDIT_RE.search(previous_ai)) or previous_ai.strip() == _SCRUBBED_DENIAL_PLACEHOLDER
    )
    if is_confirmation and words <= 14 and (recent_explicit or ai_offered_or_denied):
        return True
    # A longer complaint at a refusal ("you CAN apply the change but are refusing to") right after
    # the assistant offered or refused an edit that the user had explicitly asked for.
    return ai_offered_or_denied and recent_explicit and bool(_EDIT_VERB_RE.search(last))


def is_stale_capability_denial(text: str) -> bool:
    """An assistant reply claiming it can't write/edit files (or can only read and search)."""
    return bool(_STALE_DENIAL_RE.search(text or ""))


_SCRUBBED_DENIAL_PLACEHOLDER = "(I hadn't made that change yet.)"


def scrub_stale_capability_denials(messages):
    """A copy of `messages` with earlier assistant replies that denied being able to edit files
    swapped for a neutral placeholder. With a local folder connected those replies are false, and a
    thread full of them teaches the model to keep saying it — the exact pattern observed. New
    message objects are built so the persisted transcript is never altered; only what the model
    sees changes."""
    scrubbed = []
    for message in messages:
        if not _is_human(message) and is_stale_capability_denial(_message_text(message)):
            scrubbed.append(AIMessage(content=_SCRUBBED_DENIAL_PLACEHOLDER))
        else:
            scrubbed.append(message)
    return scrubbed


def format_steering_notes(notes: list) -> str:
    """The prompt block for messages the user sent while the agent was already working (see
    backend/services/steering.py). They are listed oldest first and kept in every later step's
    prompt, since the model has no other memory of them between steps."""
    if not notes:
        return ""
    numbered = "\n".join(f"{i}. {note}" for i, note in enumerate(notes, 1))
    return (
        "\n\nUSER STEERING: the user sent the following while you were already working on this "
        "(oldest first). They update the task. Where they conflict with the request above or with "
        "your plan so far, follow the steering. Keep any work already done that is still valid "
        "rather than redoing it, and change your NEXT action to reflect it. If the user says to "
        "stop or is no longer interested, return action=\"final\" and say so briefly.\n"
        + numbered
    )

