"""Shared moments (backend/utils/shared_moments.py): real moments from a person's history with Sonic, used only once they approved them, chosen by code when their message
names a cue, at most once in a while. No model; a fake database."""
import pathlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.tests.fake_runner_db import FakeDB
from backend.utils import memory_erase, self_counters, shared_moments as sm

ROOT = pathlib.Path(__file__).resolve().parents[2]
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
PAST = (TODAY - timedelta(days=30)).isoformat()


def _moment(text="the night the plan loop cost fifty cents for two asks", cues=("cost", "cents", "budget"), status=sm.APPROVED, last_used=None, **extra):
    return {"id": text[:10], "text": text, "when": PAST, "cues": list(cues), "status": status, "last_used_at": last_used, **extra}


GATES = dict(source_type="conversational", risk_active=False, closing=False, energy_tier="open", proactive_taken=False)


def _pick(moments, message, recent=(), **overrides):
    return sm.pick(moments, message, list(recent), now=NOW, **{**GATES, **overrides})


# ---- what a moment must be ----

def test_a_clean_moment_has_one_line_a_real_past_day_and_cues():
    cleaned, errors = sm.clean({"text": "  your four-round onsite  at Casey's ", "when": PAST, "cues": ["Onsite", "onsite", "Casey's", "ab"]}, TODAY)
    assert errors == [] and cleaned == {"text": "your four-round onsite at Casey's", "when": PAST, "cues": ["onsite", "casey's"]}


@pytest.mark.parametrize("raw, fragment", [
    ({"text": "short", "when": PAST, "cues": ["x x"]}, "text:"),
    ({"text": "a long enough line", "when": "yesterday", "cues": ["cue"]}, "when: a real date"),
    ({"text": "a long enough line", "when": (TODAY + timedelta(days=2)).isoformat(), "cues": ["cue"]}, "has not happened yet"),
    ({"text": "a long enough line", "when": PAST, "cues": ["ab", ""]}, "cues:"),
    (None, "text:"),
])
def test_a_moment_without_a_real_line_day_or_cue_is_refused(raw, fragment):
    cleaned, errors = sm.clean(raw, TODAY)
    assert cleaned is None and any(fragment in e for e in errors)


# ---- when one is used ----

def test_a_cue_in_the_message_calls_up_an_approved_moment_as_a_whole_word_any_case():
    moment = _moment()
    assert _pick([moment], "that run COST way too much") is moment
    assert _pick([moment], "the costume party was fun") is None                   # not part of a longer word
    assert sm.mentions("how did caseys go", "casey's") and sm.mentions("how did Casey’s go", "casey's")


def test_only_an_approved_moment_is_ever_used():
    assert _pick([_moment(status=sm.PROPOSED)], "it cost a lot") is None
    assert _pick([_moment(status=sm.DISMISSED)], "it cost a lot") is None


@pytest.mark.parametrize("overrides", [
    {"source_type": "tool_output"}, {"risk_active": True}, {"closing": True}, {"energy_tier": "subdued"}, {"proactive_taken": True},
])
def test_no_moment_off_a_plain_conversation_in_a_hard_moment_a_closing_or_beside_another_item(overrides):
    assert _pick([_moment()], "it cost a lot", **overrides) is None


def test_moments_are_spaced_out_and_one_used_recently_rests():
    marked = SimpleNamespace(additional_kwargs={sm.MARK: True})
    plain = SimpleNamespace(additional_kwargs={})
    assert _pick([_moment()], "it cost a lot", [marked] + [plain] * (sm.GAP_MESSAGES - 1)) is None
    assert _pick([_moment()], "it cost a lot", [marked] + [plain] * sm.GAP_MESSAGES) is not None
    rested = (NOW - timedelta(days=sm.COOLDOWN_DAYS + 1)).isoformat()
    fresh = (NOW - timedelta(days=sm.COOLDOWN_DAYS - 1)).isoformat()
    assert _pick([_moment(last_used=fresh)], "it cost a lot") is None
    assert _pick([_moment(last_used=rested)], "it cost a lot") is not None


def test_the_moment_naming_most_cues_wins_then_the_one_used_longest_ago():
    one = _moment("the browser migration in plan mode", cues=("plan", "migration"))
    two = _moment("the night the plan loop cost fifty cents", cues=("plan", "cost"))
    assert _pick([one, two], "the plan cost a lot") is two
    older = _moment("first", cues=("plan",), last_used=(NOW - timedelta(days=90)).isoformat())
    newer = _moment("second", cues=("plan",), last_used=(NOW - timedelta(days=20)).isoformat())
    never = _moment("third", cues=("plan",))
    assert _pick([newer, older], "about the plan") is older
    assert _pick([newer, never, older], "about the plan") is never


def test_the_block_names_the_moment_and_its_day_and_says_how_lightly_to_use_it():
    block = sm.build_block({**_moment(), "when": "2026-10-31"})
    assert "the night the plan loop cost fifty cents for two asks (Oct 31, 2026)" in block
    assert "one light clause" in block and "If it does not fit, leave it out" in block and "do not add details" in block
    assert sm.build_block(None) == ""


def test_a_reply_used_it_when_it_shares_two_words_with_it_or_names_a_cue():
    moment = _moment()
    assert sm.used("Not another fifty cents night, I promise.", moment)
    assert sm.used("Keep an eye on the budget.", moment)
    assert not sm.used("Sure, here is the plan.", moment) and not sm.used("", moment) and not sm.used("x", None)


# ---- storage ----

def test_a_proposed_moment_is_stored_once_and_only_its_owner_can_approve_dismiss_or_remove_it():
    db = FakeDB()
    moment, errors = sm.propose(db, "jack", {"text": "your four-round onsite at Casey's", "when": PAST, "cues": ["onsite"]}, now=NOW)
    assert errors == [] and moment["status"] == sm.PROPOSED and moment["uses"] == 0
    assert sm.propose(db, "jack", {"text": "Your four-round onsite at Casey's", "when": PAST, "cues": ["x y z"]}, now=NOW) == (None, ["this moment is already there"])
    assert sm.approved_for(db, "jack") == []
    assert not sm.set_status(db, "someone_else", moment["id"], sm.APPROVED)
    assert not sm.set_status(db, "jack", moment["id"], "whatever")
    assert sm.set_status(db, "jack", moment["id"], sm.APPROVED) and [m["id"] for m in sm.approved_for(db, "jack")] == [moment["id"]]
    sm.note_used(db, "jack", moment["id"], NOW)
    stored = sm.list_for(db, "jack")[0]
    assert stored["uses"] == 1 and stored["last_used_at"] == NOW.isoformat()
    assert not sm.delete(db, "someone_else", moment["id"]) and sm.delete(db, "jack", moment["id"]) and sm.list_for(db, "jack") == []


def test_a_person_has_a_limit_and_a_moment_with_problems_is_not_stored():
    db = FakeDB()
    for i in range(sm.MAX_PER_PERSON):
        assert sm.propose(db, "jack", {"text": f"moment number {i} here", "when": PAST, "cues": ["cue"]}, now=NOW)[0]
    assert sm.propose(db, "jack", {"text": "one moment too many", "when": PAST, "cues": ["cue"]}, now=NOW)[0] is None
    assert sm.propose(FakeDB(), "jack", {"text": "x", "when": PAST}, now=NOW)[0] is None


def test_the_page_sees_only_what_it_needs_and_forget_everything_removes_moments():
    assert set(sm.public(_moment(username="jack", created_at="x"))) == {"id", "text", "when", "cues", "status", "source", "uses", "last_used_at"}
    assert sm.COLLECTION in memory_erase.ERASED_COLLECTIONS


# ---- the wiring ----

def test_the_reply_offers_a_moment_in_the_one_slot_marks_it_and_counts_it():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    pick = source.index("shared_moment = shared_moments.pick(")
    assert source.rfind('if not slot["proactive_taken"] and get_db() is not None:', 0, pick) > source.rfind("slot = dict(", 0, pick)
    assert pick < source.index("clarify_level = clarify_utils.level_of(user_profile)")              # ahead of a getting-to-know-you question
    block = source[pick: pick + 700]
    assert "prompt = prompt + shared_moments.build_block(shared_moment)" in block and 'slot["proactive_taken"] = True' in block
    assert 'ai_message.additional_kwargs[shared_moments.MARK] = True' in source
    assert "if shared_moments.used(full_response, shared_moment):" in source and "shared_moments.note_used, get_db(), username, shared_moment[\"id\"]" in source
    for key in ("shared_moment_offered", "shared_moment_used"):
        assert key in self_counters.KEYS and f'"{key}"' in source


def test_the_endpoints_are_for_members_only():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    for route in ('@app.get("/api/shared-moments")', '@app.post("/api/shared-moments/{moment_id}/status")', '@app.delete("/api/shared-moments/{moment_id}")'):
        start = source.index(route)
        assert "Depends(get_member_user)" in source[start: start + 300], route


def test_a_moment_offered_twenty_messages_ago_still_holds_the_next_one_back():
    marked = SimpleNamespace(additional_kwargs={sm.MARK: True})
    plain = SimpleNamespace(additional_kwargs={})
    assert _pick([_moment()], "it cost a lot", [marked] + [plain] * 20) is None          # a literal: the gap itself is pinned
