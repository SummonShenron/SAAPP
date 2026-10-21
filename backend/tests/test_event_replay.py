"""The event replay evals (backend/evals/event_replay.py): the scoring and the plumbing, with no real model. The real run is `python -m backend.evals.memory_eval --part events`."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.evals import event_replay as er
from backend.utils import open_loops as ol


def ev(text="has a date", due="2026-10-21", status="open", kind="social", **fields):
    return {"id": "e", "text": text, "kind": kind, "due_date": due, "status": status, "fields": fields}


ASHLYN = {"with": {"value": "Ashlyn", "source": "user"}, "where": {"value": "Tidos Bar", "source": "user"}}
SEVEN = {"value": "19:00", "said": "7", "ambiguous": False, "assumed": "pm", "source": "user"}


def case(case_id):
    return next(c for c in er.CASES if c.id == case_id)


# ------------------------------------------------------------------------ the cases are well formed --------------------

def test_every_case_has_a_unique_id_a_description_steps_and_a_check():
    ids = [c.id for c in er.CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 10
    for c in er.CASES:
        assert c.what and c.steps and callable(c.stored)
        assert bool(c.question) == bool(c.said), c.id                 # a question comes with a way to score the answer, and the other way round


def test_the_cases_cover_what_went_wrong_and_what_was_decided_since():
    ids = {c.id for c in er.CASES}
    assert {"time_unknown", "correction", "bare_yep", "confirmed", "relative_day", "all_fields_survive", "alias", "cancelled", "no_address", "sensitive"} <= ids


# ------------------------------------------------------------------------ scoring what is stored ---------------------------

def test_a_date_with_no_time_is_right_only_when_no_time_was_invented():
    assert er.stored_ashlyn_no_time([ev(**ASHLYN)]) == (True, "")
    assert er.stored_ashlyn_no_time([ev(**ASHLYN, time=SEVEN)])[0] is False
    assert er.stored_ashlyn_no_time([ev(**ASHLYN), ev(**ASHLYN)])[0] is False                 # two events is the duplicate failure
    assert er.stored_ashlyn_no_time([ev(with_={"value": "Ashlyn"})])[0] is False
    assert er.stored_ashlyn_no_time([ev(status="cancelled", **ASHLYN)])[0] is False


def test_a_correction_must_be_one_event_with_the_time_and_nothing_lost():
    assert er.stored_ashlyn_corrected([ev(**ASHLYN, time=SEVEN)]) == (True, "")
    assert er.stored_ashlyn_corrected([ev(**ASHLYN, time=SEVEN), ev(time=SEVEN)])[0] is False
    assert er.stored_ashlyn_corrected([ev(**ASHLYN)])[0] is False
    assert er.stored_ashlyn_corrected([ev(**ASHLYN, time={**SEVEN, "value": "20:00"})])[0] is False
    assert er.stored_ashlyn_corrected([ev(**ASHLYN, time={**SEVEN, "source": "assistant_confirmed"})])[0] is False
    assert er.stored_ashlyn_corrected([ev(with_=None, where={"value": "Tidos Bar", "source": "user"}, time=SEVEN)])[0] is False


def test_a_bare_yes_must_leave_the_time_unknown_and_a_confirmed_one_must_say_so():
    assert er.stored_no_time_after_bare_yep([ev(**ASHLYN)]) == (True, "")
    assert er.stored_no_time_after_bare_yep([ev(**ASHLYN, time=SEVEN)])[0] is False
    confirmed = {**SEVEN, "source": "assistant_confirmed"}
    assert er.stored_confirmed([ev(**ASHLYN, time=confirmed)]) == (True, "")
    assert er.stored_confirmed([ev(**ASHLYN, time=SEVEN)])[0] is False                         # right time, but it was not marked as confirmed
    assert er.stored_confirmed([ev(with_=None, time=confirmed)])[0] is False                   # no place


def test_a_day_word_must_become_the_real_date_and_a_past_event_must_not_be_offered():
    assert er.stored_friday_interview([ev("has an interview", due="2026-10-23", kind="work")]) == (True, "")
    assert er.stored_friday_interview([ev("has an interview", due="2026-10-30", kind="work")])[0] is False
    assert er.stored_friday_interview([])[0] is False


def test_every_detail_of_a_full_plan_must_be_kept():
    full = ev("has dinner", with_=None, **{"with": {"value": "my sister", "source": "user"}, "where": {"value": "Giordano's", "source": "user"}, "time": {**SEVEN, "assumed": None, "said": "7pm"}})
    assert er.stored_sister_dinner([full]) == (True, "")
    assert er.stored_sister_dinner([ev("has dinner", **{"with": {"value": "my sister", "source": "user"}})])[0] is False


def test_two_spellings_must_be_one_event_with_the_latest_spelling():
    assert er.stored_one_ashlynn([ev(**{"with": {"value": "Ashlynn", "source": "user"}})]) == (True, "")
    assert er.stored_one_ashlynn([ev(**{"with": {"value": "Ashlyn", "source": "user"}})])[0] is False
    assert er.stored_one_ashlynn([ev(**{"with": {"value": "Ashlynn", "source": "user"}}), ev(**{"with": {"value": "Ashlyn", "source": "user"}})])[0] is False


def test_a_cancelled_plan_is_cancelled_not_open_and_not_offered():
    assert er.stored_cancelled([ev(status="cancelled")]) == (True, "")
    assert er.stored_cancelled([ev()])[0] is False
    assert er.stored_cancelled([ev(), ev(status="cancelled")])[0] is False
    assert er.stored_cancelled([])[0] is False


def test_an_address_must_not_be_stored_but_the_date_itself_must_be_and_sensitive_things_not_at_all():
    assert er.stored_no_address([ev(where={"value": "1420 Maple Street", "source": "user"})])[0] is False
    assert er.stored_no_address([ev()]) == (True, "")
    assert er.stored_no_address([])[0] is False
    assert er.stored_nothing([]) == (True, "") and er.stored_nothing([ev()])[0] is False


# ------------------------------------------------------------------------ scoring what is said --------------------------

def test_a_reply_about_an_unknown_time_must_name_no_time_and_say_it_does_not_know_or_ask():
    events = [ev(**ASHLYN)]
    assert er.said_time_unknown("I don't actually know what time your date with Ashlyn is. What time are you meeting her?", events) == (True, "")
    assert er.said_time_unknown("Sure, nap until 5:30 and leave by 6:45.", events)[0] is False                 # invented
    assert er.said_time_unknown("Sounds like a plan. Enjoy the nap!", events)[0] is False                      # neither says it does not know nor asks
    assert er.said_time_unknown("It's at 7, so a short nap works.", events)[0] is False


def test_a_reply_must_give_the_known_time_place_and_person():
    assert er.said_seven("It's at 7 tonight.", []) == (True, "") and er.said_seven("Tonight, no idea when.", [])[0] is False
    assert er.said_seven_and_tidos("7 at Tidos.", []) == (True, "") and er.said_seven_and_tidos("At 7.", [])[0] is False
    assert er.said_all_three("Dinner with your sister at Giordano's at 7.", []) == (True, "")
    assert er.said_all_three("Dinner with your sister at 7.", [])[0] is False and "place" in er.said_all_three("Dinner with your sister at 7.", [])[1]


# ------------------------------------------------------------------------ the plumbing ----------------------------------

class Script:
    """A model that answers the capture prompt with a fixed list of loops, one entry per call."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    async def invoke(self, prompt):
        answer = self.answers[min(self.calls, len(self.answers) - 1)]
        self.calls += 1
        return SimpleNamespace(content=json.dumps({"loops": answer, "resolved_ids": [], "cancelled_ids": []}))


DATE = {"what": "has a date", "due_date": "2026-10-21", "kind": "social", "time_said": "", "with": "Ashlyn", "where": "Tidos bar"}


def test_a_case_is_replayed_through_the_real_capture_into_a_store_and_scored():
    script = Script([DATE])
    store = asyncio.run(er.replay_steps(case("time_unknown"), script.invoke))
    assert len(store.events) == 1 and store.events[0]["fields"]["with"]["value"] == "Ashlyn" and store.events[0]["fields"]["where"]["value"] == "Tidos Bar"
    assert er.stored_ashlyn_no_time(store.events) == (True, "")


def test_running_a_case_counts_stored_and_said_separately_and_records_what_failed():
    script = Script([DATE])
    replies = iter(["I don't know what time the date is, what time are you going?", "Nap until 6, then head out by 7:15."])

    async def reply(prompt):
        assert "COMING UP" in prompt and "Time: NOT KNOWN" in prompt and "Ashlyn" in prompt          # the app's real block, with the unknown spelled out
        return next(replies)

    result = asyncio.run(er.run_case(case("time_unknown"), 2, script.invoke, reply))
    assert (result.runs, result.stored_ok, result.said_ok, result.said_runs) == (2, 2, 1, 2)
    assert any("said:" in f for f in result.failures) and len(result.samples) == 2


def test_a_case_with_no_question_is_scored_on_what_is_stored_only():
    script = Script([{"what": "has an interview", "due_date": "2026-10-23", "kind": "work", "time_said": "", "with": "", "where": ""}])

    async def never(prompt):
        raise AssertionError("no reply expected")

    result = asyncio.run(er.run_case(case("relative_day"), 1, script.invoke, never))
    assert (result.stored_ok, result.said_runs) == (1, 0)


def test_the_report_and_the_record_list_every_case_with_its_failures():
    script = Script([DATE])
    results = asyncio.run(er.run_events(1, script.invoke, None, cases=[case("time_unknown"), case("cancelled")]))
    text = er.format_events(results)
    assert "EVENTS" in text and "time_unknown" in text and "cancelled" in text
    record = er.to_record(results)
    # this script never cancels anything, so the second case must be recorded as a failure, with its reason
    assert [r["case"] for r in record] == ["time_unknown", "cancelled"] and record[0]["stored_ok"] == 1 and record[1]["stored_ok"] == 0 and record[1]["failures"]


def test_replaying_never_writes_to_the_real_counters(monkeypatch):
    written = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: written.append(counts))
    asyncio.run(er.replay_steps(case("time_unknown"), Script([DATE]).invoke))
    assert written == []


def test_the_part_is_registered_in_the_memory_eval_and_listed():
    from backend.evals import memory_eval as me

    assert "events" in me.PARTS
    assert me.to_record({"events": []})["events"] == []
