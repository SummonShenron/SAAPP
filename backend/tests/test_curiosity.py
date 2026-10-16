"""The one open question at a lull (backend/utils/curiosity.py): every limit that keeps it from becoming engagement bait lives in code, so each is pinned here."""
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.evals import character_eval as ce
from backend.utils import curiosity
from backend.utils.self_counters import KEYS, summarize_counters

NOW = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)


def chat(*turns):
    """A conversation as the app holds it. Each turn is ("u"|"a", text[, extra kwargs])."""
    out = []
    for kind, text, *rest in turns:
        message = HumanMessage(content=text) if kind == "u" else AIMessage(content=text)
        if rest:
            message.additional_kwargs.update(rest[0])
        out.append(message)
    return out


QUIET = chat(
    ("u", "why does my docker build rerun npm install"), ("a", "Copy the lockfile before the source so that layer stays cached."),
    ("u", "ah ok that worked"), ("a", "Good, it should be much faster now."),
    ("u", "yeah, i see"),
)


def allowed(messages=QUIET, **overrides):
    args = dict(source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False)
    args.update(overrides)
    return curiosity.curiosity_allowed(messages[-1].content, messages, **args)


# ---- what counts as a lull ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["ok", "yeah, i see", "hmm interesting", "huh", "yeah i guess", "oh okay", "true, fair enough", "idk maybe"])
def test_bare_acknowledgements_are_a_lull(text):
    assert curiosity.is_lull(text)


@pytest.mark.parametrize("text", [
    "thanks", "thanks, that's all i needed", "goodnight", "makes sense", "perfect",   # a closing is never a lull
    "ok what about the other file?", "can you also fix the css", "ok so i tried it and it broke", "yeah 3 of them", "see https://x.dev",
    "", "   ", "ok " * 10,
])
def test_anything_with_content_a_question_or_a_sign_off_is_not_a_lull(text):
    assert not curiosity.is_lull(text)


# ---- the free gates ---------------------------------------------------------------------------------------------------------------

def test_a_lull_after_a_real_exchange_is_allowed():
    assert allowed()


@pytest.mark.parametrize("override", [
    {"source_type": "tool_output"}, {"source_type": "web"}, {"source_type": "knowledge_base"},
    {"risk_active": True}, {"energy_tier": "gentle"}, {"proactive_taken": True},
])
def test_other_routes_a_safety_context_a_held_down_mood_or_another_proactive_item_each_block_it(override):
    assert not allowed(**override)


def test_a_conversation_that_has_barely_started_is_not_a_lull():
    short = chat(("u", "hi"), ("a", "Hello."), ("u", "ok"))
    assert not allowed(short)


def test_once_means_once_per_conversation_however_far_back_it_was():
    asked = chat(("u", "a"), ("a", "b"), ("u", "c"), ("a", "What else have you got going on?", {curiosity.CURIOSITY_MARK: True}))
    more = asked + chat(("u", "not much"), ("a", "Fair enough."), ("u", "hm"), ("a", "Yeah."), ("u", "ok"), ("a", "ok"), ("u", "yeah, i see"))
    assert curiosity.already_offered(more) and not allowed(more)


def test_a_short_answer_to_a_question_sonic_just_asked_is_an_answer_not_a_lull():
    asked = QUIET[:-1] + chat(("u", "yeah, i see"))
    asked[-2] = AIMessage(content="Good, it should be much faster now. Did the cache also fix CI?")
    assert not allowed(asked)


def test_a_question_inside_the_follow_up_suggestion_tag_does_not_count_as_sonic_asking():
    tagged = QUIET[:-1] + chat(("u", "yeah, i see"))
    tagged[-2] = AIMessage(content="Good, it should be much faster now.\n<<<FOLLOW_UP: Can you show me the Dockerfile? >>>")
    assert allowed(tagged)


def test_a_closing_message_never_gets_the_question():
    closing = QUIET[:-1] + chat(("u", "thanks, that's all i needed"))
    assert not allowed(closing)


def test_the_kill_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("CURIOSITY_OFFERS", "false")
    assert not allowed()


# ---- the per-person gap -------------------------------------------------------------------------------------------------------------

def test_nobody_is_asked_twice_inside_the_gap():
    assert curiosity.gap_clear(None, NOW)
    assert not curiosity.gap_clear(NOW - timedelta(hours=curiosity.MIN_HOURS_BETWEEN - 1), NOW)
    assert curiosity.gap_clear(NOW - timedelta(hours=curiosity.MIN_HOURS_BETWEEN + 1), NOW)
    assert curiosity.MIN_HOURS_BETWEEN > 0


class FakeDb:
    def __init__(self):
        self.docs = {}

    def __getitem__(self, _name):
        return self

    def find_one(self, query, projection=None):
        return self.docs.get(query["username"])

    def update_one(self, query, update, upsert=False):
        doc = self.docs.setdefault(query["username"], {})
        doc.update(update.get("$set", {}))
        for key, value in update.get("$addToSet", {}).items():
            current = doc.setdefault(key, [])
            if value not in current:
                current.append(value)


def test_the_last_offer_is_stored_on_the_settings_document_and_read_back(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    assert curiosity.last_offer_at("sam") is None
    curiosity.record_offer("sam", NOW)
    assert curiosity.last_offer_at("sam") == NOW and curiosity.last_offer_at("lee") is None


def test_a_database_failure_never_raises_and_never_blocks_for_good(monkeypatch):
    def boom():
        raise RuntimeError("down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    assert curiosity.last_offer_at("sam") is None
    curiosity.record_offer("sam", NOW)  # must not raise


# ---- what it recorded, and what the invitation says ---------------------------------------------------------------------------------

def test_it_counts_only_when_a_question_was_really_asked():
    assert curiosity.asked_a_question("Glad that's sorted. What else have you got going on this week?")
    assert not curiosity.asked_a_question("Glad that's sorted.")
    assert not curiosity.asked_a_question("Glad that's sorted.\n<<<FOLLOW_UP: Want to see the lockfile? >>>")


def test_a_reply_that_picks_it_up_is_taken_and_a_brush_off_or_a_goodbye_is_not():
    assert curiosity.taken("honestly just trying to finish this migration this week")
    assert not curiosity.taken("nah")
    assert not curiosity.taken("nothing much")          # two words: a brush-off
    assert not curiosity.taken("thanks, that's all")
    assert not curiosity.taken("yeah, i see")


def test_the_invitation_asks_for_one_light_question_and_forbids_the_engagement_shapes():
    block = curiosity.build_curiosity_block()
    for needle in ("ONE", "<<<FOLLOW_UP>>>", "wrap up", "not a probe", "Do not say you are curious"):
        assert needle in block


# ---- counters ---------------------------------------------------------------------------------------------------------------------

def test_the_counters_are_in_the_closed_key_list_and_the_taken_rate_is_per_offer():
    assert {"curiosity_offered", "curiosity_taken"} <= KEYS
    day = NOW.strftime("%Y-%m-%d")
    summary = summarize_counters([{"day": day, "counts": {"turns_total": 100, "curiosity_offered": 4, "curiosity_taken": 1}}], NOW)
    rates = summary["current"]["rates"] if "current" in summary else summary["rates"]
    assert rates["curiosity_offered"] == 0.04 and rates["curiosity_taken"] == 0.25


# ---- the character eval pieces ------------------------------------------------------------------------------------------------------

def test_the_scorer_wants_exactly_one_question_and_no_claimed_curiosity():
    assert ce.score_open_question("Glad that's sorted. What else have you got going on this week?") == (True, "")
    assert not ce.score_open_question("Glad that's sorted.")[0]
    assert not ce.score_open_question("What are you building? And what else is new?")[0]
    assert not ce.score_open_question("I'm curious, what else is going on?")[0]
    assert not ce.score_open_question("Good.\n<<<FOLLOW_UP: What else is new? >>>")[0]


def test_the_eval_scenarios_are_registered_and_only_the_lull_one_carries_the_invitation():
    by_id = {s.id: s for s in ce.SCENARIOS}
    lull, answered = by_id["lull_open_question"], by_id["lull_question_answered_wraps_up"]
    assert lull.curiosity and not answered.curiosity
    assert "OPEN QUESTION" in ce.build_prompt(lull) and "OPEN QUESTION" not in ce.build_prompt(answered)


# ---- the wiring in the chat endpoint ------------------------------------------------------------------------------------------------

def test_the_chat_endpoint_invites_it_only_on_conversational_non_guest_turns_and_marks_it_on_what_was_asked():
    source = open("app.py", encoding="utf-8").read()
    gate = source[source.index("curiosity_invited = False"):source.index("# A clearly closing message")]
    assert 'source_type == "conversational"' in gate and "not is_guest_username(username)" in gate
    assert "callback_offered" in gate and "goal_nudge_context" in gate          # shares the turn's one proactive slot
    assert gate.index("curiosity_allowed(") < gate.index("gap_clear(")           # the free checks run before the database read
    marking = source[source.index("if curiosity_invited and"):]
    assert marking.index("asked_a_question(full_response)") < marking.index("CURIOSITY_MARK] = True") < marking.index("record_offer")


# ---- a conversation is a stretch of a chat, not the whole saved thread ---------------------------------------------------------------------

def stamped(turns, start, minutes):
    """The same turns as chat(), each stamped `minutes[i]` after `start`."""
    from backend.utils.time_utils import stamp

    out = chat(*turns)
    for message, offset in zip(out, minutes):
        stamp(message, start + timedelta(minutes=offset))
    return out


def test_a_pause_of_hours_starts_a_new_conversation_inside_the_same_saved_chat():
    base = [("u", "a"), ("a", "b"), ("u", "c"), ("a", "What else is going on?", {curiosity.CURIOSITY_MARK: True})]
    later = [("u", "morning, quick one about my config"), ("a", "Sure, paste it."), ("u", "here it is"), ("a", "That looks right."),
             ("u", "ok that works"), ("a", "Good."), ("u", "yeah, i see")]
    same_day = stamped(base + later, NOW, [0, 1, 2, 3, 30, 31, 32, 33, 34, 35, 36])
    next_day = stamped(base + later, NOW, [0, 1, 2, 3, 60 * 24, 60 * 24 + 1, 60 * 24 + 2, 60 * 24 + 3, 60 * 24 + 4, 60 * 24 + 5, 60 * 24 + 6])
    assert curiosity.already_offered(same_day) and not allowed(same_day)       # still the same conversation: spent
    assert not curiosity.already_offered(next_day) and allowed(next_day)       # a day later it is a new one, so a long-lived chat can offer again


def test_the_new_conversation_needs_its_own_real_exchange_before_a_lull_counts():
    turns = [("u", "a"), ("a", "b"), ("u", "c"), ("a", "d"), ("u", "e"), ("a", "f"), ("u", "ok")]
    messages = stamped(turns, NOW, [0, 1, 2, 3, 4, 5, 60 * 24])
    assert curiosity.user_turns(messages) == 1 and not allowed(messages)


def test_messages_saved_before_they_were_stamped_are_never_taken_as_a_pause():
    old = chat(("u", "a"), ("a", "b", {curiosity.CURIOSITY_MARK: True}), ("u", "c"), ("a", "d"), ("u", "e"), ("a", "f"), ("u", "ok"))
    assert curiosity.already_offered(old) and len(curiosity.current_stretch(old)) == len(old)


def test_the_stretch_is_cut_only_at_a_pause_at_least_as_long_as_the_gap():
    gap = curiosity.CONVERSATION_GAP_HOURS * 60
    messages = stamped([("u", "a"), ("a", "b"), ("u", "c")], NOW, [0, gap - 5, gap - 4])
    assert len(curiosity.current_stretch(messages)) == 3                      # just under the gap is not a pause
    messages = stamped([("u", "a"), ("a", "b"), ("u", "c")], NOW, [0, gap + 5, gap + 6])
    assert len(curiosity.current_stretch(messages)) == 2


# ---- stage (b): something of its own to steer by ------------------------------------------------------------------------------------------

def test_a_leaning_is_picked_from_the_curated_ones_the_person_has_not_heard_and_none_when_they_have_heard_them_all():
    from backend.components.sonic_profile import INTERESTS

    ids = [e["id"] for e in INTERESTS]
    assert curiosity.pick_lean([])["id"] in ids
    assert curiosity.pick_lean(ids[:-1])["id"] == ids[-1]
    assert curiosity.pick_lean(ids) is None


def test_the_lean_block_gives_the_line_asks_lightly_and_forbids_activities_feelings_and_thinking_about_them():
    from backend.components.sonic_profile import INTERESTS

    block = curiosity.build_lean_block(INTERESTS[0])
    for needle in (INTERESTS[0]["line"], "not an activity or a story", "never claim a feeling", "never say you were thinking about it", "wrap up"):
        assert needle in block


def test_it_counts_a_leaning_as_said_only_when_the_reply_carries_its_substance():
    entry = {"id": "game_design", "line": "Game design is a topic I lean toward, especially how a good level teaches the rules without a tutorial."}
    assert curiosity.lean_used("Glad that's sorted. Game design is a topic I lean toward, how a level teaches the rules. Do you follow it?", entry)
    assert not curiosity.lean_used("Glad that's sorted. What else is going on this week?", entry)


def test_the_leanings_a_person_has_heard_are_remembered_on_their_settings_document(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: db)
    assert curiosity.lean_seen("sam") == []
    curiosity.mark_lean("sam", "scifi")
    curiosity.mark_lean("sam", "scifi")
    curiosity.mark_lean("sam", "tabletop")
    assert curiosity.lean_seen("sam") == ["scifi", "tabletop"] and curiosity.lean_seen("lee") == []


def test_the_callback_has_a_lull_framing_that_picks_something_back_up_and_the_default_is_unchanged():
    from backend.utils.shared_history import build_callback_block

    lull, default = build_callback_block(lull=True), build_callback_block()
    assert "gone quiet" in lull and "worth picking back up" in lull and "never mention memory" in lull
    assert "gone quiet" not in default and default == build_callback_block(lull=False)


def test_the_chat_endpoint_spends_the_one_extender_on_a_callback_or_a_leaning_in_the_stated_order():
    source = open("app.py", encoding="utf-8").read()
    callback = source[source.index("callback_lull = curiosity_utils.is_lull(question)"):source.index("callback_at_lull = callback_lull")]
    assert "slot_open(" in callback and "gap_clear(" in callback and "last_reply_asked(" in callback        # a lull callback obeys the same limits
    lull = source[source.index("if shelf_offer is None and lull_ok:"):source.index("if shelf_offer:\n")]
    assert lull.index("pick_lean(") < lull.index("build_curiosity_block()")                                   # the plain question is the last resort
    marking = source[source.index("if callback_at_lull and not is_guest_username(username):"):source.index('tally(turn_counts, "shelf_listed")')]
    assert "lean_used(full_response, lean_entry)" in marking and marking.index("lean_used(") < marking.index("mark_lean,")
    assert 'lean_offered' in open("backend/utils/self_counters.py", encoding="utf-8").read()
