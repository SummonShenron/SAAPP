"""Sonic having something of its own to say in personal talk, and reading the moment better (the 2026-11-27 entityhood work):
a view when asked (view_check), stances (stances), remembering its views (own_views), bringing up its reading unasked (volunteer), what lands with a person (what_lands), what a message is
doing (message_doing), the capped stronger voice (top_tier), and the changes to the low moves and the "just listen" mode. Every message below is made up."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.components import sonic_stances
from backend.tests.fake_runner_db import FakeDB
from backend.utils import (emotion_checks, low_moves, memory_erase, message_doing, own_views, people_notes, placement, reply_stamp, revision_reasons, self_counters, stances, support_choice, top_tier,
                           view_check, volunteer, what_lands)
from backend.utils.time_utils import SENT_AT_KEY

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
NOW = datetime.now(timezone.utc)


def _msg(kind, text="ok", hours_ago=0.0, **marks):
    return SimpleNamespace(type=kind, content=text, additional_kwargs={SENT_AT_KEY: (NOW - timedelta(hours=hours_ago)).isoformat(), **marks})


def _mood(valence="low", need="venting", lift=False, read=True):
    return {"valence": valence, "need": need, "intensity": 0.8, "read_this_turn": read, "lift_this_turn": lift}


# ---- a view when asked ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", ["what do you think I should do about it", "am I overreacting?", "should I text her back tonight", "be honest, was I wrong to say that", "is it weird that I still care",
                                     "what’s your take on this"])
def test_a_message_that_asks_for_its_view_is_read_as_one(message):
    assert view_check.asks_for_view(message) is True


@pytest.mark.parametrize("message", ["she cancelled on me again", "today was long", "what do you think about being an AI", "do you think you'll remember this tomorrow", ""])
def test_a_message_that_does_not_ask_for_a_view_of_their_situation_is_not(message):
    assert view_check.asks_for_view(message) is False


def test_a_reply_that_opens_with_feelings_or_hands_the_question_back_is_sent_back_and_one_that_answers_is_not():
    asked = "am I overreacting about her cancelling?"
    assert view_check.issue(asked, "That is a lot to sit with. It makes sense that it stings. What does your gut say?") == "no_view"
    assert view_check.issue(asked, "I don't think you are. Three cancellations in a month is a pattern, not a slip.") is None
    assert view_check.issue(asked, "I think it is fair to be annoyed, but it's really up to you how to read it.") == "no_view"          # states one, then hands it back
    late = "That is a lot. She has done this before. You told me about March. And the week after. I think you are right."
    assert view_check.issue(asked, late) == "no_view"                                # the view has to be in the opening, not the fifth sentence
    assert view_check.issue("she cancelled again", "That is a lot to sit with.") is None      # nobody asked
    assert view_check.issue(asked, "") is None


def test_the_view_sentence_is_the_first_that_states_one_and_is_cut_short():
    assert view_check.view_sentence("Thank you for telling me. I'd wait a day before answering. It gives you room.") == "I'd wait a day before answering."
    assert view_check.view_sentence("Thank you for telling me. That is hard.") is None
    assert len(view_check.view_sentence("I think " + "really " * 80 + "so.")) == view_check.MAX_VIEW_CHARS
    assert view_check.states_view("No, that was not out of line.") and not view_check.states_view("It sounds like a hard week.")


def test_the_block_says_to_answer_first_and_the_reach_dial_decides_how_plainly_it_disagrees():
    assert "Answer that first" in view_check.build_block("default") and "say so once, plainly" in view_check.build_block("default")
    assert "say so once, gently" in view_check.build_block("low")
    assert "see it differently" not in view_check.build_block("none") and "Answer that first" in view_check.build_block("none")       # they asked, so they still get the view


def test_the_rewrite_reason_exists_on_every_end():
    prompt, reason = emotion_checks.build_emotional_revision_prompt("P", "draft", "no_view")
    assert prompt.startswith("P") and "they asked what you think" in reason
    assert "no_view" in revision_reasons.REASONS and revision_reasons.reason_for("no_view") == "no_view" and "revised_reason_no_view" in self_counters.KEYS
    assert "no_view:" in (ROOT / "local" / "src" / "components" / "traceFacts.ts").read_text(encoding="utf-8")
    assert "view_issue = None if (safety_plan.active or not view_asked) else view_check.issue(" in APP and "revision_tag = view_issue" in APP
    assert 'if view_asked and emotional_issue == "venting_advice":\n                    emotional_issue = None' in APP


# ---- stances --------------------------------------------------------------------------------------------------------------------------------

def test_the_shipped_stances_are_well_formed_views_and_only_an_approved_one_is_live():
    assert stances.check_registry() == [] and len(sonic_stances.STANCES) >= 10
    assert set(sonic_stances.APPROVED_IDS) <= {entry["id"] for entry in sonic_stances.STANCES} and len(set(sonic_stances.APPROVED_IDS)) == len(sonic_stances.APPROVED_IDS)
    assert [entry["id"] for entry in stances.approved()] == [entry["id"] for entry in sonic_stances.STANCES if entry["id"] in sonic_stances.APPROVED_IDS]
    assert stances.approved(approved_ids=()) == []                              # with nothing approved, nothing is live
    draft = {"id": "stance_a_new_draft", "triggers": ["x"], "line": "I think a draft is a draft until someone approves it."}
    assert draft["id"] not in [entry["id"] for entry in stances.approved(sonic_stances.STANCES + [draft])]       # a new entry stays a draft
    from backend.utils.identity_checks import identity_reply_issue
    for entry in sonic_stances.STANCES:
        assert identity_reply_issue(entry["line"]) is None, entry["id"]            # a view, never a claimed feeling or a past of its own


def test_a_stance_is_chosen_only_when_a_view_is_wanted_by_a_phrase_or_by_meaning():
    pool = stances.approved(approved_ids=("stance_rest_is_part_of_the_work", "stance_anger_is_information"))
    assert [e["id"] for e in pool] == ["stance_rest_is_part_of_the_work", "stance_anger_is_information"]
    assert stances.select("I took a day off and feel like I wasted the day", wanted=True, entries=pool)["id"] == "stance_rest_is_part_of_the_work"
    assert stances.select("I took a day off and feel like I wasted the day", wanted=False, entries=pool) is None
    assert stances.select("the weather is nice", wanted=True, entries=pool) is None
    near = stances.select("something with no phrase in it", wanted=True, entries=pool, vector=[1.0, 0.0], vectors={"stance_anger_is_information": [1.0, 0.0], "stance_rest_is_part_of_the_work": [0.0, 1.0]})
    assert near["id"] == "stance_anger_is_information" and near["match"] == "meaning"
    assert stances.select("something with no phrase in it", wanted=True, entries=pool, vector=[1.0, 0.0], vectors={"stance_anger_is_information": [0.0, 1.0]}) is None
    assert stances.select("I wasted the day", wanted=True, entries=[]) is None


def test_the_registry_check_refuses_a_rule_a_feeling_a_bad_id_and_an_approval_with_no_entry():
    good = {"id": "stance_made_up_one", "triggers": ["x"], "line": "I think a made-up thing is fine because it is made up."}
    assert stances.check_registry([good], ()) == []
    assert any("tells the person what to do" in p for p in stances.check_registry([{**good, "line": "I think you should rest more often than you do."}], ()))
    assert any("must open as a view" in p for p in stances.check_registry([{**good, "line": "Rest matters a great deal to everyone."}], ()))
    assert any("claims a feeling" in p for p in stances.check_registry([{**good, "line": "I think rest matters, and I remember learning that."}], ()))
    assert any("the id must be" in p for p in stances.check_registry([{**good, "id": "rest"}], ()))
    assert any("used twice" in p for p in stances.check_registry([good, good], ()))
    assert any("no such stance" in p for p in stances.check_registry([good], ("stance_missing",)))
    assert any("trigger" in p for p in stances.check_registry([{**good, "triggers": []}], ()))


def test_a_stance_is_offered_as_its_own_view_and_counted_as_used_only_when_the_reply_shares_its_words():
    entry = {"id": "stance_rest_is_part_of_the_work", "line": "I think rest is part of the work and not time taken from it; a plan that only holds if you never stop is a plan with a flaw in it."}
    block = stances.build_block(entry)
    assert entry["line"] in block and "A VIEW YOU HOLD" in block and "not a rule for them" in block and stances.build_block(None) == ""
    assert stances.used("I think the rest was part of the work, not a break from it.", entry) is True
    assert stances.used("That sounds like a long day.", entry) is False and stances.used("anything", None) is False
    assert stances.is_stance(entry) and not stances.is_stance({"id": "take_x"})


# ---- remembering what it said it thinks ------------------------------------------------------------------------------------------------------

def test_a_stated_view_is_kept_for_the_person_once_and_only_the_sentence():
    db = FakeDB()
    reply = "Thank you for telling me. I think waiting until Friday before answering the landlord is reasonable. He has been slow before."
    assert own_views.note(db, "sam", "should I answer the landlord about the deposit now", reply, NOW) is True
    kept = db[own_views.COLLECTION].find({"username": "sam"})
    assert len(kept) == 1 and kept[0]["text"] == "I think waiting until Friday before answering the landlord is reasonable."
    assert "deposit" in kept[0]["words"] and "landlord" in kept[0]["words"] and "should I answer" not in str(kept[0])       # words as a bag, never their message
    assert own_views.note(db, "sam", "should I answer the landlord about the deposit now", reply, NOW) is False and len(db[own_views.COLLECTION].find({})) == 1
    assert own_views.note(db, "sam", "today was long", "That sounds like a long day.", NOW) is False                           # no view, nothing kept
    assert own_views.note(None, "sam", "x", reply, NOW) is False and own_views.note(db, "", "x", reply, NOW) is False


def test_only_the_newest_views_are_kept_past_the_limit(monkeypatch):
    monkeypatch.setattr(own_views, "MAX_PER_PERSON", 3)
    db = FakeDB()
    for n in range(5):
        own_views.note(db, "sam", f"should I move the meeting number {n} for the committee", f"I think moving meeting number {n} for the committee is fine.", NOW - timedelta(hours=10 - n))
    texts = sorted(doc["text"] for doc in db[own_views.COLLECTION].find({"username": "sam"}))
    assert texts == [f"I think moving meeting number {n} for the committee is fine." for n in (2, 3, 4)]


def test_a_kept_view_comes_back_when_the_message_shares_enough_words_and_it_is_not_from_this_sitting():
    db = FakeDB()
    own_views.note(db, "sam", "should I answer the landlord about the deposit now", "I think waiting until Friday before answering the landlord is reasonable.", NOW - timedelta(days=3))
    found = own_views.recall(db, "sam", "the landlord wrote again about the deposit, should I answer before Friday", NOW)
    assert found and found["text"].startswith("I think waiting until Friday")
    assert own_views.recall(db, "sam", "what should I cook tonight", NOW) is None
    assert own_views.recall(db, "someone_else", "the landlord wrote again about the deposit, should I answer before Friday", NOW) is None       # one person's, never another's
    own_views.note(db, "kim", "should I answer the landlord about the deposit now", "I think waiting until Friday before answering the landlord is reasonable.", NOW - timedelta(hours=1))
    assert own_views.recall(db, "kim", "the landlord wrote again about the deposit, should I answer before Friday", NOW) is None                # said an hour ago: this sitting
    block = own_views.build_block(found)
    assert found["text"] in block and "stand by it" in block and "I'd put it differently now" in block and (NOW - timedelta(days=3)).strftime("%B") in block
    assert own_views.build_block(None) == ""


def test_the_kept_views_and_the_numbers_are_erased_with_everything_else_of_theirs():
    assert own_views.COLLECTION in memory_erase.ERASED_COLLECTIONS and what_lands.COLLECTION in memory_erase.ERASED_COLLECTIONS


# ---- bringing up its own reading unasked -----------------------------------------------------------------------------------------------------

OPEN = dict(source_type="conversational", risk_active=False, energy_tier="open", closing=False, proactive_taken=False, offers_allowed=True, reach="default")


def test_it_may_only_at_an_open_moment_and_at_most_once_a_day():
    quiet = [_msg("human", "made it home"), _msg("ai", "good", hours_ago=30)]
    assert volunteer.may("made it home, long day", quiet, now=NOW, **OPEN) is True
    for change in ({"source_type": "tool_output"}, {"risk_active": True}, {"energy_tier": "subdued"}, {"closing": True}, {"proactive_taken": True}, {"offers_allowed": False}, {"reach": "none"}):
        assert volunteer.may("made it home, long day", quiet, now=NOW, **{**OPEN, **change}) is False, change
    assert volunteer.may("can you look at this?", quiet, now=NOW, **OPEN) is False              # they asked something
    assert volunteer.may("word " * 31, quiet, now=NOW, **OPEN) is False                          # a long message is not a pause
    assert volunteer.may("", quiet, now=NOW, **OPEN) is False
    recent = [_msg("ai", "x", hours_ago=19, volunteered="take_a")]
    assert volunteer.may("made it home", recent, now=NOW, **OPEN) is False
    assert volunteer.may("made it home", [_msg("ai", "x", hours_ago=21, volunteered="take_a")], now=NOW, **OPEN) is True
    undated = SimpleNamespace(type="ai", content="x", additional_kwargs={"volunteered": "take_a"})
    assert volunteer.recently([undated], NOW) is True                                            # an unknown time is never guessed to be old


def test_a_take_the_conversation_has_heard_is_not_said_again_and_the_words_are_placed_last():
    takes = [{"id": "take_a", "line": "I read a piece on a."}, {"id": "take_b", "line": "I read a piece on b."}]
    heard = [_msg("ai", "x", hours_ago=50, volunteered="take_a")]
    assert volunteer.pick(takes, heard)["id"] == "take_b" and volunteer.pick(takes, [])["id"] == "take_a"
    assert volunteer.pick(takes, heard + [_msg("ai", "y", hours_ago=25, volunteered="take_b")]) is None and volunteer.pick([], []) is None
    words = volunteer.spoken(takes[1])
    assert words == "Something from my own reading, apart from all this: I read a piece on b."
    assert placement.at_end("First part.\n\nSecond part.", words) == "First part.\n\nSecond part.\n\n" + words
    tagged = placement.at_end("Only part.\n\n<<<FOLLOW_UP: tell me more>>>", words)
    assert tagged == "Only part.\n\n" + words + "\n\n<<<FOLLOW_UP: tell me more>>>"
    assert placement.at_end("Has it. " + words, words) == "Has it. " + words and placement.at_end("", words) == "" and placement.at_end("x", "") == "x"


# ---- what lands -----------------------------------------------------------------------------------------------------------------------------

def test_a_reply_is_marked_with_what_it_did_and_the_next_message_says_how_it_went():
    assert what_lands.kinds_of(low_move="push_back", view=True, question=True) == ["push_back", "view", "question"]
    assert what_lands.kinds_of(low_move="not_a_kind") == [] and what_lands.kinds_of(story=True, record=True, stance=True) == ["stance", "story", "record"]
    marked = [_msg("ai", "x", move_kinds=["read", "question", "nonsense"]), _msg("human", "y")]
    assert what_lands.last_reply_kinds(marked) == ["read", "question"] and what_lands.last_reply_kinds([_msg("human", "y")]) == []
    assert what_lands.outcome({"lift_this_turn": True}, "ok", False) == "lift"
    assert what_lands.outcome({}, "one two three four five six seven eight", False) == "engaged"
    assert what_lands.outcome({}, "one two three four five six seven eight", True) == "flat" and what_lands.outcome({}, "ok thanks", False) == "flat"


def test_the_numbers_are_kept_per_person_and_order_the_kinds_once_there_is_enough_to_go_on():
    db = FakeDB()
    for _ in range(4):
        assert what_lands.record(db, "sam", ["read", "question"], "lift") is True
        what_lands.record(db, "sam", ["push_back"], "flat")
    what_lands.record(db, "sam", ["pattern"], "engaged")
    stats = what_lands.load(db, "sam")
    assert stats["read"] == {"tries": 4, "lifts": 4, "engaged": 4} and stats["push_back"] == {"tries": 4} and stats["pattern"] == {"tries": 1, "engaged": 1}
    assert what_lands.load(db, "someone_else") == {} and what_lands.load(None, "sam") == {}
    assert what_lands.rate(stats["read"]) == 1.0 and what_lands.rate(stats["push_back"]) == 0.0 and what_lands.rate(stats["pattern"]) is None      # one try says nothing yet
    assert what_lands.order(stats, low_moves.KINDS) == ["read", "pattern", "company", "push_back"]      # helped first, then the untried in their own order, the never-helped last
    assert what_lands.order({}, low_moves.KINDS) == list(low_moves.KINDS)
    assert what_lands.record(db, "sam", ["nonsense"], "lift") is False and what_lands.record(db, "sam", ["read"], "nonsense") is False and what_lands.record(db, "", ["read"], "lift") is False


# ---- what a message is doing ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message, expected", [
    ("lol of course she cancelled again", "joking"),
    ("ok I'm going to bed, long day", "winding_down"),
    ("did I do the right thing", None),
    ("did I do the right thing?", "asking"),
    ("she finally texted back", "news"),
    ("I just got the email and they said no", "news"),
    ("everything feels heavy", None),
    ("today was long and grey", None),                    # a time word with nothing that happened is not news
    ("she said it would be fine", None),                  # nor is something said with no "just now" to it
    ("", None),
])
def test_what_a_message_is_doing_is_read_from_its_own_words(message, expected):
    assert message_doing.kind(message) == expected


def test_the_first_reading_that_applies_wins_and_each_gets_one_line():
    assert message_doing.kind("haha should I even bother?") == "joking"                       # a joke with a question in it is met as a joke
    assert message_doing.kind("going to bed, did she text you?") == "asking"                  # a question keeps them here: not winding down
    assert message_doing.kind("going to bed " + "word " * 40) is None                         # a long message is not a goodnight
    assert "Answer it in your first sentence" in message_doing.build_block("asking") and "ask nothing" in message_doing.build_block("winding_down")
    assert "Meet the joke first" in message_doing.build_block("joking") and "Respond to the news itself first" in message_doing.build_block("news")
    assert message_doing.build_block(None) == "" and message_doing.build_block("nonsense") == ""
    assert self_counters._GROUPS["doing"] == message_doing.KINDS and "doing_news" in self_counters.KEYS


# ---- the capped stronger voice ---------------------------------------------------------------------------------------------------------------

def test_the_stronger_voice_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("REPLY_TOP_TIER", raising=False)
    assert top_tier.enabled() is False and top_tier.wanted("emotional", 0.9, risk_active=False, used=0, guest=False) is False
    monkeypatch.setenv("REPLY_TOP_TIER", "on")
    assert top_tier.enabled() is True and top_tier.wanted("emotional", 0.9, risk_active=False, used=0, guest=False) is True


def test_when_on_it_is_only_for_a_strong_feeling_under_the_daily_cap_and_never_in_a_safety_context():
    yes = dict(risk_active=False, used=0, guest=False, on=True, cap=2)
    assert top_tier.wanted("emotional", 0.6, **yes) is True and top_tier.wanted("checkin", 0.7, **yes) is True
    assert top_tier.wanted("emotional", 0.59, **yes) is False and top_tier.wanted("companion", 0.9, **yes) is False and top_tier.wanted("safety", 0.9, **yes) is False
    assert top_tier.wanted("emotional", 0.9, **{**yes, "risk_active": True}) is False and top_tier.wanted("emotional", 0.9, **{**yes, "guest": True}) is False
    assert top_tier.wanted("emotional", 0.9, **{**yes, "used": 2}) is False and top_tier.wanted("emotional", 0.9, **{**yes, "used": 1}) is True
    assert top_tier.wanted("emotional", 0.9, **{**yes, "on": False}) is False


def test_the_days_use_is_counted_from_the_saved_stamps_and_the_stamp_knows_the_voice():
    stamp = reply_stamp.build(top_tier.TIER, "haiku_setting", "haiku")
    assert stamp["tier"] == "sonnet" and stamp["label"] == "sonnet" and reply_stamp.clean(stamp)["label"] == "sonnet"
    other = reply_stamp.build("haiku", "haiku_setting", "haiku")
    messages = [_msg("ai", "a", hours_ago=2, voice=stamp), _msg("ai", "b", hours_ago=23, voice=stamp), _msg("ai", "c", hours_ago=25, voice=stamp), _msg("ai", "d", hours_ago=1, voice=other),
                _msg("human", "e", hours_ago=1, voice=stamp)]
    assert top_tier.used_today(messages, NOW) == 2
    assert top_tier.used_today([SimpleNamespace(type="ai", content="x", additional_kwargs={"voice": stamp})], NOW) == 1      # no time on it: counted


# ---- the low moves and "just listen" ---------------------------------------------------------------------------------------------------------

LONG_VENT = "she said she would call after work and then nothing all evening and I just sat there waiting"


def _choose(message, mood=None, **extra):
    return low_moves.choose(message, mood or _mood(), facts=extra.pop("facts", []), interests=extra.pop("interests", ["learning guitar"]), recent_messages=extra.pop("recent", []),
                            risk_active=False, closing=False, **extra)


def test_its_own_read_is_a_move_for_someone_venting_at_length_and_not_while_just_listening_or_with_the_dial_at_none():
    assert _choose(LONG_VENT) == {"kind": "read", "items": []}
    assert _choose("today just sucked") is None                                              # too little to have a read of
    assert _choose(LONG_VENT, support_mode="listen") is None and _choose(LONG_VENT, reach="none") is None
    assert _choose(LONG_VENT, mood=_mood(need="reassurance")) is None and _choose(LONG_VENT, mood=_mood(need="solving")) is None
    assert _choose(LONG_VENT, recent=[SimpleNamespace(type="ai", content="x", additional_kwargs={low_moves.MOVE_MARK: "read"})]) is None      # not twice in a row
    block = low_moves.build_block({"kind": "read", "items": []})
    assert "YOUR OWN READ" in block and "It is not advice" in block and "{items}" not in block
    assert "low_move_read" in self_counters.KEYS


def test_company_is_never_offered_while_just_listening_and_the_persons_own_order_wins():
    wants_out = _mood(need="distraction")
    assert _choose("today just sucked", mood=wants_out)["kind"] == "company" and _choose("today just sucked", mood=wants_out, support_mode="listen") is None
    pattern_fact = {"fact": "Tends to wait by the phone all evening after a promised call", "source": "pattern", "active": True}
    assert _choose(LONG_VENT, facts=[pattern_fact])["kind"] == "pattern"                     # the default order: a pattern before a read
    assert _choose(LONG_VENT, facts=[pattern_fact], prefer=["read", "pattern"])["kind"] == "read"
    assert _choose(LONG_VENT, facts=[pattern_fact], prefer=["company", "push_back"])["kind"] == "pattern"       # a preferred kind that is not available changes nothing
    assert set(low_moves.available(LONG_VENT, _mood(), facts=[pattern_fact], interests=[], recent_messages=[], risk_active=False, closing=False)) == {"pattern", "read"}


@pytest.mark.parametrize("message", ["I'm the problem, clearly", "im just too much for people", "no one could love me", "everyone leaves eventually", "I deserve this", "I never get anything right"])
def test_more_ways_of_being_hard_on_themselves_are_read_as_that(message):
    assert low_moves.harsh_about_self(message) is True
    assert low_moves.harsh_about_self("she said I was too much trouble to visit") is False


def test_just_listen_ends_after_a_stretch_and_steps_aside_when_they_ask_what_it_thinks():
    pick = _msg("human", "Just listen", hours_ago=1, **{support_choice.MODE_MARK: "listen"})
    during = [pick] + [_msg("human", f"more {n}") for n in range(support_choice.LISTEN_TURNS)]
    assert support_choice.current_mode(during, NOW) == "listen"
    assert support_choice.current_mode(during + [_msg("human", "and one more")], NOW) is None
    assert support_choice.current_mode([pick, _msg("ai", "ok"), _msg("human", "am I overreacting?")], NOW) is None
    assert support_choice.current_mode([pick, _msg("human", "am I overreacting?"), _msg("ai", "no"), _msg("human", "ok. she also said")], NOW) == "listen"      # only for the message that asks
    advice = _msg("human", "Advice", hours_ago=1, **{support_choice.MODE_MARK: "advice"})
    assert support_choice.current_mode([advice] + [_msg("human", f"more {n}") for n in range(30)], NOW) == "advice"            # only "just listen" is cut short
    assert support_choice.current_mode([_msg("human", "Just listen", hours_ago=7, **{support_choice.MODE_MARK: "listen"})], NOW) is None
    assert "Listening is not saying nothing" in support_choice.mode_block("listen") and "No advice" in support_choice.mode_block("listen")


# ---- counting and wiring ---------------------------------------------------------------------------------------------------------------------

def test_a_people_note_counts_as_used_only_when_the_reply_shares_its_words():
    note = {"id": "made_up", "pattern": "People often replay the conversation for days afterwards", "helps": "Naming the one sentence they keep returning to"}
    assert people_notes.used("It sounds like one sentence keeps returning: the one about naming the budget.", [note]) is True
    assert people_notes.used("That is a long week.", [note]) is False and people_notes.used("anything", []) is False


def test_every_new_counter_is_registered_and_counted_in_the_app():
    keys = {"view_asked", "view_stated", "view_handed_back", "own_view_kept", "own_view_recalled", "stance_offered", "stance_used", "volunteer_placed", "lands_recorded", "lands_lift", "lands_ordered",
            "reply_top_tier_used", "people_notes_used"}
    assert keys <= self_counters.KEYS
    for key in keys:
        assert f'tally(turn_counts, "{key}")' in APP, key
    assert 'tally(turn_counts, "doing", _doing)' in APP


def test_the_app_keeps_the_new_things_out_of_safety_turns_and_counts_them_as_bringing_something_up():
    assert "view_wanted = source_type == \"conversational\" and risk_level == RISK_NONE and not is_closing_message(question)" in APP
    assert "message_doing.kind(_their_words) if (view_wanted and emotional_context and not safety_plan.active) else None" in APP
    assert "if (not placed_own and not safety_plan.active and not is_guest_username(username) and get_db() is not None and volunteer.may(" in APP
    assert "or low_move or step_back_entry or stance_entry or volunteer_take or view_asked or llm_turn.experience_item or llm_turn.shape_offered" in APP
    assert 'energy_tier="open" if own_tier == "open" else energy_tier(' in APP
    assert "support_mode=support_choice_utils.current_mode(chat_sessions[history_key]), reach=reach_level.level, prefer=_move_order," in APP
    assert "top_tier.wanted(reply_stakes, effective_intensity(" in APP and "risk_active=risk_level != RISK_NONE,\n                               used=top_tier.used_today(" in APP
