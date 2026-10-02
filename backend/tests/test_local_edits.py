"""Edits Sonic proposes to a connected local folder: validation against the snapshot
(backend/services/local_edits.py) and the propose_local_edits action in tool_agent_node. Nothing
here writes to disk — the browser applies approved edits (see local/src/localEdits.ts)."""
import asyncio
import functools
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_edits as le
from backend.services import local_workspace as lw
from backend.services import repo_checkout as rc

FILES = {
    "src/app.py": "import os\n\ndef greet(name):\n    return 'hi ' + name\n\ndef bye(name):\n    return 'bye ' + name\n",
    "src/data.json": '{"a": 1}\n',
    "src/crlf.ts": "const a = 1;\r\nconst b = 2;\r\n",
    "src/broken.py": "def oops(:\n    pass\n",
}


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _read(path):
    return FILES.get(path)


def _replace(path, old, new):
    return {"path": path, "old_string": old, "new_string": new}


# ---------------------------------------------------------------------------
# apply_replace
# ---------------------------------------------------------------------------

def test_replace_swaps_the_one_exact_occurrence():
    result, error = le.apply_replace("a\nb\nc\n", "b", "B")
    assert (result, error) == ("a\nB\nc\n", None)


def test_replace_ignores_crlf_vs_lf_and_preserves_the_files_crlf():
    result, error = le.apply_replace("const a = 1;\r\nconst b = 2;\r\n", "const a = 1;\nconst b = 2;", "const a = 9;\nconst b = 2;")
    assert error is None
    assert result == "const a = 9;\r\nconst b = 2;\r\n"


@pytest.mark.parametrize("old,new,fragment", [
    ("", "x", "empty"),
    ("same", "same", "identical"),
    ("nowhere at all", "x", "not found"),
    ("a", "b", "matches 3 places"),
])
def test_replace_errors_are_specific(old, new, fragment):
    _, error = le.apply_replace("a a a", old, new)
    assert fragment in error


def test_not_found_hint_says_when_the_first_line_exists_but_the_rest_differs():
    _, error = le.apply_replace("def f():\n    return 1\n", "def f():\n    return 2", "x")
    assert "first line does appear at line 1" in error


def test_not_found_hint_says_when_even_the_first_line_is_absent():
    _, error = le.apply_replace("def f():\n    return 1\n", "totally different", "x")
    assert "Not even its first line" in error


# ---------------------------------------------------------------------------
# validate_edit_proposal
# ---------------------------------------------------------------------------

def test_valid_replace_returns_a_diff_card_and_normalized_edit():
    result = le.validate_edit_proposal([_replace("src/app.py", "'hi ' + name", "f'hello {name}'")], _read)

    assert result["ok"] is True
    assert result["edits"] == [{"type": "replace", "path": "src/app.py", "old_string": "'hi ' + name", "new_string": "f'hello {name}'"}]
    card = result["files"][0]
    assert (card["path"], card["kind"], card["additions"], card["deletions"]) == ("src/app.py", "edit", 1, 1)
    assert "-    return 'hi ' + name" in card["diff"]
    assert "+    return f'hello {name}'" in card["diff"]


def test_create_a_new_file():
    result = le.validate_edit_proposal([{"path": "src/new.py", "content": "x = 1\n"}], _read)
    assert result["ok"] is True
    assert result["files"][0]["kind"] == "create"
    assert result["files"][0]["diff"].startswith("--- /dev/null")


def test_later_edits_see_earlier_ones_on_the_same_file():
    result = le.validate_edit_proposal([
        _replace("src/app.py", "def greet(name):", "def greet(name, punct='!'):"),
        _replace("src/app.py", "def greet(name, punct='!'):\n    return 'hi ' + name", "def greet(name, punct='!'):\n    return 'hi ' + name + punct"),
    ], _read)
    assert result["ok"] is True
    assert len(result["files"]) == 1


@pytest.mark.parametrize("edits,fragment", [
    ("nope", "non-empty list"),
    ([], "non-empty list"),
    (["x"], "must be an object"),
    ([{"path": "../escape.py", "content": "x"}], "invalid path"),
    ([{"path": ".env", "content": "K=1"}], "not allowed"),
    ([{"path": "node_modules/p/i.js", "content": "x"}], "not allowed"),
    ([{"path": "src/app.py", "content": "x = 1"}], "already exists"),
    ([_replace("src/missing.py", "a", "b")], "no such file"),
    ([{"path": "src/new.py", "old_string": "a"}], "needs old_string and new_string"),
    ([_replace("src/app.py", "not in the file", "x")], "not found"),
])
def test_invalid_edits_are_rejected_with_a_specific_reason(edits, fragment):
    result = le.validate_edit_proposal(edits, _read)
    assert result["ok"] is False
    assert any(fragment in e for e in result["errors"])


def test_too_many_edits_are_rejected():
    edits = [_replace("src/app.py", "greet", "greet") for _ in range(le.MAX_EDIT_OPS + 1)]
    assert "too many edits" in le.validate_edit_proposal(edits, _read)["errors"][0]


def test_one_bad_edit_rejects_the_whole_proposal_and_reports_every_problem():
    result = le.validate_edit_proposal([
        _replace("src/app.py", "'hi ' + name", "'hello ' + name"),
        _replace("src/app.py", "not in the file", "x"),
        {"path": ".env", "content": "K=1"},
    ], _read)
    assert result["ok"] is False
    assert len(result["errors"]) == 2


def test_a_python_syntax_error_in_the_result_is_caught_before_the_user_sees_it():
    result = le.validate_edit_proposal([_replace("src/app.py", "def greet(name):", "def greet(name:")], _read)
    assert result["ok"] is False
    assert "Python syntax error at line" in result["errors"][0]


def test_a_file_that_was_already_broken_is_not_blamed_on_the_proposal():
    result = le.validate_edit_proposal([_replace("src/broken.py", "pass", "return None")], _read)
    assert result["ok"] is True


def test_invalid_json_in_the_result_is_caught():
    result = le.validate_edit_proposal([_replace("src/data.json", '"a": 1', '"a": ')], _read)
    assert result["ok"] is False
    assert "not be valid JSON" in result["errors"][0]


def test_crlf_files_diff_cleanly_without_phantom_changes():
    result = le.validate_edit_proposal([_replace("src/crlf.ts", "const b = 2;", "const b = 3;")], _read)
    card = result["files"][0]
    assert (card["additions"], card["deletions"]) == (1, 1)


# ---------------------------------------------------------------------------
# tool_agent_node: the action only exists when a folder is connected, validates, and emits a card
# ---------------------------------------------------------------------------

def _connect_folder(user="jack"):
    lw.apply_sync(user, "local-rag", [{"path": p, "content": c} for p, c in FILES.items()], [], True)


async def _drive_turn(monkeypatch, decisions):
    """Runs tool_agent_node with a fake loop that feeds each decision to the real _act and
    records what it returned. Returns (observations, captured_loop_kwargs, emit_mock)."""
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    observed, captured = [], {}

    async def fake_run_react_loop(**kwargs):
        captured.update(kwargs)
        for decision in decisions:
            observed.append(await kwargs["act"](decision))
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)
    await aw.tool_agent_node(_state("make the greeting friendlier", username="jack"))
    return observed, captured, emit


@run_async
async def test_propose_local_edits_emits_a_diff_card_and_tells_the_model_nothing_changed_yet(monkeypatch):
    _connect_folder()
    decision = {"tool_action": "propose_local_edits", "args": {
        "summary": "friendlier greeting",
        "edits": [_replace("src/app.py", "'hi ' + name", "'hello ' + name")],
    }}

    observed, captured, emit = await _drive_turn(monkeypatch, [decision])

    event_calls = [c for c in emit.call_args_list if c.args[0] == "local_edit_proposal"]
    assert len(event_calls) == 1
    payload = event_calls[0].args[1]
    assert payload["folder"] == "local-rag"
    assert payload["summary"] == "friendlier greeting"
    assert payload["files"][0]["path"] == "src/app.py"
    assert payload["proposal_id"]
    assert "nothing has been changed yet" in observed[0]
    assert "propose_local_edits" in captured["prompt_template"]


@run_async
async def test_propose_local_edits_returns_the_validation_errors_to_the_model_and_emits_nothing(monkeypatch):
    _connect_folder()
    decision = {"tool_action": "propose_local_edits", "args": {
        "edits": [_replace("src/app.py", "not in the file", "x")],
    }}

    observed, _, emit = await _drive_turn(monkeypatch, [decision])

    assert observed[0].startswith("ERROR: these edits could not be applied cleanly")
    assert "not found" in observed[0]
    assert [c for c in emit.call_args_list if c.args[0] == "local_edit_proposal"] == []


@run_async
async def test_propose_local_edits_is_not_offered_or_usable_without_a_connected_folder(monkeypatch):
    decision = {"tool_action": "propose_local_edits", "args": {"edits": [{"path": "a.py", "content": "x"}]}}

    observed, captured, emit = await _drive_turn(monkeypatch, [decision])

    assert "no local folder is connected" in observed[0]
    assert "propose_local_edits" not in captured["prompt_template"]
    assert [c for c in emit.call_args_list if c.args[0] == "local_edit_proposal"] == []


@run_async
async def test_github_index_tools_are_withheld_and_refused_while_a_folder_is_connected(monkeypatch):
    """search_code/trace_symbol query GitHub's index of the committed branch, which can't see the
    connected folder — observed answering "No matches" four times in one turn."""
    _connect_folder()
    decision = {"tool_action": "search_code", "args": {"query": "greet"}}

    observed, captured, _ = await _drive_turn(monkeypatch, [decision])

    assert "cannot see the user's connected local folder" in observed[0]
    assert "- search_code" not in captured["prompt_template"]
    assert "- trace_symbol" not in captured["prompt_template"]
    assert "- search_literal" in captured["prompt_template"]


@run_async
async def test_github_index_tools_are_still_offered_without_a_connected_folder(monkeypatch):
    _, captured, _ = await _drive_turn(monkeypatch, [])

    assert "- search_code" in captured["prompt_template"]
    assert "- trace_symbol" in captured["prompt_template"]
