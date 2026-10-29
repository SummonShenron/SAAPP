"""The operational log lines added with the rhythm loop, the split prompt and the shared command rules: each is there, uses the project's logger ("SASS Logger"), and carries no user content."""
import asyncio
import logging

from backend.services import native_loop, react_loop
from backend.utils import runner_commands

APPLIED = "Proposed edits to src/app.py. APPLIED: the person applied the edits, so those files now contain them."


class Reply:
    def __init__(self, calls=(), content=""):
        self.tool_calls, self.content = list(calls), content


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    async def ainvoke_tools(self, messages, tools):
        return self.replies.pop(0)


def _loop(replies, hint):
    async def act(decision):
        return APPLIED if decision["tool_action"] == "propose_local_edits" else "text of the file"

    return asyncio.run(native_loop.run(llm=ScriptedLLM(replies), tools=[{"name": n} for n in ("read_repo_file", "propose_local_edits")], act=act, question="make the greeting friendlier UNIQUE_QUESTION_MARKER", max_steps=12, after_apply_hint=hint))


def test_the_hint_and_the_reading_streak_notice_are_logged_by_step_and_nothing_else(monkeypatch, caplog):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    calls = [{"name": "propose_local_edits", "args": {"summary": "s", "edits": []}, "id": "1"}] + [{"name": "read_repo_file", "args": {"path": f"f{n}.py"}, "id": str(n)} for n in range(2, 5)]
    replies = [Reply([c]) for c in calls] + [Reply([], "done")]
    with caplog.at_level(logging.INFO, logger="SASS Logger"):
        _loop(replies, "NEXT: run the check now UNIQUE_HINT_MARKER.")
    assert "an edit was applied; the check hint was added" in caplog.text and "a reading-streak notice was added" in caplog.text
    assert "UNIQUE_QUESTION_MARKER" not in caplog.text and "UNIQUE_HINT_MARKER" not in caplog.text


def test_a_prompt_template_that_cannot_be_split_is_said_once_not_on_every_step(caplog, monkeypatch):
    import json
    from types import SimpleNamespace

    from backend.services import agent_workflow as aw

    monkeypatch.setattr(react_loop, "_split_warned", False)
    steps = {"n": 0}

    async def ainvoke(prompt):
        steps["n"] += 1
        action = {"action": "query", "tool_action": "read_repo_file", "args": {"path": f"a{steps['n']}.py"}, "purpose": "look"} if steps["n"] < 3 else {"action": "final", "answer": "done", "show_work": False}
        return SimpleNamespace(content=json.dumps(action))

    async def act(decision):
        return "file text"

    with caplog.at_level(logging.INFO, logger="SASS Logger"):
        asyncio.run(aw.run_react_loop(question="why", schema="repo=x", prompt_template="{question} | {schema} | {attempts}", act=act, max_iterations=5, node_name="t", llm=SimpleNamespace(ainvoke=ainvoke)))
    assert caplog.text.count("could not be split into cacheable parts") == 1


def test_a_failure_to_load_the_shared_command_rules_is_logged_and_commands_are_marked_unavailable(monkeypatch, caplog):
    monkeypatch.setattr(runner_commands, "_FILE", runner_commands._FILE.with_name("no_such_file.py"))
    saved = (runner_commands.commands, runner_commands.AVAILABLE)
    try:
        with caplog.at_level(logging.WARNING, logger="SASS Logger"):
            runner_commands._load()
        assert runner_commands.AVAILABLE is False and "the shared command rules could not be loaded" in caplog.text
    finally:
        monkeypatch.undo()
        runner_commands._load()
    assert runner_commands.AVAILABLE is True and saved[1] is True


def test_every_new_module_that_logs_uses_the_projects_logger():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    for path in ("backend/services/mcp_client.py", "backend/utils/runner_commands.py", "backend/services/react_loop.py", "backend/services/native_loop.py"):
        assert 'logging.getLogger("SASS Logger")' in (root / path).read_text(encoding="utf-8"), path
