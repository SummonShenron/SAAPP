import asyncio
import functools
import json
import pathlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from backend.components.constraints import REASONER_PROMPT
from backend.services import agent_workflow as aw
from backend.state.graph_state import GraphState
from backend.utils import safety_utils as su

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ---------------------------------------------------------------------------
# Explicit-language detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "i want to kill myself",
    "i've been thinking about ending my life",
    "I'm thinking about suicide",
    "i am suicidal",
    "i'm going to take my own life",
    "I keep having suicidal thoughts",
    "i'm gonna hurt myself",
    "i'm planning on ending my life",
    "KILLING MYSELF would be easier",
])
def test_stated_intent_or_active_suicidal_thoughts_without_a_plan_are_acute(message):
    assert su.detect_risk_language(message) == su.RISK_ACUTE


@pytest.mark.parametrize("message", [
    "i want to kill myself tonight",
    "i'm going to end my life right now",
    "i have the pills right here next to me",
    "i've got a gun in front of me",
    "i already took a bunch of pills",
    "i'm standing on the edge of the bridge",
    "i'm writing a goodbye note",
    "this is goodbye",
    "i'm going to jump off the bridge",
    "i'm about to do it",
    "i want to kill myself, i have a rope",
])
def test_a_plan_means_a_time_or_something_already_done_is_imminent(message):
    assert su.detect_risk_language(message) == su.RISK_IMMINENT


@pytest.mark.parametrize("message", [
    "i just want to die",
    "I wish I was dead",
    "i wish i could just disappear",
    "everyone would be better off without me",
    "i don't want to be here anymore",
    "there's no point in living",
    "i can't go on anymore",
    "i've been cutting myself again",
    "i want it all to stop",
    "i've been self-harming",
    "nobody would notice if i was gone",
])
def test_wishing_to_be_gone_or_self_harm_urges_are_elevated(message):
    assert su.detect_risk_language(message) == su.RISK_ELEVATED


@pytest.mark.parametrize("message", [
    "this bug is killing me",
    "i could die of embarrassment",
    "that joke killed me lol",
    "i'm dying laughing",
    "my boss humiliated me in front of the team today",
    "i'm so stressed about the deadline",
    "what's the suicide rate among veterans? i'm writing a paper",
    "the character in my novel wants to die in chapter 3",
    "kill the process and restart the server",
    "how do i kill a python thread",
    "we have a pistol grip on the new drill, ready for tonight's project",
    "thanks, goodnight",
    "",
    None,
])
def test_idioms_research_fiction_and_ordinary_distress_are_not_flagged(message):
    assert su.detect_risk_language(message) == su.RISK_NONE


def test_a_joke_about_an_everyday_annoyance_excuses_an_elevated_phrase_only():
    assert su.detect_risk_language("monday meeting again lol i want to die") == su.RISK_NONE
    assert su.detect_risk_language("i want to die lol") == su.RISK_ELEVATED
    assert su.detect_risk_language("ha, i want to kill myself, this deploy") == su.RISK_ACUTE


def test_a_clear_statement_buried_in_a_longer_message_is_still_found():
    text = "ok so work was fine. whatever. i keep thinking about ending my life. anyway how was your day"
    assert su.detect_risk_language(text) == su.RISK_ACUTE


def test_the_highest_level_in_a_message_wins():
    assert su.detect_risk_language("i wish i was dead. i'm going to kill myself.") == su.RISK_ACUTE
    assert su.detect_risk_language("i wish i was dead. i have the pills right here.") == su.RISK_IMMINENT


@pytest.mark.parametrize("raw,expected", [
    ({"risk": "imminent"}, "imminent"), ({"risk": "acute"}, "acute"), ({"risk": " Elevated "}, "elevated"),
    ({"risk": "none"}, "none"), ({"risk": "severe"}, "none"), ({}, "none"), (None, "none"), ({"risk": 3}, "none"),
])
def test_the_reasoners_value_is_only_used_when_recognized(raw, expected):
    assert su.normalize_risk(raw) == expected


# ---------------------------------------------------------------------------
# The support ladder: reading what they said, and where it stands
# ---------------------------------------------------------------------------

def test_only_answers_the_message_actually_gave_are_taken_from_the_reading():
    reading = {"support": {"immediate_family": "unavailable", "friends": "available", "extended_family": "unknown", "professional": "maybe"},
               "contact": "  my friend  Dani, she lives close  "}
    updates, contact = su.normalize_support(reading)
    assert updates == {"immediate_family": "unavailable", "friends": "available"}
    assert contact == "my friend Dani, she lives close"


@pytest.mark.parametrize("raw", [None, {}, {"support": "yes"}, {"support": {"friends": 3}}, "text"])
def test_junk_in_the_support_reading_is_ignored(raw):
    assert su.normalize_support(raw)[0] == {}


def test_the_contact_is_bounded_to_one_short_line():
    _, contact = su.normalize_support({"support": {"friends": "available"}, "contact": "x" * 500})
    assert len(contact) == 120
    assert su.normalize_support({"contact": 7})[1] == ""


def test_a_fresh_ladder_starts_at_immediate_family():
    info = su.ladder_status(None)
    assert info["next"] == "immediate_family" and not info["exhausted"] and info["available"] is None
    assert set(info["statuses"].values()) == {"unknown"}


def test_the_ladder_moves_down_in_order_as_each_group_is_answered():
    state = {"support": {"immediate_family": "unavailable"}}
    assert su.ladder_status(state)["next"] == "friends"
    state["support"]["friends"] = "unavailable"
    assert su.ladder_status(state)["next"] == "extended_family"
    state["support"]["extended_family"] = "unavailable"
    assert su.ladder_status(state)["next"] == "professional"


def test_a_group_with_someone_available_is_reported_as_the_one_to_reach():
    info = su.ladder_status({"support": {"immediate_family": "unavailable", "friends": "available"}, "contacts": ["Dani"]})
    assert info["available"] == "friends" and not info["exhausted"] and info["contacts"] == ["Dani"]


def test_the_ladder_is_exhausted_only_when_every_group_has_been_asked_and_has_no_one():
    all_no = {"support": {k: "unavailable" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    assert su.ladder_status(all_no)["exhausted"] is True
    all_no["support"]["professional"] = "unknown"
    assert su.ladder_status(all_no)["exhausted"] is False


# ---------------------------------------------------------------------------
# Carrying the level and the ladder
# ---------------------------------------------------------------------------

def _state(level, turns=0, minutes_ago=0, **extra):
    return {"level": level, "updated_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "turns_since": turns, **extra}


def test_no_state_means_no_risk():
    assert su.effective_risk(None, NOW) == su.RISK_NONE
    assert su.effective_risk({}, NOW) == su.RISK_NONE
    assert su.effective_risk({"level": "none"}, NOW) == su.RISK_NONE


def test_a_raised_level_holds_then_relaxes_one_step_at_a_time():
    assert su.effective_risk(_state("imminent", turns=0), NOW) == su.RISK_IMMINENT
    assert su.effective_risk(_state("imminent", turns=su.IMMINENT_TURNS), NOW) == su.RISK_IMMINENT
    assert su.effective_risk(_state("imminent", turns=su.IMMINENT_TURNS + 1), NOW) == su.RISK_ACUTE
    assert su.effective_risk(_state("acute", turns=su.ACUTE_TURNS), NOW) == su.RISK_ACUTE
    assert su.effective_risk(_state("acute", turns=su.ACUTE_TURNS + 1), NOW) == su.RISK_ELEVATED
    assert su.effective_risk(_state("acute", turns=su.ELEVATED_TURNS + 1), NOW) == su.RISK_NONE


def test_the_watch_also_expires_with_time():
    assert su.effective_risk(_state("imminent", minutes_ago=su.IMMINENT_HOURS * 60 + 1), NOW) == su.RISK_ACUTE
    assert su.effective_risk(_state("acute", minutes_ago=su.ACUTE_HOURS * 60 + 1), NOW) == su.RISK_ELEVATED
    assert su.effective_risk(_state("elevated", minutes_ago=su.ELEVATED_HOURS * 60 + 1), NOW) == su.RISK_NONE


def test_a_malformed_state_means_no_risk_rather_than_crashing():
    assert su.effective_risk({"level": "acute", "updated_at": "nope", "turns_since": 0}, NOW) == su.RISK_NONE
    assert su.effective_risk({"level": "acute", "updated_at": NOW.isoformat(), "turns_since": "x"}, NOW) == su.RISK_NONE


def test_a_new_message_at_or_above_the_current_level_restarts_the_clock():
    merged = su.merge_safety_state(_state("elevated", turns=10), su.RISK_ACUTE, NOW)
    assert merged["level"] == "acute" and merged["turns_since"] == 0
    assert su.merge_safety_state(_state("acute", turns=5), su.RISK_ACUTE, NOW)["turns_since"] == 0
    assert su.merge_safety_state(_state("acute", turns=1), su.RISK_IMMINENT, NOW)["level"] == "imminent"


def test_a_calm_message_after_a_frightening_one_never_resets_the_watch():
    merged = su.merge_safety_state(_state("acute", turns=0), su.RISK_NONE, NOW)
    assert merged["level"] == "acute" and merged["turns_since"] == 1
    assert su.effective_risk(merged, NOW) == su.RISK_ACUTE


def test_a_weaker_signal_does_not_downgrade_an_active_state():
    merged = su.merge_safety_state(_state("acute", turns=2), su.RISK_ELEVATED, NOW)
    assert merged["level"] == "acute" and merged["turns_since"] == 3


def test_nothing_in_force_and_nothing_new_stays_none_even_if_a_support_answer_arrives():
    assert su.merge_safety_state(None, su.RISK_NONE, NOW) is None
    assert su.merge_safety_state(None, su.RISK_NONE, NOW, {"friends": "available"}, "Dani") is None
    assert su.merge_safety_state(_state("acute", turns=su.ELEVATED_TURNS + 5), su.RISK_NONE, NOW) is None


def test_the_ladder_answers_are_remembered_across_turns_and_re_raises():
    state = su.merge_safety_state(None, su.RISK_ACUTE, NOW, {"immediate_family": "unavailable"})
    state = su.merge_safety_state(state, su.RISK_NONE, NOW, {"friends": "unavailable"})
    assert state["support"] == {"immediate_family": "unavailable", "friends": "unavailable"}
    state = su.merge_safety_state(state, su.RISK_ACUTE, NOW)  # said it again: the clock restarts, the answers stay
    assert state["turns_since"] == 0 and state["support"]["friends"] == "unavailable"


def test_an_explicit_later_answer_changes_an_earlier_one_and_contacts_accumulate_without_repeats():
    state = su.merge_safety_state(None, su.RISK_ACUTE, NOW, {"friends": "unavailable"}, "")
    state = su.merge_safety_state(state, su.RISK_NONE, NOW, {"friends": "available"}, "Dani, close by")
    state = su.merge_safety_state(state, su.RISK_NONE, NOW, {}, "Dani, close by")
    assert state["support"]["friends"] == "available" and state["contacts"] == ["Dani, close by"]


def test_unrecognized_ladder_keys_and_values_are_ignored_when_merging():
    state = su.merge_safety_state(None, su.RISK_ACUTE, NOW, {"neighbors": "available", "friends": "maybe"}, "")
    assert "support" not in state


# ---------------------------------------------------------------------------
# The SAFETY block: people first, a human line only when due
# ---------------------------------------------------------------------------

def _plan(level, fresh=True, state=None, history="", message="", people=None):
    return su.plan_safety_turn(level, fresh, state, history, message, people)


def test_nothing_active_means_no_block_and_no_checks():
    plan = _plan(su.RISK_NONE)
    assert plan.context == "" and not plan.active
    assert plan.reply_issue("Have a great night! 😊") is None
    assert plan.needs_resource_line("anything") is False


def test_an_acute_first_reply_asks_directly_then_begins_with_immediate_family_and_names_no_hotline():
    plan = _plan(su.RISK_ACUTE, state={"level": "acute"})
    block = plan.context
    assert "outranks everything else" in block and "Ask about their safety directly" in block
    assert "THEIR PEOPLE" in block and "immediate family: not asked yet" in block
    assert "NEXT STEP: ask about their immediate family" in block
    assert "Do NOT mention any crisis line or hotline" in block
    for forbidden in ("988", "findahelpline", "911"):
        assert forbidden not in block
    assert plan.line_allowed is False and plan.line_required is False


def test_the_conversation_moves_to_friends_once_immediate_family_is_answered():
    state = {"level": "acute", "support": {"immediate_family": "unavailable"}}
    block = _plan(su.RISK_ACUTE, fresh=False, state=state).context
    assert "immediate family: they said no one they can reach" in block
    assert "NEXT STEP: ask about friends" in block
    assert "988" not in block


def test_when_someone_is_available_the_job_is_helping_them_reach_that_person():
    state = {"level": "acute", "support": {"immediate_family": "unavailable", "friends": "available"}, "contacts": ["Dani, lives close"]}
    block = _plan(su.RISK_ACUTE, fresh=False, state=state).context
    assert "friends: they said there is someone (you know: Dani, lives close)" in block
    assert "NEXT STEP: they have someone. Help them actually reach that person now" in block
    assert "word the message with them" in block and "988" not in block


def test_people_remembered_from_earlier_conversations_are_offered_by_name():
    block = _plan(su.RISK_ELEVATED, people=["A person they can turn to when things get dark: older sister Kayla."]).context
    assert "from what you remember about them from before" in block and "Kayla" in block
    assert "ask whether one of them is around, by name" in block


def test_only_after_every_group_is_exhausted_does_a_human_line_come_in_and_as_a_warm_handoff():
    state = {"level": "acute", "support": {k: "unavailable" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    plan = _plan(su.RISK_ACUTE, fresh=False, state=state)
    block = plan.context
    assert "no one in any of those groups" in block and "and only now" in block
    for required in ("988", "warm handoff", "never as a brush-off or a stock line", "stay right here in this conversation"):
        assert required in block
    assert plan.exhausted and plan.line_allowed and plan.line_required


def test_once_a_line_was_given_it_is_not_recited_again():
    state = {"level": "acute", "support": {k: "unavailable" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    plan = _plan(su.RISK_ACUTE, fresh=False, state=state, history="Assistant: you can call or text 988 any time")
    assert "already pointed them to a human line" in plan.context and "988" not in plan.context
    assert plan.line_required is False


def test_asking_for_a_number_gets_one_at_once_without_walking_the_ladder():
    plan = _plan(su.RISK_ELEVATED, message="can you give me the 988 number or something")
    assert plan.asked_for_line and plan.line_allowed and plan.line_required
    assert "warm handoff" in plan.context


def test_imminent_danger_skips_the_ladder_for_an_immediate_human_line_alongside_a_person():
    plan = _plan(su.RISK_IMMINENT)
    block = plan.context
    for required in ("988", "911", "findahelpline.com", "keep them alive and not alone", "move away from it", "word the text or the call with them", "call 911 now"):
        assert required in block
    assert "THEIR PEOPLE" not in block and "NEXT STEP" not in block
    assert plan.line_allowed and plan.line_required


def test_a_later_turn_with_nothing_left_to_ask_keeps_the_register_and_does_not_close_cheerfully():
    state = {"level": "elevated", "support": {"immediate_family": "available", "friends": "available", "extended_family": "available", "professional": "available"}}
    state_none_left = {"level": "elevated", "support": {k: "available" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    block = _plan(su.RISK_ELEVATED, fresh=False, state=state_none_left).context
    assert "someone" in block  # available people: help reaching them
    carried = _plan(su.RISK_ELEVATED, fresh=False, state={"level": "elevated", "support": {}}).context
    assert "NEXT STEP" in carried  # still working the ladder


def test_the_block_always_outranks_everything_and_forbids_breeziness():
    for level in (su.RISK_ELEVATED, su.RISK_ACUTE, su.RISK_IMMINENT):
        block = _plan(level, state={"level": level}).context
        assert "outranks everything else" in block and "wrapping up conversations" in block
        for rule in ("no emoji", "no jokes", "no sign-off", "no <<<FOLLOW_UP>>> tag"):
            assert rule in block


# ---------------------------------------------------------------------------
# Checking the reply
# ---------------------------------------------------------------------------

def test_a_reply_that_reaches_for_a_hotline_before_the_ladder_is_worked_through_is_rejected():
    plan = _plan(su.RISK_ACUTE, state={"level": "acute"})
    reply = "I'm so sorry you're hurting. Please call 988 right now. Is anyone with you?"
    assert "before the people in their life" in plan.reply_issue(reply)


def test_the_same_reply_is_fine_once_a_line_is_allowed():
    state = {"level": "acute", "support": {k: "unavailable" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    plan = _plan(su.RISK_ACUTE, fresh=False, state=state)
    assert plan.reply_issue("I'm glad you told me. I'd like a person on the line with you: you can call or text 988, and I'll stay right here.") is None


def test_a_due_line_that_the_reply_leaves_out_is_flagged_and_then_added_in_code():
    state = {"level": "acute", "support": {k: "unavailable" for k in ("immediate_family", "friends", "extended_family", "professional")}}
    plan = _plan(su.RISK_ACUTE, fresh=False, state=state)
    reply = "I'm here with you. How are you doing right now?"
    assert "offers no real human line" in plan.reply_issue(reply)
    assert plan.needs_resource_line(reply) is True
    assert plan.needs_resource_line("call or text 988, I'll stay") is False


def test_an_imminent_reply_must_name_a_human_line():
    plan = _plan(su.RISK_IMMINENT)
    assert "offers no real human line" in plan.reply_issue("I'm so glad you told me. Are you safe right now?")
    assert plan.reply_issue("Are you safe right now? Please call 911 if you've taken anything, and I'm staying with you.") is None


def test_a_good_ladder_reply_passes():
    plan = _plan(su.RISK_ACUTE, state={"level": "acute"})
    reply = ("I'm really glad you told me, and I'm staying right here. Are you thinking about ending your life tonight? "
             "I'd feel better if someone could be there with you. Is there anyone in your family, a parent or a sibling, you could reach?")
    assert plan.reply_issue(reply) is None


def test_a_fresh_reply_that_never_asks_is_rejected_and_a_carried_one_is_not():
    assert "never asks" in _plan(su.RISK_ELEVATED, fresh=True, state={"level": "elevated"}).reply_issue("That sounds really heavy, and I'm here with you.")
    assert _plan(su.RISK_ELEVATED, fresh=False, state={"level": "elevated", "support": {"immediate_family": "unavailable"}}).reply_issue("That sounds really heavy, and I'm here with you.") is None


@pytest.mark.parametrize("reply,fragment", [
    ("I hear you. Is anyone with you? <<<FOLLOW_UP: Want some coping tips? >>>", "follow-up"),
    ("I hear you 😔. Is anyone with you?", "emoji"),
    ("I'm here! I'm so glad you told me! Is anyone with you?", "exclamatory"),
    ("I hear you. Is anyone with you? Have a great night.", "breezy"),
    ("I hear you. Is anyone with you? Anything else I can help with?", "breezy"),
])
def test_breezy_or_engagement_shaped_replies_are_rejected_at_any_active_level(reply, fragment):
    for level in (su.RISK_ELEVATED, su.RISK_ACUTE):
        for fresh in (True, False):
            assert fragment in _plan(level, fresh=fresh, state={"level": level}).reply_issue(reply)


def test_the_revision_prompt_keeps_the_original_includes_the_draft_and_names_the_problem():
    prompt, reason = su.build_safety_revision_prompt("ORIGINAL", "Draft text.", "it uses emoji")
    assert prompt.startswith("ORIGINAL") and "Draft text." in prompt and "never mention this note" in prompt
    assert "it uses emoji" in reason and "SAFETY block" in reason and "NEXT STEP" in reason


def test_the_last_resort_line_contains_a_resource_and_reads_as_a_handoff_not_a_script():
    line = su.CRISIS_RESOURCE_LINE
    assert su.has_crisis_resource(line) and "988" in line and "911" in line
    assert "I'll be right here the whole time" in line and "a person on the line with you" in line
    assert "if you are experiencing" not in line.lower()


def test_resources_already_given_is_read_from_the_history():
    assert su.resources_already_given("Assistant: you can call or text 988 any time") is True
    assert su.resources_already_given("Assistant: that sounds heavy. what happened?") is False
    assert su.resources_already_given("") is False


# ---------------------------------------------------------------------------
# Remembering their people
# ---------------------------------------------------------------------------

def test_a_person_they_have_is_saved_as_a_durable_fact(monkeypatch):
    saved = []
    monkeypatch.setattr("backend.utils.memory_utils.save_user_fact", lambda *a, **k: saved.append((a, k)))
    su.remember_support("jack", "immediate_family", "available", "older sister Kayla, lives nearby")
    su.remember_support("jack", "professional", "available", "")
    facts = [a[1] for a, _ in saved]
    assert "older sister Kayla, lives nearby" in facts[0]
    assert "a professional they can turn to" in facts[1]
    assert all(k["category"] == "relationship" and k["source"] == su.SUPPORT_FACT_SOURCE for _, k in saved)


def test_a_no_one_answer_is_never_written_to_long_term_memory(monkeypatch):
    """It is true of one bad night, circumstances change, and a wrong negative would make a later conversation
    skip someone who is there."""
    saved = []
    monkeypatch.setattr("backend.utils.memory_utils.save_user_fact", lambda *a, **k: saved.append(a))
    su.remember_support("jack", "friends", "unavailable", "")
    su.remember_support("jack", "immediate_family", "unavailable", "mother passed away")
    assert saved == []


def test_remembering_can_never_break_anything(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.memory_utils.save_user_fact", boom)
    su.remember_support("jack", "friends", "available", "Dani")  # must not raise
    su.remember_support(None, "friends", "available", "Dani")


def test_only_support_facts_are_read_back_as_the_people_to_ask_about(monkeypatch):
    facts = [
        SimpleNamespace(source=su.SUPPORT_FACT_SOURCE, active=True, fact="A person they can turn to when things get dark: Kayla."),
        SimpleNamespace(source="explicit", active=True, fact="Their brother Tom lives in Denver."),
        SimpleNamespace(source=su.SUPPORT_FACT_SOURCE, active=False, fact="An old, superseded one."),
    ]
    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", lambda username, category=None: facts)
    assert su.remembered_support_people("jack") == ["A person they can turn to when things get dark: Kayla."]
    assert su.remembered_support_people(None) == []


def test_reading_remembered_people_degrades_to_nothing_on_any_problem(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.memory_utils.load_user_facts", boom)
    assert su.remembered_support_people("jack") == []


# ---------------------------------------------------------------------------
# Logging: that it happened, never what was said
# ---------------------------------------------------------------------------

class _Col:
    def __init__(self):
        self.docs, self.indexes = [], []

    def insert_one(self, doc):
        self.docs.append(doc)

    def create_index(self, *a, **k):
        self.indexes.append((a, k))


class _DB:
    def __init__(self):
        self.col = _Col()

    def __getitem__(self, name):
        assert name == su.SAFETY_EVENTS_COLLECTION
        return self.col


def test_a_safety_event_records_who_and_how_serious_but_never_the_message(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    su.log_safety_event("jack", "acute", "language")
    doc = db.col.docs[0]
    assert set(doc) == {"username", "kind", "level", "source", "at"}
    assert doc["username"] == "jack" and doc["level"] == "acute" and doc["kind"] == "risk_raised"


def test_logging_can_never_break_a_reply(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    su.log_safety_event("jack", "acute", "language")  # must not raise


# ---------------------------------------------------------------------------
# The reasoner: detection runs on every message, even when the classification call fails
# ---------------------------------------------------------------------------

def _flags(**emotional):
    return SimpleNamespace(content=json.dumps({"needs_conversation": True, "emotional_state": emotional}))


@pytest.fixture
def events(monkeypatch):
    class _Events(list):
        pass

    seen = _Events()
    seen.remembered = []
    seen.ladder = []

    def _log(user, level, source, kind="risk_raised", rung=None, status=None):
        if kind == "ladder_answer":
            seen.ladder.append((user, level, rung, status))
        else:
            seen.append((user, level, source))

    monkeypatch.setattr(aw, "log_safety_event", _log)
    monkeypatch.setattr(aw, "remember_support", lambda *a: seen.remembered.append(a))
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    return seen


@run_async
async def test_explicit_language_raises_the_level_even_when_the_model_call_fails(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=RuntimeError("quota")))
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="i want to kill myself")]})
    assert result["safety_state"]["level"] == "acute" and result["safety_state"]["turns_since"] == 0
    assert events == [("jack", "acute", "language")]


@run_async
async def test_the_models_own_read_catches_what_the_patterns_cannot(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(valence="distressed", intensity=0.9, risk="elevated")))
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="i just can't see how any of this ends well for me")]})
    assert result["safety_state"]["level"] == "elevated"
    assert events == [("jack", "elevated", "reasoner")]


@run_async
async def test_the_higher_of_the_two_sources_wins(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(valence="distressed", intensity=0.9, risk="elevated")))
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="i'm going to kill myself")]})
    assert result["safety_state"]["level"] == "acute"


@run_async
async def test_an_ordinary_message_creates_no_safety_state_and_no_event(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(valence="neutral", intensity=0.0, risk="none")))
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="this bug is killing me")]})
    assert result.get("safety_state") is None and events == []


@run_async
async def test_the_level_is_carried_to_the_next_turn_and_a_calm_message_does_not_reset_it(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(valence="neutral", intensity=0.0, risk="none")))
    prior = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0}
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="ok thanks, goodnight")], "safety_state": prior})
    assert result["safety_state"]["level"] == "acute" and result["safety_state"]["turns_since"] == 1
    assert events == []


@run_async
async def test_what_they_say_about_the_people_in_their_life_is_recorded_in_the_ladder_and_remembered(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(
        valence="distressed", intensity=0.8, risk="none",
        support={"immediate_family": "unavailable", "friends": "available", "extended_family": "unknown", "professional": "unknown"},
        contact="my friend Dani, lives ten minutes away",
    )))
    prior = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0}
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="no, my parents and i don't talk. my friend dani is close though")], "safety_state": prior})
    assert result["safety_state"]["support"] == {"immediate_family": "unavailable", "friends": "available"}
    assert result["safety_state"]["contacts"] == ["my friend Dani, lives ten minutes away"]
    await asyncio.sleep(0.05)  # the fire-and-forget memory writes
    # only the person they HAVE is remembered long-term; the "no" about family stays in this conversation's ladder
    assert events.remembered == [("jack", "friends", "available", "my friend Dani, lives ten minutes away")]


@run_async
async def test_an_answer_already_recorded_is_not_remembered_twice(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(
        valence="distressed", intensity=0.8, risk="none", support={"friends": "unavailable"}, contact="",
    )))
    prior = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0, "support": {"friends": "unavailable"}}
    await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="still no friends")], "safety_state": prior})
    await asyncio.sleep(0.05)
    assert events.remembered == []


@run_async
async def test_support_answers_are_ignored_when_there_is_no_risk_in_force(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(
        valence="neutral", intensity=0.0, risk="none", support={"friends": "available"}, contact="Dani",
    )))
    result = await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="my friend dani is great")]})
    assert result.get("safety_state") is None
    await asyncio.sleep(0.05)
    assert events.remembered == []


# ---------------------------------------------------------------------------
# It is wired in, and can't be dropped without a test failing
# ---------------------------------------------------------------------------

def test_the_graph_state_declares_safety_state_so_langgraph_does_not_drop_it():
    assert "safety_state" in GraphState.__annotations__


def test_the_reasoner_prompt_teaches_the_risk_levels_and_the_ladder_without_flagging_everyday_talk():
    for required in ('"risk"', '"imminent"', '"acute"', '"elevated"', '"support"', '"contact"', "immediate_family", "extended_family"):
        assert required in REASONER_PROMPT
    assert "this bug is killing me" in REASONER_PROMPT
    assert "Never infer risk from topic alone" in REASONER_PROMPT
    assert "never invent people" in REASONER_PROMPT


def test_the_app_overrides_routing_suppresses_nudges_and_guarantees_a_line_when_one_is_due():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert 'if risk_level != RISK_NONE:\n                source_type = "conversational"' in source
    assert 'data = "" if risk_level != RISK_NONE else' in source
    assert 'goal_nudge_context = "" if (risk_level != RISK_NONE or open_loop_entry) else fetch_goal_nudge_context(username)' in source
    assert 'source_type == "conversational" and risk_level == RISK_NONE and is_closing_message(question)' in source
    assert "safety_plan = plan_safety_turn(" in source and "safety_plan.context" in source
    assert "safety_plan.reply_issue(full_response)" in source
    assert "safety_plan.needs_resource_line(full_response)" in source and "full_response += CRISIS_RESOURCE_LINE" in source
    assert "remembered_support_people" in source


# ---------------------------------------------------------------------------
# "Fresh" means this message brought the level up, not that they said it again
# ---------------------------------------------------------------------------

def test_the_first_time_a_level_is_raised_it_is_an_escalation():
    assert su.merge_safety_state(None, su.RISK_ACUTE, NOW)["escalated"] is True


def test_repeating_the_same_level_restarts_the_clock_without_being_a_new_disclosure():
    prior = su.merge_safety_state(None, su.RISK_ACUTE, NOW)
    again = su.merge_safety_state(prior, su.RISK_ACUTE, NOW)
    assert again["turns_since"] == 0 and again["escalated"] is False


def test_a_higher_level_is_an_escalation_and_a_carried_turn_is_not():
    prior = su.merge_safety_state(None, su.RISK_ELEVATED, NOW)
    assert su.merge_safety_state(prior, su.RISK_ACUTE, NOW)["escalated"] is True
    assert su.merge_safety_state(prior, su.RISK_NONE, NOW)["escalated"] is False


def test_a_description_that_came_with_a_no_one_answer_is_not_a_contact():
    state = su.merge_safety_state(None, su.RISK_ACUTE, NOW, {"immediate_family": "unavailable"}, "mother passed away, estranged from father")
    assert "contacts" not in state
    state = su.merge_safety_state(state, su.RISK_NONE, NOW, {"friends": "available"}, "Dani, lives close")
    assert state["contacts"] == ["Dani, lives close"]


def test_the_app_treats_only_an_escalation_as_a_fresh_disclosure():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    assert 'bool(safety_state.get("escalated"))' in source


# ---------------------------------------------------------------------------
# Telemetry: counts about the layer's own behavior, with no free text anywhere
# ---------------------------------------------------------------------------

@run_async
async def test_a_ladder_answer_is_counted_with_the_rung_and_status_but_never_who(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(
        valence="distressed", intensity=0.8, risk="none",
        support={"immediate_family": "unavailable", "friends": "available"}, contact="my friend Dani",
    )))
    prior = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0}
    await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="no family, but my friend dani")], "safety_state": prior})
    await asyncio.sleep(0.05)
    assert sorted(events.ladder) == [("jack", "acute", "friends", "available"), ("jack", "acute", "immediate_family", "unavailable")]
    assert all("dani" not in str(row).lower() for row in events.ladder)


@run_async
async def test_a_repeated_ladder_answer_is_not_counted_twice(monkeypatch, events):
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=_flags(
        valence="distressed", intensity=0.8, risk="none", support={"friends": "unavailable"}, contact="",
    )))
    prior = {"level": "acute", "updated_at": datetime.now(timezone.utc).isoformat(), "turns_since": 0, "support": {"friends": "unavailable"}}
    await aw.reasoner_node({"username": "jack", "messages": [HumanMessage(content="still no friends")], "safety_state": prior})
    assert events.ladder == []


def test_only_fixed_values_can_reach_a_safety_record_never_free_text(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    su.log_safety_event("jack", "my private message about Kayla", "x" * 200, "not-a-kind", rung="Kayla", status="she lives nearby")
    doc = db.col.docs[0]
    assert doc["kind"] == "risk_raised" and doc["level"] == "unknown" and doc["source"] == "unknown"
    assert "rung" not in doc and "status" not in doc
    assert "Kayla" not in str(doc) and "private" not in str(doc)


def test_a_real_rung_and_status_are_kept(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    su.log_safety_event("jack", "acute", "reasoner", su.KIND_LADDER_ANSWER, rung="friends", status="unavailable")
    assert db.col.docs[0]["rung"] == "friends" and db.col.docs[0]["status"] == "unavailable"


def test_record_safety_turn_writes_the_turn_and_only_the_things_that_happened(monkeypatch):
    db = _DB()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    su.record_safety_turn("jack", "acute", revised=False, line_in_reply=False, last_resort_line=False)
    assert [d["kind"] for d in db.col.docs] == ["risk_turn"]
    db.col.docs.clear()
    su.record_safety_turn("jack", "imminent", revised=True, line_in_reply=True, last_resort_line=True)
    assert [d["kind"] for d in db.col.docs] == ["risk_turn", "reply_revised", "line_in_reply", "last_resort_line"]
    assert all(d["level"] == "imminent" for d in db.col.docs)


def test_record_safety_turn_can_never_break_a_reply(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    su.record_safety_turn("jack", "acute", True, True, True)  # must not raise
