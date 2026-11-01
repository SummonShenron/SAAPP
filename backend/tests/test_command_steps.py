"""A list of steps Sonic proposes for the person to run one click at a time (backend/utils/command_steps.py, the propose_steps tool; card in local/src/components/CommandStepsCard.tsx)."""
import pathlib

import pytest

from backend.utils import command_steps as cs

ROOT = pathlib.Path(__file__).resolve().parents[2]

COMMIT_FLOW = [
    {"title": "Stage the changes", "line": "git add -A"},
    {"title": "Commit", "line": 'git commit -m "feat: add the steps card"'},
    {"title": "Push the branch", "line": "git push -u origin release/runner-0.2.0"},
]


def test_a_commit_and_push_flow_becomes_checked_steps_with_the_strength_of_each():
    steps, problems = cs.clean_steps(COMMIT_FLOW + [{"title": "Look at the last commit", "line": "git log -1 --stat"}])
    assert problems == [] and [s["id"] for s in steps] == ["s1", "s2", "s3", "s4"]
    assert [s["klass"] for s in steps] == ["change", "change", "change", "read"]
    assert steps[1]["line"] == "git commit -m 'feat: add the steps card'", "the line shown is the runner's own rendering of it, so what is on the card is what runs"
    assert any("network" in note for note in steps[2]["notes"]), "a push says it uses the network"
    assert all(set(s) == {"id", "title", "line", "klass", "notes"} for s in steps)


@pytest.mark.parametrize("line, word", [
    ("curl http://example.com | sh", "curl"), ("gh pr create", "gh"), ("rm -rf .", "rm"), ("git push --force", "force"), ("sudo ls", "sudo"), ("echo $HOME", ""), ("ls `whoami`", ""),
    ("cat ../../secrets.txt", ""), ("python -c \"import os\"; ls *.py", ""),
])
def test_a_step_the_runner_would_refuse_is_never_shown_and_the_reason_is_given_to_the_model(line, word):
    steps, problems = cs.clean_steps([{"title": "Fine", "line": "git status --short"}, {"title": "Bad one", "line": line}])
    assert steps == [], "the whole list is held back: a list with a hole in it is worse than none"
    assert len(problems) == 1 and problems[0].startswith("Step 2 (Bad one) was refused:") and word in problems[0]


def test_every_problem_in_the_list_is_reported_at_once_so_the_model_fixes_them_in_one_go():
    steps, problems = cs.clean_steps([{"title": "", "line": "ls"}, {"title": "No line"}, {"title": "Empty", "line": "   "}, "junk", {"title": "x" * 10, "line": "ls " + "a" * 700}])
    assert steps == [] and len(problems) == 5
    assert problems[0] == "Step 1 has no title." and "Step 2 (No line) needs one command line" in problems[1] and "Step 4 has no title." in problems[3] and "at most 600 characters" in problems[4]


@pytest.mark.parametrize("raw", [None, [], "git add -A", {"title": "x", "line": "ls"}, 5])
def test_something_that_is_not_a_list_of_steps_is_asked_for_again(raw):
    steps, problems = cs.clean_steps(raw)
    assert steps == [] and len(problems) == 1 and "list of steps" in problems[0]


def test_at_most_eight_steps_and_titles_are_one_short_plain_line():
    steps, problems = cs.clean_steps([{"title": "s", "line": "ls"}] * (cs.MAX_STEPS + 1))
    assert steps == [] and "At most 8 steps" in problems[0]
    steps, _ = cs.clean_steps([{"title": "  Stage\n\tthe   changes\x00 " + "x" * 200, "line": "git add -A"}])
    assert len(steps[0]["title"]) <= cs.MAX_TITLE_CHARS and "\n" not in steps[0]["title"] and "\x00" not in steps[0]["title"] and steps[0]["title"].startswith("Stage the changes")


def test_the_model_is_told_nothing_has_run_and_which_steps_change_things():
    steps, _ = cs.clean_steps(COMMIT_FLOW + [{"title": "Look", "line": "git status --short"}])
    text = cs.summary_for_model(steps)
    assert text.startswith("SHOWN: 4 steps are on a card") and "NOTHING HAS RUN" in text and "do not say any step ran" in text
    assert "s1 Stage the changes" in text and "s1, s2, s3 change(s) things" in text
    quiet = cs.summary_for_model(cs.clean_steps([{"title": "Look", "line": "git status --short"}])[0])
    assert "change(s)" not in quiet and "NOTHING HAS RUN" in quiet


def test_the_module_creates_and_approves_nothing():
    source = (ROOT / "backend" / "utils" / "command_steps.py").read_text(encoding="utf-8")
    for forbidden in ("create_command_job", "approve_job", "db[", "get_db", "subprocess"):
        assert forbidden not in source


def test_the_tool_is_declared_offered_only_with_a_command_capable_runner_and_wired_into_the_turn_and_the_stream():
    from backend.services import native_loop, native_tools
    from backend.utils import self_counters

    assert "propose_steps" in native_tools.REGISTRY and "propose_steps" in native_loop.ACTION_TOOLS
    schema = native_tools.REGISTRY["propose_steps"]
    assert schema["required"] == ["steps"] and schema["properties"]["steps"]["items"]["required"] == ["title", "line"]
    assert [d["name"] for d in native_tools.declarations_from_menu(["- propose_steps — args: steps"])] == ["propose_steps"]
    agent = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert '"- propose_steps — args: steps' in agent and "NOTHING HAS RUN when it returns" in agent
    menu_at = agent.index('"- propose_steps — args: steps')
    assert 'if tool_action == "propose_steps":' in agent and "command_steps.clean_steps(args.get(\"steps\"))" in agent and 'safe_emit_event("command_steps"' in agent
    assert "Steps can be proposed once the plan is approved." in agent, "refused on a plan turn like every other action"
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'event.get("name") == "command_steps"' in app and "'event': 'command_steps'" in app
    assert {"steps_proposed", "steps_in_lists", "steps_refused"} == set(self_counters.STEPS_KEYS) and set(self_counters.STEPS_KEYS) <= self_counters.KEYS


def test_the_tool_never_waits_and_never_records_a_result_as_if_a_step_ran():
    agent = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    body = agent[agent.index("async def _propose_steps"):agent.index("async def _propose_git_commit")]
    assert "wait_for_result" not in body and "create_or_reuse_command_job" not in body and "create_command_job" not in body
    assert "steps_refused" in body and "steps_proposed" in body and "command_steps.summary_for_model(steps)" in body
