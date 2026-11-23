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
    block = app[app.index("# Recurring struggles (backend/utils/recurring_struggles.py)"):][:900]
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
