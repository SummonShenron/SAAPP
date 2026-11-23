"""The support card in conversations (backend/utils/support_choice.py): when it is offered, what a pick does and how long it lasts, and that the marks survive saving. Mood states come from the
real merge_emotional_state, messages are stamped with real times. No model, no database."""
import os
from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils, self_counters
from backend.utils import support_choice as sc
from backend.utils.emotion_utils import merge_emotional_state
from backend.utils.time_utils import stamp

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime.now(timezone.utc)
LOW = merge_emotional_state(None, {"valence": "low", "intensity": 0.6}, NOW)
GATES = dict(page_can_show=True, conversational=True, closing=False, safety_active=False)


def human(text, mode=None, ago_hours=0.0):
    return stamp(HumanMessage(content=text, additional_kwargs={sc.MODE_MARK: mode} if mode else {}), NOW - timedelta(hours=ago_hours))


def ai(text, card=False, ago_hours=0.0):
    return stamp(AIMessage(content=text, additional_kwargs={sc.CARD_MARK: True} if card else {}), NOW - timedelta(hours=ago_hours))


# ---- when it is offered --------------------------------------------------------------------------------------------------------------------------

def test_a_hard_message_with_no_stated_want_gets_the_card():
    assert sc.should_offer(LOW, "today was awful and she cancelled again", [], now=NOW, **GATES)


def test_no_card_when_they_said_what_they_want_or_the_moment_is_wrong():
    for message in ("i just need to vent about today", "what should i do about her", "please just listen", "any advice?", "how do i fix this"):
        assert not sc.should_offer(LOW, message, [], now=NOW, **GATES), message
    for gate in ("page_can_show", "conversational"):
        assert not sc.should_offer(LOW, "today was awful", [], now=NOW, **{**GATES, gate: False})
    assert not sc.should_offer(LOW, "today was awful", [], now=NOW, **{**GATES, "closing": True})
    assert not sc.should_offer(LOW, "today was awful", [], now=NOW, **{**GATES, "safety_active": True})


def test_no_card_for_a_calm_weak_or_lighter_message():
    assert not sc.should_offer(merge_emotional_state(None, {"valence": "positive", "intensity": 0.7}, NOW), "great day", [], now=NOW, **GATES)
    assert not sc.should_offer(merge_emotional_state(None, {"valence": "low", "intensity": sc.OFFER_MIN_INTENSITY - 0.05}, NOW), "meh", [], now=NOW, **GATES)
    lighter = merge_emotional_state(LOW, {"valence": "positive", "intensity": 0.15}, NOW)
    assert not sc.should_offer(lighter, "haha ok", [], now=NOW, **GATES)
    assert not sc.should_offer(None, "today was awful", [], now=NOW, **GATES)


def test_only_once_a_stretch():
    assert not sc.should_offer(LOW, "still awful", [ai("reply", card=True, ago_hours=1)], now=NOW, **GATES)
    assert sc.should_offer(LOW, "awful again", [ai("reply", card=True, ago_hours=sc.MODE_HOURS + 1)], now=NOW, **GATES)
    assert not sc.should_offer(LOW, "still awful", [human("Just listen", mode="listen", ago_hours=1)], now=NOW, **GATES)


# ---- what a pick does ---------------------------------------------------------------------------------------------------------------------------

def test_the_newest_pick_in_the_last_hours_is_the_mode():
    history = [human("Just listen", mode="listen", ago_hours=3), ai("ok"), human("Give me advice", mode="advice", ago_hours=1), ai("ok"), human("so yeah")]
    assert sc.current_mode(history, NOW) == "advice"
    assert sc.current_mode([human("Just listen", mode="listen", ago_hours=sc.MODE_HOURS + 1)], NOW) is None
    assert sc.current_mode([human("x", mode="nonsense")], NOW) is None
    assert sc.current_mode([ai("x", card=True)], NOW) is None


def test_each_mode_tells_the_reply_what_to_do_and_to_follow_a_change_of_mind():
    assert "No advice" in sc.mode_block("listen") and "ADVICE" in sc.mode_block("advice") and "next steps" in sc.mode_block("fix")
    assert all("follow that instead" in sc.mode_block(mode) for mode in sc.KEYS)
    assert sc.mode_block(None) == "" and sc.mode_block("other") == ""


def test_the_pick_outranks_the_guessed_need_in_the_reply_check():
    advice_reply = "You could try telling her plainly what you need, and maybe suggest a call tomorrow."
    assert sc.adjust_issue("venting_advice", "advice", advice_reply, False) is None
    assert sc.adjust_issue("venting_advice", "fix", advice_reply, False) is None
    assert sc.adjust_issue(None, "listen", advice_reply, False) == "venting_advice"
    assert sc.adjust_issue(None, "listen", "That sounds heavy. I'm with you in it.", False) is None
    assert sc.adjust_issue("venting_advice", None, advice_reply, False) == "venting_advice"
    assert sc.adjust_issue(None, "listen", advice_reply, True) is None                     # a safety plan has its own rules
    assert sc.adjust_issue("thin_comfort", "advice", advice_reply, False) == "thin_comfort"


def test_the_card_has_three_keyed_options_in_message_mode():
    card = sc.card()
    assert card["mode"] == "message" and card["question"] == sc.QUESTION and len(card["choice_id"]) == 32
    assert [o["key"] for o in card["options"]] == ["listen", "advice", "fix"] and not any(o["recommended"] for o in card["options"])
    assert "do not end with a question" in sc.OFFER_NOTE
    assert sc.counts_for_pick("fix") == {"support_choice_fix": 1} and sc.counts_for_pick("nope") == {}


def test_the_marks_survive_saving_and_loading_the_conversation():
    saved = app_utils._serialize_messages([human("Just listen", mode="listen"), ai("reply", card=True)])
    assert saved[0][sc.MODE_MARK] == "listen" and saved[1][sc.CARD_MARK] is True
    loaded = app_utils._deserialize_messages(saved)
    assert sc.current_mode(loaded, NOW) == "listen" and sc.card_offered_recently(loaded, NOW)


def test_the_reply_offers_the_card_applies_the_mode_and_counts_both():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "support_choice: Optional[str] = None" in app
    assert "support_pick = request.support_choice if request.support_choice in support_choice_utils.KEYS else None" in app
    assert "if support_mode and risk_level == RISK_NONE:\n                prompt = prompt + support_choice_utils.mode_block(support_mode)" in app
    assert "page_can_show=bool(request.choice_cards)" in app and "safety_active=risk_level != RISK_NONE" in app
    assert "yield f\"data: {json.dumps({'event': 'choice_card', **support_choice_utils.card()})}" in app
    assert "ai_message.additional_kwargs[support_choice_utils.CARD_MARK] = True" in app
    assert "emotional_issue = support_choice_utils.adjust_issue(emotional_issue, support_mode, checked_reply, safety_plan.active)" in app
    assert {"support_choice_offered", "support_choice_listen", "support_choice_advice", "support_choice_fix", "support_mode_applied"} <= self_counters.KEYS
