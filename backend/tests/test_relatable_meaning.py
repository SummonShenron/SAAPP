import pathlib
from types import SimpleNamespace

import pytest

from backend.components.sonic_profile import INTERESTS, RELATABLE
from backend.utils import proactive_gates as pg
from backend.utils import relatable_utils as ru
from backend.utils import self_counters

OPEN = dict(source_type="conversational", risk_active=False, energy_tier="open", closing=False)
ENTRIES = RELATABLE + INTERESTS


def vectors_pointing_at(entry_id):
    """Each entry gets its own axis; a message vector pointing along one entry's axis is closest in meaning to that entry and nothing else."""
    axes = {entry["id"]: [1.0 if i == k else 0.0 for k in range(len(ENTRIES))] for i, entry in enumerate(ENTRIES)}
    return axes, axes[entry_id]


def pick(message, vector=None, vectors=None, recent=(), **overrides):
    return ru.select_relatable(message, list(recent), vector=vector, vectors=vectors, **{**OPEN, **overrides})


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(ru, "_vectors", {})
    monkeypatch.setattr(ru, "_last_attempt", -ru.MEANING_RETRY_SECONDS)


def test_a_message_with_no_trigger_word_gets_the_entry_closest_in_meaning_marked_as_such():
    vectors, vector = vectors_pointing_at("intermittent_failures")
    entry = pick("it passes on one run and fails on the next", vector, vectors)
    assert entry["id"] == "intermittent_failures" and entry["match"] == "meaning"
    assert "match" not in next(e for e in RELATABLE if e["id"] == "intermittent_failures")  # a copy: the profile's own entry is never changed


def test_a_trigger_word_still_wins_over_meaning_and_is_not_marked():
    vectors, vector = vectors_pointing_at("naming")
    entry = pick("any idea how to bisect this, which commit broke it?", vector, vectors)
    assert entry["id"] == "find_the_commit" and "match" not in entry


def test_a_weak_overlap_gets_nothing():
    vectors, _ = vectors_pointing_at("naming")
    halfway = [0.5] * len(ENTRIES)  # equally near everything, and not near enough to any
    assert pick("something unrelated", halfway, vectors) is None
    assert ru.RELATABLE_MEANING_MIN >= 0.62  # above the highest unrelated score measured when it was set (0.616)


def test_without_a_vector_or_entry_vectors_it_behaves_exactly_as_before():
    vectors, vector = vectors_pointing_at("naming")
    assert pick("what's the capital of France") is None
    assert pick("what's the capital of France", vector, None) is None
    assert pick("what's the capital of France", None, vectors) is None
    assert pick("what's the capital of France", vector, {}) is None


@pytest.mark.parametrize("override", [{"risk_active": True}, {"closing": True}, {"energy_tier": "subdued"}, {"source_type": "kb_strict"}])
def test_meaning_goes_through_the_same_gates_as_a_trigger_word(override):
    vectors, vector = vectors_pointing_at("scifi")
    assert pick("a book about colonists", vector, vectors) is not None
    assert pick("a book about colonists", vector, vectors, **override) is None


def test_the_rate_limit_applies_to_a_meaning_match_too():
    vectors, vector = vectors_pointing_at("scifi")
    marked = SimpleNamespace(additional_kwargs={ru.RELATABLE_MARK: True})
    assert pick("a book about colonists", vector, vectors, recent=[marked]) is None


def test_entry_text_is_the_line_and_its_trigger_words():
    entry = RELATABLE[0]
    assert ru.entry_text(entry) == f"{entry['line']} ({', '.join(entry['triggers'])})"


def test_entries_are_embedded_once_per_process():
    calls = []

    def embed(text):
        calls.append(text)
        return [1.0, 0.0]

    first = ru.entry_vectors(embed)
    assert set(first) == {entry["id"] for entry in ENTRIES} and len(calls) == len(ENTRIES)
    ru.entry_vectors(embed)
    assert len(calls) == len(ENTRIES)  # nothing is embedded again


def test_a_failed_embedding_is_retried_only_after_the_wait_and_never_raises(monkeypatch):
    attempts = []

    def embed(text):
        attempts.append(text)
        raise RuntimeError("service down")

    assert ru.entry_vectors(embed) == {} and len(attempts) == len(ENTRIES)
    assert ru.entry_vectors(embed) == {} and len(attempts) == len(ENTRIES)  # too soon: not called again every turn
    monkeypatch.setattr(ru, "_last_attempt", ru.time.monotonic() - ru.MEANING_RETRY_SECONDS - 1)
    assert ru.entry_vectors(lambda text: [1.0]) != {}
    assert len(attempts) == len(ENTRIES)


def test_an_empty_embedding_leaves_that_entry_out():
    only = ENTRIES[0]
    out = ru.entry_vectors(lambda text: [1.0] if text == ru.entry_text(only) else None)
    assert set(out) == {only["id"]}


# ---- why a turn brought nothing up -----------------------------------------------------------------------------

QUIET = dict(brought_something_up=False, guest=False, risk_active=False, closing=False, energy_tier="open", source_type="conversational", rate_limited=False)


def test_a_turn_that_brought_something_up_has_nothing_to_explain():
    assert pg.quiet_reason(**{**QUIET, "brought_something_up": True, "risk_active": True}) is None


@pytest.mark.parametrize("override,expected", [
    ({}, "nothing_matched"),
    ({"guest": True, "risk_active": True}, "guest"),
    ({"risk_active": True, "closing": True}, "risk"),
    ({"closing": True, "energy_tier": "subdued"}, "closing"),
    ({"energy_tier": "subdued", "source_type": "kb_strict"}, "energy"),
    ({"source_type": "kb_strict", "rate_limited": True}, "route"),
    ({"rate_limited": True}, "rate_limit"),
    ({"source_type": "tool_output"}, "nothing_matched"),
])
def test_the_first_gate_that_applies_is_the_reason(override, expected):
    assert pg.quiet_reason(**{**QUIET, **override}) == expected


def test_every_reason_is_in_the_counters_closed_list_and_counts_by_key():
    reasons = {pg.quiet_reason(**{**QUIET, **o}) for o in ({}, {"guest": True}, {"risk_active": True}, {"closing": True}, {"energy_tier": "x"}, {"source_type": "x"}, {"rate_limited": True})}
    assert reasons == set(self_counters._GROUPS["proactive_quiet"])
    counts = {}
    for reason in reasons:
        self_counters.tally(counts, "proactive_quiet", reason)
    assert counts == {f"proactive_quiet_{reason}": 1 for reason in reasons}
    self_counters.tally(counts, "proactive_quiet", "made_up")
    assert "proactive_quiet_made_up" not in counts  # an unknown reason is never recorded
    self_counters.tally(counts, "relatable_by_meaning")
    assert counts["relatable_by_meaning"] == 1


def test_the_app_computes_the_embedding_before_choosing_the_line_and_counts_both_new_things():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    embedding = "question_embedding = await asyncio.to_thread(embed_text, question) if question else None"
    assert source.count(embedding) == 1  # still one embedding call a turn, now made before the relatable line
    assert source.index(embedding) < source.index("relatable_entry = None if (open_loop_entry or headsup_entry) else select_relatable(")
    for needle in (
        "vector=question_embedding, vectors=await asyncio.to_thread(entry_vectors, embed_text)",
        'tally(turn_counts, "relatable_by_meaning")',
        "proactive_gates.quiet_reason(",
        'tally(turn_counts, "proactive_quiet", quiet)',
    ):
        assert needle in source, needle
