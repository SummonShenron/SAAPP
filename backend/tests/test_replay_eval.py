"""The replay eval (backend/evals/replay_eval.py): real changes from the repository's history, graded by running their own tests. These tests need no model, no network and no database."""
from langchain_core.messages import HumanMessage

from backend.evals import replay_eval as re_
from backend.services import native_loop
from backend.utils.agent_utils import looks_like_edit_request


def test_every_ticket_reads_as_an_edit_request():
    for task in re_.TASKS:
        assert looks_like_edit_request([HumanMessage(content=task.prompt)]), task.id


def test_each_task_names_a_real_commit_and_the_tests_that_decide_it():
    assert len({task.id for task in re_.TASKS}) == len(re_.TASKS)
    for task in re_.TASKS:
        assert task.tests and all(path.endswith(".py") for path in task.tests), task.id
        assert task.base == f"{task.commit}^1"


def test_the_models_proposals_are_applied_in_order_and_a_proposal_that_does_not_apply_is_skipped(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
    good = {"edits": [{"path": "a.py", "old_string": "x = 1", "new_string": "x = 10"}]}
    bad = {"edits": [{"path": "a.py", "old_string": "this text is not in the file", "new_string": "z"}]}
    later = {"edits": [{"path": "b/new.py", "content": "print('hi')\n"}]}
    applied = re_.apply_proposals([good, bad, later], str(tmp_path))
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 10\ny = 2\n"
    assert (tmp_path / "b" / "new.py").read_text(encoding="utf-8") == "print('hi')\n"
    assert applied == {"touched": ["a.py", "b/new.py"], "skipped": 1}


def test_a_delete_proposal_removes_the_file(tmp_path):
    (tmp_path / "old.py").write_text("x = 1\n", encoding="utf-8")
    applied = re_.apply_proposals([{"edits": [{"path": "old.py", "delete": True}]}], str(tmp_path))
    assert not (tmp_path / "old.py").exists() and applied["touched"] == ["old.py"]


def test_nothing_the_model_sees_comes_from_the_notes_around_a_change():
    assert "docs/" in re_.HIDDEN_PREFIXES                      # a plan written before a change would hand the model its answer
    assert ".png" in re_.SKIPPED_SUFFIXES and re_.MAX_FILE_BYTES <= 500_000


def test_the_test_run_gets_no_keys_or_database(monkeypatch, tmp_path):
    seen = {}

    class Done:
        returncode = 0
        stdout = b"113 passed in 5s\n"
        stderr = b""

    def fake_run(command, **kwargs):
        seen["env"] = kwargs["env"]
        seen["cwd"] = kwargs["cwd"]
        return Done()

    monkeypatch.setenv("MONGO_URI", "mongodb://real")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real")
    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    result = re_.run_tests(re_.TASKS[0], str(tmp_path))
    assert result["passed"] and result["summary"] == "113 passed in 5s"
    assert "MONGO_URI" not in seen["env"] and "OPENAI_API_KEY" not in seen["env"]
    assert seen["env"]["GOOGLE_API_KEY"] == "test-not-real" and seen["env"]["USE_DB"] == "false"
    assert seen["env"]["PYTHONPATH"].startswith(str(tmp_path)) and seen["cwd"] == str(tmp_path)           # the code under test is the copy, not this checkout


def test_an_import_is_added_after_the_last_top_level_import_and_never_twice():
    source = '"""doc"""\nimport os\nfrom typing import (\n    Any,\n    Dict,\n)\n\nX = 1\n\n\ndef f():\n    import json\n    return json\n'
    out = re_.insert_import(source, "from a.b import C")
    assert out.split("\n")[6] == "from a.b import C" and "X = 1" in out.split("\n")[8]      # after the closing bracket of the multi-line import, not inside it, not inside f()
    assert re_.insert_import(out, "from a.b import C") == out                                  # already there: unchanged
    assert re_.insert_import("X = 1\n", "import os").startswith("import os\n")                  # a file with no imports gets it first


def test_the_first_task_knows_which_import_a_model_tends_to_leave_out():
    task = re_.TASKS[0]
    path, line = task.repair
    assert path == "backend/utils/initiation.py" and "RISK_ELEVATED" in line and "ELEVATED_HOURS" in line
