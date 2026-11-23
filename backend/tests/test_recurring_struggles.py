"""Recurring struggles (backend/utils/recurring_struggles.py): upset turns about a saved person are grouped into episodes by sitting, and a person becomes recurring after enough episodes over
enough days. Mood states come from the real merge_emotional_state, facts are UserFact-shaped, and the database is the shared FakeDB: no model, no real database."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from backend.tests.fake_runner_db import FakeDB
from backend.utils import memory_erase, self_counters
from backend.utils import recurring_struggles as rs
from backend.utils.emotion_utils import merge_emotional_state

NOW = datetime.now(timezone.utc)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FACTS = [
    SimpleNamespace(fact="The user has been seeing Yashu since October.", category="relationship", active=True),
    SimpleNamespace(fact="The user's sister Mira lives in Austin.", category="relationship", active=True),
    SimpleNamespace(fact="The user likes climbing.", category="preference", active=True),
]


def mood(valence, intensity, prior=None, when=NOW):
    return merge_emotional_state(prior, {"valence": valence, "intensity": intensity}, when)


def load(_user):
    return FACTS


# ---- what counts as an upset turn about someone --------------------------------------------------------------------------------------------------

def test_an_upset_reading_of_this_message_counts_and_a_calm_or_weak_one_does_not():
    assert rs.upset_this_turn(mood("low", 0.6)) and rs.upset_this_turn(mood("distressed", 0.8)) and rs.upset_this_turn(mood("frustrated", 0.5))
    assert not rs.upset_this_turn(mood("positive", 0.6))
    assert not rs.upset_this_turn(mood("low", rs.UPSET_MIN - 0.05))
    assert not rs.upset_this_turn(None) and not rs.upset_this_turn("low")


def test_a_carried_low_on_a_calm_or_lighter_message_does_not_count():
    low = mood("low", 0.7)
    assert not rs.upset_this_turn(merge_emotional_state(low, {"valence": "neutral", "intensity": 0.0}, NOW))          # carried, not read on this message
    lighter = merge_emotional_state(low, {"valence": "positive", "intensity": 0.15}, NOW)                             # a small lift keeps the old valence in the state
    assert lighter["valence"] == "low" and lighter["read_this_turn"] and not rs.upset_this_turn(lighter)


def test_the_person_is_the_one_named_or_the_one_the_conversation_is_clearly_about():
    assert rs.person_of_turn("Yashu didn't text back again", [], FACTS) == "Yashu"
    talk = [HumanMessage(content="so Yashu said she'd call"), AIMessage(content="ok"), HumanMessage(content="Yashu never did")]
    assert rs.person_of_turn("I'm scared I won't see her again", talk, FACTS) == "Yashu"
    assert rs.person_of_turn("I'm scared I won't see her again", [], FACTS) is None              # nobody named and nobody in the conversation: not guessed
    assert rs.person_of_turn("work was awful today", [], FACTS) is None
    both = talk + [HumanMessage(content="Yashu again")]
    assert rs.person_of_turn("Mira and Yashu both annoyed me", both, FACTS) == "Yashu"           # several named: the one the conversation is about


# ---- episodes and recurring ----------------------------------------------------------------------------------------------------------------------

def test_turns_in_one_sitting_are_one_episode_and_a_later_sitting_starts_another():
    people, started = rs.update([], "Yashu", NOW)
    assert started and len(people[0]["episodes"]) == 1
    people, started = rs.update(people, "Yashu", NOW + timedelta(hours=rs.EPISODE_GAP_HOURS - 1))
    assert not started and len(people[0]["episodes"]) == 1
    people, started = rs.update(people, "Yashu", NOW + timedelta(hours=2 * rs.EPISODE_GAP_HOURS - 2))  # more than the gap after the episode began, but measured from the last upset turn
    assert not started
    people, started = rs.update(people, "yashu", NOW + timedelta(hours=4 * rs.EPISODE_GAP_HOURS))
    assert started and len(people) == 1 and len(people[0]["episodes"]) == 2


def test_update_does_not_change_its_input():
    people, _ = rs.update([], "Yashu", NOW)
    before = [dict(p, episodes=list(p["episodes"])) for p in people]
    rs.update(people, "Yashu", NOW + timedelta(days=2))
    assert people == before


def episodes_on(days):
    people = []
    for day in days:
        people, _ = rs.update(people, "Yashu", NOW - timedelta(days=day))
    return people


def test_a_person_is_recurring_after_enough_episodes_spread_over_enough_days():
    assert rs.is_recurring(episodes_on([20, 14, 7, 0])[0], NOW)
    assert not rs.is_recurring(episodes_on([14, 7, 0])[0], NOW)                                         # too few
    assert not rs.is_recurring(episodes_on([3, 2, 1, 0])[0], NOW)                                       # four sittings, but all in one bad stretch
    assert not rs.is_recurring(episodes_on([rs.WINDOW_DAYS + 30, rs.WINDOW_DAYS + 20, 7, 0])[0], NOW)   # old episodes fall out of the window
    found = rs.recurring(episodes_on([20, 14, 7, 0]), NOW)
    assert [(p["name"], p["episodes"]) for p in found] == [("Yashu", 4)]


def test_old_episodes_are_dropped_and_the_lists_are_capped():
    people = episodes_on([rs.WINDOW_DAYS + 5, 1])
    assert len(people[0]["episodes"]) == 1
    many = []
    for i in range(rs.MAX_PEOPLE + 5):
        many, _ = rs.update(many, f"Person{i}", NOW + timedelta(minutes=i))
    assert len(many) == rs.MAX_PEOPLE and many[0]["name"] == f"Person{rs.MAX_PEOPLE + 4}"              # the most recently seen are kept
    one = []
    for i in range(rs.MAX_EPISODES + 5):
        one, _ = rs.update(one, "Yashu", NOW + timedelta(days=i * 0.5))
    assert len(one[0]["episodes"]) == rs.MAX_EPISODES


# ---- recording a turn -----------------------------------------------------------------------------------------------------------------------------

def test_a_recorded_turn_stores_dates_only_and_returns_its_counts():
    db = FakeDB()
    counts = rs.note_turn(db, "jack", "Yashu didn't text back and I feel awful", [], mood("low", 0.7), load, now=NOW)
    assert counts == {"struggle_turns": 1, "struggle_episodes": 1}
    doc = db[rs.COLLECTION].find_one({"username": "jack"})
    assert set(doc) >= {"username", "people", "updated_at"}
    assert set(doc["people"][0]) == {"key", "name", "last_seen", "episodes"}                  # no message text, no feeling
    assert "awful" not in str(doc)
    assert rs.note_turn(db, "jack", "Yashu again, ugh", [], mood("low", 0.7), load, now=NOW + timedelta(hours=1)) == {"struggle_turns": 1}


def test_a_turn_about_a_recurring_person_is_counted_as_recurring():
    db = FakeDB()
    for day in (21, 14, 7):
        rs.note_turn(db, "jack", "Yashu", [], mood("low", 0.7), load, now=NOW - timedelta(days=day))
    assert rs.note_turn(db, "jack", "Yashu again", [], mood("low", 0.7), load, now=NOW) == {"struggle_turns": 1, "struggle_episodes": 1, "struggle_recurring": 1}


def test_nothing_is_recorded_without_an_upset_reading_a_person_a_user_or_a_database():
    db = FakeDB()
    assert rs.note_turn(db, "jack", "Yashu and I had a great day", [], mood("positive", 0.7), load, now=NOW) == {}
    assert rs.note_turn(db, "jack", "work was awful", [], mood("low", 0.7), load, now=NOW) == {}
    assert rs.note_turn(db, "", "Yashu", [], mood("low", 0.7), load, now=NOW) == {}
    assert rs.note_turn(None, "jack", "Yashu", [], mood("low", 0.7), load, now=NOW) == {}
    assert db[rs.COLLECTION].find_one({"username": "jack"}) is None


def test_a_failure_is_never_raised():
    def broken(_user):
        raise RuntimeError("down")

    assert rs.note_turn(FakeDB(), "jack", "Yashu", [], mood("low", 0.7), broken, now=NOW) == {}


def test_record_turn_adds_the_counts_to_the_days_totals(monkeypatch):
    recorded = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    rs.record_turn(FakeDB(), "jack", "Yashu", [], mood("low", 0.7), load)
    rs.record_turn(FakeDB(), "jack", "nothing", [], mood("positive", 0.7), load)
    assert recorded == [{"struggle_turns": 1, "struggle_episodes": 1}]


def test_the_counters_are_known_keys_and_clear_all_erases_the_record():
    assert {"struggle_turns", "struggle_episodes", "struggle_recurring"} <= self_counters.KEYS
    assert rs.COLLECTION in memory_erase.ERASED_COLLECTIONS


def test_the_turn_records_it_after_the_reply_and_never_for_a_guest():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    block = app[app.index("# Recurring struggles (backend/utils/recurring_struggles.py)"):][:1800]
    assert "if not is_guest_username(username) and get_db() is not None:" in block
    assert "recurring_struggles.record_turn, get_db(), username, question, list(chat_sessions[history_key]), final_state.get(\"emotional_state\"), load_user_facts" in block
    assert "spawn_background_task(asyncio.to_thread(" in block


def test_a_person_mentioned_once_long_ago_is_not_guessed_and_several_named_go_to_the_one_the_talk_is_about():
    long_ago = [HumanMessage(content="Yashu called earlier")] + [HumanMessage(content=f"unrelated message {i}") for i in range(45)]
    assert rs.person_of_turn("I'm scared I won't see her again", long_ago, FACTS) is None               # too faint to be who "her" means
    about_mira = [HumanMessage(content="Mira is visiting"), HumanMessage(content="Mira said something mean"), HumanMessage(content="still thinking about Mira")]
    assert rs.person_of_turn("Yashu and Mira both annoyed me today", about_mira, FACTS) == "Mira"


def test_a_turn_about_nobody_is_not_treated_as_an_error(caplog):
    with caplog.at_level("WARNING", logger="SASS Logger"):
        assert rs.note_turn(FakeDB(), "jack", "work was awful", [], mood("low", 0.7), load, now=NOW) == {}
    assert not [r for r in caplog.records if "recurring struggles" in r.getMessage()]


# ---- step 2 and 3: the step-back question, dropped or pressed -----------------------------------------------------------------------------------

def recurring_person(**extra):
    person = episodes_on([20, 14, 7, 1])[0]
    person.update(extra)
    return person


def test_a_step_back_is_due_for_a_recurring_person_and_the_reach_dial_decides_how_far():
    person = recurring_person(answer="I want her to make time for me")
    assert rs.step_back_due(person, NOW, "default") == {"key": "yashu", "name": "Yashu", "press": ""}
    assert rs.step_back_due(person, NOW, "low")["press"] == ""
    assert rs.step_back_due(person, NOW, "more")["press"] == "I want her to make time for me"      # only "more" presses with their own words
    assert rs.step_back_due(person, NOW, "none") is None
    assert rs.step_back_due(episodes_on([7, 1])[0], NOW, "default") is None                        # not recurring yet
    assert rs.step_back_due(None, NOW, "default") is None


def test_a_step_back_waits_after_being_asked_and_stays_dropped_after_a_deflection():
    asked = recurring_person(asked_at=(NOW - timedelta(days=rs.ASK_GAP_DAYS - 1)).isoformat())
    assert rs.step_back_due(asked, NOW, "default") is None
    assert rs.step_back_due(recurring_person(asked_at=(NOW - timedelta(days=rs.ASK_GAP_DAYS + 1)).isoformat()), NOW, "default") is not None
    assert rs.step_back_due(recurring_person(dropped_until=(NOW + timedelta(days=1)).isoformat()), NOW, "default") is None
    assert rs.step_back_due(recurring_person(dropped_until=(NOW - timedelta(days=1)).isoformat()), NOW, "default") is not None


def seeded_db():
    db = FakeDB()
    for day in (21, 14, 7):
        rs.note_turn(db, "jack", "Yashu", [], mood("low", 0.6), load, now=NOW - timedelta(days=day))
    return db


def test_the_turn_decides_on_a_step_back_only_for_an_upset_not_acute_message_about_a_recurring_person():
    db = seeded_db()
    rs.note_turn(db, "jack", "Yashu", [], mood("low", 0.6), load, now=NOW - timedelta(days=1))
    assert rs.step_back_for_turn(db, "jack", "Yashu cancelled again", [], mood("low", 0.6), load, "default", now=NOW)["name"] == "Yashu"
    assert rs.step_back_for_turn(db, "jack", "Yashu cancelled again", [], mood("distressed", rs.ACUTE_INTENSITY), load, "default", now=NOW) is None   # acute hurt: not now
    assert rs.step_back_for_turn(db, "jack", "Yashu cancelled again", [], mood("positive", 0.6), load, "default", now=NOW) is None
    assert rs.step_back_for_turn(db, "jack", "work was awful", [], mood("low", 0.6), load, "default", now=NOW) is None
    assert rs.step_back_for_turn(db, "jack", "Yashu cancelled again", [], mood("low", 0.6), load, "none", now=NOW) is None
    assert rs.step_back_for_turn(None, "jack", "Yashu", [], mood("low", 0.6), load, "default", now=NOW) is None


def test_the_block_asks_one_bigger_question_without_a_count_or_a_verdict():
    block = rs.step_back_block({"key": "yashu", "name": "Yashu", "press": ""})
    assert "Yashu" in block and "ONE question" in block and "never tell them to stay or leave" in block and "Do not say how many times" in block
    assert not any(ch.isdigit() for ch in block)                                                     # no count is ever given to the model to repeat
    pressed = rs.step_back_block({"key": "yashu", "name": "Yashu", "press": "I want her to make time for me"})
    assert '"I want her to make time for me"' in pressed and "as their words" in pressed
    assert rs.step_back_block(None) == ""


def test_answers_are_read_as_deflected_engaged_or_unclear():
    assert rs.classify_answer("I don't want to get into that right now") == "deflected"
    assert rs.classify_answer("idk") == "deflected" and rs.classify_answer("can we not") == "deflected"
    assert rs.classify_answer("Honestly I want someone who shows up when they say they will.") == "engaged"
    assert rs.classify_answer("hmm") == "unclear"


def test_asking_and_answering_are_recorded_and_a_deflection_drops_it():
    db = seeded_db()
    assert rs.note_asked(db, "jack", "yashu", now=NOW)
    person = db[rs.COLLECTION].find_one({"username": "jack"})["people"][0]
    assert person["asked_at"] == NOW.isoformat()
    assert rs.note_answer(db, "jack", "yashu", "I want someone who shows up when they say they will.", now=NOW) == {"step_back_engaged": 1}
    person = db[rs.COLLECTION].find_one({"username": "jack"})["people"][0]
    assert person["answer"] == "I want someone who shows up when they say they will."
    assert rs.note_answer(db, "jack", "yashu", "I don't want to talk about it", now=NOW) == {"step_back_deflected": 1}
    person = db[rs.COLLECTION].find_one({"username": "jack"})["people"][0]
    assert person["dropped_until"] == (NOW + timedelta(days=rs.DROP_DAYS)).isoformat() and person["answer"] == ""
    assert rs.note_answer(db, "jack", "yashu", "hmm", now=NOW) == {}
    assert rs.note_asked(db, "jack", "nobody", now=NOW) is False and rs.note_answer(None, "jack", "yashu", "x") == {}


def test_a_later_upset_turn_keeps_the_step_back_fields():
    people, _ = rs.update([recurring_person(asked_at="2026-01-01T00:00:00+00:00", answer="their words")], "Yashu", NOW)
    assert people[0]["asked_at"] == "2026-01-01T00:00:00+00:00" and people[0]["answer"] == "their words"


def test_the_answer_is_counted_through_record_answer(monkeypatch):
    recorded = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    db = seeded_db()
    rs.record_answer(db, "jack", "yashu", "I don't want to talk about it")
    assert recorded == [{"step_back_deflected": 1}]


def test_the_reply_offers_marks_and_reads_the_step_back_answer():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    block = app[app.index("step_back_entry = None"):][:1400]
    for gate in ('source_type == "conversational"', "risk_level == RISK_NONE", "not is_closing_message(question)", "not is_guest_username(username)", "not self_report_kind",
                 "not curiosity_utils.last_reply_asked(chat_sessions[history_key])", "reach_level.level"):
        assert gate in block, gate
    assert 'tally(turn_counts, "step_back_offered")' in block and 'tally(turn_counts, "step_back_pressed")' in block
    assert "if step_back_entry and curiosity_utils.asked_a_question(full_response):" in app and "recurring_struggles.note_asked" in app
    assert "previous_marks.get(recurring_struggles.STEP_BACK_MARK)" in app and "recurring_struggles.record_answer" in app
    assert "placed_own = bool(step_back_entry)" in app
    assert {"step_back_offered", "step_back_asked", "step_back_pressed", "step_back_deflected", "step_back_engaged"} <= self_counters.KEYS
