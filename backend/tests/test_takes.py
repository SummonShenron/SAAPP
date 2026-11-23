"""Sonic's takes on its interests (backend/components/sonic_takes.py, backend/utils/takes.py): drafts until approved, used only while their piece is live on the shelf, and chosen through
the relatable pool. No model; the shelf is the shared FakeDB."""
import os
from datetime import datetime, timezone

from backend.components import sonic_takes
from backend.tests.fake_runner_db import FakeDB
from backend.utils import reading_shelf, relatable_utils, self_counters
from backend.utils import takes

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOW = datetime.now(timezone.utc)
DICE = next(t for t in sonic_takes.TAKES if t["id"] == "take_dice_probability")


def test_the_registry_is_clean_and_jack_approved_all_eleven_by_id():
    assert takes.check_registry() == []
    assert len(sonic_takes.APPROVED_IDS) == 11 and set(sonic_takes.APPROVED_IDS) == {t["id"] for t in sonic_takes.TAKES}
    assert len(takes.approved()) == 11


def test_a_take_that_is_not_approved_is_never_used():
    assert takes.approved(sonic_takes.TAKES, approved_ids=()) == []
    assert takes.approved(sonic_takes.TAKES + [{"id": "take_new_draft"}], approved_ids=sonic_takes.APPROVED_IDS) == sonic_takes.TAKES      # a later draft is not approved by accident


def test_the_registry_check_catches_bad_entries():
    bad = [{"id": "Take-1", "topic": "x", "source_url": "u", "line": "I lean toward x.", "triggers": ["x"]}, {"id": "take_ok_one", "topic": "", "source_url": "u", "line": "I read y.", "triggers": ["y"]}]
    problems = takes.check_registry(bad, approved_ids=("take_missing",))
    assert any("bad id" in p for p in problems) and any("missing topic" in p for p in problems)
    assert any("must say it was read" in p for p in problems) and any("approved id has no take" in p for p in problems)


def test_every_take_is_a_leaning_never_a_verdict_or_a_feeling():
    for take in sonic_takes.TAKES:
        line = take["line"].lower()
        assert ("i lean toward" in line or "i'd rather" in line or "i'd look" in line), take["id"]
        for banned in ("great article", "loved", "i love", "enjoyed", "i played", "i watched", "i felt", "favorite"):
            assert banned not in line, (take["id"], banned)


def test_a_take_is_live_only_while_its_piece_is_on_the_shelf():
    shelf = [{"url": DICE["source_url"] + "/"}, {"url": "https://example.com/other"}]
    assert takes.live_from([DICE], shelf) == [DICE]                                           # the same page, spelled with a trailing slash
    assert takes.live_from([DICE], [{"url": "https://example.com/other"}]) == []
    assert takes.live_from([DICE], []) == []


def test_live_takes_reads_the_live_shelf_for_approved_takes(monkeypatch):
    monkeypatch.setattr(takes, "APPROVED_IDS", ("take_dice_probability",))
    monkeypatch.setattr(reading_shelf, "live_items", lambda db, now=None: [{"url": DICE["source_url"]}])
    assert [t["id"] for t in takes.live_takes(FakeDB(), use_cache=False)] == ["take_dice_probability"]
    monkeypatch.setattr(reading_shelf, "live_items", lambda db, now=None: [])
    assert takes.live_takes(FakeDB(), use_cache=False) == []
    def broken(db, now=None):
        raise RuntimeError("down")
    monkeypatch.setattr(reading_shelf, "live_items", broken)
    assert takes.live_takes(FakeDB(), use_cache=False) == []
    assert takes.live_takes(None) == []


def test_a_take_in_the_pool_is_chosen_like_a_relatable_line():
    pool = [DICE]
    chosen = relatable_utils.select_relatable("my d20 modifiers feel off in this campaign", [], source_type="conversational", risk_active=False, energy_tier="open",
                                              closing=False, entries=pool)
    assert chosen == DICE and takes.is_take(chosen) and not takes.is_take({"id": "naming"})


def test_the_reply_adds_live_takes_to_the_pool_and_counts_an_offered_take():
    app = open(os.path.join(REPO, "app.py"), encoding="utf-8").read()
    assert "relatable_pool = RELATABLE_LINES + RELATABLE_INTERESTS + await asyncio.to_thread(takes_utils.live_takes, get_db())" in app
    assert "entries=relatable_pool," in app and "entry_vectors, embed_text, relatable_pool" in app
    assert 'if takes_utils.is_take(relatable_entry):\n                    tally(turn_counts, "take_offered")' in app
    assert "take_offered" in self_counters.KEYS
