import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.utils.agent_utils import (
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








