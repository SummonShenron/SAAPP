"""The run_sandbox_tests tool in the tool agent: only with a folder connected, edits applied to a copy, nothing shown or changed (backend/services/agent_workflow.py)."""
import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_workspace as lw
from backend.services import native_tools as nt
from backend.services import python_sandbox
from backend.services import repo_checkout as rc

ROOT = pathlib.Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not python_sandbox.SANDBOX_WASM_PATH.exists(), reason="the sandbox interpreter is not downloaded (backend/sandbox/README.md)")

BUGGY = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x)) + 1\n"
FIXED = "def clamp(x, lo, hi):\n    return max(lo, min(hi, x))\n"
TEST = "import unittest\nfrom mathy import clamp\n\nclass T(unittest.TestCase):\n    def test_high(self):\n        self.assertEqual(clamp(9, 0, 5), 5)\n"


def run(coro):
    return asyncio.run(coro)


async def _drive(monkeypatch, decision, *, connect=True, engine="native"):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    if connect:
        lw.apply_sync("jack", "demo", [{"path": "mathy.py", "content": BUGGY}], [], True)
    monkeypatch.setenv("TOOL_AGENT_ENGINE", engine)
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    seen = {}

    async def fake_run(**kwargs):
        seen["observation"] = await kwargs["act"](decision)
        seen["prompt"] = kwargs.get("prompt_template", "")
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_run)
    monkeypatch.setattr(aw, "run_react_loop", fake_run)
    await aw.tool_agent_node(_state("fix the clamp function", username="jack"))
    seen["events"] = [c.args[0] for c in emit.call_args_list]
    return seen


def decision(**args):
    return {"tool_action": "run_sandbox_tests", "args": args}


def test_a_proposed_fix_and_a_new_test_are_checked_together_against_a_copy_and_nothing_is_shown_or_changed(monkeypatch):
    edits = [{"path": "mathy.py", "old_string": "max(lo, min(hi, x)) + 1", "new_string": "max(lo, min(hi, x))"}, {"path": ".sonic/tests/test_mathy.py", "content": TEST}]
    seen = run(_drive(monkeypatch, decision(tests=[".sonic/tests/test_mathy.py"], edits=edits)))
    assert seen["observation"].startswith("SANDBOX TESTS PASSED against the folder as it would be AFTER your proposed edits") and "1 test(s) ran" in seen["observation"]
    assert "local_edit_proposal" not in seen["events"]                                                  # no diff card
    snapshot = pathlib.Path(lw.get_workspace_handle("jack").root)
    assert (snapshot / "mathy.py").read_text(encoding="utf-8") == BUGGY and not (snapshot / ".sonic").exists()      # the server's copy is untouched


def test_the_same_test_fails_against_the_folder_as_it_is_and_says_why(monkeypatch):
    edits = [{"path": ".sonic/tests/test_mathy.py", "content": TEST}]
    seen = run(_drive(monkeypatch, decision(tests=[".sonic/tests/test_mathy.py"], edits=edits)))
    assert "FAILED" in seen["observation"] and "AssertionError: 6 != 5" in seen["observation"]


def test_edits_that_do_not_apply_cleanly_are_reported_and_nothing_is_run(monkeypatch):
    edits = [{"path": "mathy.py", "old_string": "text that is not in the file", "new_string": "x"}]
    seen = run(_drive(monkeypatch, decision(tests=["tests/test_x.py"], edits=edits)))
    assert seen["observation"].startswith("ERROR: these edits could not be applied cleanly, so nothing was tested")


def test_without_a_connected_folder_there_is_nothing_to_test_and_the_tool_is_not_offered(monkeypatch):
    seen = run(_drive(monkeypatch, decision(tests=["tests/test_x.py"]), connect=False, engine="react"))
    assert seen["observation"] == "ERROR: no local folder is connected, so there is nothing to test"
    connected = run(_drive(monkeypatch, decision(tests=["x.py"]), engine="react"))
    assert "run_sandbox_tests" in connected["prompt"] and "run_sandbox_tests" not in seen["prompt"]


def test_the_native_engine_offers_it_with_a_real_schema_and_the_old_engine_menu_line_matches():
    declared = nt.declarations_from_menu(["- run_sandbox_tests — args: tests (a list), edits (optional)"])
    assert [d["name"] for d in declared] == ["run_sandbox_tests"]
    schema = declared[0]["parameters"]
    assert schema["required"] == ["tests"] and set(schema["properties"]) == {"tests", "edits"}
    assert "locked-down sandbox" in declared[0]["description"] and "standard library only" in declared[0]["description"]
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert '"- run_sandbox_tests — args: tests' in source and 'if tool_action == "run_sandbox_tests":' in source
