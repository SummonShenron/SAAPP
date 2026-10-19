import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import HumanMessage

from backend.utils import meetings, progress_notes as pn

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime(2026, 10, 14, 15, 0, tzinfo=timezone.utc)        # Wednesday morning in Chicago


class Col:
    """Just enough of one Mongo collection: documents as dicts, with the handful of queries the module uses."""

    def __init__(self):
        self.docs = []
        self.indexes = []

    def create_index(self, *args, **kwargs):
        self.indexes.append(args)

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def _match(self, doc, flt):
        for key, want in flt.items():
            have = doc.get(key)
            if isinstance(want, dict):
                if "$gte" in want and not (have is not None and have >= want["$gte"]):
                    return False
                if "$lte" in want and not (have is not None and have <= want["$lte"]):
                    return False
            elif have != want:
                return False
        return True

    def find(self, flt, projection=None):
        return Cursor([dict(d) for d in self.docs if self._match(d, flt)], projection)

    def find_one(self, flt, projection=None):
        found = [d for d in self.docs if self._match(d, flt)]
        return dict(found[0]) if found else None

    def count_documents(self, flt):
        return sum(1 for d in self.docs if self._match(d, flt))

    def update_one(self, flt, update):
        for d in self.docs:
            if self._match(d, flt):
                d.update(update["$set"])
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()

    def update_many(self, flt, update):
        n = 0
        for d in self.docs:
            if self._match(d, flt):
                d.update(update["$set"])
                n += 1
        return type("R", (), {"matched_count": n, "modified_count": n})()

    def delete_one(self, flt):
        for i, d in enumerate(self.docs):
            if self._match(d, flt):
                del self.docs[i]
                return type("R", (), {"deleted_count": 1})()
        return type("R", (), {"deleted_count": 0})()

    def delete_many(self, flt):
        keep = [d for d in self.docs if not self._match(d, flt)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        return type("R", (), {"deleted_count": n})()


class Cursor(list):
    def __init__(self, docs, projection=None):
        super().__init__(docs)

    def sort(self, key, direction=1):
        return Cursor(sorted(self, key=lambda d: d.get(key) or "", reverse=direction < 0))

    def limit(self, n):
        return Cursor(self[:n])

    def skip(self, n):
        return Cursor(self[n:])


class DB(dict):
    def __missing__(self, name):
        self[name] = Col()
        return self[name]


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(pn, "_index_ready", False)


def at(minutes=0):
    return NOW + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------------------------------------------
# Recognising "log: ..."
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message,label,text", [
    ("log: applied to three jobs", "note", "applied to three jobs"),
    ("Log: Applied to 3 jobs", "note", "Applied to 3 jobs"),
    ("log a win: finished the PR", "win", "finished the PR"),
    ("log a blocker: waiting on reference table access", "blocker", "waiting on reference table access"),
    ("add a progress note: reviewed the parser", "note", "reviewed the parser"),
    ("progress note: pushed the fix", "note", "pushed the fix"),
    ("win: shipped the cards", "win", "shipped the cards"),
    ("blocker - no access yet", "blocker", "no access yet"),
    ("please log: updated my resume", "note", "updated my resume"),
    ("log that I met with the recruiter", "note", "I met with the recruiter"),
    ("jot down a win: got the interview", "win", "got the interview"),
    ("log   a   WIN :   closed the loop  ", "win", "closed the loop"),
])
def test_an_explicit_request_to_log_is_recognised_and_the_words_are_kept_as_written(message, label, text):
    assert pn.parse_log_request(message) == (label, text)


@pytest.mark.parametrize("message", [
    "log in to my account",
    "how do I log out",
    "the log said error 500",
    "can you check the logs",
    "I want to win the lottery",
    "what's the blocker on the deploy?",
    "note to self buy milk",
    "log",
    "log:",
    "log: x",
    "",
    "tell me about progress notes",
])
def test_ordinary_messages_are_not_taken_for_a_log_request(message):
    assert pn.parse_log_request(message) is None


def test_a_pasted_block_after_log_is_a_paste_not_a_note():
    assert pn.parse_log_request("log: Traceback (most recent call last):\n  File x.py, line 3\nValueError") is None
    assert pn.parse_log_request("log: " + "x" * 700) is None


@pytest.mark.parametrize("message,expected", [
    ('add a win to my note "applied to 3 jobs": got a second interview', ("append", "applied to 3 jobs", "got a second interview", "win")),
    ("add to note 'waiting on access': got access", ("append", "waiting on access", "got access", None)),
    ('update my note "applied to 3 jobs": applied to 4 jobs', ("replace", "applied to 3 jobs", "applied to 4 jobs", None)),
])
def test_explicit_note_updates_name_the_note_and_distinguish_append_from_replace(message, expected):
    assert pn.parse_update_request(message) == expected


def test_note_update_requests_need_an_explicit_quoted_note_and_new_text():
    for message in ("add a win to my note: got an interview", 'update my note "applied to 3 jobs":', "add to note 'x': got a win"):
        assert pn.parse_update_request(message) is None


def test_the_words_asking_about_the_log_are_recognised():
    for message in ("what did I log this week?", "show me my progress notes", "what have I logged", "what have I been working on this week", "what are my wins"):
        assert pn.asks_about_notes(message) is True, message
    for message in ("hello", "log: applied", "what's the weather", "how do I write a progress report for work"):
        assert pn.asks_about_notes(message) is False, message


# ---------------------------------------------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------------------------------------------

def test_a_note_is_saved_with_the_persons_own_day_and_starts_uncovered():
    db = DB()
    note, issue = pn.add_note(db, "jack", "applied to three jobs", "win", "chat", "America/Chicago", NOW)
    assert issue is None and note["label"] == "win" and note["text"] == "applied to three jobs"
    assert note["day"] == "2026-10-14" and note["reviewed_at"] is None and note["source"] == "chat"
    assert db[pn.COLLECTION].docs[0]["username"] == "jack"


def test_the_day_is_the_persons_day_not_utc():
    late = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)      # still the 14th in Chicago
    assert pn.add_note(DB(), "jack", "late night work", tz_name="America/Chicago", now=late)[0]["day"] == "2026-10-14"


def test_too_short_notes_and_unknown_labels_are_handled():
    db = DB()
    note, issue = pn.add_note(db, "jack", " ")
    assert note is None and "few words" in issue
    assert pn.add_note(db, "jack", "did a thing", label="gold-star")[0]["label"] == "note"
    assert pn.add_note(db, "jack", "did another", source="somewhere")[0]["source"] == "chat"


def test_one_line_bounded_text():
    note, _ = pn.add_note(DB(), "jack", "line one\n\nline   two\x07 " + "x" * 600)
    assert "\n" not in note["text"] and "\x07" not in note["text"] and len(note["text"]) <= pn.MAX_TEXT_CHARS and note["text"].startswith("line one line two")


def test_a_day_holds_a_bounded_number_and_the_next_day_starts_fresh():
    db = DB()
    for i in range(pn.MAX_PER_DAY):
        assert pn.add_note(db, "jack", f"note number {i}", now=at(i), tz_name="UTC")[0] is not None
    refused, issue = pn.add_note(db, "jack", "one too many", now=at(30), tz_name="UTC")
    assert refused is None and str(pn.MAX_PER_DAY) in issue
    assert pn.add_note(db, "jack", "tomorrow", now=at(24 * 60), tz_name="UTC")[0] is not None
    assert pn.add_note(db, "alice", "someone else", now=at(30), tz_name="UTC")[0] is not None


def test_only_the_newest_notes_are_kept_per_person(monkeypatch):
    monkeypatch.setattr(pn, "MAX_PER_PERSON", 5)
    monkeypatch.setattr(pn, "MAX_PER_DAY", 100)
    db = DB()
    for i in range(8):
        pn.add_note(db, "jack", f"note number {i}", now=at(i), tz_name="UTC")
    kept = sorted(d["text"] for d in db[pn.COLLECTION].docs)
    assert kept == [f"note number {i}" for i in range(3, 8)]


# ---------------------------------------------------------------------------------------------------------------
# Listing, covering, editing, deleting
# ---------------------------------------------------------------------------------------------------------------

def test_notes_are_listed_newest_first_and_only_the_owners():
    db = DB()
    pn.add_note(db, "jack", "first note", now=at(0))
    pn.add_note(db, "jack", "second note", now=at(5))
    pn.add_note(db, "alice", "not jacks", now=at(6))
    assert [n["text"] for n in pn.list_notes(db, "jack")] == ["second note", "first note"]


def test_note_references_match_text_without_crossing_user_boundaries():
    db = DB()
    pn.add_note(db, "jack", "Applied to three jobs")
    pn.add_note(db, "alice", "Applied to three jobs")
    matches = pn.find_notes_by_reference(db, "jack", "applied to three")
    assert len(matches) == 1 and matches[0]["username"] == "jack"
    assert pn.find_notes_by_reference(db, "jack", "not in a note") == []


def test_a_duplicate_note_reference_is_returned_as_ambiguous():
    db = DB()
    pn.add_note(db, "jack", "Applied to three jobs")
    pn.add_note(db, "jack", "Applied to three jobs again")
    assert len(pn.find_notes_by_reference(db, "jack", "applied to three")) == 2


def test_uncovered_notes_come_back_oldest_first_and_bounded():
    db = DB()
    for i in range(15):
        pn.add_note(db, "jack", f"note number {i}", now=at(i), tz_name="UTC")
    listed = pn.uncovered(db, "jack")
    assert len(listed) == pn.IN_PROMPT and listed[0]["text"] == "note number 3" and listed[-1]["text"] == "note number 14"


def test_a_meetings_notes_cover_what_was_logged_up_to_then_and_not_what_comes_later():
    db = DB()
    pn.add_note(db, "jack", "before the meeting", now=at(0))
    pn.add_note(db, "jack", "after the meeting", now=at(60))
    assert pn.mark_covered(db, "jack", "meeting-note-1", at(30).isoformat(), at(30)) == 1
    assert [n["text"] for n in pn.uncovered(db, "jack")] == ["after the meeting"]
    covered = [d for d in db[pn.COLLECTION].docs if d["reviewed_at"]][0]
    assert covered["reviewed_in"] == "meeting-note-1" and pn.public_note(covered)["covered"] is True


def test_covering_never_touches_another_persons_notes():
    db = DB()
    pn.add_note(db, "jack", "mine", now=at(0))
    pn.add_note(db, "alice", "hers", now=at(0))
    pn.mark_covered(db, "jack", "n", at(10).isoformat())
    assert [n["text"] for n in pn.uncovered(db, "alice")] == ["hers"]


def test_editing_changes_only_what_was_asked_and_only_for_the_owner():
    db = DB()
    note, _ = pn.add_note(db, "jack", "applied to 3 jobs", "note")
    assert pn.update_note(db, "jack", note["id"], {"text": "applied to 4 jobs", "label": "win"})["text"] == "applied to 4 jobs"
    assert pn.update_note(db, "alice", note["id"], {"text": "hijacked"}) is None
    assert pn.update_note(db, "jack", note["id"], {"text": " "}) is None and pn.update_note(db, "jack", note["id"], {}) is None
    assert pn.update_note(db, "jack", note["id"], {"label": "nonsense"}) is None
    assert pn.list_notes(db, "jack")[0]["label"] == "win"


def test_editing_a_covered_note_makes_the_updated_content_uncovered_again():
    db = DB()
    note, _ = pn.add_note(db, "jack", "applied to three jobs")
    pn.mark_covered(db, "jack", "meeting-note-1", at(1).isoformat(), at(1))
    assert pn.update_note(db, "jack", note["id"], {"text": "applied to four jobs"})["reviewed_at"] is None
    assert [n["text"] for n in pn.uncovered(db, "jack")] == ["applied to four jobs"]


def test_deleting_and_forgetting_everything_only_remove_the_persons_own():
    db = DB()
    a, _ = pn.add_note(db, "jack", "one note")
    pn.add_note(db, "jack", "two note")
    pn.add_note(db, "alice", "hers")
    assert pn.delete_note(db, "alice", a["id"]) is False
    assert pn.delete_note(db, "jack", a["id"]) is True
    assert pn.delete_all_for_user(db, "jack") == 1
    assert [d["username"] for d in db[pn.COLLECTION].docs] == ["alice"]


def test_the_public_view_has_no_username_and_no_internal_fields():
    db = DB()
    note, _ = pn.add_note(db, "jack", "a progress note")
    assert set(pn.public_note(note)) == {"id", "text", "label", "day", "at", "source", "covered"}


# ---------------------------------------------------------------------------------------------------------------
# What Sonic is told
# ---------------------------------------------------------------------------------------------------------------

def _note(text, label="note", minutes=0):
    return {"text": text, "label": label, "at": at(minutes).isoformat(), "day": "2026-10-14"}


def test_the_meeting_lists_the_notes_in_their_words_with_the_day_and_kind():
    items = pn.prompt_items([_note("applied to 3 jobs", "win"), _note("waiting on access", "blocker", 60)], "America/Chicago")
    assert items[0] == {"when": "Wed Oct 14", "label": "win", "text": "applied to 3 jobs"} and items[1]["label"] == "blocker"


def test_the_meeting_prompt_includes_the_notes_and_the_rules_for_using_them():
    meeting = {"name": "Weekly check-in", "purpose": "", "agenda": ["What did you get done?"]}
    prep = {"goals": [], "task_findings": [], "progress_notes": pn.prompt_items([_note("applied to 3 jobs", "win")], "UTC")}
    text = meetings.build_meeting_context(meeting, [], prep, "UTC", NOW)
    assert "Progress notes they logged since the last meeting, oldest first, in their own words:" in text and "[win]: applied to 3 jobs" in text
    for rule in ("in their own words", "do not rank them", "point out patterns", "mention only notes that are listed", "do not tell them they could have logged some",
                 "[blocker] is something in their way, not something they got done"):
        assert rule in text


def test_a_meeting_with_no_notes_says_nothing_about_them():
    meeting = {"name": "Weekly check-in", "purpose": "", "agenda": []}
    text = meetings.build_meeting_text(meeting, [], {"goals": [], "task_findings": [], "progress_notes": []}, "UTC", NOW)
    assert "Progress notes" not in text


def test_the_prep_reads_the_uncovered_notes_and_survives_a_failure(monkeypatch):
    db = DB()
    pn.add_note(db, "jack", "applied to 3 jobs", "win")
    prep = meetings.gather_prep(db, "jack", "m1", None)
    assert [n["text"] for n in prep["progress_notes"]] == ["applied to 3 jobs"]
    monkeypatch.setattr(pn, "uncovered", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    assert meetings.gather_prep(db, "jack", "m1", None)["progress_notes"] == []


def test_saving_a_meetings_notes_covers_the_progress_notes_logged_so_far():
    db = DB()
    pn.add_note(db, "jack", "logged during the week", now=datetime.now(timezone.utc) - timedelta(days=2))
    run = {"id": "run1", "meeting_id": "m1", "session_id": "s1"}
    saved = meetings.save_note(db, "jack", run, {"summary": "We reviewed the week.", "decisions": [], "action_items": []})
    assert pn.uncovered(db, "jack") == []
    assert db[pn.COLLECTION].docs[0]["reviewed_in"] == saved["id"]


def test_a_failure_marking_notes_never_loses_the_meetings_own_note(monkeypatch):
    db = DB()
    monkeypatch.setattr(pn, "mark_covered", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    saved = meetings.save_note(db, "jack", {"id": "run1", "meeting_id": "m1", "session_id": "s1"}, {"summary": "Reviewed."})
    assert saved["summary"] == "Reviewed." and len(db[meetings.NOTES_COLLECTION].docs) == 1


def test_the_answer_when_asked_lists_what_is_there_or_says_there_is_nothing():
    asked = pn.build_asked_context([_note("applied to 3 jobs", "win")], 3, "UTC")
    assert "applied to 3 jobs" in asked and "3 logged in the last seven days" in asked and "point out no patterns" in asked
    empty = pn.build_asked_context([], 0, "UTC")
    assert "not logged any yet" in empty and "Do not invent any" in empty


def test_the_week_count_covers_seven_days_including_today():
    db = DB()
    pn.add_note(db, "jack", "today note", now=NOW, tz_name="UTC")
    pn.add_note(db, "jack", "six days ago", now=NOW - timedelta(days=6), tz_name="UTC")
    pn.add_note(db, "jack", "seven days ago", now=NOW - timedelta(days=7), tz_name="UTC")
    assert pn.count_this_week(db, "jack", "UTC", NOW) == 2


# ---------------------------------------------------------------------------------------------------------------
# The chat route and the app around it
# ---------------------------------------------------------------------------------------------------------------

def test_a_log_message_is_routed_to_the_saver_with_no_model_call():
    from backend.services import agent_workflow as aw

    state = {"messages": [HumanMessage(content="log a win: finished the workspace feature")]}
    assert aw.build_agent_plan("conversational", state)["agents"] == ["progress_note", "formatter"]
    update_state = {"messages": [HumanMessage(content='add a win to my note "applied to three jobs": got an interview')]}
    assert aw.build_agent_plan("conversational", update_state)["agents"] == ["progress_note", "formatter"]
    assert aw._AGENT_NODE_MAP["progress_note"] == "progress_note_node"
    assert "progress_note_node" in aw.create_workflow(type("S", (), {})()).get_graph().nodes


def test_an_ordinary_message_never_reaches_the_saver():
    from backend.services import agent_workflow as aw

    assert "progress_note" not in aw.build_agent_plan("conversational", {"messages": [HumanMessage(content="how do I log in")]})["agents"]


def test_a_pending_approval_still_wins_over_a_log_message():
    from backend.services import agent_workflow as aw

    plan = aw.build_agent_plan("execute_write", {"messages": [HumanMessage(content="log: x done")]})
    assert plan["agents"] == ["execute_write", "formatter"]


def _node_env(monkeypatch, db):
    from backend.services import agent_workflow as aw

    monkeypatch.setattr(aw, "get_db", lambda: db)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "UTC")
    return aw


def test_the_node_saves_the_note_as_written_and_tells_the_voice_to_confirm_only(monkeypatch):
    db = DB()
    aw = _node_env(monkeypatch, db)
    result = asyncio.run(aw.progress_note_node({"messages": [HumanMessage(content="log a win: applied to three jobs")], "username": "jack"}))
    assert db[pn.COLLECTION].docs[0]["text"] == "applied to three jobs" and db[pn.COLLECTION].docs[0]["label"] == "win"
    facts = result["content_to_format"]
    assert result["relevance_grade"] == "tool_agent" and "\"applied to three jobs\"" in facts and "a win" in facts
    assert "Do not add praise" in facts and "Office Hours page" in facts


def test_the_node_appends_a_win_to_the_explicitly_referenced_note(monkeypatch):
    db = DB()
    original, _ = pn.add_note(db, "jack", "applied to three jobs")
    aw = _node_env(monkeypatch, db)
    result = asyncio.run(aw.progress_note_node({
        "messages": [HumanMessage(content=f'add a win to my note "{original["text"]}": got a second interview')],
        "username": "jack",
    }))
    saved = pn.list_notes(db, "jack")[0]
    assert saved["text"] == "applied to three jobs — got a second interview" and saved["label"] == "win"
    assert "Added the new text" in result["content_to_format"] and "got a second interview" in result["content_to_format"]


def test_the_node_replaces_the_explicitly_referenced_note_without_changing_its_label(monkeypatch):
    db = DB()
    original, _ = pn.add_note(db, "jack", "applied to three jobs", "win")
    aw = _node_env(monkeypatch, db)
    result = asyncio.run(aw.progress_note_node({
        "messages": [HumanMessage(content=f'update my note "{original["text"]}": applied to four jobs')],
        "username": "jack",
    }))
    saved = pn.list_notes(db, "jack")[0]
    assert saved["text"] == "applied to four jobs" and saved["label"] == "win"
    assert "Updated their progress note" in result["content_to_format"]


def test_the_node_does_not_change_ambiguous_note_references(monkeypatch):
    db = DB()
    pn.add_note(db, "jack", "applied to three jobs")
    pn.add_note(db, "jack", "applied to three jobs and followed up")
    aw = _node_env(monkeypatch, db)
    result = asyncio.run(aw.progress_note_node({
        "messages": [HumanMessage(content='add a win to my note "applied to three": got an interview')],
        "username": "jack",
    }))
    assert "More than one" in result["content_to_format"]
    assert all("got an interview" not in note["text"] for note in pn.list_notes(db, "jack"))


def test_the_node_refuses_to_truncate_an_updated_note(monkeypatch):
    db = DB()
    original, _ = pn.add_note(db, "jack", "applied to three jobs")
    aw = _node_env(monkeypatch, db)
    addition = "x" * pn.MAX_TEXT_CHARS
    result = asyncio.run(aw.progress_note_node({
        "messages": [HumanMessage(content=f'add to my note "{original["text"]}": {addition}')],
        "username": "jack",
    }))
    assert "exceed" in result["content_to_format"]
    assert pn.list_notes(db, "jack")[0]["text"] == original["text"]


def test_a_guest_is_told_notes_are_for_accounts_and_nothing_is_saved(monkeypatch):
    db = DB()
    aw = _node_env(monkeypatch, db)
    result = asyncio.run(aw.progress_note_node({"messages": [HumanMessage(content="log: something")], "username": "guest-recruiter@example.com"}))
    assert "signed-in" in result["content_to_format"] and db[pn.COLLECTION].docs == []


def test_a_refused_note_is_reported_not_claimed_as_saved(monkeypatch):
    db = DB()
    aw = _node_env(monkeypatch, db)
    monkeypatch.setattr(pn, "MAX_PER_DAY", 0)
    result = asyncio.run(aw.progress_note_node({"messages": [HumanMessage(content="log: one more")], "username": "jack"}))
    assert "was not saved" in result["content_to_format"]


def test_the_app_has_the_endpoints_the_forget_hook_and_the_asked_context():
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    for route in ('@app.get("/api/progress-notes")', '@app.post("/api/progress-notes")', '@app.patch("/api/progress-notes/{note_id}")', '@app.delete("/api/progress-notes/{note_id}")'):
        assert route in source
    assert "await asyncio.to_thread(progress_notes_utils.forget_all, username)" in source
    assert "progress_notes_context=progress_notes_context," in source and "progress_notes_utils.asks_about_notes(" in source
    body = source[source.index('@app.get("/api/progress-notes")'):source.index('@app.post("/api/meetings/{meeting_id}/start")')]
    assert body.count("_meetings_user(current_user)") == 4, "every endpoint is owner-scoped and refuses guests"


def test_the_voice_prompt_has_a_place_for_the_notes():
    from backend.components import constraints

    prompt = constraints.build_voice_prompt(constraints.GROUNDING_BLOCKS["conversational"], "", "", "what did I log", progress_notes_context="PROGRESS NOTES THEY ASKED ABOUT")
    assert "PROGRESS NOTES THEY ASKED ABOUT" in prompt
