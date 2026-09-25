import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.services import agent_workflow as aw


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


@run_async
async def test_run_react_loop_uses_the_passed_llm_not_the_default():
    """Deep thinking passes lite_llm_deep — the loop must actually call that object, not
    silently fall back to the module-level lite_llm regardless of what's passed in."""
    custom_llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=[
        _llm_response(action="final", answer="answered by the custom llm", show_work=False),
    ]))

    async def act(decision):
        return "unused"

    result = await aw.run_react_loop(
        question="anything",
        schema="repo=x",
        prompt_template="{question} | {schema} | {attempts}",
        act=act,
        max_iterations=5,
        node_name="test_node",
        llm=custom_llm,
    )

    assert result["final_answer"] == "answered by the custom llm"
    custom_llm.ainvoke.assert_called_once()


@run_async
async def test_premature_final_after_error_is_rejected_once_then_accepted():
    """The exact scenario this guards: the model's first action fails, it still has steps left,
    and it tries to conclude right away instead of retrying. The loop must reject that one
    "final" and force a real extra step — but only once, so a second genuine failure doesn't
    loop forever."""
    captured_prompts = []

    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="final", answer="Giving up despite having steps left."),
        _llm_response(action="final", answer="Okay, here is my honest final answer."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: could not fetch x.py (404)"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    # Three LLM calls: the failing query, the rejected premature "final", then the accepted one.
    assert len(captured_prompts) == 3
    assert "Okay, here is my honest final answer." == result["final_answer"]
    assert len(result["attempts"]) == 1  # only the one real (failed) action was ever recorded
    assert "retry it" in captured_prompts[1]  # the nudge shown right before the rejected "final"
    # The tool was never actually retried in this scenario, so the reminder keeps showing — but
    # the HARD rejection only fires once: the second "final" is accepted despite the nudge still
    # being present, rather than forcing a third, unbounded round.
    assert "retry it" in captured_prompts[2]


def test_is_empty_observation_detects_common_empty_shapes():
    for value in ["", "[]", "{}", "None", "null", "no results", "No Results Found", "  "]:
        assert aw._is_empty_observation(value), f"expected {value!r} to be treated as empty"


def test_is_empty_observation_does_not_flag_real_content():
    for value in ["0", "main", "[1, 2, 3]", "found nothing wrong with the config"]:
        assert not aw._is_empty_observation(value), f"expected {value!r} to NOT be treated as empty"


@run_async
async def test_premature_final_after_empty_result_is_rejected_once_then_accepted():
    """Same enforcement as the error case, but for an empty (not erroring) result — an empty
    search is often a sign of the wrong repo/query, not proof nothing exists, so it shouldn't
    be treated as good enough to conclude on the first try either."""
    captured_prompts = []

    responses = [
        _llm_response(action="query", purpose="Search for the config", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="Nothing exists, giving up despite having steps left."),
        _llm_response(action="final", answer="Okay, retried and here's the honest answer."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "[]"  # empty, not an error

        result = await aw.run_react_loop(
            question="find the config file",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 3
    assert result["final_answer"] == "Okay, retried and here's the honest answer."
    assert "came back empty" in captured_prompts[1]
    assert "wrong, not that" in captured_prompts[1]


@run_async
async def test_diagnostic_detour_does_not_hide_an_unretried_failure():
    """Real failure mode this guards: read_repo_file 404s, list_repo_tree (a different action,
    taken to diagnose the 404) then SUCCEEDS, and the model tries to conclude right after —
    checking only 'did the last action fail' would miss this, since the last action (the list)
    genuinely succeeded even though the thing the user actually asked for (the file's contents)
    was never retrieved."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "agent_workflow.py"}),
        _llm_response(action="query", purpose="List the tree to find the right path", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="Giving up despite the tree confirming the path."),
        _llm_response(action="final", answer="Okay, retried and here's the honest answer."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    def act(decision):
        tool_action = decision.get("tool_action")
        if tool_action == "read_repo_file":
            return "ERROR: could not fetch agent_workflow.py (404)"
        if tool_action == "list_repo_tree":
            return "backend/services/agent_workflow.py\n... (real tree contents)"
        return "unexpected"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="read agent_workflow.py and summarize it",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    # 4 calls: failing read, successful list, rejected premature "final", accepted "final".
    assert len(captured_prompts) == 4
    assert result["final_answer"] == "Okay, retried and here's the honest answer."
    assert len(result["attempts"]) == 2  # the rejected "final" never got recorded as an attempt
    assert "read_repo_file" in captured_prompts[2]  # the nudge names the specific unretried tool
    assert "does not count as retrying it" in captured_prompts[2]


@run_async
async def test_final_without_any_prior_error_is_accepted_immediately():
    async def act(decision):
        return "some real result"

    call_count = {"n": 0}

    async def fake_ainvoke(prompt):
        call_count["n"] += 1
        return _llm_response(action="final", answer="Straightforward honest answer.")

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert call_count["n"] == 1
    assert result["final_answer"] == "Straightforward honest answer."


@run_async
async def test_max_retry_nudges_raises_the_rejection_budget():
    """Deep thinking mode passes a higher max_retry_nudges so the loop keeps rejecting a
    premature 'final' (as long as the failed tool is still unretried) more than once, not just
    the single rejection every other test in this file exercises with the default budget."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="final", answer="Giving up on attempt 1."),
        _llm_response(action="final", answer="Giving up on attempt 2."),
        _llm_response(action="final", answer="Giving up on attempt 3."),
        _llm_response(action="final", answer="Okay, honestly giving up after repeated nudging."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: could not fetch x.py (404)"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=6,
            node_name="test_node",
            max_retry_nudges=3,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    # 1 failing query + 3 rejected finals + 1 accepted final = 5 LLM calls.
    assert len(captured_prompts) == 5
    assert result["final_answer"] == "Okay, honestly giving up after repeated nudging."
    assert len(result["attempts"]) == 1  # only the one real (failed) action was ever recorded


@run_async
async def test_default_max_retry_nudges_still_rejects_only_once():
    """Confirms the default (no max_retry_nudges passed) preserves the exact pre-existing
    one-shot behavior other tests in this file rely on."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="final", answer="Giving up despite having steps left."),
        _llm_response(action="final", answer="Okay, accepted on the second try."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: could not fetch x.py (404)"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 3
    assert result["final_answer"] == "Okay, accepted on the second try."


@run_async
async def test_verbatim_repeat_of_failed_query_does_not_clear_the_nudge():
    """The bug this closes: retrying a failed action with the EXACT same args used to satisfy
    "it was retried" and silently clear the outstanding flag even though nothing about the call
    actually changed — the real-world symptom was the model calling search_code with the
    identical query twice in a row and then giving up. A verbatim repeat must NOT clear the
    flag (contrast with test_second_consecutive_error_does_not_trigger_a_second_nudge, where a
    genuinely different args value on the retry does clear it)."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search for navbar", tool_action="search_code", args={"query": "navbar"}),
        _llm_response(action="query", purpose="Search again", tool_action="search_code", args={"query": "navbar"}),  # verbatim repeat, not a real retry
        _llm_response(action="final", answer="Giving up."),
        _llm_response(action="final", answer="Okay, honestly giving up."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "No matches."

        result = await aw.run_react_loop(
            question="where is the navbar?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 4
    assert result["final_answer"] == "Okay, honestly giving up."
    # The crux of the regression: the nudge must still be showing in the prompt right after
    # the verbatim repeat (shown before the premature "final" attempt) — if the repeat had
    # incorrectly cleared the flag, this would be absent.
    assert "retry it" in captured_prompts[2]
    assert len(result["attempts"]) == 2  # both real (failed) search_code calls, repeat included


@run_async
async def test_stuck_action_redirect_rejects_repeated_search_code_after_threshold():
    """Reproduces the real production failure this mechanism exists to close: search_code
    called with several genuinely DIFFERENT queries in a row, all empty. The args-signature
    retry tracking (tested above) clears itself every time here, since each differently-worded
    call counts as 'a genuine retry' — which is exactly the loophole that let the real trace
    burn ~15 steps without ever switching tools. After 2 consecutive misses on search_code
    specifically (regardless of wording), a further search_code call must be rejected outright
    — not executed, not recorded — until the model actually switches to find_file."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search for trace-panel", tool_action="search_code", args={"query": "trace-panel"}),
        _llm_response(action="query", purpose="Try different wording", tool_action="search_code", args={"query": "trace panel styling"}),
        _llm_response(action="query", purpose="Try again", tool_action="search_code", args={"query": "hero trace overlay"}),  # should get rejected
        _llm_response(action="query", purpose="Switch to find_file", tool_action="find_file", args={"query": "trace panel"}),
        _llm_response(action="final", answer="Found it via find_file."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision)
            if decision.get("tool_action") == "find_file":
                return "local/src/pages/Chat.tsx (similarity 0.80)"
            return "No matches."

        result = await aw.run_react_loop(
            question="where is the trace-panel?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=6,
            node_name="test_node",
            stuck_action_redirects={"search_code": (2, "Switch to find_file instead of search_code.")},
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 5
    assert result["final_answer"] == "Found it via find_file."
    # The 3rd search_code attempt was rejected before execution — act() only ever saw the 2
    # real search_code misses and the real find_file success, never the rejected retry.
    assert len(act_calls) == 3
    assert [c["tool_action"] for c in act_calls] == ["search_code", "search_code", "find_file"]
    # The redirect text appears in the prompt shown right before the rejected attempt.
    assert "Switch to find_file instead of search_code." in captured_prompts[2]
    # 2 failed search_code + 1 successful find_file; the rejected attempt is never recorded.
    assert len(result["attempts"]) == 3


@run_async
async def test_stuck_action_streak_resets_on_success_before_threshold():
    """Guards against a false positive: one miss followed by a real success on the second,
    differently-worded search_code call must never trigger the redirect — the streak resets
    on success before it reaches the threshold."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_code", args={"query": "foo"}),
        _llm_response(action="query", purpose="Search again", tool_action="search_code", args={"query": "bar"}),
        _llm_response(action="final", answer="Found bar.py."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            query = (decision.get("args") or {}).get("query")
            return "No matches." if query == "foo" else "backend/bar.py"

        result = await aw.run_react_loop(
            question="find bar",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            stuck_action_redirects={"search_code": (2, "switch to find_file")},
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Found bar.py."
    assert "switch to find_file" not in "".join(captured_prompts)


@run_async
async def test_unlisted_tool_still_gets_generic_stuck_redirect():
    """Reproduces a second real production failure: list_repo_tree (a no-argument action, not
    listed in stuck_action_redirects at all) 404'd repeatedly after a bad repo resolution, and
    the model just kept calling it again with a differently-worded purpose each time — a
    no-arg action has nothing else to vary, so this technically satisfied 'try something
    different' without changing anything real. Any tool not explicitly listed must still get a
    generic circuit breaker instead of no backstop at all."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="List the tree", tool_action="list_repo_tree", args={}),
        _llm_response(action="query", purpose="List it again, might be transient", tool_action="list_repo_tree", args={}),
        _llm_response(action="query", purpose="One more time to be sure", tool_action="list_repo_tree", args={}),
        _llm_response(action="query", purpose="One last time", tool_action="list_repo_tree", args={}),  # rejected (default threshold is 3)
        # A no-arg action's args_signature never changes, so unretried_inconclusive_tools can
        # never clear itself — the first "final" attempt still gets the ordinary retry-nudge
        # rejection (a separate, independent mechanism from the stuck-action redirect above)
        # before the forced-final step lets a second one through.
        _llm_response(action="final", answer="Premature — should be rejected by the retry nudge."),
        _llm_response(action="final", answer="Could not access the repo tree."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision)
            return "ERROR: could not fetch tree (404)"

        result = await aw.run_react_loop(
            question="list the repo",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=6,
            node_name="test_node",
            # No stuck_action_redirects entry for list_repo_tree at all — the generic fallback
            # (_DEFAULT_STUCK_ACTION_THRESHOLD) is what must catch this.
            stuck_action_redirects={"search_code": (2, "Switch to find_file instead of search_code.")},
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Could not access the repo tree."
    # The 4th list_repo_tree attempt was rejected before execution (default threshold is 3).
    assert len(act_calls) == 3
    assert "list_repo_tree multiple times in a row" in captured_prompts[3]


@run_async
async def test_paginated_reads_of_a_large_file_never_trip_the_stuck_action_backstop():
    """Real progress through a genuinely huge file (a different, advancing start_line each
    call) must never be confused with the same doomed action repeating — the generic
    stuck-action circuit breaker (default threshold 3) would otherwise fire on the 4th
    read_repo_file call even though each one is legitimately reading further, not bouncing off
    the same wall. Every call here reports "more lines below" (still incomplete) except the
    last, which reaches the end — 5 read_repo_file calls in a row, well past the threshold of 3,
    must all go through uninterrupted."""
    responses = [
        _llm_response(action="query", purpose="Read page 1", tool_action="read_repo_file", args={"path": "big.py"}),
        _llm_response(action="query", purpose="Read page 2", tool_action="read_repo_file", args={"path": "big.py", "start_line": 150}),
        _llm_response(action="query", purpose="Read page 3", tool_action="read_repo_file", args={"path": "big.py", "start_line": 300}),
        _llm_response(action="query", purpose="Read page 4", tool_action="read_repo_file", args={"path": "big.py", "start_line": 450}),
        _llm_response(action="query", purpose="Read page 5, reaches the end", tool_action="read_repo_file", args={"path": "big.py", "start_line": 600}),
        _llm_response(action="final", answer="Read the whole file across 5 pages."),
    ]

    call_count = {"n": 0}

    async def fake_ainvoke(prompt):
        response = responses[call_count["n"]]
        call_count["n"] += 1
        return response

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision)
            start_line = (decision.get("args") or {}).get("start_line", 0)
            if start_line >= 600:
                return "URL: x\nLines 600-650 of 650 total:\n...(final page, no more remaining)"
            return f"URL: x\nLines {start_line}-{start_line + 149} of 650 total:\n... [more lines below — re-call with a higher start_line to keep reading]"

        result = await aw.run_react_loop(
            question="read the whole file",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=8,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Read the whole file across 5 pages."
    # All 5 reads actually executed — none were rejected by the generic stuck-action backstop,
    # despite read_repo_file being called 5 times in a row (well past its threshold of 3).
    assert len(act_calls) == 5


@run_async
async def test_second_consecutive_error_does_not_trigger_a_second_nudge():
    """A genuinely doomed action (still failing after the forced retry) must still get an
    honest 'final' on the next try rather than the loop nudging forever."""
    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_file", args={"path": "x.py"}),
        _llm_response(action="query", purpose="Retry with corrected path", tool_action="read_file", args={"path": "y.py"}),
        _llm_response(action="final", answer="Still couldn't verify it after retrying."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "ERROR: still not found"

        result = await aw.run_react_loop(
            question="what does x.py do?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Still couldn't verify it after retrying."
    assert len(result["attempts"]) == 2
    # The nudge appears exactly once (before the second, still-failing query), never again
    # before the final honest answer.
    nudge_count = sum(1 for p in captured_prompts if "retry it" in p)
    assert nudge_count == 1


# ---------------------------------------------------------------------------
# capability_denial_watchlist — the second real production failure: a "final"
# confidently denying a capability (browser access) that was sitting in that
# exact turn's own action menu the whole time.
# ---------------------------------------------------------------------------

PROMPT_WITH_BROWSER_ACTION = (
    "{question} | {schema} | {attempts}\n"
    "AVAILABLE ACTIONS THIS TURN:\n- browser_navigate — args: url\n"
)


@run_async
async def test_capability_denial_is_rejected_once_then_corrected_final_accepted():
    """Reproduces the real trace: a confident denial of browser access while browser_navigate
    is right there in the menu gets rejected once, and only a genuinely corrected answer (or a
    real attempt) gets accepted afterward."""
    captured_prompts = []
    responses = [
        _llm_response(
            action="final",
            answer="I don't actually have a live browser tool or sandbox execution environment right now.",
        ),
        _llm_response(action="final", answer="You're right, I do have browser access — let me check the live page."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "unused"

        result = await aw.run_react_loop(
            question="open a browser to btyfitness.app and check the widget",
            schema="repo=x",
            prompt_template=PROMPT_WITH_BROWSER_ACTION,
            act=act,
            max_iterations=5,
            node_name="test_node",
            capability_denial_watchlist=aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 2
    assert result["final_answer"] == "You're right, I do have browser access — let me check the live page."
    assert "browser_navigate" in captured_prompts[1]
    assert "is listed in AVAILABLE ACTIONS THIS TURN above" in captured_prompts[1]


@run_async
async def test_capability_denial_rejection_is_budget_limited():
    """A second consecutive denial (the model insists despite the correction) must still get
    an honest 'final' rather than looping forever."""
    captured_prompts = []
    responses = [
        _llm_response(action="final", answer="I don't have a browser tool available."),
        _llm_response(action="final", answer="I really don't have a browser tool, sorry."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "unused"

        result = await aw.run_react_loop(
            question="open a browser to btyfitness.app",
            schema="repo=x",
            prompt_template=PROMPT_WITH_BROWSER_ACTION,
            act=act,
            max_iterations=5,
            node_name="test_node",
            capability_denial_watchlist=aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 2
    assert result["final_answer"] == "I really don't have a browser tool, sorry."


@run_async
async def test_genuine_capability_denial_not_in_menu_is_accepted_immediately():
    """False-positive guard: if the capability genuinely ISN'T in this turn's menu (no
    browser_navigate substring present), a denial must be accepted as-is, not rejected."""
    captured_prompts = []
    responses = [
        _llm_response(action="final", answer="I don't have a browser tool available for this."),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "unused"

        result = await aw.run_react_loop(
            question="open a browser to btyfitness.app",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",  # no browser_navigate in menu
            act=act,
            max_iterations=5,
            node_name="test_node",
            capability_denial_watchlist=aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 1
    assert result["final_answer"] == "I don't have a browser tool available for this."
