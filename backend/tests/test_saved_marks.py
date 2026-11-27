"""The marks a reply carries for later turns are saved with the conversation and come back after a restart (backend/utils/app_utils.py SAVED_MARKS).

Why (2026-11-27): only the support card's marks were saved, so every spacing rule that reads a mark (one joke a conversation, the gap between relatable lines, low moves and shared moments) and the
step-back answer capture started from nothing whenever the server restarted. The text below is made up."""
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils as au


def _round_trip(message):
    return au._deserialize_messages(au._serialize_messages([message]))[0]


def test_every_mark_a_later_turn_reads_is_on_the_saved_list():
    from backend.utils import (clarifications, code_outcomes, curiosity, emotion_checkin, encouragement_utils, experience_capture, experiences, jokes, low_moves, reading_shelf, recurring_struggles,
                               relatable_utils, self_history_utils, shared_history, shared_moments, view_check, volunteer, what_lands)
    names = {
        relatable_utils.RELATABLE_MARK, encouragement_utils.ENCOURAGEMENT_MARK, shared_history.CALLBACK_MARK, recurring_struggles.STEP_BACK_MARK, low_moves.MOVE_MARK, shared_moments.MARK,
        emotion_checkin.CHECKIN_MARK, curiosity.CURIOSITY_MARK, reading_shelf.SHELF_MARK, code_outcomes.CODE_MARK, code_outcomes.LEANINGS_MARK, experience_capture.UNTRUSTED_MARK,
        experiences.EXPERIENCE_MARK, jokes.JOKE_MARK, clarifications.CLARIFY_MARK, self_history_utils.SELF_HISTORY_MARK, view_check.VIEW_MARK, volunteer.MARK, what_lands.MARK,
    }
    assert names == set(au.SAVED_MARKS)


def test_a_mark_survives_being_saved_and_loaded():
    reply = AIMessage(content="made up", additional_kwargs={"joke_told": True, "low_move": "push_back", "step_back_asked": "a person", "leanings_used": ["keep_to_the_ask", "say_the_cause"]})
    back = _round_trip(reply)
    assert back.additional_kwargs["joke_told"] is True and back.additional_kwargs["low_move"] == "push_back" and back.additional_kwargs["step_back_asked"] == "a person"
    assert back.additional_kwargs["leanings_used"] == ["keep_to_the_ask", "say_the_cause"]
    assert "relatable_offered" not in back.additional_kwargs                    # a mark that was not set is not invented


def test_the_spacing_rules_still_hold_after_a_reload():
    from backend.utils import low_moves
    from backend.utils.relatable_utils import recently_offered

    saved = au._serialize_messages([HumanMessage(content="made up"), AIMessage(content="made up", additional_kwargs={"relatable_offered": True, "low_move": "company"})])
    loaded = au._deserialize_messages(saved)
    assert recently_offered(loaded) is True and low_moves.recent_kinds(loaded) == {"company"}


def test_only_a_mark_on_the_list_and_only_a_plain_value_is_saved():
    reply = AIMessage(content="made up", additional_kwargs={"joke_told": {"anything": 1}, "low_move": "x" * 500, "not_a_mark": True, "code_turn": 1, "leanings_used": [3, "", "ok"]})
    entry = au._serialize_messages([reply])[0]
    assert "joke_told" not in entry and "not_a_mark" not in entry and "code_turn" not in entry and entry["low_move"] == "x" * 120 and entry["leanings_used"] == ["ok"]
    assert au.clean_mark(True) is True and au.clean_mark(False) is None and au.clean_mark("") is None and au.clean_mark([]) is None and au.clean_mark(None) is None
    assert len(au.clean_mark(["a"] * 40)) == 12
