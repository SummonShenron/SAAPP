"""Honest writes: Sonic saves a detail about a person only if the person gave it (backend/utils/memory_grounding.py).

The first test replays the real failure of 2026-10-20: the assistant invented "a quick nap before heading downtown by 7:30", and one second later a note saying so was
saved as if the person had said it.
"""
import asyncio
import json
import os
from types import SimpleNamespace

from backend.tests.test_open_loops import run_capture
from backend.utils import app_utils
from backend.utils import memory_grounding as g
from backend.utils import self_counters

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ---- the real failure ----------------------------------------------------------------------------------------------------------------------

def test_the_note_that_recorded_an_invention_is_not_saved():
    user = "yep chekc the time, i gotta go remember lol"
    reply = "Haha right, almost 6:45 PM—time for that quick nap before heading downtown by 7:30."
    checked = g.ground_note("Jack needs to take a nap before heading downtown by 7:30 PM.", user_text=user, assistant_text=reply)
    assert checked.text is None and checked.dropped == 1 and checked.kinds == ["time"]


def test_the_persons_own_correction_is_saved_whole():
    checked = g.ground_note("Has a date at 7 at Tidos.", user_text="lmao no the date is at 7 silly at tidos remember?", assistant_text="Oh right, Tidos at 7!")
    assert checked.text == "Has a date at 7 at Tidos." and checked.dropped == 0


# ---- what counts as a detail the person gave ---------------------------------------------------------------------------------------------

def test_times_are_matched_by_hour_and_minute_not_by_how_they_are_written():
    assert g.ungrounded_kinds("Meets at 7:30 PM.", user_text="we meet at 7:30") == []
    assert g.ungrounded_kinds("Meets at 7:30.", user_text="we meet at 7:30pm") == []
    assert g.ungrounded_kinds("Meets at 7 PM.", user_text="the date is at 7") == []           # a bare 7 grounds 7:00
    assert g.ungrounded_kinds("Meets at 7:30.", user_text="the date is at 7") == ["time"]      # but not 7:30
    assert g.ungrounded_kinds("Meets at 8.", user_text="the date is at 7") == ["number"]


def test_numbers_amounts_and_dates_match_however_the_person_wrote_them():
    assert g.ungrounded_kinds("Rent is $2,100.", user_text="my rent is 2100") == []
    assert g.ungrounded_kinds("Rent is $2,000.", user_text="my rent is 2k") == []
    assert g.ungrounded_kinds("Has 7 meetings.", user_text="i have seven meetings") == []
    assert g.ungrounded_kinds("Wedding on June 14th.", user_text="my sister's wedding is june 14") == []
    assert g.ungrounded_kinds("Wedding on June 1.", user_text="my sister's wedding is on june first") == []
    assert g.ungrounded_kinds("Rent is $2,500.", user_text="my rent is 2100") == ["number"]


def test_a_weekday_or_month_the_person_did_not_say_is_a_detail_not_theirs():
    assert g.ungrounded_kinds("Has a date on Thursday.", user_text="i have a date tonight") == ["weekday"]
    assert g.ungrounded_kinds("Has a date on Thursday.", user_text="i have a date thursday") == []
    assert g.ungrounded_kinds("Flies in November.", user_text="i fly out soon") == ["month"]
    assert g.ungrounded_kinds("It may be late.", user_text="no idea") == []                    # "may" is a verb, never a month


def test_a_name_is_only_a_problem_when_the_assistant_introduced_it():
    assert g.ungrounded_kinds("Is meeting Priya for lunch.", user_text="lunch tomorrow", assistant_text="Say hi to Priya!") == ["name"]
    assert g.ungrounded_kinds("Is meeting Priya for lunch.", user_text="lunch with priya tomorrow", assistant_text="Say hi to Priya!") == []
    assert g.ungrounded_kinds("Is taking Ashlynn out.", user_text="taking ashlyn out", assistant_text="have fun with Ashlynn") == []      # a spelling one letter off
    assert g.ungrounded_kinds("Is meeting Priya for lunch.", user_text="lunch tomorrow", assistant_text="Say hi to Priya!", known_text="Has a friend named Priya.") == []
    assert g.ungrounded_kinds("Is meeting Priya for lunch.", user_text="lunch tomorrow") == []                                          # without the reply, names are not judged
    assert g.ungrounded_kinds("Has a date tonight with Ashlyn.", user_text="date tonight", assistant_text="enjoy tonight!") == []        # a name in neither is not the assistant's


def test_a_sentence_start_is_not_a_name_and_the_note_without_details_is_untouched():
    checked = g.ground_note("Tomorrow has an interview. Wants help preparing.", user_text="i have an interview tomorrow, can you help me prepare", assistant_text="Sure!")
    assert checked.text == "Tomorrow has an interview. Wants help preparing." and checked.dropped == 0


def test_only_the_sentence_with_the_invented_detail_is_taken_out():
    checked = g.ground_note("Has a date with Ashlyn tonight. Needs to leave by 8:15.", user_text="i have a date with ashlyn tonight", assistant_text="You should leave by 8:15!")
    assert checked.text == "Has a date with Ashlyn tonight." and checked.dropped == 1 and checked.kinds == ["time"]


# ---- confirmation ---------------------------------------------------------------------------------------------------------------------------

def test_only_a_short_yes_can_confirm():
    for yes in ("yep", "yeah that's right", "Yes!", "correct", "ok", "uh huh", "sounds good"):
        assert g.is_confirmation(yes), yes
    for no in ("", "no", "i went to the gym and then had lunch with my sister and it was a really long day honestly", "tell me more", "what time is it"):
        assert not g.is_confirmation(no), no


def test_a_yes_confirms_the_last_question_and_only_that_one():
    reply = "Nice. You'll head out at 6. So the date is at 7:30 at Tidos, right?"
    assert g.confirmed_text("yep", reply) == "So the date is at 7:30 at Tidos, right?"
    assert g.confirmed_text("yep", "You'll head out around 7:30.") == ""                       # no question asked
    assert g.confirmed_text("it is at 8 actually", reply) == ""                                # not a yes


def test_a_yes_to_an_explicit_question_grounds_its_detail_and_a_bare_yes_does_not():
    ok = g.ground_note("Has a date at 7:30 at Tidos.", user_text="yep", previous_reply="So the date is at 7:30 at Tidos, right?")
    assert ok.text == "Has a date at 7:30 at Tidos." and ok.dropped == 0
    bare = g.ground_note("Has a date at 7:30.", user_text="yep", previous_reply="You'll head out around 7:30.")
    assert bare.text is None and bare.kinds == ["time"]
    other = g.ground_note("Has a date at 9.", user_text="yep", previous_reply="So the date is at 7:30 at Tidos, right?")
    assert other.text is None                                                                  # a yes to 7:30 is not a yes to 9


# ---- facts ----------------------------------------------------------------------------------------------------------------------------------

def test_a_fact_that_adds_a_detail_becomes_the_persons_own_words():
    added = g.ground_fact("Has a date at 7:30 at Tidos.", user_text="i have a date at tidos tonight")
    assert added.text == "i have a date at tidos tonight" and added.dropped == 1 and added.kinds == ["time"]
    clean = g.ground_fact("Has a date at Tidos tonight.", user_text="i have a date at tidos tonight")
    assert clean.text == "Has a date at Tidos tonight." and clean.dropped == 0
    assert len(g.ground_fact("Meets at 9.", user_text="x " * 400).text) <= 300


# ---- the note writer ------------------------------------------------------------------------------------------------------------------------

def _fake_llm(text):
    async def call(prompt):
        return SimpleNamespace(content=text)
    return call


def test_the_note_writer_drops_a_note_built_on_the_assistants_invention_and_says_why_in_counts():
    report = {}
    note = asyncio.run(app_utils.summarize_turn(
        "yep chekc the time, i gotta go remember lol", "Haha right, almost 6:45 PM—time for that quick nap before heading downtown by 7:30.",
        llm=_fake_llm("Jack needs to take a nap before heading downtown by 7:30 PM."), report=report))
    assert note is None and report == {"dropped": 1, "kinds": ["time"]}


def test_the_note_writer_keeps_a_note_whose_details_are_the_persons_own():
    report = {}
    note = asyncio.run(app_utils.summarize_turn(
        "my sister's wedding is in Lisbon on june 14 and i have to give the toast", "Congrats! Want help drafting it?",
        llm=_fake_llm("Is giving the toast at their sister's wedding in Lisbon on June 14th."), report=report))
    assert note == "Is giving the toast at their sister's wedding in Lisbon on June 14th." and report == {"dropped": 0, "kinds": []}


def test_the_note_writer_still_says_none_for_nothing_worth_keeping_and_strips_a_label():
    assert asyncio.run(app_utils.summarize_turn("hi", "hello", llm=_fake_llm("NONE"))) is None
    assert asyncio.run(app_utils.summarize_turn("i love dark mode", "ok", llm=_fake_llm("SUMMARY: Prefers dark mode."))) == "Prefers dark mode."


def test_indexing_a_turn_stores_nothing_for_an_invented_detail_and_counts_it_without_text(monkeypatch):
    stored, counted = [], []
    monkeypatch.setattr(app_utils, "embed_and_store_memory_chunk", lambda store, user, text, **kw: stored.append(text))
    async def no_compaction(*a, **k):
        return None
    monkeypatch.setattr(app_utils, "maybe_trigger_compaction", no_compaction)
    monkeypatch.setattr(app_utils, "_known_vocabulary", lambda username: "")
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: counted.append(counts))
    monkeypatch.setattr(app_utils.lite_llm, "ainvoke", _fake_llm("Jack needs to take a nap before heading downtown by 7:30 PM."))
    asyncio.run(app_utils.index_conversation_turn(object(), "jack", "yep chekc the time", "time for that nap before heading downtown by 7:30", "s1", "Want me to check the time?"))
    assert stored == [] and counted == [{"memory_note_dropped": 1}]
    monkeypatch.setattr(app_utils.lite_llm, "ainvoke", _fake_llm("Has a date with Ashlyn tonight. Needs to leave by 8:15."))
    asyncio.run(app_utils.index_conversation_turn(object(), "jack", "i have a date with ashlyn tonight", "You should leave by 8:15!", "s1", ""))
    assert stored == ["Has a date with Ashlyn tonight."] and counted[-1] == {"memory_note_trimmed": 1}


def test_the_two_new_counters_exist_and_the_app_passes_the_previous_reply():
    assert {"memory_note_dropped", "memory_note_trimmed"} <= self_counters.KEYS
    source = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "index_conversation_turn(services.get(\"user_memory_vector_store\"), username, question, full_response, session_id, previous_reply)" in source


# ---- open loops -----------------------------------------------------------------------------------------------------------------------------

def test_an_open_loop_with_a_time_the_person_never_gave_is_not_saved(monkeypatch):
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: None)
    bad = json.dumps({"loops": [{"what": "has a date with Ashlyn at 7:30", "due_date": "2026-10-10", "kind": "social"}], "resolved_ids": []})
    result, db, _ = run_capture("i have a date with ashlyn tomorrow night", bad)
    assert result["captured"] == 0 and db.col.docs == []
    good = json.dumps({"loops": [{"what": "has a date with Ashlyn at 7", "due_date": "2026-10-10", "kind": "social"}], "resolved_ids": []})
    result, db, _ = run_capture("i have a date with ashlyn tomorrow night at 7", good)
    assert result["captured"] == 1 and db.col.docs[0]["text"] == "has a date with Ashlyn at 7"


# ---- explicit facts -------------------------------------------------------------------------------------------------------------------------

def test_the_explicit_fact_writer_uses_the_check():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    node = source[source.index("async def memory_save_node"):source.index("def memory_recall_node")]
    assert "memory_grounding.ground_fact(fact_text, user_text=user_msg" in node and "save_user_fact(" in node
    assert node.index("ground_fact") < node.index("save_user_fact(")
