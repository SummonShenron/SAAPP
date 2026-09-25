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
async def test_unresolved_truncation_rejects_final_beyond_the_normal_retry_budget():
    """Reproduces the exact real production failure: given ordinary ERROR/empty budget rules
    (max_retry_nudges=1, or even deep thinking's 3), the model just kept re-submitting 'final'
    without ever taking the corrective action, and once the budget ran out it walked straight
    through with the file still unread. An unresolved truncation must keep rejecting with NO
    budget limit at all — it's never a genuine dead end — until the model actually reads
    further or the loop is forced to conclude."""
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "big.py"}),
        # 4 premature "final" attempts in a row — more than even deep thinking's 3-rejection
        # budget would normally allow — must ALL be rejected since the file is still truncated.
        _llm_response(action="final", answer="Premature attempt 1"),
        _llm_response(action="final", answer="Premature attempt 2"),
        _llm_response(action="final", answer="Premature attempt 3"),
        _llm_response(action="final", answer="Premature attempt 4"),
        # Finally takes the corrective action.
        _llm_response(action="query", purpose="Actually read further", tool_action="read_repo_file", args={"path": "big.py", "start_line": 999}),
        _llm_response(action="final", answer="Genuinely grounded now."),
    ]

    call_index = {"n": 0}

    async def fake_ainvoke(prompt):
        response = responses[call_index["n"]]
        call_index["n"] += 1
        return response

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision)
            start_line = (decision.get("args") or {}).get("start_line", 0)
            if start_line >= 999:
                return "URL: x\nLines 999-1050 of 1050 total:\n...(reaches the end, no more remaining)"
            return "URL: x\nLines 1-150 of 1050 total:\n... [900 more lines below — re-call with a higher start_line to keep reading]"

        result = await aw.run_react_loop(
            question="investigate big.py",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=10,
            node_name="test_node",
            max_retry_nudges=1,  # even the smallest, non-deep-thinking budget must not matter here
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Genuinely grounded now."
    # Only 2 real actions ever executed (the initial truncated read, then the completing one) —
    # all 4 premature "final" attempts were rejected without ever reaching act().
    assert len(act_calls) == 2


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


# ---------------------------------------------------------------------------
# Ungrounded diffs — the third real fabrication shape from a self-drive
# attempt (docs/coding-agent-roadmap.md, Section 7): a confidently-formatted
# diff editing a data structure that was never verified to exist anywhere in
# the real codebase, presented alongside genuine quotes from elsewhere in the
# same file so it read as thoroughly researched.
# ---------------------------------------------------------------------------

GROUNDED_FILE_PATH = "backend/app.py"
GROUNDED_FILE_CONTENT = "def foo():\n    return 1\n"

GROUNDED_INITIAL_ATTEMPTS = [{
    "purpose": "Read the real file",
    "action_desc": f"read_repo_file(path={GROUNDED_FILE_PATH})",
    "observation": f"URL: https://example/{GROUNDED_FILE_PATH}\n{GROUNDED_FILE_CONTENT}",
}]

UNGROUNDED_DIFF_ANSWER = (
    "Here's the fix:\n```diff\n"
    f"diff --git a/{GROUNDED_FILE_PATH} b/{GROUNDED_FILE_PATH}\n"
    f"--- a/{GROUNDED_FILE_PATH}\n"
    f"+++ b/{GROUNDED_FILE_PATH}\n"
    "@@ -1,3 +1,3 @@\n"
    " CONSTANTS = {\n"
    '-    "a": 1,\n'
    '+    "a": 2,\n'
    " }\n"
    "```"
)

GROUNDED_DIFF_ANSWER = (
    "Here's the fix:\n```diff\n"
    f"diff --git a/{GROUNDED_FILE_PATH} b/{GROUNDED_FILE_PATH}\n"
    f"--- a/{GROUNDED_FILE_PATH}\n"
    f"+++ b/{GROUNDED_FILE_PATH}\n"
    "@@ -1,2 +1,2 @@\n"
    " def foo():\n"
    "-    return 1\n"
    "+    return 2\n"
    "```"
)


@run_async
async def test_ungrounded_diff_is_rejected_once_then_corrected_final_accepted():
    """Reproduces the real shape: a diff claims 'CONSTANTS = {\"a\": 1}' already exists in
    backend/app.py, but the only real read_repo_file result for that path this turn shows a
    completely different file — rejected once, then a diff actually matching real content is
    accepted."""
    captured_prompts = []
    responses = [
        _llm_response(action="final", answer=UNGROUNDED_DIFF_ANSWER),
        _llm_response(action="final", answer=GROUNDED_DIFF_ANSWER),
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
            question="fix the bug in backend/app.py",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            initial_attempts=list(GROUNDED_INITIAL_ATTEMPTS),
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 2
    assert result["final_answer"] == GROUNDED_DIFF_ANSWER
    assert GROUNDED_FILE_PATH in captured_prompts[1]
    assert "ever appeared in a real read_repo_file result" in captured_prompts[1]


@run_async
async def test_ungrounded_diff_rejection_is_budget_limited():
    """A second consecutive ungrounded diff must still get an honest 'final' rather than
    looping forever."""
    captured_prompts = []
    responses = [
        _llm_response(action="final", answer=UNGROUNDED_DIFF_ANSWER),
        _llm_response(action="final", answer=UNGROUNDED_DIFF_ANSWER),
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
            question="fix the bug in backend/app.py",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            initial_attempts=list(GROUNDED_INITIAL_ATTEMPTS),
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 2
    assert result["final_answer"] == UNGROUNDED_DIFF_ANSWER


@run_async
async def test_diff_grounded_in_real_read_is_accepted_immediately():
    """False-positive guard: a diff whose claimed pre-existing lines really did come back from
    a real read_repo_file result this turn must be accepted right away, not rejected."""
    captured_prompts = []
    responses = [_llm_response(action="final", answer=GROUNDED_DIFF_ANSWER)]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "unused"

        result = await aw.run_react_loop(
            question="fix the bug in backend/app.py",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            initial_attempts=list(GROUNDED_INITIAL_ATTEMPTS),
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 1
    assert result["final_answer"] == GROUNDED_DIFF_ANSWER


@run_async
async def test_diff_for_a_brand_new_file_is_never_flagged():
    """A `new file mode` diff has nothing pre-existing to verify — must never be rejected even
    with zero prior read_repo_file attempts for that path."""
    new_file_answer = (
        "```diff\n"
        "diff --git a/backend/new_thing.py b/backend/new_thing.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/backend/new_thing.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+def bar():\n"
        "+    return 2\n"
        "```"
    )
    captured_prompts = []
    responses = [_llm_response(action="final", answer=new_file_answer)]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        async def act(decision):
            return "unused"

        result = await aw.run_react_loop(
            question="add a new helper file",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(captured_prompts) == 1
    assert result["final_answer"] == new_file_answer


def test_extract_diff_file_grounding_lines_skips_added_and_header_lines():
    lines = aw._extract_diff_file_grounding_lines(UNGROUNDED_DIFF_ANSWER)
    assert lines[GROUNDED_FILE_PATH] == ["CONSTANTS = {", '"a": 1,', "}"]


def test_final_diff_disagrees_with_fetched_content_negative_when_no_diff_present():
    assert aw._final_diff_disagrees_with_fetched_content(
        "No diff here, just a plain answer.", list(GROUNDED_INITIAL_ATTEMPTS)
    ) is None


# ---------------------------------------------------------------------------
# Batching independent actions ("queries") — built after the extensive
# diagnostic loop in docs/coding-agent-roadmap.md (Sections 4b-4j), where
# every real trace burned most of a turn's step budget reading files one at a
# time before ever reasoning about them. A batch must get real concurrent
# execution AND the exact same mechanical scrutiny (retry-nudge/truncation
# tracking, stuck-action streak) that N real sequential steps would have.
# ---------------------------------------------------------------------------

@run_async
async def test_batched_queries_execute_concurrently_not_sequentially():
    """Proves real concurrency, not just "multiple actions in one step" bookkeeping — three
    actions that each sleep 0.2s must finish in well under 0.6s (sequential) if asyncio.gather
    is actually being used."""
    import time

    responses = [
        _llm_response(action="query", purpose="Read three independent files", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
            {"tool_action": "read_repo_file", "args": {"path": "b.py"}, "purpose": "Read b.py"},
            {"tool_action": "read_repo_file", "args": {"path": "c.py"}, "purpose": "Read c.py"},
        ]),
        _llm_response(action="final", answer="Read all three files."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision["args"]["path"])
            await asyncio.sleep(0.2)
            return f"Content of {decision['args']['path']}"

        start = time.monotonic()
        result = await aw.run_react_loop(
            question="compare three files",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
        )
        elapsed = time.monotonic() - start
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Read all three files."
    assert sorted(act_calls) == ["a.py", "b.py", "c.py"]
    # 3 sequential 0.2s calls would take >= 0.6s; real concurrency keeps this well under that.
    assert elapsed < 0.5
    # All 3 individual actions are visible in the attempts list, not collapsed into one entry —
    # the model's next prompt needs to see each result distinctly.
    assert len(result["attempts"]) == 3


@run_async
async def test_batch_rejects_non_batchable_action_but_still_runs_the_others():
    """A non-batchable action (run_snippet — a real CI dispatch, never safe to run concurrently
    with anything else) slipped into a batch must be rejected with its own synthetic error,
    while the genuinely batchable actions alongside it still execute for real. The rejection
    text starts with "ERROR:", so — same as any other ERROR observation — it correctly earns
    one ordinary retry-nudge rejection of a premature "final" before being accepted; that's
    consistent with how every other ERROR is treated, not a special case for this one."""
    responses = [
        _llm_response(action="query", purpose="Read a file and also run a snippet", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
            {"tool_action": "run_snippet", "args": {"code": "print(1)"}, "purpose": "Run a snippet"},
        ]),
        _llm_response(action="final", answer="Premature — should be nudged once."),
        _llm_response(action="final", answer="Done."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision["tool_action"])
            return f"Content of {decision['args'].get('path', '')}"

        result = await aw.run_react_loop(
            question="investigate",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Done."
    # run_snippet was never actually executed — only read_repo_file was.
    assert act_calls == ["read_repo_file"]
    # Both items still show up in attempts — the rejected one with its own explicit reason.
    assert len(result["attempts"]) == 2
    rejected = next(a for a in result["attempts"] if a["action_desc"].startswith("run_snippet"))
    assert "cannot be batched" in rejected["observation"]


@run_async
async def test_batch_containing_the_stuck_tool_is_rejected_entirely():
    """A stuck-tool redirect must reject the WHOLE batch if the stuck tool is slipped in
    alongside other, legitimate actions — not silently drop just that one item and run the
    rest, which would let the model route around the redirect by hiding the stuck call in a
    batch with unrelated actions."""
    responses = [
        _llm_response(action="query", purpose="Search for trace-panel", tool_action="search_code", args={"query": "trace-panel"}),
        _llm_response(action="query", purpose="Try different wording", tool_action="search_code", args={"query": "trace panel styling"}),
        # This batch smuggles search_code in alongside a legitimate read — must be rejected
        # entirely, not partially executed.
        _llm_response(action="query", purpose="Search again, plus read a related file", queries=[
            {"tool_action": "search_code", "args": {"query": "hero trace overlay"}, "purpose": "Try again"},
            {"tool_action": "read_repo_file", "args": {"path": "trace.py"}, "purpose": "Read trace.py"},
        ]),
        _llm_response(action="query", purpose="Switch to find_file", tool_action="find_file", args={"query": "trace panel"}),
        _llm_response(action="final", answer="Found it via find_file."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision.get("tool_action"))
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
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Found it via find_file."
    # The batch's read_repo_file never ran — the whole batch was rejected because it contained
    # the stuck tool (search_code).
    assert act_calls == ["search_code", "search_code", "find_file"]


@run_async
async def test_batched_truncated_read_still_blocks_a_premature_final():
    """A truncated read inside a batch must still trigger the same unconditional
    (no-budget-limit) rejection as a truncated read in a single-action step — the mechanical
    bookkeeping must not get weaker just because the read happened as part of a batch."""
    responses = [
        _llm_response(action="query", purpose="Read two files at once", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "small.py"}, "purpose": "Read small.py"},
            {"tool_action": "read_repo_file", "args": {"path": "big.py"}, "purpose": "Read big.py"},
        ]),
        _llm_response(action="final", answer="Premature — big.py was still truncated."),
        _llm_response(action="query", purpose="Actually finish reading big.py", tool_action="read_repo_file", args={"path": "big.py", "start_line": 999}),
        _llm_response(action="final", answer="Genuinely grounded now."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision["args"].get("path"))
            path = decision["args"]["path"]
            if path == "small.py":
                return "URL: x\nimport os\n"  # short, complete read
            if decision["args"].get("start_line", 0) >= 999:
                return "URL: x\nLines 999-1050 of 1050 total:\n...(reaches the end)"
            return "URL: x\nLines 1-150 of 1050 total:\n... [900 more lines below — re-call with a higher start_line to keep reading]"

        result = await aw.run_react_loop(
            question="investigate two files",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=6,
            node_name="test_node",
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
            max_retry_nudges=1,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Genuinely grounded now."
    assert act_calls == ["small.py", "big.py", "big.py"]


@run_async
async def test_single_item_queries_list_still_executes_correctly():
    """A "queries" list with exactly one item puts the real tool_action/args inside that item,
    not at the top level — must still go through real execution correctly rather than being
    silently ignored (which would look like a no-op action)."""
    responses = [
        _llm_response(action="query", purpose="Read one file via queries", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
        ]),
        _llm_response(action="final", answer="Done."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision["args"]["path"])
            return "Content of a.py"

        result = await aw.run_react_loop(
            question="read a file",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Done."
    assert act_calls == ["a.py"]


@run_async
async def test_batch_ignored_entirely_when_no_batchable_actions_configured():
    """A caller that doesn't pass batchable_actions (the default, None) must not honor
    "queries" at all — every item is rejected as non-batchable, since there is no allowlist to
    check against. Backward-compatible: an old caller with no opinion on batching gets the
    original one-action-per-step behavior, not a silent security-relevant change in what runs."""
    responses = [
        _llm_response(action="query", purpose="Try to batch", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
            {"tool_action": "read_repo_file", "args": {"path": "b.py"}, "purpose": "Read b.py"},
        ]),
        _llm_response(action="final", answer="Done."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision)
            return "unused"

        result = await aw.run_react_loop(
            question="read two files",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            # batchable_actions intentionally omitted — defaults to None.
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Done."
    assert act_calls == []  # neither item ever ran
    assert all("cannot be batched" in a["observation"] for a in result["attempts"])


@run_async
async def test_batch_size_beyond_cap_is_dropped_with_its_own_error():
    """More than _MAX_BATCH_SIZE genuinely batchable actions in one step must not all execute —
    excess ones get a distinct "too many actions" error instead of silently running unbounded
    concurrent work."""
    over_cap = aw._MAX_BATCH_SIZE + 2
    items = [
        {"tool_action": "read_repo_file", "args": {"path": f"file{i}.py"}, "purpose": f"Read file{i}.py"}
        for i in range(over_cap)
    ]
    responses = [
        _llm_response(action="query", purpose="Read many files", queries=items),
        _llm_response(action="final", answer="Done."),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        act_calls = []

        async def act(decision):
            act_calls.append(decision["args"]["path"])
            return f"Content of {decision['args']['path']}"

        result = await aw.run_react_loop(
            question="read many files",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            batchable_actions=aw.TOOL_AGENT_BATCHABLE_ACTIONS,
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "Done."
    assert len(act_calls) == aw._MAX_BATCH_SIZE
    dropped = [a for a in result["attempts"] if "too many actions" in a["observation"]]
    assert len(dropped) == over_cap - aw._MAX_BATCH_SIZE


# ---------------------------------------------------------------------------
# Redundant-repeat backstop — a real production trace (docs/coding-agent-
# roadmap.md, Section 9) burned its entire step budget re-reading the same
# file/interface it had already gotten a real result for several steps
# earlier, on a turn that then ran out of budget without ever answering.
# ---------------------------------------------------------------------------

@run_async
async def test_exact_repeat_of_a_successful_action_is_skipped_not_re_executed():
    """The real gap: a byte-identical repeat of an already-succeeded call must be skipped
    before ever reaching act() again, not silently re-executed for zero new information."""
    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "foo.py"}),
        _llm_response(action="query", purpose="Read it again", tool_action="read_repo_file", args={"path": "foo.py"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    act_calls = []

    async def act(decision):
        act_calls.append(decision)
        return "def foo(): pass"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="what does foo.py contain?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(act_calls) == 1  # the second identical call never reached act()
    assert result["final_answer"] == "done"
    assert len(result["attempts"]) == 2
    assert "already ran this exact action" in result["attempts"][1]["observation"]


@run_async
async def test_repeat_with_different_args_is_not_treated_as_redundant():
    """False-positive guard: two calls to the same tool_action with genuinely different args
    are two real, distinct lookups — neither should ever be skipped."""
    responses = [
        _llm_response(action="query", purpose="Read file A", tool_action="read_repo_file", args={"path": "a.py"}),
        _llm_response(action="query", purpose="Read file B", tool_action="read_repo_file", args={"path": "b.py"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    act_calls = []

    async def act(decision):
        act_calls.append(decision["args"]["path"])
        return f"content of {decision['args']['path']}"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="compare a.py and b.py",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert act_calls == ["a.py", "b.py"]
    assert result["final_answer"] == "done"


@run_async
async def test_repeat_of_a_failed_action_is_not_treated_as_redundant():
    """A repeated call that keeps failing is a genuinely different problem (the existing
    retry-nudge/stuck-action tracking), not a redundant success — it must still execute for
    real each time, never silently skipped."""
    responses = [
        _llm_response(action="query", purpose="Read missing file", tool_action="read_repo_file", args={"path": "missing.py"}),
        _llm_response(action="query", purpose="Try again", tool_action="read_repo_file", args={"path": "missing.py"}),
        _llm_response(action="final", answer="giving up"),
        _llm_response(action="final", answer="giving up honestly"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    act_calls = []

    async def act(decision):
        act_calls.append(decision)
        return "ERROR: 404 not found"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="what's in missing.py?",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert len(act_calls) == 2  # both failed attempts genuinely executed, neither skipped


@run_async
async def test_redundant_repeat_inside_a_batch_is_skipped_but_others_still_run():
    """The same backstop must apply per-item inside a batch: a redundant item is skipped with
    its own synthetic result while genuinely new items in the same batch still execute."""
    responses = [
        _llm_response(action="query", purpose="Read foo", tool_action="read_repo_file", args={"path": "foo.py"}),
        _llm_response(action="query", purpose="Batch read foo again and read bar", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "foo.py"}, "purpose": "Read foo again"},
            {"tool_action": "read_repo_file", "args": {"path": "bar.py"}, "purpose": "Read bar"},
        ]),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    act_calls = []

    async def act(decision):
        act_calls.append(decision["args"]["path"])
        return f"content of {decision['args']['path']}"

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="read foo and bar",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
            batchable_actions=frozenset({"read_repo_file"}),
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert act_calls == ["foo.py", "bar.py"]  # foo.py only ever actually executed once
    assert result["final_answer"] == "done"
    assert len(result["attempts"]) == 3
    assert "already ran this exact action" in result["attempts"][1]["observation"]
    assert result["attempts"][2]["observation"] == "content of bar.py"


# ---------------------------------------------------------------------------
# Mismatched start_line note — reproduces the exact real trace (docs/coding-
# agent-roadmap.md, Section 10): a truncated read_repo_file's own definition
# index already named tool_agent_node's real line, but the model ran two more
# search tools and then still guessed the wrong start_line.
# ---------------------------------------------------------------------------

TRUNCATED_WITH_INDEX = (
    "URL: https://github.com/x/y/blob/main/backend/services/agent_workflow.py\n"
    "from __future__ import annotations\nimport ast\n...\n\n"
    "... [truncated — this file has 4849 lines total, too long to show in full. "
    "Top-level definitions found in it:\n"
    "  line 3306: def tool_agent_node\n"
    "Call read_repo_file again with start_line set to the one you actually need — do not "
    "assume the file's contents past this point from general knowledge of what a file like "
    "this usually contains.]"
)
AGENT_WORKFLOW_PATH = "backend/services/agent_workflow.py"


@run_async
async def test_wrong_start_line_guess_gets_a_corrective_note_pointing_at_the_real_line():
    responses = [
        _llm_response(
            action="query", purpose="Locate tool_agent_node", tool_action="read_repo_file",
            args={"path": AGENT_WORKFLOW_PATH},
        ),
        _llm_response(
            action="query", purpose="Read tool_agent_node implementation.", tool_action="read_repo_file",
            args={"path": AGENT_WORKFLOW_PATH, "start_line": 400, "line_count": 200},
        ),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    async def act(decision):
        if decision["args"].get("start_line"):
            return "URL: x\nLines 400-599 of 4849 total:\ndef is_valid_pending_pr(pending_action):\n    ...\n"
        return TRUNCATED_WITH_INDEX

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="scan tool_agent_node's architecture",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "done"
    second_observation = result["attempts"][1]["observation"]
    assert "already placed at line 3306" in second_observation
    assert "start_line=3306" in second_observation


@run_async
async def test_correct_start_line_gets_no_corrective_note():
    """False-positive guard: a start_line that genuinely reaches the indexed symbol must not
    get flagged."""
    responses = [
        _llm_response(
            action="query", purpose="Locate tool_agent_node", tool_action="read_repo_file",
            args={"path": AGENT_WORKFLOW_PATH},
        ),
        _llm_response(
            action="query", purpose="Read tool_agent_node implementation.", tool_action="read_repo_file",
            args={"path": AGENT_WORKFLOW_PATH, "start_line": 3300, "line_count": 150},
        ),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        return responses.pop(0)

    async def act(decision):
        if decision["args"].get("start_line"):
            return "URL: x\nLines 3300-3449 of 4849 total:\nasync def tool_agent_node(state):\n    ...\n"
        return TRUNCATED_WITH_INDEX

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        result = await aw.run_react_loop(
            question="scan tool_agent_node's architecture",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=5,
            node_name="test_node",
        )
    finally:
        aw.lite_llm.ainvoke = orig

    assert result["final_answer"] == "done"
    second_observation = result["attempts"][1]["observation"]
    assert "already placed at line" not in second_observation
