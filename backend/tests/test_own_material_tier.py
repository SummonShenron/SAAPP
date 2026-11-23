"""The mood gate for Sonic's OWN material (emotion_utils.own_material_tier): frustration at the work and a low that has eased no longer silence its habits, record cases and captures; distress,
an unlifted low and frustration about life still do. The person-facing extras keep the plain energy tier. No model, no database."""
import os
from datetime import datetime, timedelta, timezone

from backend.utils import emotion_utils as eu
from backend.utils import self_counters

NOW = datetime.now(timezone.utc)
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def feeling(valence, intensity=0.7, lift=None):
    state = {"valence": valence, "intensity": intensity, "updated_at": (NOW - timedelta(minutes=1)).isoformat(), "turns_since": 0}
    if lift is not None:
        state["positive_peak"] = lift
    return state


def own(state, work):
    return eu.own_material_tier(state, NOW, work_context=work)


def test_an_open_mood_stays_open_with_or_without_work():
    assert own(None, False) == "open" and own(None, True) == "open"
    assert own(feeling("positive"), False) == "open"


def test_frustration_at_the_work_opens_it_and_frustration_about_life_does_not():
    assert eu.energy_tier(feeling("frustrated", 0.9), NOW) == "subdued"                   # what used to hold everything down
    assert own(feeling("frustrated", 0.9), True) == "open"
    assert own(feeling("frustrated", 0.9), False) == "subdued"


def test_a_low_that_has_eased_opens_it_but_an_unlifted_low_does_not():
    assert own(feeling("low", 0.6, lift=0.2), False) == "open"
    assert own(feeling("low", 0.6), False) == "subdued"
    assert own(feeling("low", 0.6, lift=eu.EMOTION_LIFT_FLOOR / 2), False) == "subdued"   # a flicker below the floor is not a lift


def test_distress_holds_it_even_when_eased_or_during_work():
    assert own(feeling("distressed", 0.8, lift=0.2), False) == "easing"
    assert own(feeling("distressed", 0.8, lift=0.2), True) == "easing"
    assert own(feeling("distressed", 0.8), True) == "subdued"
    assert own(feeling("low", 0.6), True) == "subdued"                                   # a low during code work is not frustration at the work


def test_the_counter_is_a_known_key():
    assert "own_material_opened" in self_counters.KEYS


def _source(path):
    return open(os.path.join(REPO, path), encoding="utf-8").read()


def test_the_own_material_gates_use_it_and_the_person_facing_extras_do_not():
    app = _source("app.py")
    assert "work_context=code_followup is not None or bool(set(llm_turn.tools) & code_outcomes.REPO_TOOLS)" in app
    assert 'tally(turn_counts, "own_material_opened")' in app
    assert app.count("energy_tier=own_tier,") == 6                                         # the pushback/praise capture, the relatable line, the record case, the three post-turn captures
    assert 'if own_tier == "open" and question:' in app and "energy_tier=own_tier, closing=is_closing_message(question)" in app     # the record case in a chat
    relatable = app[app.index("relatable_entry = None if (open_loop_entry or headsup_entry) else select_relatable("):][:600]
    assert "energy_tier=own_tier" in relatable
    callback = app[app.index("if callback_slot_ok and callback_allowed("):][:400]
    assert "energy_tier=energy_tier(" in callback                                           # a memory callback is about the person: unchanged
    workflow = _source("backend/services/agent_workflow.py")
    assert "energy_tier=_own_material_tier(state.get(\"emotional_state\"), moment, work_context=True)" in workflow
