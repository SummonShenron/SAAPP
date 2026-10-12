import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage

from backend.utils import llm_turns as lt
from backend.utils import llm_usage

DAY = "2026-10-12"


@pytest.fixture(autouse=True)
def clean():
    lt.reset()
    llm_usage.reset()
    yield
    lt.reset()
    llm_usage.reset()


def calls(turn, n, input=1000, output=100, reasoning=0):
    for _ in range(n):
        lt.note_call({"input": input, "output": output, "reasoning": reasoning})


def one_turn(n_calls, *, tool=None, setting=False, audit=False, suppressed=False, outcome="ok", **kw):
    turn = lt.begin()
    calls(turn, n_calls, **kw)
    if tool:
        lt.note_tool_agent(tool, setting, audit, suppressed)
    lt.finish(turn, outcome, DAY)
    return turn


class FakeDB:
    def __init__(self, fail=False):
        self.fail, self.writes, self.docs = fail, [], []

    def __getitem__(self, name):
        db = self

        class Coll:
            def update_one(self, flt, update, upsert=False):
                if db.fail:
                    raise RuntimeError("down")
                db.writes.append((name, flt, update))

            def find(self, query, projection=None):
                return list(db.docs)

        return Coll()


# ---- counting calls into the turn that made them ---------------------------------------------------------------------------

def test_calls_made_during_a_turn_are_added_to_it_and_calls_outside_one_are_ignored():
    lt.note_call({"input": 5, "output": 5})  # no turn: nothing happens, nothing raises
    turn = lt.begin()
    calls(turn, 3, input=1000, output=100, reasoning=40)
    assert (turn.calls, turn.input, turn.output, turn.reasoning) == (3, 3000, 300, 120)


def test_the_meter_feeds_the_turn_without_any_call_site_changing():
    turn = lt.begin()
    llm_usage.record("gemini-3.6-flash", "agent_workflow.reasoner_node", {"input": 800, "output": 60, "reasoning": 10, "cached": 0})
    llm_usage.record("gemini-3.1-flash-lite", "react_loop.run_react_loop", {"input": 8000, "output": 90, "reasoning": 0, "cached": 4000})
    assert (turn.calls, turn.input, turn.output) == (2, 8800, 150)


def test_calls_made_on_another_thread_by_to_thread_still_count_for_the_turn():
    async def go():
        turn = lt.begin()
        await asyncio.gather(*(asyncio.to_thread(llm_usage.record, "m", "x.y", {"input": 10, "output": 1}) for _ in range(4)))
        return turn

    assert asyncio.run(go()).calls == 4


def test_a_finished_turn_stops_counting():
    turn = one_turn(2)
    lt.note_call({"input": 1, "output": 1})
    assert turn.calls == 2


# ---- finishing a turn -----------------------------------------------------------------------------------------------------

def test_the_histogram_puts_each_turn_in_its_bucket():
    for n in (1, 2, 3, 4, 5, 8, 9, 20):
        one_turn(n)
    slot = lt.snapshot()[DAY]
    assert [slot[b] for b in lt.BUCKETS] == [1, 1, 2, 2, 2] and slot["max_calls"] == 20 and slot["turns"] == 8
    assert slot["calls"] == 1 + 2 + 3 + 4 + 5 + 8 + 9 + 20


def test_plain_tool_and_deep_turns_are_kept_apart_and_a_deep_turn_is_also_a_tool_turn():
    one_turn(2)                                                   # plain chat
    one_turn(6, tool="react")                                     # tool agent, not deep
    one_turn(12, tool="react", setting=True, input=2000)          # deep by the user's setting
    slot = lt.snapshot()[DAY]
    assert (slot["plain_turns"], slot["plain_calls"]) == (1, 2)
    assert (slot["tool_turns"], slot["tool_calls"]) == (2, 18)
    assert (slot["deep_turns"], slot["deep_calls"], slot["deep_input"]) == (1, 12, 24000)
    assert slot["turns"] == 3 and slot["calls"] == 20


@pytest.mark.parametrize("setting,audit,suppressed,source,held,audit_counted", [
    (True, False, False, "setting", False, 0),
    (False, True, False, None, False, 1),   # audit wording alone no longer runs deep mode, but is still counted
    (True, True, False, "setting", False, 1),
    (False, False, False, None, False, 0),
    (True, False, True, None, True, 0),     # the setting was on, held back by low-power mode
])
def test_only_the_users_setting_runs_deep_mode_and_audit_wording_is_counted_on_its_own(setting, audit, suppressed, source, held, audit_counted):
    turn = one_turn(3, tool="react", setting=setting, audit=audit, suppressed=suppressed)
    assert turn.deep_source == source and turn.deep_suppressed is held
    slot = lt.snapshot()[DAY]
    assert slot["deep_suppressed_turns"] == int(held) and slot["audit_wording_turns"] == audit_counted
    assert slot["deep_turns"] == int(source is not None)
    for name in ("audit", "both"):
        assert slot[f"deep_{name}_turns"] == 0  # no longer produced; only older days carry them


def test_the_engine_is_counted():
    one_turn(2, tool="native")
    one_turn(2, tool="react")
    assert lt.snapshot()[DAY]["native_turns"] == 1


def test_a_turn_is_counted_once_however_many_times_it_is_finished():
    turn = lt.begin()
    calls(turn, 2)
    for _ in range(3):
        lt.finish(turn, "ok", DAY)
    assert lt.snapshot()[DAY]["turns"] == 1 and lt.snapshot()[DAY]["calls"] == 2
    lt.finish(None)  # nothing to finish: no error


def test_a_paused_chat_and_a_failed_turn_are_counted_without_skewing_the_averages():
    one_turn(0, outcome="blocked")
    one_turn(4, outcome="error")
    slot = lt.snapshot()[DAY]
    assert slot["blocked_turns"] == 1 and slot["error_turns"] == 1
    assert slot["turns"] == 1 and slot["calls"] == 4  # the paused chat made no model call and is not a turn of the average


# ---- saving ----------------------------------------------------------------------------------------------------------------

def test_flush_writes_one_increment_per_day_with_the_biggest_turn_as_a_max():
    one_turn(3)
    one_turn(7, tool="react", audit=True)
    db = FakeDB()
    assert lt.flush(db) == 1
    (name, flt, update), = db.writes
    assert name == lt.COLLECTION and flt == {"day": DAY}
    assert update["$inc"]["turns"] == 2 and update["$inc"]["audit_wording_turns"] == 1 and "deep_audit_turns" not in update["$inc"] and update["$max"] == {"max_calls": 7}
    assert lt.snapshot() == {}  # handed over


def test_a_failed_save_keeps_the_counts_for_the_next_one():
    one_turn(3)
    assert lt.flush(FakeDB(fail=True)) == 0
    assert lt.snapshot()[DAY]["turns"] == 1 and lt.snapshot()[DAY]["max_calls"] == 3
    db = FakeDB()
    assert lt.flush(db) == 1 and db.writes[0][2]["$inc"]["turns"] == 1


def test_the_usage_flush_saves_the_turn_counts_too_and_an_empty_one_writes_nothing():
    one_turn(2)
    db = FakeDB()
    llm_usage.flush(db)
    assert [w[0] for w in db.writes] == [lt.COLLECTION]
    other = FakeDB()
    llm_usage.flush(other)
    assert other.writes == []


# ---- reading it back ---------------------------------------------------------------------------------------------------------

def test_the_summary_gives_averages_per_kind_the_histogram_and_the_unasked_deep_share():
    for _ in range(6):
        one_turn(1, input=1500, output=80)                                      # plain
    for _ in range(2):
        one_turn(5, tool="react", input=3000, output=100)                       # tool
    one_turn(14, tool="react", setting=True, input=6000, output=200, reasoning=300)
    one_turn(10, tool="react", audit=True, input=6000, output=200)  # audit wording: a tool turn, not a deep one
    out = lt.summarize_turns([{**lt.snapshot()[DAY]}])
    assert out["turns"] == 10 and out["plain"]["turns"] == 6 and out["plain"]["avg_calls"] == 1.0
    assert out["tool"]["turns"] == 4 and out["tool"]["avg_calls"] == (5 + 5 + 14 + 10) / 4
    assert out["deep"]["turns"] == 1 and out["deep"]["avg_input"] == 6000 * 14 and out["deep"]["avg_calls"] == 14.0
    assert out["deep_sources"] == {"setting": 1, "audit": 0, "both": 0} and out["deep_unasked"] == 0 and out["deep_share"] == 0.1
    assert out["audit_wording_turns"] == 1
    assert {b["bucket"]: b["turns"] for b in out["histogram"]} == {"c1": 6, "c2": 0, "c3_4": 0, "c5_8": 2, "c9_plus": 2}
    assert out["max_calls"] == 14


def test_an_empty_period_summarizes_to_nothing_and_never_divides_by_zero():
    out = lt.summarize_turns([])
    assert out["turns"] == 0 and out["overall"]["avg_calls"] is None and out["deep_share"] is None and out["deep_unasked"] == 0


def test_days_are_added_together_and_the_biggest_turn_is_the_biggest_of_all_days():
    out = lt.summarize_turns([{"turns": 2, "calls": 4, "plain_turns": 2, "plain_calls": 4, "max_calls": 3}, {"turns": 1, "calls": 9, "plain_turns": 1, "plain_calls": 9, "max_calls": 9}])
    assert out["turns"] == 3 and out["plain"]["avg_calls"] == 4.3  # 13 calls over 3 turns
    assert out["max_calls"] == 9


def test_fetching_reads_the_saved_days_and_a_broken_database_gives_nothing():
    db = FakeDB()
    db.docs = [{"day": DAY, "turns": 1}]
    assert lt.fetch_turn_docs(db, 7) == [{"day": DAY, "turns": 1}]

    class Broken:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert lt.fetch_turn_docs(Broken(), 7) == []


def test_only_numbers_and_bucket_names_are_ever_kept_never_a_word_a_person_said():
    one_turn(3, tool="react", setting=True)
    saved = json.dumps(lt.snapshot()) + json.dumps(lt.summarize_turns([lt.snapshot()[DAY]]))
    allowed = ["turns", "tool"] + [f"route_{r}" for r in lt.ROUTE_REASONS] + [f"route_alone_{r}" for r in lt.ROUTE_REASONS] + list(lt.ROUTE_REASONS)
    for word in sorted(allowed, key=len, reverse=True):
        saved = saved.replace(word, "")
    assert not re.search(r"user|message|question|prompt|text", saved, re.IGNORECASE)
    assert all(isinstance(v, int) for v in lt.snapshot()[DAY].values())


# ---- wired in --------------------------------------------------------------------------------------------------------------

def test_the_health_payload_carries_the_turn_summary():
    from backend.tests.test_sonic_health import NOW, _DB
    from backend.utils import sonic_health as sh

    out = sh.build_sonic_health(_DB([], [], []), 7, NOW)
    assert "turns" in out["usage"] and out["usage"]["turns"]["turns"] == 0


def test_the_chat_endpoint_starts_a_turn_and_finishes_it_on_every_way_out():
    source = open("app.py", encoding="utf-8").read()
    assert "llm_turn = llm_turns.begin()" in source
    assert 'llm_turns.finish(llm_turn, "blocked")' in source and 'llm_turns.finish(llm_turn, "error")' in source
    assert source.count("llm_turns.finish(llm_turn)") >= 2  # the normal end, and the safety net for a turn the client walked away from


def test_the_real_tool_agent_reports_its_engine_and_what_made_it_deep(monkeypatch):
    from backend.evals import behavior_eval as be

    final = SimpleNamespace(content=json.dumps({"action": "final", "answer": "Nothing conclusive."}))

    async def model(prompt):
        return final

    for question, audit in (("scan the whole repo and tell me every file that reads GRACE_DAYS", True), ("what does mark_overdue do?", False)):
        turn = lt.begin()
        asyncio.run(be.run_tool_agent(model, question, repo=True, engine="react"))
        assert turn.tool_agent and turn.engine == "react" and turn.audit_wording is audit and turn.deep_source is None, question


# ---- why a turn went to the tool agent ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("flags,admin,expected", [
    ({"needs_github_search": True}, False, ["repo_question"]),
    ({"needs_github_search": True, "follow_up_intent": True}, False, ["follow_up"]),
    ({"needs_web_search": True}, False, ["web_search"]),
    ({"needs_code_interpreter": True}, False, ["code_or_data"]),
    ({"needs_calendar_lookup": True, "needs_gmail_lookup": True, "needs_drive_lookup": True}, False, ["calendar", "gmail", "drive"]),
    ({}, True, ["admin_ops"]),
    ({"needs_github_search": True, "needs_web_search": True}, True, ["admin_ops", "repo_question", "web_search"]),
    ({"needs_conversation": True, "needs_memory_save": True, "follow_up_intent": True}, False, []),
    (None, False, []),
])
def test_the_reasons_a_turn_goes_to_the_tool_agent_come_from_the_routers_flags(flags, admin, expected):
    assert lt.tool_agent_reasons(flags, admin) == expected


def test_reasons_are_counted_per_turn_and_a_turn_with_one_reason_is_also_counted_alone():
    for reasons in (["repo_question"], ["repo_question"], ["repo_question", "web_search"], ["follow_up"]):
        turn = lt.begin()
        calls(turn, 3)
        lt.note_tool_agent("native", False, False, False)
        lt.note_route(reasons)
        lt.finish(turn, "ok", DAY)
    slot = lt.snapshot()[DAY]
    assert slot["route_repo_question_turns"] == 3 and slot["route_alone_repo_question_turns"] == 2
    assert slot["route_web_search_turns"] == 1 and slot["route_alone_web_search_turns"] == 0
    assert slot["route_follow_up_turns"] == 1 and slot["route_alone_follow_up_turns"] == 1


def test_a_reason_noted_twice_in_a_turn_counts_once_and_unknown_names_are_dropped():
    turn = lt.begin()
    lt.note_route(["repo_question", "repo_question", "not_a_reason"])
    lt.note_route(["repo_question"])
    lt.finish(turn, "ok", DAY)
    slot = lt.snapshot()[DAY]
    assert slot["route_repo_question_turns"] == 1 and slot["route_alone_repo_question_turns"] == 1
    lt.note_route(["gmail"])  # no turn running: nothing happens, nothing raises


def test_the_summary_lists_the_reasons_biggest_first_with_how_often_each_stood_alone():
    docs = [{"turns": 5, "route_repo_question_turns": 4, "route_alone_repo_question_turns": 3, "route_follow_up_turns": 2,
             "route_alone_follow_up_turns": 2, "route_web_search_turns": 1}]
    out = lt.summarize_turns(docs)
    assert out["routing"] == [
        {"reason": "repo_question", "turns": 4, "alone": 3},
        {"reason": "follow_up", "turns": 2, "alone": 2},
        {"reason": "web_search", "turns": 1, "alone": 0},
    ]
    assert lt.summarize_turns([])["routing"] == []


def test_the_router_notes_why_it_chose_the_tool_agent_for_every_path_into_it(monkeypatch):
    from backend.services import agent_workflow as aw

    monkeypatch.setattr(aw, "_is_admin_ops_question", lambda state: False)
    cases = [
        ("conversational", {"needs_github_search": True}, {"repo_question"}),
        ("conversational", {"needs_github_search": True, "follow_up_intent": True}, {"follow_up"}),
        ("conversational", {"needs_web_search": True, "needs_code_interpreter": True}, {"web_search", "code_or_data"}),
        ("web_search", {}, {"approval_continuation"}),
        ("resume_tool_agent", {}, {"clarification_resume"}),
        ("conversational", {"needs_conversation": True}, set()),
    ]
    for intent, flags, expected in cases:
        turn = lt.begin()
        state = {"reasoner_flags": flags, "messages": [HumanMessage(content="x")]}
        plan = aw.build_agent_plan(intent, state)
        assert turn.route_reasons == expected, (intent, flags)
        assert ("tool_agent" in plan["agents"]) is bool(expected)


def test_an_admin_ops_question_is_a_reason_too(monkeypatch):
    from backend.services import agent_workflow as aw

    monkeypatch.setattr(aw, "_is_admin_ops_question", lambda state: True)
    turn = lt.begin()
    aw.build_agent_plan("conversational", {"reasoner_flags": {}, "messages": [HumanMessage(content="x")]})
    assert turn.route_reasons == {"admin_ops"}
