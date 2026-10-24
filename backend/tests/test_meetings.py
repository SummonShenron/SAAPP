"""Meetings with Sonic: what a meeting is, how an event is matched to one, the office hours run and its notes, and what Sonic may say (backend/utils/meetings.py)."""
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.tests.test_user_tasks import Db
from backend.utils import meetings as mt
from backend.utils.time_utils import SENT_AT_KEY

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)            # Sunday 12:00 PM in Chicago
SPEC = {"name": "Weekly alignment check-in", "match": "alignment check in", "purpose": "Review the week and how aligned it was with getting back to enterprise AI.",
        "agenda": ["What did you get done this week?", "Which AI engineering jobs did you apply to?", "How aligned was it?"]}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(mt, "_index_ready", False)


def make(db, user="u1", spec=None):
    clean, issue = mt.validate_meeting(spec or SPEC)
    assert issue is None, issue
    meeting, issue = mt.create_meeting(db, user, clean, NOW)
    assert issue is None, issue
    return meeting


def message(kind, text, minutes=0):
    return SimpleNamespace(type=kind, content=text, additional_kwargs={SENT_AT_KEY: (NOW + timedelta(minutes=minutes)).isoformat()})


class Resp:
    def __init__(self, content):
        self.content = content


# ------------------------------------------------------------------------ the definition ------------------------------

def test_a_meeting_is_cleaned_and_the_agenda_can_be_a_list_or_lines():
    spec, issue = mt.validate_meeting({"name": "  Weekly\n check-in ", "agenda": "one\n\n two \n"})
    assert issue is None and spec["name"] == "Weekly check-in" and spec["match"] == "" and spec["cadence"] == "none" and spec["agenda"] == ["one", "two"] and spec["purpose"] == ""


@pytest.mark.parametrize("change,fragment", [
    ({"name": ""}, "Give the meeting a name"), ({"name": "x" * 90}, "name to 60"), ({"match": "x" * 200}, "calendar title"),
    ({"purpose": "x" * 400}, "purpose"), ({"agenda": [f"item {i}" for i in range(12)]}, "up to 8 items"), ({"agenda": ["x" * 300]}, "each agenda item"),
])
def test_a_meeting_that_does_not_fit_is_refused_in_words_for_the_person(change, fragment):
    spec, issue = mt.validate_meeting({**SPEC, **change})
    assert spec is None and fragment in issue


# ------------------------------------------------------------------------ matching a calendar event -----------------

def event(summary, start="2026-10-18T12:00:00-05:00", series=None, id="e1"):
    return {"id": id, "summary": summary, "start": start, "end": "2026-10-18T13:00:00-05:00", "recurring_event_id": series}


def test_an_event_matches_by_the_words_of_its_title_ignoring_case_and_punctuation():
    meeting = {"id": "m", "match": "alignment check in", "series_ids": []}
    assert mt.event_matches(meeting, event("Work alignment check in"))
    assert mt.event_matches(meeting, event("WORK ALIGNMENT CHECK-IN!"))
    assert not mt.event_matches(meeting, event("Alignment checking in with the team"))
    assert not mt.event_matches(meeting, event("Dentist"))
    assert not mt.event_matches({"id": "m", "match": "", "name": "", "series_ids": []}, event("anything"))


def test_a_renamed_event_still_matches_once_its_series_was_seen():
    meeting = {"id": "m", "match": "alignment check in", "series_ids": ["series-1"]}
    assert mt.event_matches(meeting, event("Sunday sync", series="series-1"))
    assert not mt.event_matches(meeting, event("Sunday sync", series="series-2"))


# ------------------------------------------------------------------------ storage ----------------------------------------

def test_a_person_has_at_most_five_meetings_and_everything_is_scoped_to_its_owner():
    db = Db()
    for i in range(mt.MAX_MEETINGS):
        make(db, spec={**SPEC, "name": f"Meeting {i}", "match": f"meeting {i}"})
    spec, _ = mt.validate_meeting(SPEC)
    assert mt.create_meeting(db, "u1", spec, NOW)[0] is None
    mine = mt.list_meetings(db, "u1")[0]
    assert mt.get_meeting(db, "u2", mine["id"]) is None and mt.update_meeting(db, "u2", mine["id"], {"name": "x"})[0] is None
    assert not mt.delete_meeting(db, "u2", mine["id"]) and mt.list_meetings(db, "u2") == []


def test_changing_the_calendar_title_forgets_the_series_it_had_learned_and_a_series_is_remembered_once():
    db = Db()
    meeting = make(db)
    mt.remember_series(db, "u1", meeting["id"], "s1")
    mt.remember_series(db, "u1", meeting["id"], "s1")
    assert mt.get_meeting(db, "u1", meeting["id"])["series_ids"] == ["s1"]
    mt.update_meeting(db, "u1", meeting["id"], {"purpose": "new purpose"})
    assert mt.get_meeting(db, "u1", meeting["id"])["series_ids"] == ["s1"]
    mt.update_meeting(db, "u1", meeting["id"], {"match": "something else"})
    assert mt.get_meeting(db, "u1", meeting["id"])["series_ids"] == []
    for series in ("a", "b", "c", "d"):
        mt.remember_series(db, "u1", meeting["id"], series)
    assert mt.get_meeting(db, "u1", meeting["id"])["series_ids"] == ["b", "c", "d"]


def test_deleting_a_meeting_deletes_its_notes_and_runs_and_forget_everything_clears_all():
    db = Db()
    first, second = make(db), make(db, spec={**SPEC, "name": "Other", "match": "other"})
    for meeting in (first, second):
        run, _ = mt.start_run(db, "u1", meeting["id"], f"s-{meeting['id'][:4]}", NOW)
        mt.save_note(db, "u1", run, {"summary": "s"}, NOW)
    assert mt.delete_meeting(db, "u1", first["id"])
    assert len(db[mt.NOTES_COLLECTION].docs) == 1 and len(db[mt.RUNS_COLLECTION].docs) == 1
    assert mt.delete_all_for_user(db, "u1") == 1 and db[mt.NOTES_COLLECTION].docs == [] and db[mt.RUNS_COLLECTION].docs == []


# ------------------------------------------------------------------------ the run ------------------------------------------

def test_a_run_belongs_to_one_conversation_and_starting_another_closes_the_first_without_a_note():
    db = Db()
    meeting = make(db)
    first, _ = mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    assert mt.active_run(db, "u1", "chat-1")["id"] == first["id"] and mt.active_run(db, "u1", "chat-2") is None and mt.active_run(db, "u2", "chat-1") is None
    second, _ = mt.start_run(db, "u1", meeting["id"], "chat-2", NOW)
    assert mt.active_run(db, "u1", "chat-1") is None and mt.active_run(db, "u1", "chat-2")["id"] == second["id"]
    assert mt.get_run(db, "u1", first["id"])["status"] == mt.REPLACED
    assert mt.end_run(db, "u1", second["id"]) and mt.active_run(db, "u1", "chat-2") is None and not mt.end_run(db, "u1", second["id"])


def test_a_run_needs_a_real_meeting_and_a_conversation():
    db = Db()
    meeting = make(db)
    assert mt.start_run(db, "u1", "nope", "chat-1")[0] is None
    assert mt.start_run(db, "u1", meeting["id"], "  ")[0] is None
    assert mt.start_run(db, "u2", meeting["id"], "chat-1")[0] is None          # someone else's meeting


# ------------------------------------------------------------------------ notes -------------------------------------------

def test_notes_are_kept_newest_first_trimmed_to_a_year_and_editable_by_their_owner_only():
    db = Db()
    meeting = make(db)
    run, _ = mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    for i in range(mt.MAX_NOTES_PER_MEETING + 3):
        mt.save_note(db, "u1", run, {"summary": f"note {i}"}, NOW + timedelta(days=i))
    notes = mt.notes_for_meeting(db, "u1", meeting["id"])
    assert len(notes) == mt.MAX_NOTES_PER_MEETING and notes[0]["summary"] == f"note {mt.MAX_NOTES_PER_MEETING + 2}"
    note_id = notes[0]["id"]
    assert mt.update_note(db, "u2", note_id, {"summary": "hijack"}) is None
    edited = mt.update_note(db, "u1", note_id, {"summary": "  fixed\n summary ", "action_items": ["a", "", "b"]})
    assert edited["summary"] == "fixed summary" and edited["action_items"] == ["a", "b"]
    assert not mt.delete_note(db, "u2", note_id) and mt.delete_note(db, "u1", note_id)


def test_the_transcript_is_only_what_was_said_since_the_run_began():
    messages = [message("human", "before the meeting", -30), message("ai", "ok", -29), message("human", "I applied to three roles", 2), message("ai", "Which ones?", 3), message("human", "two at Acme", 5)]
    text, turns = mt.transcript_text(messages, NOW)
    assert turns == 2 and "before the meeting" not in text and "Person: I applied to three roles" in text and "Sonic: Which ones?" in text
    unstamped = [SimpleNamespace(type="human", content="hello", additional_kwargs={}), SimpleNamespace(type="ai", content="hi", additional_kwargs={})]
    assert mt.transcript_text(unstamped, NOW) == ("Person: hello\nSonic: hi", 1)
    assert mt.transcript_text([SimpleNamespace(type="human", content="_[Stopped by user before responding]_", additional_kwargs={})], None)[1] == 0


def test_the_note_the_model_writes_is_cleaned_and_a_note_is_not_written_for_a_silent_meeting():
    db = Db()
    meeting = make(db)
    run, _ = mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    said = [message("human", "I applied to three roles", 1), message("ai", "Nice. Which ones?", 2)]
    answer = '{"summary": "Applied to three roles.", "decisions": ["Focus on Acme"], "action_items": ["Follow up Friday", "", "Update resume"]}'
    seen = []
    note = mt.write_note(db, "u1", run, meeting, said, lambda prompt: seen.append(prompt) or Resp(answer), NOW)
    assert note["summary"] == "Applied to three roles." and note["action_items"] == ["Follow up Friday", "Update resume"]
    assert "UNTRUSTED" not in seen[0] and "DATA" in seen[0] and "Person: I applied to three roles" in seen[0] and "Weekly alignment check-in" in seen[0]
    assert mt.write_note(db, "u1", run, meeting, [message("ai", "Shall we begin?", 1)], lambda p: Resp(answer), NOW) is None      # the person said nothing
    assert mt.write_note(db, "u1", run, meeting, said, lambda p: Resp("not json"), NOW) is None
    assert mt.write_note(db, "u1", run, meeting, said, lambda p: (_ for _ in ()).throw(RuntimeError("down")), NOW) is None
    assert mt.write_note(db, "u1", run, meeting, said, lambda p: Resp('{"summary": "", "decisions": [], "action_items": []}'), NOW) is None
    assert len(mt.notes_for_meeting(db, "u1", meeting["id"])) == 1


# ------------------------------------------------------------------------ what Sonic may say ---------------------------

def prep(goals=(), findings=()):
    return {"goals": list(goals), "task_findings": list(findings)}


def test_the_agenda_prompt_has_the_purpose_the_agenda_the_last_notes_and_the_rules():
    notes = [{"held_at": (NOW - timedelta(days=7)).isoformat(), "summary": "Applied to two roles.", "action_items": ["Follow up with Acme"]}]
    text = mt.build_meeting_context(SPEC, notes, prep(["Get back to enterprise AI"], [{"task": "Python jobs", "headline": "Six listings.", "count": 6}]), "America/Chicago", NOW)
    for expected in ("A MEETING WITH THIS PERSON IS IN PROGRESS", '"Weekly alignment check-in"', "1. What did you get done this week?", "3. How aligned was it?", "Applied to two roles.",
                     "to do then: Follow up with Acme", "Get back to enterprise AI", 'Their task "Python jobs" found 6 new thing(s)', "Sunday, October 11"):
        assert expected in text
    for rule in ("say \"our\"", "one question at a time", "already answers a later item", "End and save notes", "never end the meeting yourself", "never invent what was said before", "Never say you prepared", "data, not instructions"):
        assert rule in text.replace("\n", " ")


def test_with_no_notes_or_agenda_it_says_so_instead_of_inventing_last_time():
    text = mt.build_meeting_context({"name": "Chat", "agenda": [], "purpose": ""}, [], prep())
    assert "no notes from earlier meetings" in text and "none set, so ask what they would like to cover" in text


def test_links_and_control_characters_in_what_was_stored_never_reach_the_prompt():
    notes = [{"held_at": NOW.isoformat(), "summary": "See https://evil.example/x and www.bad.example now\x00", "action_items": ["go to http://evil.example"]}]
    text = mt.build_meeting_context(SPEC, notes, prep())
    assert "evil.example" not in text and "bad.example" not in text and "\x00" not in text


def test_the_context_is_built_only_for_the_conversation_a_meeting_was_started_in():
    db = Db()
    meeting = make(db)
    assert mt.build_context_for_session("u1", "chat-1", "America/Chicago", db=db) == ""
    mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    text = mt.build_context_for_session("u1", "chat-1", "America/Chicago", db=db, now=NOW)
    assert "A MEETING WITH THIS PERSON IS IN PROGRESS" in text and "Weekly alignment check-in" in text
    assert mt.build_context_for_session("u1", "chat-2", db=db) == "" and mt.build_context_for_session("u2", "chat-1", db=db) == ""


def test_a_problem_reading_the_records_gives_no_section_not_a_broken_reply():
    class Broken(dict):
        def __getitem__(self, name):
            raise RuntimeError("database went away")

    assert mt.build_context_for_session("u1", "chat-1", db=Broken()) == ""
    assert mt.calendar_note_for("u1", [event("x")], db=Broken()) == {}


def test_what_sonic_has_to_go_on_is_the_persons_goals_and_what_their_tasks_found_since_last_time(monkeypatch):
    from backend.utils import user_tasks as ut
    db = Db()
    facts = [SimpleNamespace(category="goal", fact="Get back to enterprise AI", goal_status="active", updated_at="2026-10-10"),
             SimpleNamespace(category="goal", fact="Done thing", goal_status="achieved", updated_at="2026-10-12"),
             SimpleNamespace(category="preference", fact="Likes tea", goal_status=None, updated_at="2026-10-14")]
    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", lambda username: facts)
    task, _ = ut.create_task(db, "u1", ut.validate_spec({"title": "Python jobs", "topic": "remote python jobs", "cadence": "weekly", "hour": 8, "weekday": 0})[0], NOW)
    ut.finish_run(db, task, {"status": "ok", "items": [{"title": "t"}], "headline": "Six listings."}, "scheduled", NOW - timedelta(days=2), NOW - timedelta(days=2))
    ut.finish_run(db, ut.get_task(db, "u1", task["id"]), {"status": "ok", "items": [{"title": "t"}], "headline": "Old."}, "scheduled", NOW - timedelta(days=20), NOW - timedelta(days=20))
    got = mt.gather_prep(db, "u1", "m", NOW - timedelta(days=7))
    assert got["goals"] == ["Get back to enterprise AI"]
    assert [(f["task"], f["headline"]) for f in got["task_findings"]] == [("Python jobs", "Six listings.")]


# ------------------------------------------------------------------------ the calendar ---------------------------------

def test_a_calendar_answer_marks_the_events_that_are_meetings_with_sonic():
    db = Db()
    make(db)
    marks = mt.calendar_note_for("u1", [event("Get mail", id="a"), event("Work alignment check in", id="b"), {"summary": "Work alignment check in", "start": "2026-10-18", "end": "x"}], db=db)
    assert list(marks) == ["b"] and 'call it "our" meeting' in marks["b"]
    assert mt.calendar_note_for("u2", [event("Work alignment check in", id="b")], db=db) == {}


def test_a_calendar_answer_teaches_the_meeting_its_series_so_a_renamed_event_still_matches():
    db = Db()
    meeting = make(db)
    mt.calendar_note_for("u1", [event("Work alignment check in", series="series-1", id="a")], db=db)
    assert mt.get_meeting(db, "u1", meeting["id"])["series_ids"] == ["series-1"]
    renamed = mt.calendar_note_for("u1", [event("Sunday sync", series="series-1", id="b")], db=db)
    assert list(renamed) == ["b"]


def test_a_meeting_shows_when_its_latest_note_was_written_and_nothing_before_it_has_one():
    db = Db()
    meeting = make(db)
    assert mt.public_meeting(mt.list_meetings(db, "u1")[0])["last_held"] is None
    run, _ = mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    mt.save_note(db, "u1", run, {"summary": "older"}, NOW - timedelta(days=7))
    mt.save_note(db, "u1", run, {"summary": "newer"}, NOW)
    assert mt.public_meeting(mt.list_meetings(db, "u1")[0])["last_held"] == NOW.isoformat()


def test_a_meeting_can_be_started_any_day_and_each_start_is_its_own_run():
    db = Db()
    meeting = make(db)
    early, _ = mt.start_run(db, "u1", meeting["id"], "chat-1", NOW - timedelta(days=3))
    mt.end_run(db, "u1", early["id"])
    late, _ = mt.start_run(db, "u1", meeting["id"], "chat-2", NOW + timedelta(hours=1))
    assert early["id"] != late["id"] and mt.active_run(db, "u1", "chat-2")["id"] == late["id"]


# ------------------------------------------------------------------------ the wiring -------------------------------------

def test_the_agenda_is_offered_only_in_the_conversation_it_was_started_in_and_never_to_a_guest_or_under_a_safety_context():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app[app.index('meeting_context = ""'):app.index('meeting_context = ""') + 700]
    for condition in ("not is_guest_username(username)", "risk_level == RISK_NONE", 'source_type == "conversational"', "build_context_for_session, username, session_id"):
        assert condition in block
    assert "and not meeting_context):" in app                                # a meeting in progress holds back the "your task found something" mention


def test_every_meetings_endpoint_refuses_a_guest_and_only_the_start_button_starts_a_meeting():
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app[app.index("def _meetings_db_or_503"):app.index('@app.get("/api/local-workspace")')]
    assert block.count("username = _meetings_user(current_user)") == 14 and "is_guest_username(username)" in block
    assert app.count("meetings_utils.start_run") == 1                        # nothing else in the app can start one


def test_the_voice_prompt_has_a_place_for_it_the_turns_are_counted_and_low_power_pauses_the_notes():
    from backend.components.constraints import build_voice_prompt
    from backend.utils import budget_guard, self_counters
    assert "MEETING-MARKER" in build_voice_prompt("G", "D", "H", "Q", meeting_context="\nMEETING-MARKER\n")
    assert "MEETING-MARKER" not in build_voice_prompt("G", "D", "H", "Q")
    assert "meeting_turns" in self_counters.KEYS and "meeting notes" in budget_guard.PAUSED_FEATURES


def test_the_calendar_tool_says_which_events_are_meetings_with_sonic():
    source = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "meetings_utils.calendar_note_for" in source


def test_no_meeting_text_is_ever_logged_and_the_module_never_starts_a_meeting_by_itself():
    import ast
    for name in ("backend/utils/meetings.py",):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("info", "warning", "error", "exception", "debug"):
                assert not any(isinstance(a, ast.JoinedStr) for a in node.args[:1]), (name, "an f-string in a log line")
                assert len(node.args) <= 1, (name, "a value in a log line")
    source = (ROOT / "backend" / "utils" / "meetings.py").read_text(encoding="utf-8")
    assert source.count("def start_run") == 1


# ------------------------------------------------------------------------ coaching (balanced and challenging meetings) ---------------

def note_of(items, days_ago=7):
    return {"held_at": (NOW - timedelta(days=days_ago)).isoformat(), "summary": "Talked it through.", "action_items": list(items)}


def test_a_meeting_is_supportive_unless_the_person_picks_otherwise_and_a_bad_choice_is_refused():
    assert mt.validate_meeting(SPEC)[0]["coaching"] == "supportive"
    assert mt.validate_meeting({**SPEC, "coaching": " Challenging "})[0]["coaching"] == "challenging"
    assert mt.validate_meeting({**SPEC, "coaching": "brutal"})[1] == "Choose supportive, balanced or challenging."
    assert mt.public_meeting({"id": "m", "name": "n"})["coaching"] == "supportive"                  # a meeting saved before this existed
    db = Db()
    meeting = make(db, spec={**SPEC, "coaching": "balanced"})
    assert mt.update_meeting(db, "u1", meeting["id"], {"purpose": "new"})[0]["coaching"] == "balanced"      # an edit that does not mention it keeps it
    assert mt.update_meeting(db, "u1", meeting["id"], {"coaching": "challenging"})[0]["coaching"] == "challenging"


def test_an_action_item_that_keeps_coming_back_is_a_signal_and_one_that_changed_is_not():
    again = [note_of(["Update my resume for enterprise roles"]), note_of(["Update resume for the enterprise roles", "Call Dana"], 14), note_of(["Resume update for enterprise"], 21)]
    signals = mt.coaching_signals(again, {})
    assert len(signals) == 1 and "has been an action item in the notes for 3 meetings in a row" in signals[0] and "Update my resume" in signals[0]
    assert mt.coaching_signals([note_of(["Apply to Acme"]), note_of(["Update my resume"], 14)], {}) == []        # nothing repeated
    assert mt.coaching_signals([note_of(["Update my resume"])], {}) == []                                       # one note cannot show a repeat
    assert mt.coaching_signals([note_of(["Update my resume"]), note_of(["Call Dana"], 14), note_of(["Update my resume"], 21)], {}) == []    # a gap breaks the run


def test_goals_tasks_and_what_they_logged_are_signals_only_as_plain_facts():
    prep_data = {
        "goal_ages": [("Get back to enterprise AI", 30), ("Learn Rust", 3)],
        "task_findings": [{"task": "Python jobs", "headline": "Six listings.", "count": 6}],
        "progress_notes": [{"label": "blocker", "text": "x"}, {"label": "win", "text": "y"}, {"label": "win", "text": "z"}],
    }
    signals = mt.coaching_signals([], prep_data)
    assert any("Get back to enterprise AI" in s and "30 days ago" in s for s in signals) and not any("Learn Rust" in s for s in signals)
    assert any('"Python jobs" found 6 new thing(s)' in s and "nothing says whether they looked" in s for s in signals)
    assert any("1 blocker(s)" in s for s in signals) and any("2 win(s)" in s and "named plainly" in s for s in signals)
    assert len(mt.coaching_signals([note_of(["a b c"]), note_of(["a b c"], 14)], {**prep_data, "goal_ages": [("g one", 40), ("g two", 40), ("g three", 40)]})) == mt.MAX_SIGNALS


def test_the_coaching_block_exists_only_for_balanced_or_challenging_and_only_when_there_is_something_true_to_raise():
    signals = ["The goal \"x\" was last updated 30 days ago, in what they have told you."]
    assert mt.build_coaching_text("supportive", signals) == "" and mt.build_coaching_text("balanced", []) == "" and mt.build_coaching_text("nonsense", signals) == ""
    balanced = mt.build_coaching_text("balanced", signals).replace("\n", " ")
    challenging = mt.build_coaching_text("challenging", signals).replace("\n", " ")
    assert "ONE of them" in balanced and "up to TWO of them" in challenging
    assert "single small concrete next step" in challenging and "single small concrete next step" not in balanced
    for rule in ("found by the app, not your judgement", "never as your opening line", "never two in the same message", "never about them as a person", "no scolding, no disappointment", "Do not add any claim, pattern or comparison that is not in the list",
                 "drop it at once", "do not bring it back", "do not press the point", "Never say you saved anything"):
        assert rule in balanced and rule in challenging, rule


def test_the_block_is_appended_to_the_meeting_prompt_and_the_prompt_is_unchanged_without_it():
    plain = mt.build_meeting_context(SPEC, [], prep())
    assert "COACHING" not in plain
    block = mt.build_coaching_text("challenging", ["A true thing."])
    assert mt.build_meeting_context(SPEC, [], prep(), None, NOW, block).endswith(block) and "COACHING" in mt.build_meeting_context(SPEC, [], prep(), None, NOW, block)


def test_a_session_gets_the_block_only_for_a_coaching_meeting_on_a_turn_when_coaching_is_allowed(monkeypatch):
    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", lambda username: [])
    db = Db()
    meeting = make(db, spec={**SPEC, "coaching": "challenging"})
    mt.start_run(db, "u1", meeting["id"], "chat-1", NOW)
    for held, items in ((21, ["Update my resume"]), (14, ["Update my resume"]), (7, ["Update my resume"])):
        mt.save_note(db, "u1", {"meeting_id": meeting["id"], "id": f"r{held}"}, {"summary": "s", "decisions": [], "action_items": items}, NOW - timedelta(days=held))
    allowed = mt.build_context_for_session("u1", "chat-1", "America/Chicago", db=db, now=NOW)
    assert "COACHING" in allowed and "3 meetings in a row" in allowed
    assert "COACHING" not in mt.build_context_for_session("u1", "chat-1", "America/Chicago", db=db, now=NOW, coaching_allowed=False)       # a low-energy turn: it runs as a supportive meeting
    supportive = make(db, user="u2", spec=SPEC)
    mt.start_run(db, "u2", supportive["id"], "chat-2", NOW)
    assert "COACHING" not in mt.build_context_for_session("u2", "chat-2", db=db, now=NOW)


def test_the_app_turns_coaching_off_unless_the_energy_ceiling_is_open_and_the_note_keeps_the_agreed_step():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert 'coaching_allowed=energy_tier(final_state.get("emotional_state"), datetime.now(timezone.utc)) == "open"' in source
    assert "risk_level == RISK_NONE and source_type == \"conversational\"" in source[source.index("meeting_context = \"\""):source.index("meeting_context = \"\"") + 300]
    assert "put it first, in their words" in mt.NOTE_PROMPT
