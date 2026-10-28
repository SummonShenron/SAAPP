"""Command jobs in the runner's loop (loop.py) and the person's own rules on this computer (confirm.py): the runner decides again, asks here before it changes anything, and a refused line starts nothing."""
import threading

import pytest

from saapp_runner import confirm, projects
from saapp_runner.loop import Runner


class FakeClient:
    def __init__(self, cancel_after=None):
        self.reported, self.heartbeats, self.cancel_after = [], 0, cancel_after

    def hello(self, projects, status="", launch="", request_results=None):
        return {"ok": True}

    def next_job(self, wait=25):
        return None

    def heartbeat(self, job_id):
        self.heartbeats += 1
        return {"cancel": self.cancel_after is not None and self.heartbeats >= self.cancel_after}

    def result(self, job_id, payload):
        self.reported.append((job_id, payload))
        return {"ok": True}


class FakeAsk:
    """The window on the person's computer: answers at once, or never, and remembers what it was asked."""

    def __init__(self, answer="chosen", never=False):
        self.answer, self.never, self.asked, self.cancelled = answer, never, [], False

    def __call__(self, line, notes):
        self.asked.append((line, list(notes)))
        return self

    def poll(self):
        return None if self.never else {"result": self.answer}

    def cancel(self):
        self.cancelled = True


@pytest.fixture()
def root(tmp_path):
    folder = tmp_path / "myapp"
    folder.mkdir()
    (folder / "README.md").write_text("hello\nworld\n", encoding="utf-8")
    return folder


def make(root, client=None, asker=None, rules=None, **kw):
    said = []
    client = client or FakeClient()
    runner = Runner(client, root, {}, say=said.append, poll_wait=0, heartbeat_every=0.2, asker=asker, rules_provider=(lambda: rules) if rules is not None else (lambda: confirm.Rules()), **kw)
    return runner, client, said


def job(line, klass="read", **extra):
    return {"job_id": "j1", "kind": "command", "check": "command", "line": line, "class": klass, "project_id": "", **extra}


def reported(client):
    return client.reported[0][1]


# ---- running ------------------------------------------------------------------------------------------------------------------------------------

def test_a_reading_command_the_person_approved_runs_and_reports_its_output(root):
    runner, client, said = make(root)
    runner.handle(job("cat README.md | head -1"))
    assert reported(client)["exit_code"] == 0 and reported(client)["output_tail"].strip() == "hello"
    assert not any("README" in line or "cat " in line for line in said)                          # the log names no command and no file


def test_a_line_the_rules_refuse_runs_nothing_and_says_why(root):
    runner, client, said = make(root)
    runner.handle(job("echo x > ok.txt && rm -rf ."))
    assert reported(client)["exit_code"] is None and "refused" in reported(client)["error"].lower() and not (root / "ok.txt").exists()
    assert any("Refused a command" in line for line in said) and not any("rm" in line or "ok.txt" in line for line in said)          # the log keeps the line and the reason out


def test_the_runner_will_not_run_a_line_that_is_more_than_the_card_said(root):
    asker = FakeAsk()
    runner, client, _ = make(root, asker=asker)
    runner.handle(job("echo sneaky > out.txt", klass="read"))
    assert reported(client)["exit_code"] is None and "less than" in reported(client)["error"] and not (root / "out.txt").exists() and asker.asked == []
    client.reported.clear()
    runner.handle(job("echo x > out.txt", klass="nonsense"))
    assert reported(client)["exit_code"] is None and not (root / "out.txt").exists()


def test_a_card_that_said_more_than_needed_is_fine(root):
    runner, client, _ = make(root)
    runner.handle(job("cat README.md", klass="change"))
    assert reported(client)["exit_code"] == 0


def test_a_job_for_a_project_this_computer_does_not_serve_is_refused(root):
    runner, client, _ = make(root)
    runner.handle(job("pwd", project_id="deadbeef"))
    assert reported(client)["exit_code"] is None and "does not serve" in reported(client)["error"]


# ---- changing things: the question here ----------------------------------------------------------------------------------------------------------

def test_a_command_that_changes_things_is_asked_about_here_and_runs_on_yes(root):
    asker = FakeAsk("chosen")
    runner, client, _ = make(root, asker=asker)
    runner.handle(job("echo done > out.txt", klass="change"))
    assert (root / "out.txt").read_text(encoding="utf-8").strip() == "done" and reported(client)["exit_code"] == 0
    (line, notes), = asker.asked
    assert line == "echo done > out.txt" and notes and "SAAPP" not in line


def test_no_in_the_window_runs_nothing(root):
    runner, client, _ = make(root, asker=FakeAsk("declined"))
    runner.handle(job("echo done > out.txt", klass="change"))
    assert not (root / "out.txt").exists() and reported(client)["exit_code"] is None and "You said no" in reported(client)["error"]


def test_when_no_window_can_be_shown_a_change_is_refused_not_run(root):
    runner, client, _ = make(root, asker=None)
    runner.handle(job("echo done > out.txt", klass="change"))
    assert not (root / "out.txt").exists() and "cannot show the window" in reported(client)["error"]
    runner, client, _ = make(root, asker=FakeAsk("no_picker"))
    runner.handle(job("echo done > out.txt", klass="change"))
    assert not (root / "out.txt").exists() and "could not ask you" in reported(client)["error"]


def test_a_person_who_turned_the_question_off_gets_none_but_the_rules_still_apply(root):
    asker = FakeAsk()
    runner, client, _ = make(root, asker=asker, rules=confirm.Rules(confirm_changes=False))
    runner.handle(job("echo done > out.txt", klass="change"))
    assert (root / "out.txt").exists() and asker.asked == []
    client.reported.clear()
    runner.handle(job("echo x > ../escape.txt", klass="change"))
    assert reported(client)["exit_code"] is None and not (root.parent / "escape.txt").exists()


def test_a_reading_or_running_command_is_never_asked_about(root):
    asker = FakeAsk()
    runner, client, _ = make(root, asker=asker)
    runner.handle(job("cat README.md"))
    assert asker.asked == [] and reported(client)["exit_code"] == 0


def test_stopping_it_in_saapp_while_the_question_is_open_closes_the_window_and_runs_nothing(root):
    asker = FakeAsk(never=True)
    runner, client, _ = make(root, client=FakeClient(cancel_after=1), asker=asker)
    runner.handle(job("echo done > out.txt", klass="change"))
    assert asker.cancelled and not (root / "out.txt").exists() and "Stopped before" in reported(client)["error"]


# ---- the person's own rules ----------------------------------------------------------------------------------------------------------------------

def test_the_persons_refuse_list_wins_over_what_saapp_approved(root):
    rules = confirm.Rules(refuse=(("echo", "secret"), ("git", "push")))
    runner, client, _ = make(root, rules=rules)
    runner.handle(job("echo secret stuff"))
    assert reported(client)["exit_code"] is None and "your own rules" in reported(client)["error"]
    client.reported.clear()
    runner.handle(job("echo fine"))
    assert reported(client)["exit_code"] == 0


def test_a_program_the_person_allowed_counts_as_a_change_and_is_asked_about(root):
    asker = FakeAsk("declined")
    runner, client, _ = make(root, asker=asker, rules=confirm.Rules(allow=frozenset({"mytool"})))
    runner.handle(job("mytool run", klass="change"))
    assert asker.asked and asker.asked[0][0] == "mytool run" and "allowed in your own config" in asker.asked[0][1][0]
    runner, client, _ = make(root, asker=FakeAsk())
    runner.handle(job("mytool run", klass="change"))
    assert reported(client)["exit_code"] is None                                                          # not allowed without the person's list


def test_commands_toml_is_read_and_a_broken_one_falls_back_to_the_safe_defaults(runner_home):
    path = confirm.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    assert confirm.load_rules().confirm_changes is True and confirm.load_rules().refuse == ()
    path.write_text('confirm_changes = false\nallow = ["Terraform-Lint"]\nrefuse = ["git push", "npm  install", "docker"]\n', encoding="utf-8")
    rules = confirm.load_rules()
    assert rules.confirm_changes is False and rules.allow == frozenset({"terraform-lint"}) and rules.refuse == (("git", "push"), ("npm", "install"), ("docker",)) and not rules.problem
    for broken in ("confirm_changes = [", 'allow = "x"', "confirm_changes = 3", "refuse = [1]"):
        path.write_text(broken, encoding="utf-8")
        safe = confirm.load_rules()
        assert safe.problem and safe.confirm_changes is True and safe.allow == frozenset() and safe.refuse == ()


def test_refusals_match_the_beginning_of_a_command_only(root):
    rules = confirm.Rules(refuse=(("git", "push"),))
    from saapp_runner import commands

    assert confirm.refused_by_person(commands.classify("git push origin main", root), rules)
    assert confirm.refused_by_person(commands.classify("Git.exe push", root), rules)
    assert not confirm.refused_by_person(commands.classify("git status", root), rules)
    assert not confirm.refused_by_person(commands.classify("echo git push", root), rules)


# ---- the window itself ---------------------------------------------------------------------------------------------------------------------------

class FakeTk:
    def __init__(self, answer=True, fail=False):
        self.answer, self.fail, self.shown, self.closed = answer, fail, [], False

    def confirm(self, title, text):
        if self.fail:
            raise RuntimeError("no screen")
        self.shown.append((title, text))
        return self.answer

    def close(self):
        self.closed = True


def test_the_window_shows_the_exact_command_and_why_and_answers_yes_or_no():
    yes = FakeTk(True)
    assert confirm.run_dialog("git commit -m 'a b'", ["makes a commit"], ask=yes) == {"result": "chosen"} and yes.closed
    title, text = yes.shown[0]
    assert "git commit -m 'a b'" in text and "makes a commit" in text and "changes things" in title
    assert confirm.run_dialog("ls", [], ask=FakeTk(False)) == {"result": "declined"}
    assert confirm.run_dialog("ls", [], ask=FakeTk(fail=True)) == {"result": "no_picker"}


def test_the_confirm_command_subcommand_prints_one_line_of_json(monkeypatch, capsys):
    from saapp_runner import cli

    monkeypatch.setattr(confirm, "run_dialog", lambda line, notes, ask=None: {"result": "chosen", "seen": [line, notes]})
    assert cli.main(["confirm-command", "--line", "echo hi > a.txt", "--notes", '["writes a file"]']) == 0
    assert capsys.readouterr().out.strip() == '{"result": "chosen", "seen": ["echo hi > a.txt", ["writes a file"]]}'
    assert cli.main(["confirm-command", "--line", "ls", "--notes", "not json"]) == 0


def test_the_window_asker_starts_a_separate_process_and_times_out_in_five_minutes(monkeypatch):
    import sys

    start = confirm.new_asker(lambda line, notes: [sys.executable, "-c", "import json; print(json.dumps({'result': 'chosen'}))"])
    handle = start("ls", [])
    for _ in range(100):
        answer = handle.poll()
        if answer:
            break
        threading.Event().wait(0.1)
    assert answer == {"result": "chosen"}
    slow = confirm.new_asker(lambda line, notes: [sys.executable, "-c", "import time; time.sleep(30)"])("ls", [])
    try:
        assert slow.poll() is None
        import time

        assert slow.poll(now=time.monotonic() + confirm.TIMEOUT_SECONDS + 1) == {"result": "declined"}
    finally:
        slow.cancel()
