"""The read log (backend/utils/read_log.py): what the tool agent already read and searched in a conversation, as places only, carried into later turns.

Why (2026-11-08, a real thread): a coding turn starts without the earlier turns' tool results, so six turns in ten minutes re-ran the same searches and reads and hit the step limit before proposing anything. No database is touched:
FakeDB stands in, and the text below is made up."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import read_log as rl

NOW = datetime(2026, 11, 8, 22, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("READ_LOG_ENABLED", raising=False)
    rl._indexed["done"] = False


def test_a_read_is_kept_as_its_path_and_the_line_range_asked_for():
    assert rl.entry_for("read_repo_file", {"path": "backend/utils/a.py", "start_line": 640, "line_count": 60}, "text") == {"tool": "read_repo_file", "target": "backend/utils/a.py", "range": "640-699", "hits": []}
    assert rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 10}, "text")["range"] == "from 10"
    assert rl.entry_for("read_repo_file", {"path": "a.py"}, "text")["range"] == ""
    assert rl.entry_for("read_repo_file", {"path": ""}, "text") is None
    assert rl.entry_for("list_repo_tree", {}, "a.py\nb.py")["target"] == "(the repo tree)"


def test_a_search_is_kept_as_its_term_and_the_first_few_places_it_found():
    result = "\n".join(f"backend/utils/mod{i}.py:{i * 10}: def extendable()" for i in range(1, 10)) + "\napp.py:12: x"
    entry = rl.entry_for("search_literal", {"term": "extendable"}, result)
    assert entry["target"] == "extendable" and entry["hits"][:2] == ["backend/utils/mod1.py:10", "backend/utils/mod2.py:20"] and len(entry["hits"]) == rl.MAX_HITS
    assert rl.entry_for("search_literal", {"term": "nothing here"}, "No matches.")["hits"] == []
    assert rl.entry_for("search_code", {"query": "decide_request"}, "a/b.py:5: def decide_request")["hits"] == ["a/b.py:5"]
    assert rl.entry_for("trace_symbol", {"symbol": "public_view"}, "x.py:9: use")["target"] == "public_view"
    found = rl.entry_for("find_file", {"query": "budget guard"}, "Closest files:\n- backend/utils/budget_guard.py\n- docs/paid-tier-plan.md")
    assert found["hits"] == ["backend/utils/budget_guard.py", "docs/paid-tier-plan.md"]


def test_a_failed_call_an_unknown_tool_or_a_search_with_no_term_is_not_kept():
    assert rl.entry_for("read_repo_file", {"path": "a.py"}, "ERROR: not found") is None
    assert rl.entry_for("run_snippet", {"code": "print(1)"}, "1") is None
    assert rl.entry_for("web_search", {"query": "x"}, "results") is None
    assert rl.entry_for("search_literal", {}, "a.py:1: x") is None


def test_no_file_contents_or_result_text_is_ever_kept():
    secret = "SECRET-TOKEN-abc123 and a private sentence about someone"
    read = rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 1, "line_count": 5}, f"1: {secret}\n2: more")
    search = rl.entry_for("search_literal", {"term": "needle"}, f"a.py:3: {secret}")
    for entry in (read, search):
        assert "SECRET-TOKEN" not in json.dumps(entry) and "private sentence" not in json.dumps(entry)
    assert search["hits"] == ["a.py:3"]                                                      # the place, not the line
    assert len(rl.entry_for("search_literal", {"term": "x" * 400}, "a.py:1: y")["target"]) == rl.MAX_TARGET_CHARS


def test_a_repeat_replaces_the_earlier_entry_and_the_list_is_capped_oldest_first():
    first = rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 1, "line_count": 10}, "x")
    other = rl.entry_for("search_literal", {"term": "t"}, "b.py:2: x")
    again = rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 1, "line_count": 10}, "x")
    merged = rl.merge(rl.merge([], first) + [other], again)
    assert [e["tool"] for e in merged] == ["search_literal", "read_repo_file"]            # the repeat moved to the end
    many = []
    for i in range(rl.MAX_ENTRIES + 7):
        many = rl.merge(many, rl.entry_for("search_literal", {"term": f"term{i}"}, "x.py:1: y"))
    assert len(many) == rl.MAX_ENTRIES and many[0]["target"] == "term7" and many[-1]["target"] == f"term{rl.MAX_ENTRIES + 6}"


def test_the_prompt_block_lists_places_oldest_first_says_they_may_be_stale_and_stays_short():
    assert rl.build_context([]) == ""
    entries = [rl.entry_for("search_literal", {"term": "extendable"}, "a.py:5: x\nb.py:9: y"), rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 640, "line_count": 60}, "x"),
               rl.entry_for("search_literal", {"term": "zzz"}, "No matches.")]
    block = rl.build_context(entries)
    assert block.startswith("WHAT YOU ALREADY LOOKED AT IN THIS CONVERSATION") and "places only, never contents" in block
    assert "The files may have changed since" in block and "do not run a search listed here again" in block
    assert block.index("searched for 'extendable': a.py:5, b.py:9") < block.index("read a.py lines 640-699") < block.index("searched for 'zzz': found nothing")
    big = [rl.entry_for("search_literal", {"term": f"term-number-{i:03d}-" + "x" * 60}, " ".join(f"f{j}.py:{j}: y" for j in range(6))) for i in range(40)]
    capped = rl.build_context(big)
    assert len(capped) <= rl.MAX_CONTEXT_CHARS + 400 and "term-number-039" in capped and "term-number-000" not in capped      # the oldest go first


def test_a_conversation_keeps_its_own_list_which_survives_and_expires_a_week_on():
    db = FakeDB()
    assert rl.record(db, "jack", "s1", rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 1, "line_count": 5}, "x"), NOW)
    assert rl.record(db, "jack", "s1", rl.entry_for("search_literal", {"term": "t"}, "b.py:2: x"), NOW + timedelta(minutes=1))
    assert rl.record(db, "jack", "s2", rl.entry_for("search_literal", {"term": "other"}, "c.py:3: x"), NOW)
    mine = rl.load_context("jack", "s1", lambda: db)
    assert "read a.py lines 1-5" in mine and "searched for 't': b.py:2" in mine and "other" not in mine
    assert "searched for 'other': c.py:3" in rl.load_context("jack", "s2", lambda: db)
    assert rl.load_context("someone-else", "s1", lambda: db) == ""
    doc = db[rl.COLLECTION].docs["jack|s1"]
    assert doc["username"] == "jack" and doc["session_id"] == "s1" and doc["expires_at"] == NOW + timedelta(minutes=1) + timedelta(days=rl.TTL_DAYS)
    assert rl.record(db, "jack", "s1", rl.entry_for("search_literal", {"term": "t"}, "b.py:2: x"), NOW)       # a repeat does not add a second entry
    assert len(db[rl.COLLECTION].docs["jack|s1"]["entries"]) == 2


def test_clearing_forgets_one_conversation_or_all_of_a_persons_and_nobody_elses():
    db = FakeDB()
    for user, session in (("jack", "s1"), ("jack", "s2"), ("kim", "s1")):
        rl.record(db, user, session, rl.entry_for("search_literal", {"term": "t"}, "a.py:1: x"), NOW)
    assert rl.clear(db, "jack", "s1") is True
    assert rl.load_context("jack", "s1", lambda: db) == "" and rl.load_context("jack", "s2", lambda: db) != ""
    rl.clear_all(db, "jack")
    assert rl.load_context("jack", "s2", lambda: db) == "" and rl.load_context("kim", "s1", lambda: db) != ""


def test_it_never_raises_and_can_be_switched_off(monkeypatch):
    entry = rl.entry_for("search_literal", {"term": "t"}, "a.py:1: x")
    assert rl.record(None, "jack", "s1", entry, NOW) is False and rl.record(FakeDB(), "", "s1", entry, NOW) is False and rl.record(FakeDB(), "jack", "", entry, NOW) is False
    assert rl.load_context("jack", "s1", lambda: None) == "" and rl.load_context("", "s1", lambda: FakeDB()) == ""

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("database down")

    assert rl.record(Broken(), "jack", "s1", entry, NOW) is False and rl.load_context("jack", "s1", lambda: Broken()) == ""
    assert rl.clear(Broken(), "jack", "s1") is False and rl.clear_all(Broken(), "jack") == 0
    db = FakeDB()
    monkeypatch.setenv("READ_LOG_ENABLED", "false")
    assert rl.record(db, "jack", "s1", entry, NOW) is False and rl.load_context("jack", "s1", lambda: db) == ""


def test_the_agent_records_every_reading_tool_and_loads_the_list_for_a_member_and_the_app_clears_it_with_the_conversation():
    flow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "from backend.utils import read_log\n" in flow
    assert "read_log.enabled() and not is_guest_username(username) and state.get(\"session_id\")" in flow and "read_log.load_context, username, state.get(\"session_id\")" in flow
    assert flow.index("schema_parts.append(state[\"working_notes\"])") < flow.index("read_log.load_context") < flow.index("schema = \", \".join(schema_parts)")
    assert "read_log_on = read_log.enabled() and not is_guest_username(username) and bool(state.get(\"session_id\"))" in flow
    assert "if read_log_on and name in read_log.READ_TOOLS:" in flow and "read_log.entry_for(name, decision.get(\"args\"), result)" in flow and "async with read_log_lock:" in flow
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    assert app.count("read_log_utils.clear, get_db(), username, session_id") == 2 and app.count("read_log_utils.clear_all, get_db(), username") == 2
    assert rl.READ_TOOLS <= {"read_repo_file", "search_code", "search_literal", "find_file", "trace_symbol", "list_repo_tree"}      # only tools that read: never an edit, a command or a web page
