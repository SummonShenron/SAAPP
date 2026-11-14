"""Jokes (backend/components/sonic_jokes.py, backend/utils/jokes.py): a few curated lines placed in a reply by code, in a situation code can detect, only after a person approves them.

Why (Jack, 2026-11-14): humor that lands depends on exact words and timing, and every gate here is a reason to say nothing: a joke that does not land is worse than none. The first joke is "the simulations": after a change was PROPOSED,
a big one (three files or more) gets "this is not like the simulations" and a small one "just like the simulations". No model is called."""
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.components import sonic_jokes as registry
from backend.utils import jokes
from backend.utils import reply_stamp as rs
from backend.utils import reply_tier
from backend.utils import self_counters as sc
from backend.utils import voice_review

ROOT = Path(__file__).resolve().parents[2]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
NOW = datetime.now(timezone.utc)

SMALL = [{"path": "a.py", "additions": 4, "deletions": 1}]
BIG = [{"path": "a.py", "additions": 9, "deletions": 2}, {"path": "b.py", "additions": 3, "deletions": 0}, {"path": "c.py", "additions": 1, "deletions": 1}]


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    jokes.forget_cooldowns()
    voice_review.forget_cached()
    monkeypatch.delenv("JOKES_ENABLED", raising=False)
    yield
    jokes.forget_cooldowns()
    voice_review.forget_cached()


def joke(joke_id="simulations_big_change", trigger="big_change", approved=True, lines=("Well. They did not simulate this.", "Not like the simulations, this one."), **changes):
    base = {"id": joke_id, "trigger": trigger, "situation": "A change touching three or more files was just proposed.", "lines": list(lines), "approved": approved}
    base.update(changes)
    return base


class Message:
    def __init__(self, content="", told=False, kind="ai"):
        self.content, self.type = content, kind
        self.additional_kwargs = {jokes.JOKE_MARK: True} if told else {}


def mood(valence="neutral", intensity=0.5, need="none", **changes):
    base = {"valence": valence, "intensity": intensity, "need": need, "read_this_turn": True, "lift_this_turn": False, "updated_at": NOW.isoformat(), "turns_since": 0}
    base.update(changes)
    return base


def pick(files=BIG, state=None, messages=(), entries=None, **kw):
    return jokes.pick(files, state, list(messages), user="jack", entries=[joke()] if entries is None else entries, rng=random.Random(1), now=NOW, **kw)


# ---- the registry ----------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_shipped_jokes_are_sound_and_start_as_drafts_with_one_for_each_situation():
    assert jokes.check_registry() == []
    assert {e["trigger"] for e in registry.JOKES} == set(jokes.TRIGGERS)
    assert set(registry.APPROVED_IDS) <= {e["id"] for e in registry.JOKES}
    assert all(e["approved"] is (e["id"] in registry.APPROVED_IDS) for e in registry.JOKES)
    assert jokes.live([{**e, "approved": False} for e in registry.JOKES]) == []
    assert [e["id"] for e in jokes.live([joke(), joke("other_joke", approved=False)])] == ["simulations_big_change"]
    # the meme's own logic: the straight line for a small change, the reality check for a big one
    by_trigger = {e["trigger"]: e for e in registry.JOKES}
    assert all("Just like the simulations" in line for line in by_trigger["small_change"]["lines"])
    assert all("simulat" in line.lower() for line in by_trigger["big_change"]["lines"]) and not any("Just like the simulations" in line for line in by_trigger["big_change"]["lines"])


def test_which_jokes_are_approved_is_pinned_so_a_new_approval_is_always_a_decision():
    # Jack, 2026-11-14: both "simulations" jokes, all their lines.
    assert set(registry.APPROVED_IDS) == {"simulations_big_change", "simulations_small_change"}
    assert {e["id"] for e in jokes.live()} == set(registry.APPROVED_IDS)


def test_the_changelog_says_so_truthfully_and_within_its_length():
    from backend.components.sonic_changelog import CHANGELOG

    entry = next(e for e in CHANGELOG if e["id"] == "simulations_jokes")
    assert len(entry["line"]) <= 260 and entry["line"].startswith("After I propose a code change I can now")
    assert "just like the simulations" in entry["line"] and "not like the simulations" in entry["line"]
    for ref in entry["requires"]:
        path, _, needle = ref.partition("::")
        assert needle and needle.replace("def ", "") in (ROOT / path).read_text(encoding="utf-8"), ref


@pytest.mark.parametrize("change, expected", [
    ({"id": "Bad Id"}, "id must be unique"),
    ({"id": "has_digit_1"}, "id must be unique"),
    ({"trigger": "whenever"}, "trigger must be one of"),
    ({"situation": ""}, "situation of at most"),
    ({"lines": []}, "lines are needed"),
    ({"lines": ["a"] * 6}, "lines are needed"),
    ({"lines": ["x" * 130]}, "at most"),
    ({"lines": ["Well — they did not simulate this."]}, "em dash"),
    ({"lines": ["I love how small this is. Just like the simulations."]}, "identity check"),
    ({"lines": ["Sonic did not simulate this."]}, "carries 'sonic'"),
    ({"approved": "yes"}, "approved must be true or false"),
])
def test_the_registry_check_refuses_what_a_joke_must_never_say(change, expected):
    problems = jokes.check_registry([joke(**change)])
    assert any(expected in p for p in problems), problems


def test_the_registry_check_refuses_a_repeated_id():
    assert any("unique" in p for p in jokes.check_registry([joke(), joke()]))


# ---- when ------------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_a_big_change_is_three_files_or_more_and_a_small_one_is_one_file_and_a_few_lines_and_between_is_neither():
    assert jokes.trigger_of(BIG) == "big_change" and jokes.trigger_of(BIG + [{"path": "d.py"}]) == "big_change"
    assert jokes.trigger_of(SMALL) == "small_change"
    assert jokes.trigger_of([{"path": "a.py", "additions": 20, "deletions": 0}]) == "small_change"          # the outcome counters' own rule: one file, at most 20 changed lines
    assert jokes.trigger_of([{"path": "a.py", "additions": 40, "deletions": 5}]) is None                  # one big file: neither
    assert jokes.trigger_of(BIG[:2]) is None                                                               # two files: neither
    assert jokes.trigger_of([]) is None and jokes.trigger_of(None) is None and jokes.trigger_of("nonsense") is None
    assert jokes.trigger_of([{"path": "a.py", "additions": "lots"}]) == "small_change"                      # an unreadable count never raises (it counts as nothing)
    assert jokes.BIG_FILES == 3 and jokes.changes_of(BIG) == {"a.py": (9, 2), "b.py": (3, 0), "c.py": (1, 1)}


def test_a_joke_is_never_told_to_someone_low_distressed_or_frustrated_or_asking_to_be_reassured_and_a_faded_feeling_does_not_count():
    assert jokes.mood_ok(None, NOW) and jokes.mood_ok({}, NOW)
    for valence in ("neutral", "positive", "excited"):
        assert jokes.mood_ok(mood(valence), NOW), valence
    for valence in ("low", "distressed", "frustrated"):
        assert not jokes.mood_ok(mood(valence), NOW), valence
        assert not jokes.mood_ok(mood(valence, read_this_turn=False), NOW), valence                  # still carried
    assert jokes.mood_ok(mood("low", read_this_turn=False, updated_at=(NOW - timedelta(days=365)).isoformat()), NOW)          # faded to nothing
    assert not jokes.mood_ok(mood("neutral", need="reassurance"), NOW)
    assert jokes.mood_ok(mood("neutral", need="reassurance", read_this_turn=False), NOW)                  # the need belongs to this message


def test_every_gate_is_a_reason_to_say_nothing():
    assert pick() is not None
    assert pick(files=BIG[:2]) is None                                                                  # neither a big change nor a small one
    assert pick(state=mood("frustrated")) is None
    assert pick(messages=[Message("an earlier reply"), Message("a joke", told=True)]) is None            # one a conversation
    assert pick(risk_active=True) is None
    assert pick(entries=[joke(approved=False)]) is None                                                  # a draft is never told
    assert pick(entries=[joke(trigger="small_change")]) is None                                         # no approved joke for this situation
    assert jokes.pick(BIG, None, [], user="jack", entries=[]) is None


def test_it_can_be_switched_off_and_a_user_is_not_told_two_within_the_cooldown(monkeypatch):
    monkeypatch.setenv("JOKES_ENABLED", "off")
    assert pick() is None
    monkeypatch.delenv("JOKES_ENABLED")
    assert pick() is not None and not jokes.cooling_down("jack", NOW)
    jokes.note_told("jack", NOW)
    assert jokes.cooling_down("jack", NOW + timedelta(hours=1)) and pick() is None
    assert not jokes.cooling_down("jack", NOW + timedelta(hours=jokes.COOLDOWN_HOURS + 1))
    assert not jokes.cooling_down("someone_else", NOW)                                                  # per person


def test_the_line_comes_from_the_joke_for_this_situation_and_is_never_the_one_used_last():
    big, small = joke(), joke("simulations_small_change", "small_change", lines=("Small and clean. Just like the simulations.",))
    got = pick(files=BIG, entries=[big, small])
    assert got["id"] == "simulations_big_change" and got["line"] in big["lines"]
    assert pick(files=SMALL, entries=[big, small]) == {"id": "simulations_small_change", "line": "Small and clean. Just like the simulations."}
    earlier = [Message("Done. " + big["lines"][0])]
    for seed in range(20):
        assert jokes.pick(BIG, None, earlier, user="jack", entries=[big], rng=random.Random(seed), now=NOW)["line"] == big["lines"][1]          # the other one: never the line used last
    both_used = [Message(big["lines"][0]), Message(big["lines"][1])]
    assert jokes.pick(BIG, None, both_used, user="jack", entries=[big], rng=random.Random(1), now=NOW)["line"] in big["lines"]                     # all used: any rather than none


def test_a_dashboard_approval_makes_a_draft_usable_and_a_removal_stops_an_approved_one(monkeypatch):
    draft, live = joke(approved=False), joke("simulations_small_change", "small_change")
    monkeypatch.setattr(voice_review, "overrides", lambda db, **kw: {("joke", "simulations_big_change"): "approved", ("joke", "simulations_small_change"): "removed"})
    assert jokes.pick(BIG, None, [], user="jack", entries=[draft, live], rng=random.Random(1), now=NOW)["id"] == "simulations_big_change"
    assert jokes.pick(SMALL, None, [], user="jack", entries=[draft, live], rng=random.Random(1), now=NOW) is None


def test_a_failure_while_choosing_says_nothing_and_never_raises():
    class Broken:
        def get(self, *args):
            raise RuntimeError("no")

    assert jokes.pick(BIG, None, [], user="jack", entries=[{"id": "x", "trigger": "big_change", "approved": True, "lines": None}]) is None


# ---- placing it ------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_line_goes_at_the_end_as_a_paragraph_of_its_own_word_for_word_before_the_follow_up_tag():
    line = "Well. They did not simulate this."
    assert jokes.place("Proposed three edits.\n\nApply when ready.", line) == "Proposed three edits.\n\nApply when ready.\n\n" + line
    assert jokes.place("Proposed.\n\n<<<FOLLOW_UP: apply it>>>", line) == "Proposed.\n\n" + line + "\n\n<<<FOLLOW_UP: apply it>>>"
    assert jokes.place("Proposed.", line) == "Proposed.\n\n" + line
    assert jokes.place("", line) == "" and jokes.place("   ", line) == "   " and jokes.place("Proposed.", "") == "Proposed." and jokes.place("Proposed.", None) == "Proposed."
    already = "Proposed. " + line
    assert jokes.place(already, line) == already                                                          # never twice


def test_the_counters_are_registered_and_a_turn_counts_one_joke_and_which():
    assert "jokes_placed" in sc.KEYS
    for entry in registry.JOKES:
        assert f"joke_used_{entry['id']}" in sc.KEYS
    counts = sc.new_turn_counts()
    jokes.tally_turn(counts, {"id": "simulations_big_change", "line": "x"}, sc.tally)
    jokes.tally_turn(counts, None, sc.tally)
    jokes.tally_turn(counts, {"id": "not_a_registered_joke", "line": "x"}, sc.tally)                   # an unknown id is dropped by the counters, never recorded
    assert {k: v for k, v in counts.items() if k.startswith("joke")} == {"jokes_placed": 2, "joke_used_simulations_big_change": 1}


# ---- the stamp and the dashboard -------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_stamp_records_which_joke_was_told_and_nothing_else_can_be_stored_there():
    assert rs.build(reply_tier.HAIKU, "haiku_setting", "haiku", joke="simulations_big_change")["joke"] == "simulations_big_change"
    for junk in (None, 5, "Not An Id", "has_digit_1", "x" * 80, ""):
        assert rs.build(reply_tier.HAIKU, "haiku_setting", "haiku", joke=junk)["joke"] == "none", junk
    assert rs.build(reply_tier.HAIKU, "haiku_setting", "haiku")["joke"] == "none"
    old = rs.clean({"label": "haiku", "tier": "haiku", "reason": "auto_plain", "choice": "auto", "examples": [], "notes": []})                   # a stamp saved before this field existed
    assert old["joke"] == "none"


def test_the_summary_counts_thumbs_by_joke_so_one_that_lands_badly_shows_up():
    def row(kind, joke_id="none"):
        return {"kind": kind, "voice": {**rs.build(reply_tier.HAIKU, "haiku_setting", "haiku", joke=joke_id)}}
    summary = rs.summarize([row("negative", "simulations_big_change"), row("negative", "simulations_big_change"), row("positive", "simulations_big_change"), row("positive"), row("positive")])
    assert {r["id"]: (r["positive"], r["negative"]) for r in summary["jokes"]} == {"none": (2, 0), "simulations_big_change": (1, 2)}


def test_a_person_can_approve_or_remove_a_joke_in_the_voice_tab_and_it_shows_what_would_be_said_and_when():
    assert "joke" in voice_review.KINDS and voice_review.known("joke", "simulations_big_change") and not voice_review.known("joke", "nope")
    view = voice_review.view(None)
    shown = [i for i in view["items"] if i["kind"] == "joke"]
    assert {i["id"] for i in shown} == {e["id"] for e in registry.JOKES}
    one = next(i for i in shown if i["id"] == "simulations_big_change")
    source = next(e for e in registry.JOKES if e["id"] == "simulations_big_change")
    assert one["text"] == source["lines"] and one["tone"] == "big_change" and one["situation"] == source["situation"] and one["approved"] is True and one["default"] is True          # approved in the registry (Jack, 2026-11-14)


# ---- the app ---------------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_app_remembers_a_proposals_size_when_the_proposal_is_sent_and_never_its_content():
    at = APP.index('event.get("name") == "local_edit_proposal":')
    block = APP[at: at + 900]
    assert 'llm_turn.proposal_files = [{"path": f.get("path"), "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}' in block
    assert "'edits'" not in block.split("yield")[0]                                                       # the edits' text is never kept for this


def test_the_app_picks_and_places_a_joke_only_after_a_proposal_outside_a_safety_plan_after_the_reply_is_written_and_marks_and_stamps_it():
    at = APP.index("joke = await asyncio.to_thread(")
    guard, block = APP[at - 700: at], APP[at: at + 900]
    assert "if llm_turn.proposal_files and not safety_plan.active:" in guard
    assert 'jokes_utils.pick, llm_turn.proposal_files, final_state.get("emotional_state"), chat_sessions[history_key],' in block
    assert "user=username, risk_active=risk_level != RISK_NONE" in block
    assert 'full_response = jokes_utils.place(full_response, joke["line"])' in block and "jokes_utils.note_told(username)" in block and "jokes_utils.tally_turn(turn_counts, joke, tally)" in block
    assert at > APP.index("identity_issue = None if safety_plan.active else identity_reply_issue(")             # after the checks
    assert at < APP.index("event': 'final_generation', 'text': full_response")                                  # before the reply is sent and saved
    assert "ai_message.additional_kwargs[jokes_utils.JOKE_MARK] = True" in APP
    assert 'joke=(llm_turn.joke or {}).get("id", "none")' in APP
