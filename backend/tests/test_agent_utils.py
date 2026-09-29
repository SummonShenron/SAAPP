import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.utils.agent_utils import (
    parse_definition_index_from_observation,
    find_mismatched_start_line_note,
    _check_final_answer_grounding,
    _check_idiom_grounding,
    _final_answer_has_code_block,
    _has_successful_code_precedent_research,
)


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


TRUNCATED_OBSERVATION = (
    "URL: https://github.com/SummonShenron/SAAPP/blob/main/backend/services/agent_workflow.py\n"
    "from __future__ import annotations\nimport ast\n...\n\n"
    "... [truncated — this file has 4849 lines total, too long to show in full. "
    "Top-level definitions found in it:\n"
    "  line 183: def _build_definition_index\n"
    "  line 3306: def tool_agent_node\n"
    "Call read_repo_file again with start_line set to the one you actually need — do not "
    "assume the file's contents past this point from general knowledge of what a file like "
    "this usually contains.]"
)


def test_parse_definition_index_from_observation_extracts_real_line_numbers():
    index = parse_definition_index_from_observation(TRUNCATED_OBSERVATION)
    assert index == {"_build_definition_index": 183, "tool_agent_node": 3306}


def test_parse_definition_index_from_observation_empty_for_non_truncated_read():
    plain = "URL: https://github.com/x/y/blob/main/foo.py\ndef foo():\n    return 1\n"
    assert parse_definition_index_from_observation(plain) == {}


def test_parse_definition_index_from_observation_empty_for_windowed_read():
    windowed = (
        "URL: https://github.com/x/y/blob/main/foo.py\nLines 400-599 of 4849 total:\n"
        "def is_valid_pending_pr(pending_action):\n    ...\n"
        "\n... [523 more lines below — re-call with a higher start_line to keep reading]"
    )
    assert parse_definition_index_from_observation(windowed) == {}


def test_find_mismatched_start_line_note_flags_a_real_production_shape():
    """Reproduces the exact real trace: purpose names 'tool_agent_node', an earlier index
    placed it at line 3306, but the chosen start_line=400/window=200 doesn't reach it."""
    index = {"_build_definition_index": 183, "tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        400, 200, 150, index,
    )
    assert note is not None
    assert "tool_agent_node" in note
    assert "line 3306" in note
    assert "start_line=3306" in note


def test_find_mismatched_start_line_note_negative_when_window_actually_reaches_it():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        3300, 150, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_purpose_names_no_indexed_symbol():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read the top of the file.", "backend/services/agent_workflow.py",
        1, 150, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_no_index_for_this_path():
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        400, 200, 150, {},
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_no_start_line_given():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        None, None, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_uses_default_window_when_line_count_missing():
    index = {"tool_agent_node": 3306}
    # default_window=150, start_line=3200 -> covers lines 3200-3349, which includes 3306
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        3200, None, 150, index,
    )
    assert note is None


# ---------------------------------------------------------------------------
# _check_final_answer_grounding — the general claim-vs-observation check run_react_loop uses
# on every non-forced "final" with real attempts, since the reward evaluator structurally can't
# do this job (it only ever sees final_answer after it's already been folded into "the DATA" a
# second LLM is told to trust — see react_loop.py's docstring for the full reasoning).
# ---------------------------------------------------------------------------

_SOME_ATTEMPTS = [{
    "purpose": "Read the file", "action_desc": "read_repo_file(path=x.py)",
    "observation": "def foo():\n    return 1\n",
}]


@run_async
async def test_check_final_answer_grounding_skips_the_llm_call_when_no_attempts():
    """Nothing to ground a claim in yet — must not spend an LLM call on a purely conversational
    final with zero real tool observations this turn."""
    llm = SimpleNamespace(ainvoke=AsyncMock())

    result = await _check_final_answer_grounding("Some answer.", [], llm)

    assert result == []
    llm.ainvoke.assert_not_called()


@run_async
async def test_check_final_answer_grounding_returns_claims_when_flagged():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, unsupported_claims=["PR #999 titled 'Fix the thing'"],
    )))

    result = await _check_final_answer_grounding(
        "PR #999, titled 'Fix the thing', fixes the bug.", _SOME_ATTEMPTS, llm,
    )

    assert result == ["PR #999 titled 'Fix the thing'"]
    llm.ainvoke.assert_called_once()


@run_async
async def test_check_final_answer_grounding_returns_empty_when_grounded():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(grounded=True, unsupported_claims=[])))

    result = await _check_final_answer_grounding("foo() returns 1.", _SOME_ATTEMPTS, llm)

    assert result == []


@run_async
async def test_check_final_answer_grounding_ignores_non_string_or_blank_claims():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, unsupported_claims=["a real claim", "", "   ", 42, None],
    )))

    result = await _check_final_answer_grounding("some answer", _SOME_ATTEMPTS, llm)

    assert result == ["a real claim"]


@run_async
async def test_check_final_answer_grounding_fails_open_when_llm_call_raises():
    """A broken checker must never be able to block every future answer — same soft-failure
    convention as every other backstop in this module."""
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("boom")))

    result = await _check_final_answer_grounding("some answer", _SOME_ATTEMPTS, llm)

    assert result == []


@run_async
async def test_check_final_answer_grounding_fails_open_on_unparseable_response():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content="not json at all")))

    result = await _check_final_answer_grounding("some answer", _SOME_ATTEMPTS, llm)

    assert result == []


# ---------------------------------------------------------------------------
# _final_answer_has_code_block / _has_successful_code_precedent_research / _check_idiom_grounding
# — the second, distinct axis _check_final_answer_grounding is structurally forbidden from
# covering: not "is any claim false" but "is this code actually derived from what was found, or
# generic/exampleish boilerplate that would look the same regardless." See react_loop.py's
# docstring for the full reasoning on why this needs its own separate check.
# ---------------------------------------------------------------------------

def test_final_answer_has_code_block_detects_fenced_code():
    assert _final_answer_has_code_block("Here's the fix:\n```python\ndef foo():\n    pass\n```")


def test_final_answer_has_code_block_detects_diff_blocks_too():
    assert _final_answer_has_code_block("```diff\n+def foo():\n+    pass\n```")


def test_final_answer_has_code_block_false_for_plain_prose():
    assert not _final_answer_has_code_block("The login flow is implemented in auth.py.")


def test_has_successful_code_precedent_research_true_for_real_read():
    attempts = [{
        "action_desc": "read_repo_file(path=backend/services/x.py)",
        "observation": "def existing():\n    return 1\n",
    }]
    assert _has_successful_code_precedent_research(attempts)


def test_has_successful_code_precedent_research_false_when_only_failed():
    attempts = [{
        "action_desc": "read_repo_file(path=backend/services/x.py)",
        "observation": "ERROR: could not fetch x.py (404)",
    }]
    assert not _has_successful_code_precedent_research(attempts)


def test_has_successful_code_precedent_research_false_when_empty_result():
    attempts = [{"action_desc": "search_code(query=foo)", "observation": "No matches."}]
    assert not _has_successful_code_precedent_research(attempts)


def test_has_successful_code_precedent_research_false_for_unrelated_actions_only():
    # A successful list_repo_tree/list_commits proves the model looked around, but not that it
    # actually read anything resembling an implementation to model new code on.
    attempts = [{"action_desc": "list_repo_tree()", "observation": "app.py\nbackend/services/x.py"}]
    assert not _has_successful_code_precedent_research(attempts)


def test_has_successful_code_precedent_research_false_for_no_attempts():
    assert not _has_successful_code_precedent_research([])


_CODE_PRECEDENT_ATTEMPTS = [{
    "purpose": "Read the existing draft function",
    "action_desc": "read_repo_file(path=backend/services/agent_workflow.py)",
    "observation": "def _draft_create_calendar_event(state):\n    ...\n",
}]


@run_async
async def test_check_idiom_grounding_skips_when_no_code_block():
    llm = SimpleNamespace(ainvoke=AsyncMock())

    result = await _check_idiom_grounding("Plain prose, no code here.", _CODE_PRECEDENT_ATTEMPTS, llm)

    assert result is None
    llm.ainvoke.assert_not_called()


@run_async
async def test_check_idiom_grounding_skips_when_no_attempts():
    llm = SimpleNamespace(ainvoke=AsyncMock())

    result = await _check_idiom_grounding("```python\ndef foo(): pass\n```", [], llm)

    assert result is None
    llm.ainvoke.assert_not_called()


@run_async
async def test_check_idiom_grounding_returns_none_when_grounded():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(grounded=True)))

    result = await _check_idiom_grounding(
        "```python\ndef _draft_new_thing(state): ...\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result is None


@run_async
async def test_check_idiom_grounding_returns_real_example_ignored():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, reason_category="real_example_ignored",
        reason="backend/services/agent_workflow.py's _draft_create_calendar_event was read but not followed.",
    )))

    result = await _check_idiom_grounding(
        "```python\ndef _draft_new_thing(): pass\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result == {
        "reason_category": "real_example_ignored",
        "reason": "backend/services/agent_workflow.py's _draft_create_calendar_event was read but not followed.",
    }


@run_async
async def test_check_idiom_grounding_returns_no_real_example_found():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, reason_category="no_real_example_found",
        reason="No existing tool_action registration was ever read this turn.",
    )))

    result = await _check_idiom_grounding(
        "```python\ndef foo(): pass\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result["reason_category"] == "no_real_example_found"


@run_async
async def test_check_idiom_grounding_defaults_unknown_category_to_no_real_example_found():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, reason_category="something_unexpected", reason="a real reason",
    )))

    result = await _check_idiom_grounding(
        "```python\ndef foo(): pass\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result["reason_category"] == "no_real_example_found"


@run_async
async def test_check_idiom_grounding_fails_open_when_reason_is_blank():
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=_llm_response(
        grounded=False, reason_category="real_example_ignored", reason="   ",
    )))

    result = await _check_idiom_grounding(
        "```python\ndef foo(): pass\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result is None


@run_async
async def test_check_idiom_grounding_fails_open_when_llm_call_raises():
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("boom")))

    result = await _check_idiom_grounding(
        "```python\ndef foo(): pass\n```", _CODE_PRECEDENT_ATTEMPTS, llm,
    )

    assert result is None
