"""A commit (and optionally a push) with real git on the person's computer, as one safe command line (backend/utils/git_terminal.py), offered as the tool propose_git_commit."""
import pathlib

import pytest

from backend.utils import git_terminal as gt
from backend.utils import runner_commands as rc

ROOT = pathlib.Path(__file__).resolve().parents[2]
TRAILER = 'Co-authored-by: Sonic Assistant <sonic-assistant@users.noreply.github.com>'


def accepted(line):
    """Whether the runner's own rules (the same ones the server and the runner apply) allow the line, and how they class it."""
    return rc.classify(line).klass


# --- the message ---

def test_a_message_becomes_a_subject_and_an_optional_body_without_what_the_command_language_refuses():
    assert gt.clean_message("fix: stop the loop") == "fix: stop the loop"
    assert gt.clean_message("feat: terminal\n\nIt runs real git.\nOn their machine.") == "feat: terminal\n\nIt runs real git. On their machine."
    cleaned = gt.clean_message('fix: use `$HOME` and "quotes" \\ here')
    assert "`" not in cleaned and "$" not in cleaned and "\\" not in cleaned and '"' not in cleaned and "'quotes'" in cleaned


def test_a_message_that_says_nothing_is_refused_in_words_the_model_can_act_on():
    for bad in ("", "  ", "x", None):
        with pytest.raises(gt.GitTerminalError, match="says what the change does"):
            gt.clean_message(bad)


def test_a_long_subject_and_body_are_cut():
    text = gt.clean_message("s" * 300 + "\n\n" + "b" * 900)
    subject, _, body = text.partition("\n\n")
    assert len(subject) == gt.MAX_SUBJECT_CHARS and len(body) == gt.MAX_BODY_CHARS


# --- the paths ---

def test_paths_are_plain_files_inside_the_project():
    assert gt.clean_paths(None) == [] and gt.clean_paths([]) == []
    assert gt.clean_paths(["app.py", "./local/src/api.ts", "backend\\utils\\x.py"]) == ["app.py", "local/src/api.ts", "backend/utils/x.py"]
    assert gt.clean_paths("one.py") == ["one.py"]


@pytest.mark.parametrize("bad", ["-rf", "--all", "../outside.py", "a/../../b", "*.py", "dir/", 'a"b', "a b.py", "$HOME/x", "a;b", ""])
def test_flags_wildcards_quotes_parent_folders_and_spaces_are_refused(bad):
    with pytest.raises(gt.GitTerminalError):
        gt.clean_paths([bad])


@pytest.mark.parametrize("secret", [".env", "backend/.env.local", "id_rsa", "keys/server.pem", "deploy/private.key", "credentials.json", "config/secrets.yaml", ".npmrc"])
def test_a_file_that_looks_like_it_holds_a_secret_is_never_committed(secret):
    with pytest.raises(gt.GitTerminalError, match="secret"):
        gt.clean_paths([secret])


def test_a_template_env_file_is_not_a_secret_but_the_real_one_is():
    assert gt.clean_paths([".env.example", "backend/.env.sample", "x/.env.template"]) == [".env.example", "backend/.env.sample", "x/.env.template"]
    with pytest.raises(gt.GitTerminalError):
        gt.clean_paths([".env"])


def test_too_many_files_are_refused():
    with pytest.raises(gt.GitTerminalError, match="more than"):
        gt.clean_paths([f"f{i}.py" for i in range(gt.MAX_PATHS + 1)])


# --- the line ---

def test_the_default_commit_stages_only_files_git_already_tracks():
    line = gt.build_commit_line("fix: clamp the budget")
    assert line == f'git add -u && git commit -m "fix: clamp the budget" -m "{TRAILER}"'
    assert accepted(line) == "change"


def test_named_files_are_staged_by_name_and_everything_is_only_ever_asked_for_outright():
    named = gt.build_commit_line("feat: terminal", paths=["app.py", "backend/utils/git_terminal.py"])
    assert named.startswith("git add -- app.py backend/utils/git_terminal.py && git commit ")
    everything = gt.build_commit_line("chore: sync", everything=True)
    assert everything.startswith("git add -A && ") and "git add -u" not in everything
    assert gt.build_commit_line("chore: sync", paths=["a.py"], everything=True).startswith("git add -- a.py")        # a named list wins over "everything"
    assert accepted(named) == accepted(everything) == "change"


def test_a_new_branch_and_a_push_are_joined_in_order_and_the_first_failure_stops_the_rest():
    line = gt.build_commit_line("feat: x", new_branch="sonic/fix-clamp", push=True, paths=["a.py"])
    steps = line.split(" && ")
    assert steps[0] == "git switch -c sonic/fix-clamp" and steps[1] == "git add -- a.py" and steps[2].startswith("git commit -m ") and steps[3] == "git push -u origin sonic/fix-clamp"
    assert accepted(line) == "change"
    assert gt.build_commit_line("feat: x", push=True).endswith("git push -u origin HEAD")                           # no new branch: the one they are on


def test_a_body_and_the_sonic_trailer_are_separate_message_parts():
    line = gt.build_commit_line("feat: x\n\nWhy it matters.")
    assert '-m "feat: x" -m "Why it matters." -m "Co-authored-by: Sonic Assistant' in line
    assert accepted(line) == "change"


@pytest.mark.parametrize("branch", ["bad branch", "-x", "a..b", "feat/", "a@{b", "x" * 200, "$HOME"])
def test_a_branch_name_github_would_not_accept_is_refused(branch):
    with pytest.raises(gt.GitTerminalError):
        gt.build_commit_line("feat: x", new_branch=branch)


def test_nothing_in_the_line_is_ever_a_force_push_or_a_reset_and_the_runner_would_refuse_those_anyway():
    line = gt.build_commit_line("feat: x", new_branch="sonic/x", push=True, everything=True)
    assert "--force" not in line and "reset" not in line and "-f " not in line
    for refused in ("git push --force origin main", "git reset --hard"):
        with pytest.raises(Exception):
            rc.classify(refused)


# --- the tool is wired ---

def test_the_tool_is_registered_described_and_listed_and_a_commit_is_a_free_action_step():
    from backend.services import native_loop, native_tools

    assert "propose_git_commit" in native_tools.REGISTRY and "propose_git_commit" in native_tools.NATIVE_DESCRIPTIONS
    assert native_tools.REGISTRY["propose_git_commit"]["required"] == ["message"]
    assert set(native_tools.REGISTRY["propose_git_commit"]["properties"]) == {"message", "paths", "everything", "new_branch", "push"}
    assert "INSTEAD of propose_commit" in native_tools.NATIVE_DESCRIPTIONS["propose_git_commit"]
    assert "use that instead for committing and pushing" in native_tools.NATIVE_DESCRIPTIONS["propose_commit"]
    assert "propose_git_commit" in native_loop.ACTION_TOOLS


def test_it_is_only_offered_with_a_runner_that_can_run_commands_and_runs_through_the_same_job_path():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    menu = source.index('"- propose_git_commit — args:')
    assert source.index("runner_jobs.commands_supported(runner_for_checks) and runner_commands.AVAILABLE") < menu < source.index('"- propose_merge — args:')      # inside the runner block
    assert 'return await _run_local_command({"line": line})' in source                                                          # the same card, checks and wait
    assert 'if tool_action == "propose_git_commit":' in source and "A commit can be made once the plan is approved." in source


def test_the_commit_guidance_points_at_the_terminal_route_when_it_is_there():
    from backend.services import commit_flow

    for line in (commit_flow.pending_line("nobody@example.com", "main"),):
        assert "propose_git_commit when it is among your tools" in line and "otherwise propose_commit" in line


def test_a_model_given_the_menu_gets_a_declaration_for_the_tool():
    from backend.services import native_tools

    menu = ["- propose_git_commit — args: message, paths. Commits with real git."]
    declared = native_tools.declarations_from_menu(menu)
    assert [d["name"] for d in declared] == ["propose_git_commit"]
