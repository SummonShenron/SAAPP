"""Safety that listens, and holding the person's own goals (2026-11-23, from Jack's chat review). States are built with the real merge_safety_state and plan_safety_turn; goal facts are
UserFact-shaped. No model, no database."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.services import agent_workflow as aw
from backend.utils import goal_guard as gg
from backend.utils import revision_reasons, self_counters
from backend.utils import safety_utils as su
from backend.utils.emotion_checks import build_emotional_revision_prompt
from backend.utils.emotion_utils import merge_emotional_state

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime.now(timezone.utc)


def app_source():
    return open(os.path.join(REPO, "app.py"), encoding="utf-8").read()


# ---- safety: a plain answer is an answer ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["no im okay friend i jsut wanted to use the analogy. theyre song lyrics", "it's just a metaphor", "i wasn't being literal",
                                  "im fine saapp im jsut sad", "i just said no im fine."])
def test_these_answers_count_as_saying_they_are_okay(text):
    assert su.detect_denial(text)


def test_a_model_only_acute_ends_the_questions_when_they_say_they_are_fine():
    raised = su.merge_safety_state(None, su.RISK_ACUTE, NOW, from_model=True)
    assert raised["source"] == "model"
    later = su.merge_safety_state(raised, su.RISK_NONE, NOW + timedelta(minutes=1), declined=True)
    assert later["declined"] is True
    plan = su.plan_safety_turn(su.RISK_ACUTE, False, later, "", "im fine saapp im jsut sad")
    assert "Take them at their word" in plan.context and not plan.line_required


def test_their_own_words_of_intent_keep_the_full_ladder():
    raised = su.merge_safety_state(None, su.RISK_ACUTE, NOW, from_model=False)
    assert raised["source"] == "words"
    later = su.merge_safety_state(raised, su.RISK_NONE, NOW + timedelta(minutes=1), declined=True)
    assert not later.get("declined")
    plan = su.plan_safety_turn(su.RISK_ACUTE, False, later, "", "im fine")
    assert "Take them at their word" not in plan.context


def test_imminent_never_relaxes_on_a_denial_and_elevated_still_does():
    imminent = su.merge_safety_state(None, su.RISK_IMMINENT, NOW, from_model=True)
    assert not su.merge_safety_state(imminent, su.RISK_NONE, NOW + timedelta(minutes=1), declined=True).get("declined")
    elevated = su.merge_safety_state(None, su.RISK_ELEVATED, NOW, from_model=False)
    assert su.merge_safety_state(elevated, su.RISK_NONE, NOW + timedelta(minutes=1), declined=True)["declined"] is True


def test_the_reasoner_offers_the_denial_at_elevated_and_acute_only_without_risk_words():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert '_declined = detect_denial(msg) and _before in ("elevated", "acute") and _regex_level == "none"' in source


# ---- goals: their own words, the right family ----------------------------------------------------------------------------------------------------

def fact(text, category="goal", status="active", active=True):
    return SimpleNamespace(fact=text, category=category, goal_status=status, active=active)


FACTS = [
    fact("The user intends to discuss improvements to the assistant with their therapist."),
    fact("Jack intends to resume therapy sessions once his insurance coverage allows."),
    fact("The user intends to quit using weed despite not feeling ready."),
    fact("The user is actively working to quit smoking.", status=None),
    fact("The user is working on managing alcohol cravings during periods of high stress."),
    fact("The user uses cannabis to cope.", category="trait"),
    fact("The user quit drinking soda.", status="achieved"),
]


def test_a_message_brings_up_their_goals_in_the_same_family_with_stated_intents_first():
    assert gg.related_goals(FACTS, "can you cancel all of my therapy appointments")[0] == "Jack intends to resume therapy sessions once his insurance coverage allows."
    assert gg.related_goals(FACTS, "im craving a drink so bad tonight") == ["The user is working on managing alcohol cravings during periods of high stress."]
    weed = gg.related_goals(FACTS, "just smoked a bowl")
    assert "The user intends to quit using weed despite not feeling ready." in weed and len(weed) <= gg.MAX_GOALS
    assert all("cannabis to cope" not in g for g in weed)                                   # a trait is not a goal
    assert gg.related_goals(FACTS, "big high-stakes meeting today") == [] and gg.related_goals(FACTS, "drinking water") == []
    assert gg.related_goals(FACTS, "the air fryer is my therapeutic habit") == []


def test_cancelling_care_is_recognised():
    assert gg.care_cancel_request("thank you saapp. can you cancel all of my therapy appointments for now?")
    assert gg.care_cancel_request("skip my psychiatrist on friday")
    assert not gg.care_cancel_request("cancel my dentist") and not gg.care_cancel_request("therapy went well today")


@pytest.mark.parametrize("reply, families", [
    ("Just let the bowl do its job, sink into the couch.", ["cannabis"]),
    ("Enjoy the bowl and try to relax.", ["cannabis"]),
    ("Just let the weed take the edge off.", ["cannabis"]),
    ("You can grab a vodka seltzer there.", ["alcohol"]),
    ("Go grab a glass of water or a snack.", []),
    ("Go enjoy that taco bowl.", []),
    ("That took strength, deciding not to smoke weed.", []),
    ("Knowing you can't just smoke weed to come down is hard.", []),
])
def test_encouragement_is_read_narrowly(reply, families):
    assert gg.encouraged_families(reply) == families


def test_a_reply_goes_against_them_only_when_a_goal_is_in_that_family():
    weed_goal = ["The user intends to quit using weed despite not feeling ready."]
    assert gg.reply_goes_against("Enjoy the bowl tonight.", weed_goal)
    assert not gg.reply_goes_against("Grab a beer on the patio.", weed_goal)
    assert not gg.reply_goes_against("Enjoy the bowl tonight.", [])


def test_the_blocks_name_their_goal_and_never_refuse():
    block = gg.goals_block(["The user intends to quit using weed despite not feeling ready."])
    assert "quit using weed" in block and "never lecture" in block and "the choice is theirs" in block
    assert "CARE CHECK" in gg.goals_block([], care_pause=True) and "if they say cancel" in gg.goals_block([], care_pause=True)
    assert gg.goals_block([]) == ""


def test_the_tool_agent_pauses_only_for_care_while_they_are_low():
    low = {"emotional_state": merge_emotional_state(None, {"valence": "low", "intensity": 0.7}, NOW), "safety_state": None}
    calm = {"emotional_state": None, "safety_state": None}
    assert "CARE CHECK" in aw._care_pause_note("cancel all of my therapy appointments", low)
    assert aw._care_pause_note("cancel all of my therapy appointments", calm) == ""
    assert aw._care_pause_note("cancel my dentist", low) == ""
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert "+ _care_pause_note(msg, state)," in source


def test_the_reply_shows_their_goals_and_rewrites_a_reply_that_goes_against_them():
    app = app_source()
    assert "goal_entries = goal_guard.related_goals(await asyncio.to_thread(load_user_facts, username), question)" in app
    assert "prompt = prompt + goal_guard.goals_block(goal_entries, care_pause)" in app
    assert 'tally(turn_counts, "goal_guard_shown")' in app and 'tally(turn_counts, "care_pause_shown")' in app
    assert "goal_issue = goal_entries and goal_guard.reply_goes_against(checked_reply, goal_entries)" in app
    assert 'build_emotional_revision_prompt(prompt, full_response, "against_their_goal")' in app
    assert "never for a guest" in app or "not is_guest_username(username) and (goal_guard.families_in(question)" in app
    prompt, reason = build_emotional_revision_prompt("P", "d", "against_their_goal")
    assert "Do not lecture" in reason
    assert revision_reasons.reason_for("against_their_goal") == "against_their_goal"
    assert {"goal_guard_shown", "care_pause_shown", "revised_reason_against_their_goal"} <= self_counters.KEYS


def test_saying_it_was_a_lyric_alone_is_an_answer():
    assert su.detect_denial("those were song lyrics")
    assert su.detect_denial("it's a lyric from a song i like")


def test_a_trait_without_a_goal_brings_up_nothing():
    assert gg.related_goals([fact("The user uses cannabis to cope.", category="trait")], "just smoked a bowl") == []
