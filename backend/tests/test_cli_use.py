"""Which programs Sonic reaches for on the person's machine (backend/utils/cli_use.py), counted by family and class when a command card is made (agent_workflow.py _run_command_card).
Jack: "it doesn't use its grep tool, or its CLI tools in general outside of tests". There was no way to see what the command lines were; now there is (counts only)."""
import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.tests.test_commit_pr_one_card import FakeGitHub, connect_runner, drive, run, the_person
from backend.utils import cli_use as cu
from backend.utils import self_counters
from backend.utils import runner_jobs as rj


@pytest.mark.parametrize("line, families", [
    ("grep -rn needle backend", {"search"}),
    ("git status --short", {"git"}),
    ("git grep -n needle", {"git", "search"}),
    ("git log -S needle", {"git"}),
    ("ls backend && cat README.md | head -20", {"look"}),
    ("pytest -q backend/tests/test_a.py", {"tool"}),
    ("cd runner && python -m pytest -q", {"tool"}),
    ("npx tsc -b && npm test", {"tool"}),
    ("git add -u && git commit -m 'x' && git push -u origin HEAD", {"git"}),
    ("grep -rn foo . | wc -l", {"search", "look"}),
    ("echo hi", set()),
    ("somecustomtool --flag", {"other"}),
    ("", set()),
])
def test_a_command_line_is_sorted_into_its_program_families(line, families):
    assert cu.families_of(line) == families, line


def test_counts_hold_one_per_family_the_class_and_git_grep_and_only_known_keys():
    assert cu.counts_for("git grep -n needle", "read") == {"cli_prog_git": 1, "cli_prog_search": 1, "cli_git_grep": 1, "cli_class_read": 1}
    assert cu.counts_for("pytest -q", "run") == {"cli_prog_tool": 1, "cli_class_run": 1}
    assert cu.counts_for("git commit -m x", "change") == {"cli_prog_git": 1, "cli_class_change": 1}
    assert cu.counts_for("echo hi", "") == {}
    for line in ("grep a b", "git grep a", "cd x && pytest", "weird thing"):
        assert set(cu.counts_for(line, "read")) <= set(self_counters.KEYS), line


def test_it_never_raises_whatever_it_is_given():
    for junk in (None, 5, object(), b"bytes", ["a"], {"a": 1}):
        assert isinstance(cu.counts_for(junk, junk), dict)
        assert isinstance(cu.families_of(junk), set)


def test_nothing_of_the_line_is_in_what_is_counted():
    counted = cu.counts_for("grep -rn my-secret-token-123 /home/jack/private", "read")
    assert "secret" not in str(counted) and "private" not in str(counted)


def _call(line):
    return {"tool_action": "run_local_command", "args": {"line": line}}


def test_a_new_card_is_counted_once_and_a_reused_one_is_not(monkeypatch):
    db, counts = FakeDB(), []
    record, _ = connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.5)
    run(drive(monkeypatch, [_call("grep -rn needle backend"), _call("grep -rn needle backend")], db=db, github=FakeGitHub(), counts=counts))
    searches = [c for c in counts if c.get("cli_prog_search")]
    assert searches == [{"cli_prog_search": 1, "cli_class_read": 1}], counts      # the second identical line reuses the waiting card: one card, one count


def test_a_git_commit_line_is_counted_as_git_and_a_change(monkeypatch):
    db, counts = FakeDB(), []
    record, _ = connect_runner(db)
    monkeypatch.setattr(rj, "WAIT_SECONDS", 0.5)
    run(drive(monkeypatch, [_call("git add -u && git commit -m 'fix: x'")], db=db, github=FakeGitHub(), counts=counts))
    assert {"cli_prog_git": 1, "cli_class_change": 1} in counts


def test_a_refused_line_is_not_counted_because_no_card_exists(monkeypatch):
    db, counts = FakeDB(), []
    record, _ = connect_runner(db)
    seen = run(drive(monkeypatch, [_call("curl http://example.com")], db=db, github=FakeGitHub(), counts=counts))
    assert seen["observations"][0].startswith("ERROR") and not any(k.startswith("cli_") for c in counts for k in c)


def test_the_counters_are_registered_so_sonic_health_can_show_them():
    for key in cu.KEYS:
        assert key in self_counters.KEYS, key
