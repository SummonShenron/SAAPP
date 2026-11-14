"""Pattern stories: notes for Sonic's record written by CODE from a finished day's counters, for templates a person approved (backend/utils/pattern_notes.py; drafts in docs/experience-templates-draft.md).

The day fixtures are the real counter values of the days the rules were measured on (2026-10-22 to 2026-11-10), so a rule cannot drift from what was measured."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import experience_capture as xc
from backend.utils import pattern_notes as pn
from backend.utils import self_counters as sc

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)

# the real days (counter name -> value), as measured
ROUGH = {
    "2026-10-28": {"code_edit_applied": 22, "code_edit_declined": 2, "code_pushback": 12, "code_frustrated": 10, "code_cap_hit": 8},
    "2026-10-30": {"code_edit_applied": 22, "code_pushback": 16, "code_frustrated": 15, "code_cap_hit": 7},
    "2026-10-31": {"code_edit_applied": 19, "code_edit_declined": 1, "code_edit_undone": 1, "code_pushback": 11, "code_frustrated": 11, "code_cap_hit": 3, "code_plan_stalled": 11},
    "2026-11-08": {"code_edit_applied": 7, "code_edit_declined": 2, "code_pushback": 13, "code_frustrated": 13, "code_cap_hit": 3},
}
THIN = {"2026-11-05": {"code_edit_applied": 3, "code_edit_declined": 1, "code_pushback": 4, "code_frustrated": 4}, "2026-10-22": {"code_edit_applied": 0, "code_cap_hit": 7}}
GOOD = {"2026-10-25": {"code_edit_applied": 6, "code_pushback": 1}, "2026-11-07": {"code_edit_applied": 9, "code_pushback": 2}, "2026-11-10": {"code_edit_applied": 9, "code_cap_hit": 1}}


@pytest.fixture(autouse=True)
def counted(monkeypatch):
    seen = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: seen.append(dict(counts)))
    return seen


def database(days):
    db = FakeDB()
    for day, counts in days.items():
        db[sc.COUNTERS_COLLECTION].insert_one({"_id": day, "day": day, "counts": counts})
    return db


def approve_all(db):
    for tid in pn.TEMPLATE_IDS:
        pn.approve(db, tid, "jack@example.com", NOW)


def notes(db):
    return sorted(db[xc.COLLECTION].find({"signal": "pattern"}), key=lambda n: n["on"])


# ---- the measures and the rules --------------------------------------------------------------------------------------------------------------

def test_setbacks_take_the_larger_of_pushback_and_frustrated_not_their_sum_and_junk_counts_as_nothing():
    assert pn.setbacks({"code_pushback": 5, "code_frustrated": 9, "code_cap_hit": 1}) == 10
    assert pn.setbacks({"code_edit_declined": 2, "code_edit_undone": 1, "code_plan_stalled": 3}) == 6
    assert pn.setbacks({"code_pushback": "x", "code_cap_hit": None, "code_edit_declined": -4}) == 0 and pn.setbacks({}) == 0
    assert pn.landed({"code_edit_applied": 7}) == 7 and pn.landed({"code_edit_applied": "x"}) == 0


@pytest.mark.parametrize("day,counts,setbacks,landed", [
    ("2026-10-28", ROUGH["2026-10-28"], 22, 22), ("2026-10-30", ROUGH["2026-10-30"], 23, 22), ("2026-10-31", ROUGH["2026-10-31"], 27, 19), ("2026-11-08", ROUGH["2026-11-08"], 18, 7),
])
def test_the_four_measured_rough_days_fire_the_rough_day_template_with_their_real_numbers(day, counts, setbacks, landed):
    match = pn.evaluate(counts, day)
    assert match and match["template"] == "rough_day" and match["day"] == day
    assert f"it had {setbacks} setbacks" in match["text"] and f"and {landed} edits that landed." in match["text"]


def test_a_thin_day_with_few_setbacks_is_not_rough_even_when_nothing_landed():
    for day, counts in THIN.items():
        assert pn.evaluate(counts, day) is None, day
    assert pn.evaluate({"code_edit_applied": 30, "code_pushback": 12}, "2026-11-01") is None       # many setbacks, but far more edits landed
    assert pn.evaluate({"code_edit_applied": 8, "code_cap_hit": 8}, "2026-11-01")["template"] == "rough_day"      # exactly as many as landed counts


@pytest.mark.parametrize("day", sorted(GOOD))
def test_the_three_measured_good_days_fire_the_good_day_template(day):
    match = pn.evaluate(GOOD[day], day)
    assert match and match["template"] == "good_day" and f"{GOOD[day]['code_edit_applied']} of its edits landed" in match["text"]


def test_good_day_boundaries_and_wording_for_none_one_and_several_setbacks():
    assert pn.evaluate({"code_edit_applied": 4}, "d") is None                                          # under the floor
    assert pn.evaluate({"code_edit_applied": 6, "code_pushback": 3}, "d") is None                      # 3 x 3 is more than 6
    assert pn.evaluate({"code_edit_applied": 6, "code_pushback": 2}, "2026-11-01")["text"].endswith("with 2 setbacks.")
    assert pn.evaluate({"code_edit_applied": 5, "code_pushback": 1}, "2026-11-01")["text"] == "On 2026-11-01, 5 of its edits landed, with 1 setback."
    assert pn.evaluate({"code_edit_applied": 5}, "2026-11-01")["text"] == "On 2026-11-01, 5 of its edits landed, with no setbacks."
    assert pn.evaluate({"code_edit_applied": 6, "code_edit_declined": 1}, "2026-11-01")["text"] == "On 2026-11-01, 6 of its edits landed, with 1 setback."


def test_almost_there_fires_on_the_counter_and_says_how_many_turns():
    one = pn.evaluate({"turn_end_ran_out_progress": 1}, "2026-11-09")
    assert one["template"] == "almost_there" and one["text"] == "On 2026-11-09, a coding turn ran out of steps while its last step was still making progress, so the work stopped before it was done."
    assert "3 coding turns ran out of steps while their last steps were still making progress" in pn.evaluate({"turn_end_ran_out_progress": 3}, "2026-11-09")["text"]
    assert pn.evaluate({"turn_end_ran_out_progress": 0}, "2026-11-09") is None


def test_a_day_gets_one_note_the_first_template_that_fits_and_only_from_approved_templates():
    both = {**ROUGH["2026-11-08"], "turn_end_ran_out_progress": 1}
    assert pn.evaluate(both, "2026-11-08")["template"] == "rough_day"                                   # rough comes first
    assert pn.evaluate(both, "2026-11-08", ["almost_there"])["template"] == "almost_there"
    assert pn.evaluate(both, "2026-11-08", ["good_day"]) is None and pn.evaluate(both, "2026-11-08", []) is None
    assert pn.evaluate(GOOD["2026-11-07"], "2026-11-07", ["rough_day", "almost_there"]) is None


def test_the_templates_held_back_on_purpose_are_not_built():
    assert pn.TEMPLATE_IDS == ["rough_day", "almost_there", "good_day"]


# ---- what a note may say -------------------------------------------------------------------------------------------------------------------------

def test_every_note_the_real_days_make_passes_the_note_checks_and_says_nothing_about_feelings():
    for days in (ROUGH, GOOD, {"2026-11-09": {"turn_end_ran_out_progress": 2}}):
        for day, counts in days.items():
            text = pn.evaluate(counts, day)["text"]
            assert pn.note_issue(text) is None, text
            assert text.startswith(f"On {day}, ") and text.endswith(".")


@pytest.mark.parametrize("text,issue", [
    ("It had 9 setbacks.", "not one dated sentence"),
    ("On 2026-11-01, it had 9 setbacks.\nAnd more.", "not one dated sentence"),
    ('On 2026-11-01, it had 9 "setbacks".', "contains a quote, link or path"),
    ("On 2026-11-01, it had 9 setbacks, see https://example.com.", "contains a quote, link or path"),
    ("On 2026-11-01, it felt discouraged after 9 setbacks.", "talks about feeling or intent"),
    ("On 2026-11-01, " + "word " * 60 + "ended.", "too long"),
])
def test_a_template_edited_into_something_it_must_not_say_is_refused(text, issue):
    assert pn.note_issue(text) == issue


def test_a_note_that_claims_a_lived_past_crosses_the_identity_line_even_when_it_names_no_feeling():
    for text in ("On 2026-11-01, I've been there and it had 9 setbacks.", "On 2026-11-01, it had 9 setbacks, which I once went through myself."):
        assert not pn.experience_capture._FEELING_RE.search(text)                      # only the identity check can refuse these
        assert pn.note_issue(text) == "claims a past experience"


# ---- approval ------------------------------------------------------------------------------------------------------------------------------------

def test_a_template_is_approved_and_revoked_by_id_and_unknown_ids_are_refused():
    db = FakeDB()
    assert pn.approved_ids(db) == []
    assert pn.approve(db, "rough_day", "jack@example.com", NOW) and pn.approved_ids(db) == ["rough_day"]
    assert not pn.approve(db, "made_up", "jack@example.com", NOW) and not pn.revoke(db, "made_up") and not pn.approve(None, "rough_day")
    assert pn.revoke(db, "rough_day") and pn.approved_ids(db) == []
    assert db[pn.TEMPLATES_COLLECTION].find_one({"_id": "rough_day"})["approved"] is False


def test_the_dashboard_view_lists_each_template_with_its_rule_an_example_and_its_state():
    db = database(ROUGH)
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    pn.run_pass(db, NOW, backfill=True)
    view = {t["id"]: t for t in pn.view(db)}
    assert list(view) == pn.TEMPLATE_IDS
    rough = view["rough_day"]
    assert rough["approved"] and rough["approved_at"] and rough["notes"] == 4 and rough["latest"] == "2026-11-08"
    assert rough["rule"] and rough["key"] and rough["careful"] and rough["example"].startswith("On 2026-01-01, it had ") and pn.note_issue(rough["example"]) is None
    assert not view["good_day"]["approved"] and view["good_day"]["notes"] == 0 and view["good_day"]["latest"] is None
    assert all(t["example"] and pn.note_issue(t["example"]) is None for t in view.values())


# ---- writing -------------------------------------------------------------------------------------------------------------------------------------

def test_nothing_is_written_until_a_template_is_approved_and_then_only_for_it():
    db = database({**ROUGH, **GOOD})
    assert pn.run_pass(db, NOW, backfill=True) == {"written": 0, "capped": 0} and notes(db) == []
    pn.approve(db, "good_day", "jack@example.com", NOW)
    assert pn.run_pass(db, NOW, backfill=True)["written"] == 3
    assert {n["template"] for n in notes(db)} == {"good_day"}


def test_a_note_is_an_approved_record_note_with_its_day_text_key_and_the_template_that_made_it():
    db = database({"2026-11-08": ROUGH["2026-11-08"]})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    pn.run_pass(db, NOW)
    note = notes(db)[0]
    assert note["kind"] == "record" and note["signal"] == "pattern" and note["status"] == "approved" and note["area"] == "general" and note["count"] == 1
    assert note["on"] == "2026-11-08" and note["template"] == "rough_day" and note["approved_by"] == "template:rough_day"
    assert note["what"] == "On 2026-11-08, it had 18 setbacks in its coding work (pushback, declined or undone edits, stalled plans, step limits) and 7 edits that landed."
    assert note["key"] == "A day when a lot of what I tried ran into something, and not much got through."
    assert note["_id"].startswith("pattern-") and "expires_at" not in note


def test_only_finished_days_are_read_so_today_is_never_judged_early():
    db = database({"2026-11-12": ROUGH["2026-11-08"], "2026-11-11": ROUGH["2026-10-30"]})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    assert pn.run_pass(db, NOW)["written"] == 1 and [n["on"] for n in notes(db)] == ["2026-11-11"]


def test_a_second_pass_writes_nothing_new_and_a_deleted_note_is_not_written_again():
    db = database({"2026-11-08": ROUGH["2026-11-08"]})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    assert pn.run_pass(db, NOW)["written"] == 1 and pn.run_pass(db, NOW)["written"] == 0
    assert xc.delete(db, notes(db)[0]["_id"]) and notes(db) == []
    assert pn.run_pass(db, NOW + timedelta(days=1))["written"] == 0 and pn.run_pass(db, NOW + timedelta(days=1), backfill=True)["written"] == 0 and notes(db) == []


def test_at_most_the_weekly_cap_of_notes_a_week_and_the_held_back_day_is_counted_and_can_be_written_later():
    """The cap is Jack's to set (7 on 2026-11-13: he wants as much of the behaviour as possible); the test follows pn.WEEKLY_CAP so it does not pin a number."""
    cap = pn.WEEKLY_CAP
    finished = sorted(f"2026-11-{11 - back:02d}" for back in range(cap + 1))                           # the cap plus one finished days ending the day before NOW, all inside the daily pass's window
    db = database({day: ROUGH["2026-10-28"] for day in finished})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    assert pn.run_pass(db, NOW) == {"written": cap, "capped": 1}
    assert [n["on"] for n in notes(db)] == finished[:cap]                                                    # oldest first; the newest day is the one held back
    assert pn.run_pass(db, NOW + timedelta(days=8))["written"] == 1 and [n["on"] for n in notes(db)][-1] == finished[-1]          # a week later, within the window, the held-back day is written


def test_the_daily_pass_reads_only_the_last_two_weeks_and_backfill_reads_every_earlier_day_up_to_ten():
    db = database({"2026-10-20": ROUGH["2026-10-28"], "2026-11-09": ROUGH["2026-11-08"]})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    assert pn.run_pass(db, NOW)["written"] == 1 and [n["on"] for n in notes(db)] == ["2026-11-09"]
    assert pn.run_pass(db, NOW, backfill=True)["written"] == 1 and [n["on"] for n in notes(db)] == ["2026-10-20", "2026-11-09"]
    many = database({f"2026-09-{day:02d}": ROUGH["2026-10-28"] for day in range(1, 16)})
    pn.approve(many, "rough_day", "jack@example.com", NOW)
    assert pn.run_pass(many, NOW, backfill=True) == {"written": pn.BACKFILL_LIMIT, "capped": 5}                       # backfill ignores the weekly cap but stops at its own limit


def test_one_note_a_day_even_when_two_templates_fit_and_a_revoked_template_writes_no_more():
    db = database({"2026-11-08": {**ROUGH["2026-11-08"], "turn_end_ran_out_progress": 1}, "2026-11-09": {"turn_end_ran_out_progress": 1}})
    approve_all(db)
    assert pn.run_pass(db, NOW, backfill=True)["written"] == 2 and [(n["on"], n["template"]) for n in notes(db)] == [("2026-11-08", "rough_day"), ("2026-11-09", "almost_there")]
    db = database({"2026-11-09": {"turn_end_ran_out_progress": 1}})
    pn.approve(db, "almost_there", "jack@example.com", NOW)
    pn.revoke(db, "almost_there")
    assert pn.run_pass(db, NOW)["written"] == 0


def test_a_database_that_is_missing_changes_nothing():
    assert pn.run_pass(None, NOW) == {"written": 0, "capped": 0} and pn.view(None)[0]["approved"] is False


def test_the_counters_are_registered_and_every_one_is_incremented(counted):
    assert set(sc.PATTERN_KEYS) == {"pattern_note_written", "pattern_note_capped"} and set(sc.PATTERN_KEYS) <= sc.KEYS
    db = database({f"2026-11-{day:02d}": ROUGH["2026-10-28"] for day in range(1, pn.WEEKLY_CAP + 2)})
    pn.approve(db, "rough_day", "jack@example.com", NOW)
    pn.run_pass(db, NOW)
    assert {"pattern_note_written": pn.WEEKLY_CAP} in counted and {"pattern_note_capped": 1} in counted


# ---- the record ----------------------------------------------------------------------------------------------------------------------------------

def test_a_written_note_is_citable_like_any_approved_note_and_shows_in_the_list_with_its_key():
    db = database({"2026-11-07": GOOD["2026-11-07"]})
    pn.approve(db, "good_day", "jack@example.com", NOW)
    pn.run_pass(db, NOW)
    entries = xc.approved_entries(db)
    assert len(entries) == 1 and entries[0]["kind"] == "record" and entries[0]["what"].startswith("On 2026-11-07, 9 of its edits landed") and entries[0]["when"] == entries[0]["what"]
    assert entries[0]["key"] == "A day where things mostly went right and each step held."
    listed = xc.list_all(db)
    assert listed[0]["signal"] == "pattern" and listed[0]["template"] == "good_day" and listed[0]["key"]


def test_a_pattern_note_cannot_be_edited_only_deleted():
    db = database({"2026-11-07": GOOD["2026-11-07"]})
    pn.approve(db, "good_day", "jack@example.com", NOW)
    pn.run_pass(db, NOW)
    problem = xc.edit(db, notes(db)[0]["_id"], "It did something else.")
    assert problem and "written from its template" in problem
    assert notes(db)[0]["what"].startswith("On 2026-11-07")
    assert xc.delete(db, notes(db)[0]["_id"])


# ---- wiring --------------------------------------------------------------------------------------------------------------------------------------

def test_the_endpoints_are_admin_only_and_thin_and_the_schedule_starts_and_stops_with_the_app():
    source = open("app.py", encoding="utf-8").read()
    for route in ('@app.get("/api/admin/pattern-templates")', '@app.post("/api/admin/pattern-templates/{template_id}/approve")', '@app.post("/api/admin/pattern-templates/{template_id}/revoke")',
                  '@app.post("/api/admin/pattern-templates/backfill")'):
        block = source[source.index(route):]
        assert "Depends(require_global_admin)" in block[: block.index("\n\n\n")], route
    assert "pattern_notes_task = spawn_background_task(pattern_notes.run_schedule(get_db))" in source and "    pattern_notes_task.cancel()" in source
    assert "pattern_notes.run_pass, db, None, backfill=True" in source and "from backend.utils import pattern_notes" in source


def test_the_schedule_runs_a_pass_and_survives_a_failing_one(monkeypatch):
    import asyncio

    runs = []

    def flaky(db):
        runs.append(db)
        if len(runs) == 1:
            raise RuntimeError("down")
        return {"written": 0, "capped": 0}

    monkeypatch.setattr(pn, "run_pass", flaky)

    async def go():
        task = asyncio.create_task(pn.run_schedule(lambda: "db", check_every=0.01, first_delay=0.0))
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert len(runs) >= 2                       # the first pass failed and the loop carried on
