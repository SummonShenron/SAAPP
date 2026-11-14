"""Shape stories (backend/components/sonic_shapes.py, backend/utils/shape_stories.py): when a person is venting, one real thing from Sonic's own record of the same SHAPE, said once as a record.

Why (Jack, 2026-11-13/14): record cases were offered 85 times in three weeks and cited 0 times (optional wording, matched by mechanism). Here the match is by shape, each shape has one reviewed past-event sentence, and the instruction is to say it
unless an exception applies. No model is called: a fake embedder stands in for the embedding, and the selection tests build their own entries (never relying on which shipped ones are approved)."""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.components import sonic_shapes as registry
from backend.utils import experiences
from backend.utils import identity_checks as ic
from backend.utils import self_counters as sc
from backend.utils import shape_stories as ss
from backend.utils import voice_review

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
NOW = datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    ss._cache.update({"at": 0.0, "vectors": {}})
    voice_review.forget_cached()
    monkeypatch.delenv("SHAPE_STORIES_ENABLED", raising=False)
    monkeypatch.delenv("SHAPE_STORIES_MODE", raising=False)
    yield
    ss._cache.update({"at": 0.0, "vectors": {}})
    voice_review.forget_cached()


def shape(shape_id="said_done_unchecked", approved=True, **changes):
    base = {
        "id": shape_id, "tone": "light", "situation": "Someone told them a job was handled and it was not.",
        "asks": ["my contractor swore it was finished and half of it was never done", "the vendor said it was taken care of and nothing was", "he said he'd checked it and he hadn't"],
        "story": "There was a time in my record when I told someone a job was finished before I had checked it, and it was not.", "source": "S1", "approved": approved,
    }
    base.update(changes)
    return base


def state(valence="frustrated", intensity=0.5, need="venting", **changes):
    base = {"valence": valence, "intensity": intensity, "need": need, "read_this_turn": True, "lift_this_turn": False, "updated_at": NOW.isoformat(), "turns_since": 0}
    base.update(changes)
    return base


def fake_embed(text):
    t = text.lower()
    if any(w in t for w in ("contractor", "vendor", "swore", "taken care of", "checked it")):
        return [1.0, 0.0, 0.0] + [0.0] * 51
    if any(w in t for w in ("weekend", "wrong thing", "lookalike")):
        return [0.0, 1.0, 0.0] + [0.0] * 51
    return [0.0, 0.0, 0.0] + [1.0 if i == sum(map(ord, t)) % 51 else 0.0 for i in range(51)]


def prepared(*entries):
    ss.leanings.index_vectors(None, fake_embed, [ss.view(e) for e in entries], fill=True, collection=ss.COLLECTION, cache=ss._cache)
    return list(entries)


# ---- the registry ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_shipped_shapes_are_sound_and_nobody_has_approved_one_by_accident():
    assert ss.check_registry() == []
    assert len(registry.SHAPES) == 8
    assert set(registry.APPROVED_IDS) <= {e["id"] for e in registry.SHAPES}
    assert all(e["approved"] is (e["id"] in registry.APPROVED_IDS) for e in registry.SHAPES)
    assert ss.live([{**e, "approved": False} for e in registry.SHAPES]) == []
    assert [e["id"] for e in ss.live([shape(), shape("fixed_the_wrong_lookalike", approved=False)])] == ["said_done_unchecked"]
    assert {e["tone"] for e in registry.SHAPES} <= set(registry.TONES)
    # the one that is about being told something was fine is for someone annoyed or stuck only, never someone hurt
    assert {e["id"]: e["tone"] for e in registry.SHAPES}["said_done_unchecked"] == "stuck"


def test_every_shipped_story_would_pass_the_identity_check_as_it_is_said_and_is_a_record_statement_not_a_memory():
    for entry in registry.SHAPES:
        assert ic.identity_reply_issue(entry["story"], record_offered=True) is None, entry["id"]
        assert ic.is_past_event_statement(entry["story"]), entry["id"]
        assert not ic._FELT_WORDS_RE.search(entry["story"]), entry["id"]
        assert "in my record" in entry["story"] or entry["story"].startswith("My record has a time"), entry["id"]
        assert ic.identity_reply_issue(ss.spoken(entry, True), record_offered=True) is None, entry["id"]          # the story with its bridge, as it is placed


@pytest.mark.parametrize("change, expected", [
    ({"id": "Bad Id"}, "id must be unique"),
    ({"id": "has_digit_1"}, "id must be unique"),
    ({"tone": "any"}, "tone must be one of"),
    ({"asks": ["only one", "and two"]}, "3 or 4 invented messages"),
    ({"asks": ["x" * 200, "b", "c"]}, "3 or 4 invented messages"),
    ({"situation": ""}, "situation of at most"),
    ({"story": "I remember a time when I told someone a job was finished before I had checked it."}, "past-event statement"),
    ({"story": "There was a time in my record when I felt awful about a missed check, and I felt it for days."}, "past-event statement"),
    ({"story": "There was a time in my record when I missed a check. It was bad."}, "one sentence"),
    ({"story": "There was a time in my record when I loved checking things before I told anyone."}, "identity check"),
    ({"story": "There was a time in my record when claude told someone a job was finished."}, "carries 'claude'"),
    ({"story": "There was a time in my record when I missed a check — and it showed."}, "em dash"),
    ({"approved": "yes"}, "approved must be true or false"),
])
def test_the_registry_check_refuses_what_a_shape_story_must_never_say(change, expected):
    problems = ss.check_registry([shape(**change)])
    assert any(expected in p for p in problems), problems


def test_the_registry_check_refuses_a_repeated_id():
    assert any("unique" in p for p in ss.check_registry([shape(), shape()]))


# ---- when it may be told ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_which_shapes_are_approved_is_pinned_so_a_new_approval_is_always_a_decision():
    # Jack, 2026-11-14: the four stories about a person's OWN mistake or loop, with their bridges, and then the two that tested strongest (a rough day, a fix that worked first try). Not the one aimed at someone else, and not
    # "a good day in my record" (tacked on to a happy message in every blind pair): both stay drafts until refined.
    assert set(registry.APPROVED_IDS) == {"fixed_the_wrong_lookalike", "kept_repeating_the_attempt", "ran_out_just_before_the_end", "counted_and_found_a_habit", "rough_day_in_the_record", "careful_first_worked"}
    assert {e["id"] for e in ss.live()} == set(registry.APPROVED_IDS)
    drafts = {e["id"] for e in registry.SHAPES} - set(registry.APPROVED_IDS)
    assert drafts == {"said_done_unchecked", "good_day_in_the_record"}
    tones = {e["id"]: e["tone"] for e in registry.SHAPES}
    assert tones["careful_first_worked"] == "good" and tones["good_day_in_the_record"] == "good" and tones["rough_day_in_the_record"] == "light"


def test_a_good_moment_gets_only_the_good_stories_and_never_one_about_something_that_went_wrong():
    # Jack, 2026-11-14: "relate positive things in positive moments"
    for valence in ("positive", "excited"):
        assert ss.tones_for(state(valence), NOW) == ("good",), valence
    assert ss.tones_for(state("neutral", need="celebration"), NOW) == ("good",)              # asking to be celebrated with is a good moment whatever the mood word says
    assert ss.tones_for(state("positive", need="reassurance"), NOW) == ()


def test_someone_hurt_gets_only_the_light_stories_and_someone_frustrated_the_light_and_stuck_ones_and_neither_the_good_ones():
    for valence in ("low", "distressed"):
        assert ss.tones_for(state(valence), NOW) == ("light",), valence                      # never "stuck", never "good"
    assert ss.tones_for(state("distressed", intensity=0.95), NOW) == ("light",)               # however heavy: the safety plan, not this, is what holds a story back
    assert ss.tones_for(state("frustrated"), NOW) == ("light", "stuck")
    assert ss.tones_for(state("low", lift_this_turn=True), NOW) == ("light",)                 # a flicker of lift does not make them less hurt
    assert ss.tones_for(state("low", need="celebration"), NOW) == ("good",)                  # but a message that asks to celebrate is a good moment


def test_an_ordinary_conversation_may_hear_any_tone_for_whatever_fits_by_meaning():
    # Jack, 2026-11-14: "things in general in normal conversations when they fit"
    assert ss.tones_for(None, NOW) == ("good", "light", "stuck") and ss.tones_for({}, NOW) == ("good", "light", "stuck")
    assert ss.tones_for(state("neutral"), NOW) == ("good", "light", "stuck")
    assert ss.tones_for(state("neutral", need="solving"), NOW) == ("good", "light", "stuck")


def test_a_hurt_or_frustrated_feeling_still_being_carried_keeps_the_stories_it_would_keep_until_it_has_faded():
    assert ss.tones_for(state("low", read_this_turn=False), NOW) == ("light",)               # not this message, but still carried
    assert ss.tones_for(state("frustrated", read_this_turn=False), NOW) == ("light", "stuck")
    year_ago = (NOW.replace(year=NOW.year - 1)).isoformat()
    assert ss.tones_for(state("low", read_this_turn=False, updated_at=year_ago), NOW) == ("good", "light", "stuck")          # a year ago has faded to nothing
    assert ss.tones_for(state("low", intensity=0.5, updated_at="not a date"), NOW) == ("light",)          # an unreadable state on a fresh hurt message is treated as hurt, and never raises
    assert ss.tones_for(state("positive", read_this_turn=False), NOW) == ("good", "light", "stuck")        # a good feeling from earlier is not THIS message


def test_a_message_that_asks_to_be_reassured_gets_no_story_at_any_level_and_the_need_belongs_to_this_message():
    for valence in ("frustrated", "neutral", "low", "positive"):
        assert ss.tones_for(state(valence, need="reassurance"), NOW) == (), valence
    assert ss.tones_for(state("neutral", need="reassurance", read_this_turn=False), NOW) == ("good", "light", "stuck")          # one carried from an earlier turn does not block a later one


def test_the_cheap_gates_are_the_record_cases_own_and_do_not_judge_the_mood():
    assert ss.allowed([], risk_active=False, closing=False, proactive_taken=False) is True
    assert ss.allowed([], risk_active=True, closing=False, proactive_taken=False) is False
    assert ss.allowed([], risk_active=False, closing=True, proactive_taken=False) is False
    assert ss.allowed([], risk_active=False, closing=False, proactive_taken=True) is False

    class Message:
        additional_kwargs = {experiences.EXPERIENCE_MARK: True}

    assert ss.allowed([Message()], risk_active=False, closing=False, proactive_taken=False) is False          # a citation in the last ten messages


# ---- choosing one ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_shape_closest_in_meaning_is_chosen_only_when_approved_in_a_tone_that_may_be_told_and_clear_of_the_threshold():
    entries = prepared(shape(), shape("fixed_the_wrong_lookalike", asks=["i spent the weekend on the wrong thing", "the wrong thing looked exactly the same", "fixed the lookalike and nothing changed"]))
    got = ss.select("my contractor swore it was all done and it wasn't", None, fake_embed, tones=("light",), entries=entries)
    assert got["id"] == "said_done_unchecked"
    assert ss.select("something about my weekend and the wrong thing", None, fake_embed, tones=("light",), entries=entries)["id"] == "fixed_the_wrong_lookalike"
    assert ss.select("what is the weather like today in the city", None, fake_embed, tones=("light",), entries=entries) is None          # nothing close: nothing is said


def test_a_shape_with_a_stricter_threshold_of_its_own_must_clear_it_too_and_the_next_best_is_tried_when_it_does_not():
    broad = shape("good_day_in_the_record", min_score=1.5)                                   # an impossible floor, to prove it is enforced
    other = shape("fixed_the_wrong_lookalike", asks=["my contractor swore it was finished", "the vendor said it was taken care of", "he said he'd checked it"])
    entries = prepared(broad, other)
    text = "my contractor swore it was finished"
    assert ss.select(text, None, fake_embed, tones=("light",), entries=[broad]) is None                       # clears the default threshold, not its own
    assert ss.select(text, None, fake_embed, tones=("light",), entries=entries)["id"] == "fixed_the_wrong_lookalike"
    assert ss.select(text, None, fake_embed, tones=("light",), entries=[broad], threshold=0.5)["id"] == "good_day_in_the_record"          # an explicit threshold (the eval's) overrides the floor
    assert any("min_score must be" in p for p in ss.check_registry([shape(min_score=0.2)])) and any("min_score must be" in p for p in ss.check_registry([shape(min_score="high")]))
    assert ss.check_registry([shape(min_score=0.78)]) == []
    assert {e["id"]: e.get("min_score") for e in registry.SHAPES}["good_day_in_the_record"] == 0.78          # the broad story: happy messages of every kind sit near it


def test_a_draft_is_never_chosen_and_a_tone_the_person_may_not_hear_is_never_chosen():
    stuck = shape(tone="stuck")
    entries = prepared(stuck, shape("fixed_the_wrong_lookalike", approved=False, asks=["wrong thing all weekend", "lookalike", "weekend lost"]))
    text = "my contractor swore it was finished"
    assert ss.select(text, None, fake_embed, tones=("light", "stuck"), entries=entries)["id"] == "said_done_unchecked"
    assert ss.select(text, None, fake_embed, tones=("light",), entries=entries) is None                                  # a low person never hears a "stuck" story
    assert ss.select("wrong thing weekend", None, fake_embed, tones=("light", "stuck"), entries=entries) is None         # the draft is invisible
    assert ss.select(text, None, fake_embed, tones=(), entries=entries) is None


def test_it_is_silent_under_a_safety_plan_on_other_routes_when_switched_off_and_for_a_short_message(monkeypatch):
    entries = prepared(shape())
    text = "my contractor swore it was finished"
    assert ss.select(text, None, fake_embed, tones=("light",), entries=entries, risk_active=True) is None
    assert ss.select(text, None, fake_embed, tones=("light",), entries=entries, source_type="kb_strict") is None
    assert ss.select("contractor", None, fake_embed, tones=("light",), entries=entries) is None
    monkeypatch.setenv("SHAPE_STORIES_ENABLED", "false")
    assert ss.select(text, None, fake_embed, tones=("light",), entries=entries) is None


def test_a_dashboard_approval_makes_a_draft_usable_and_a_removal_stops_an_approved_one(monkeypatch):
    draft, live = shape(approved=False), shape("fixed_the_wrong_lookalike", asks=["i spent the weekend on the wrong thing", "wrong thing", "lookalike"])
    prepared(shape(), live)                                                                    # the index, built for the entries as they will be seen once approved
    entries = [draft, live]
    monkeypatch.setattr(voice_review, "overrides", lambda db, **kw: {("shape", "said_done_unchecked"): "approved", ("shape", "fixed_the_wrong_lookalike"): "removed"})
    assert ss.select("my contractor swore it was finished", None, fake_embed, tones=("light",), entries=entries)["id"] == "said_done_unchecked"
    assert ss.select("the wrong thing all weekend", None, fake_embed, tones=("light",), entries=entries) is None


def test_the_index_is_never_built_on_the_turn_so_a_turn_with_no_index_says_nothing_and_never_raises():
    assert ss.select("my contractor swore it was finished", None, fake_embed, tones=("light",), entries=[shape()]) is None
    boom = lambda text: (_ for _ in ()).throw(RuntimeError("embedding down"))
    assert ss.select("my contractor swore it was finished", None, boom, tones=("light",), entries=[shape()]) is None


# ---- what the reply is told ------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_block_says_it_once_in_the_form_of_a_record_and_names_what_it_must_not_do_and_the_old_wording_is_kept_to_be_compared():
    entry = shape()
    block = ss.build_block(entry, "say")
    assert entry["story"] in block and "ONE sentence" in block and "unless an exception below applies" in block
    assert "Answer what they said first" in block and "go straight back to them" in block
    for forbidden in ("remember it", "reminds you", "know how they feel", "compare the two"):
        assert forbidden in block
    assert "Leave it out if" in block
    optional = ss.build_block(entry, "optional")
    assert "If, and only if, it genuinely helps" in optional and "unless an exception" not in optional and entry["story"] in optional
    assert ss.build_block(None) == "" and ss.build_block({"id": "x"}) == ""
    assert ss.mode() == "insert" and ss.build_block(entry) == ""          # the default asks nothing of the model: the sentence is placed by code (insert_story)


def test_the_mode_comes_from_the_environment_and_an_unknown_one_falls_back_to_insert(monkeypatch):
    monkeypatch.setenv("SHAPE_STORIES_MODE", "optional")
    assert ss.mode() == "optional" and "If, and only if" in ss.build_block(shape())
    monkeypatch.setenv("SHAPE_STORIES_MODE", "always")
    assert ss.mode() == "insert"
    monkeypatch.setenv("SHAPE_STORIES_MODE", "say")
    assert ss.mode() == "say" and "unless an exception below applies" in ss.build_block(shape())


def test_the_counters_are_registered_and_a_turn_counts_one_offer_and_the_shape():
    for key in ("shape_stories_offered", "shape_stories_cited"):
        assert key in sc.KEYS
    for entry in registry.SHAPES:
        assert f"shape_story_used_{entry['id']}" in sc.KEYS
    counts = sc.new_turn_counts()
    ss.tally_turn(counts, shape(), sc.tally)
    ss.tally_turn(counts, None, sc.tally)
    ss.tally_turn(counts, {"id": "not_a_registered_shape"}, sc.tally)                      # an unknown id is dropped by the counters, never recorded
    assert {k: v for k, v in counts.items() if k.startswith("shape")} == {"shape_stories_offered": 2, "shape_story_used_said_done_unchecked": 1}


# ---- how it may be phrased (identity_checks) -------------------------------------------------------------------------------------------------------------------------------------

def test_a_past_event_statement_is_allowed_only_when_a_case_was_offered_and_never_with_remembering_or_feeling_in_it():
    story = "There was a time in my record when I told someone a job was finished before I had checked it, and it was not."
    assert ic.identity_reply_issue(story, record_offered=True) is None
    jack_wording = "That sounds maddening. This is just like that one time I told someone it was done before I checked."
    assert ic.identity_reply_issue(jack_wording, record_offered=True) is None            # Jack's own example wording, with a case behind it
    assert ic.identity_reply_issue(jack_wording) == ic.FELT_RECOLLECTION                  # without one, "that one time" is still a felt recollection
    for felt in ("This is just like that one time, and I remember how it felt.", "There was a time I recall when I told someone it was done.",
                 "That reminds me of a time I told someone a job was finished.", "There was a time when I felt awful about telling someone it was finished."):
        assert ic.identity_reply_issue(felt, record_offered=True) is not None, felt


def test_asking_the_person_to_remember_something_sonic_did_is_refused_always_because_they_were_not_there():
    for text in ("Do you remember when I told you the job was done?", "Do you still remember the time I said it was finished?", "do you remember that time I checked it for you"):
        assert ic.identity_reply_issue(text) == ic.ASKS_TO_REMEMBER, text
        assert ic.identity_reply_issue(text, record_offered=True) == ic.ASKS_TO_REMEMBER, text
    assert ic.identity_reply_issue("Do you remember when YOU told them it was done?") is None          # the person's own words are theirs to recall
    assert ic.ASKS_TO_REMEMBER in ic._ISSUE_HINTS


def test_the_counter_reads_the_statement_form_too_so_it_cannot_stay_at_zero_when_the_phrasing_works():
    assert experiences.cited("There was a time in my record when I missed a check.") is True
    assert experiences.cited("That is rough. This is just like that one time I told someone it was done before I checked.") is True
    assert experiences.cited("This is close to a case in my record: a selector did not apply.") is True            # the old form still counts
    assert experiences.cited("That sounds really frustrating. What did they say when you asked?") is False
    assert experiences.cited("I remember when I told someone it was done.") is False
    assert experiences.cited("") is False


# ---- the dashboard's side --------------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_person_can_approve_or_remove_a_shape_in_the_voice_tab_and_it_shows_with_its_story_and_tone():
    assert "shape" in voice_review.KINDS and voice_review.known("shape", "said_done_unchecked") and not voice_review.known("shape", "nope")
    view = voice_review.view(None)
    shapes = [i for i in view["items"] if i["kind"] == "shape"]
    assert {i["id"] for i in shapes} == {e["id"] for e in registry.SHAPES}
    one = next(i for i in shapes if i["id"] == "said_done_unchecked")
    source = next(e for e in registry.SHAPES if e["id"] == "said_done_unchecked")
    assert one["text"] == [source["story"] + " " + source["bridge"]] and one["tone"] == "stuck" and one["approved"] is False          # what a reviewer approves is what would be said


# ---- the app ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_app_offers_a_shape_only_on_a_conversational_turn_with_no_safety_plan_and_a_free_slot_and_takes_the_slot():
    start = APP.index("vent_tones = shape_stories_utils.tones_for(")
    block = APP[start - 400: start + 1300]
    assert 'source_type == "conversational" and risk_level == RISK_NONE and not slot["proactive_taken"]' in block
    assert "shape_stories_utils.allowed(chat_sessions[history_key], risk_active=False, closing=is_closing_message(question), proactive_taken=slot[\"proactive_taken\"])" in block
    assert 'slot["proactive_taken"] = True' in block and "llm_turn.shape_offered = True" in block
    assert "prompt = prompt + shape_stories_utils.build_block(shape_entry)" in block
    assert "shape_stories_utils.tally_turn(turn_counts, shape_entry, tally)" in block


def test_the_app_lets_the_statement_form_through_only_with_a_case_offered_counts_it_cited_and_sets_the_same_spacing_mark():
    assert "record_offered=bool(llm_turn.experience_offered or llm_turn.shape_offered)" in APP
    at = APP.index("if llm_turn.shape_offered and shape_stories_utils.cited(")
    assert "EXPERIENCE_MARK] = True" in APP[at: at + 400] and 'tally(turn_counts, "shape_stories_cited")' in APP[at: at + 500]
    assert "llm_turn.shape_offered" in (ROOT / "backend/utils/llm_turns.py").read_text(encoding="utf-8").replace("self.shape_offered", "llm_turn.shape_offered")


# ---- placing the sentence in the reply, in code -----------------------------------------------------------------------------------------------------------------------------------

STORY = "There was a time in my record when I told someone a job was finished before I had checked it, and it was not."


def test_the_approved_sentence_goes_in_word_for_word_as_a_paragraph_of_its_own_after_the_first_one():
    entry = shape()
    reply = "That sounds maddening, and worse that he never looked.\n\nHere is what I would do first.\n\n1. Write it down."
    told = ss.insert_story(reply, entry)
    assert told == "That sounds maddening, and worse that he never looked.\n\n" + STORY + "\n\nHere is what I would do first.\n\n1. Write it down."
    assert STORY in told and told.count(STORY) == 1                                     # the exact approved sentence, once, never reworded
    assert ss.insert_story("That sounds maddening.", entry) == "That sounds maddening.\n\n" + STORY                      # a one-paragraph reply gets it at the end


def test_it_goes_before_the_follow_up_tag_and_the_tag_stays_last():
    entry = shape()
    told = ss.insert_story("That sounds maddening.\n\nDid he say when he would come back?\n\n<<<FOLLOW_UP: he said Tuesday>>>", entry)
    assert told.endswith("<<<FOLLOW_UP: he said Tuesday>>>") and told.index(STORY) < told.index("<<<FOLLOW_UP")
    assert ss.insert_story("That sounds maddening. <<<FOLLOW_UP: x>>>", entry) == "That sounds maddening.\n\n" + STORY + "\n\n<<<FOLLOW_UP: x>>>"


def test_it_leaves_the_reply_alone_when_there_is_nothing_to_say_or_the_reply_already_tells_a_record_case():
    entry = shape()
    assert ss.insert_story("", entry) == "" and ss.insert_story("   ", entry) == "   "
    assert ss.insert_story("That sounds maddening.", None) == "That sounds maddening."
    assert ss.insert_story("That sounds maddening.", {"id": "x", "story": ""}) == "That sounds maddening."
    already = "That sounds maddening. There was a time in my record when I missed a check too."
    assert ss.insert_story(already, entry) == already                                   # the model said one itself: not twice
    assert ss.insert_story("Here it is: " + STORY, entry) == "Here it is: " + STORY
    assert ss.insert_story("This is close to a case in my record: a selector did not apply.", entry).count("in my record") == 1


def test_a_reply_with_the_sentence_placed_is_counted_as_cited_and_passes_the_identity_check_as_the_app_runs_it():
    told = ss.insert_story("That sounds maddening.\n\nWhat did he say when you called?", shape())
    assert experiences.cited(told) is True and ss.cited(told) is True
    assert ic.identity_reply_issue(told, person_signal=True, record_offered=True) is None
    for entry in registry.SHAPES:
        assert ic.identity_reply_issue(ss.insert_story("That is rough.\n\nWhat happened next?", entry), person_signal=True, record_offered=True) is None, entry["id"]


def test_the_app_places_the_sentence_after_the_reply_is_written_and_checked_and_only_outside_a_safety_plan():
    at = APP.index("full_response = shape_stories_utils.insert_story(full_response, llm_turn.shape_entry)")
    guard = APP[at - 600: at]
    assert 'llm_turn.shape_offered and shape_stories_utils.mode() == "insert" and not safety_plan.active' in guard
    assert at > APP.index("identity_issue = None if safety_plan.active else identity_reply_issue(")            # after the checks
    assert at < APP.index("event': 'final_generation', 'text': full_response")                                  # before the reply is sent and saved
    assert "llm_turn.shape_entry = shape_entry" in APP


# ---- stance and bridge (Jack, 2026-11-14, reading the first blind sheet) ---------------------------------------------------------------------------------------------------------

def test_every_shape_has_a_stance_and_a_one_sentence_bridge_and_only_the_one_about_someone_elses_overclaiming_is_an_admission():
    by_id = {e["id"]: e for e in registry.SHAPES}
    assert {e["stance"] for e in registry.SHAPES} <= set(registry.STANCES)
    assert [i for i, e in by_id.items() if e["stance"] == "other"] == ["said_done_unchecked"]
    # frustration with ANOTHER party: a plain statement reads as defending them, so it is an admission that sides with the person
    assert "I've done something like that myself" in by_id["said_done_unchecked"]["story"]
    for entry in registry.SHAPES:
        assert entry["bridge"] and len(entry["bridge"]) <= registry.MAX_BRIDGE_CHARS and entry["bridge"].startswith("So I "), entry["id"]
        assert not ic._FELT_WORDS_RE.search(entry["bridge"]), entry["id"]


def test_the_words_placed_are_the_story_and_its_bridge_as_one_paragraph_and_the_bridge_can_be_switched_off(monkeypatch):
    entry = shape(bridge="So I know how easy it is to put real work into the wrong one.")
    assert ss.spoken(entry) == STORY + " So I know how easy it is to put real work into the wrong one."
    assert ss.spoken(entry, False) == STORY and ss.spoken(shape()) == STORY                    # no bridge in the entry: the story alone
    assert ss.spoken(None) == "" and ss.spoken({"id": "x", "story": ""}) == ""
    told = ss.insert_story("That is rough.\n\nWhat happened next?", entry)
    assert told == "That is rough.\n\n" + ss.spoken(entry) + "\n\nWhat happened next?"
    assert ss.insert_story("That is rough.", entry, bridge=False) == "That is rough.\n\n" + STORY
    monkeypatch.setenv("SHAPE_STORIES_BRIDGE", "off")
    assert ss.bridge_on() is False and ss.spoken(entry) == STORY
    monkeypatch.setenv("SHAPE_STORIES_BRIDGE", "on")
    assert ss.bridge_on() is True and ss.spoken(entry).endswith("the wrong one.")


@pytest.mark.parametrize("change, expected", [
    ({"stance": "sideways"}, "stance must be one of"),
    ({"stance": "other"}, "admission that sides with them"),
    ({"bridge": "So I know how easy it is. And I felt awful about it."}, "bridge must be one sentence"),
    ({"bridge": "x" * 130}, "bridge must be one sentence"),
    ({"bridge": "So I remember how easy it is to put real work into the wrong one."}, "bridge claims remembering or feeling"),
    ({"bridge": "So I know how easy it is — and how it stings."}, "em dash"),
    ({"bridge": "So I know how easy it is, and I love that you caught it."}, "identity check"),
    ({"bridge": "So claude knows how easy it is."}, "carries 'claude'"),
])
def test_the_registry_check_holds_stance_and_bridge_to_their_rules(change, expected):
    problems = ss.check_registry([shape(**change)])
    assert any(expected in p for p in problems), problems
    assert ss.check_registry([shape(stance="other", story="Lowkey, I've done something like that myself: there was a time in my record when I told someone a job was finished before I had checked it, and it was not.", bridge="So I get why that is not enough for you.")]) == []
