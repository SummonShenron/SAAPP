"""The reach dial: how often Sonic may bring something up on its own (backend/utils/reach.py)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import curiosity, offer_response, profile_utils, reach


def test_the_default_is_what_sonic_did_before_the_dial():
    from backend.utils import initiation, reading_shelf

    level = reach.settings(reach.DEFAULT)
    assert (level.gap_hours, level.findings_per_day, level.extenders, level.opener_hours) == (
        curiosity.MIN_HOURS_BETWEEN, reading_shelf.FINDINGS_PER_DAY, 1, initiation.MIN_HOURS_BETWEEN)


def test_the_levels_are_ordered_from_quiet_to_often():
    low, default, more = (reach.settings(n) for n in (reach.LOW, reach.DEFAULT, reach.MORE))
    assert low.gap_hours > default.gap_hours > more.gap_hours
    assert low.opener_hours > default.opener_hours > more.opener_hours
    assert low.findings_per_day < default.findings_per_day < more.findings_per_day
    assert low.extenders <= default.extenders < more.extenders
    assert more.opener_hours == 8.0     # three a day at most, and never two in a row (initiation.thread_refusal)


def test_none_turns_everything_off():
    level = reach.settings(reach.NONE)
    assert not level.offers and level.findings_per_day == 0 and level.extenders == 0 and not reach.opener_allowed({"reach": "none"})


def test_a_profile_from_before_the_dial_keeps_its_old_switch():
    assert reach.level_of({"offers": False}) == "none"
    assert reach.level_of({"offers": True}) == "default" and reach.level_of({}) == "default" and reach.level_of(None) == "default"
    assert reach.level_of({"reach": "more", "offers": True}) == "more"
    assert reach.level_of({"reach": "garbage"}) == "default"


def test_the_profile_stores_a_valid_level_and_keeps_the_yes_no_switch_in_step():
    for level in reach.LEVELS:
        out = profile_utils.normalize_profile({"reach": level})
        assert out["reach"] == level and out["offers"] is (level != "none")
        assert profile_utils.offers_allowed(out) is (level != "none")
    assert profile_utils.normalize_profile({"offers": False})["reach"] == "none"
    with pytest.raises(profile_utils.ProfileError):
        profile_utils.normalize_profile({"reach": "constantly"})


class _Msg:
    def __init__(self, kind, marked=False, when=None):
        self.type = kind
        self.content = "x"
        self.additional_kwargs = ({curiosity.CURIOSITY_MARK: True} if marked else {}) | ({"sent_at": when.isoformat()} if when else {})


def test_more_allows_a_second_extender_but_the_default_does_not():
    now = datetime.now(timezone.utc)
    turns = [_Msg("human", when=now - timedelta(minutes=9)), _Msg("ai", marked=True, when=now - timedelta(minutes=8)),
             _Msg("human", when=now - timedelta(minutes=5)), _Msg("ai", when=now - timedelta(minutes=4)), _Msg("human", when=now)]
    kw = dict(source_type="conversational", risk_active=False, energy_tier="open", proactive_taken=False)
    assert curiosity.offers_made(turns) == 1
    assert not curiosity.slot_open("tell me more about it", turns, **kw)
    assert curiosity.slot_open("tell me more about it", turns, max_offers=2, **kw)
    assert not curiosity.slot_open("tell me more about it", turns, max_offers=0, **kw)         # 0 is read as the original single one


def test_the_gap_between_offers_follows_the_level(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(curiosity, "last_offer_at", lambda username: now - timedelta(hours=3))
    assert offer_response.pacing(None, "u", now, apply=False, gap_hours=reach.settings(reach.LOW).gap_hours) == offer_response.HELD
    assert offer_response.pacing(None, "u", now, apply=False, gap_hours=reach.settings(reach.DEFAULT).gap_hours) == offer_response.CLEAR
    monkeypatch.setattr(curiosity, "last_offer_at", lambda username: now - timedelta(minutes=90))
    assert offer_response.pacing(None, "u", now, apply=False, gap_hours=reach.settings(reach.DEFAULT).gap_hours) == offer_response.HELD
    assert offer_response.pacing(None, "u", now, apply=False, gap_hours=reach.settings(reach.MORE).gap_hours) == offer_response.CLEAR


def test_the_opener_gap_follows_the_level():
    from backend.utils import initiation

    now = datetime.now(timezone.utc)
    state = {"enabled": True, "quiet_until": None, "unanswered": 0, "last_initiated_at": now - timedelta(hours=10)}
    transcript = [_Msg("human", when=now - timedelta(hours=3)), _Msg("ai", when=now - timedelta(hours=3))]
    assert initiation.refusal_reason(state, transcript, now) == "too_soon"                                        # the default 20 h
    assert initiation.refusal_reason(state, transcript, now, min_hours=reach.settings(reach.MORE).opener_hours) is None
    assert initiation.refusal_reason(state, transcript, now, min_hours=reach.settings(reach.LOW).opener_hours) == "too_soon"
    state["unanswered"] = initiation.MAX_UNANSWERED
    assert initiation.refusal_reason(state, transcript, now, min_hours=1) == "ignored"                             # more never gets past the ignored-state rule


def test_the_findings_limit_comes_from_the_level():
    from backend.utils import relevant_findings

    source = open(relevant_findings.__file__, encoding="utf-8").read()
    assert "level.findings_per_day" in source and "gap_hours=level.gap_hours" in source
