import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.utils import memory_utils


def _fake_embed(text):
    """Deterministic stand-in for embedding_utils.embed_text: normalizes case/whitespace so
    near-identical text maps to the identical vector (similarity 1.0), and hashes into a large
    number of buckets so distinct phrases land in different buckets (similarity 0.0) with
    overwhelming probability for the handful of short strings used in these tests."""
    if not text or not text.strip():
        return None
    normalized = text.strip().lower()
    bucket = hash(normalized) % 9973
    vector = [0.0] * 9973
    vector[bucket] = 1.0
    return vector


@pytest.fixture(autouse=True)
def isolated_memory_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir, forces get_db() to None so every test
    exercises the local-file fallback path deterministically, fakes embeddings so no network
    call is made, and defaults the fact-conflict LLM judge to a harmless response that only
    matters for tests that actually trigger it."""
    monkeypatch.setattr(memory_utils, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(memory_utils, "get_db", lambda: None)
    monkeypatch.setattr(memory_utils, "embed_text", _fake_embed)
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "duplicate"})))
    )
    yield


def test_save_and_load_user_fact():
    saved = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == saved.id
    assert facts[0].fact == "Prefers dark mode UI."
    assert facts[0].category == "preference"
    assert facts[0].embedding is not None


def test_save_user_fact_updates_existing_fact_in_place():
    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI", category="preference")
    updated = memory_utils.save_user_fact("jack", "prefers dark mode ui", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id == updated.id


def test_save_user_fact_contradicting_statement_supersedes_instead_of_duplicating(monkeypatch):
    # Same normalized bucket as a stand-in for "semantically similar, but contradicting" —
    # the LLM judge (mocked to "duplicate" by the autouse fixture) is what actually decides
    # to update in place rather than append.
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "supersede"})))
    )
    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "prefers dark mode ui.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id
    assert facts[0].fact == "prefers dark mode ui."


def test_save_user_fact_unrelated_text_creates_new_entry():
    # Different phrases hash into different buckets -> similarity 0.0 -> below threshold ->
    # the LLM judge is never even consulted, both are kept side by side.
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Prefers concise answers.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 2


def test_save_user_fact_llm_judges_distinct_keeps_both(monkeypatch):
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: [1.0, 0.0])  # force a "similar" match
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "distinct"})))
    )
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Prefers large font sizes.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 2


def test_save_user_fact_falls_back_to_substring_match_when_embedding_unavailable(monkeypatch):
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: None)
    llm_mock = Mock()
    monkeypatch.setattr(memory_utils.lite_llm, "invoke", llm_mock)

    first = memory_utils.save_user_fact("jack", "Prefers dark mode UI", category="preference")
    updated = memory_utils.save_user_fact("jack", "prefers dark mode ui", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1
    assert facts[0].id == first.id == updated.id
    llm_mock.assert_not_called()  # no embedding means no similarity match, so no judge call either


def test_save_user_fact_invalid_category_defaults_to_preference():
    saved = memory_utils.save_user_fact("jack", "Likes concise answers.", category="not_a_real_category")
    assert saved.category == "preference"


def test_save_user_fact_dedups_across_categories():
    """The actual bug this guards against: the same real-world fact ('works at Principal')
    extracted into 'identity' once and 'career' another time used to never be recognized as a
    duplicate of itself, because dedup only ever compared same-category facts. It's now merged
    regardless of which category each mention landed in."""
    memory_utils.save_user_fact("jack", "Works at Principal.", category="identity")
    memory_utils.save_user_fact("jack", "works at principal.", category="career")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1


def test_save_user_fact_cross_category_merge_corrects_stored_category():
    """A re-observation doesn't just update the fact text — it corrects the category too, so a
    fact that was miscategorized once (e.g. an older, less consistent extraction) self-heals the
    next time the same fact is mentioned and re-extracted with a better category."""
    memory_utils.save_user_fact("jack", "Works at Principal.", category="identity")
    updated = memory_utils.save_user_fact("jack", "works at principal.", category="career")

    assert updated.category == "career"
    facts = memory_utils.load_user_facts("jack")
    assert facts[0].category == "career"


def test_save_user_fact_cross_category_supersede_also_corrects_category(monkeypatch):
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: [1.0, 0.0])  # force a "similar" match
    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "supersede"})))
    )
    memory_utils.save_user_fact("jack", "Works at Principal.", category="identity")
    updated = memory_utils.save_user_fact("jack", "Works at Acme now.", category="career")

    assert updated.category == "career"
    assert updated.fact == "Works at Acme now."
    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 1


def test_load_user_facts_filters_by_category():
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")
    memory_utils.save_user_fact("jack", "Name is Jack.", category="identity")

    identity_facts = memory_utils.load_user_facts("jack", category="identity")
    assert len(identity_facts) == 1
    assert identity_facts[0].fact == "Name is Jack."


def test_delete_user_fact():
    saved = memory_utils.save_user_fact("jack", "Likes expressive UI.", category="preference")

    assert memory_utils.delete_user_fact("jack", saved.id) is True
    assert memory_utils.load_user_facts("jack") == []
    assert memory_utils.delete_user_fact("jack", "not-a-real-id") is False


def test_delete_all_user_facts():
    memory_utils.save_user_fact("jack", "Fact one.", category="preference")
    memory_utils.save_user_fact("jack", "Fact two.", category="trait")

    memory_utils.delete_all_user_facts("jack")
    assert memory_utils.load_user_facts("jack") == []


def test_fetch_relevant_user_facts_returns_empty_when_no_facts():
    assert memory_utils.fetch_relevant_user_facts("jack", "what do you know about me?") == ""


def test_fetch_relevant_user_facts_includes_saved_fact():
    memory_utils.save_user_fact("jack", "Prefers expressive UI.", category="preference")

    context = memory_utils.fetch_relevant_user_facts("jack", "what should the UI look like?")
    assert "Prefers expressive UI." in context
    assert "KNOWN USER CONTEXT" in context


def test_fetch_relevant_user_facts_always_includes_identity_regardless_of_question(monkeypatch):
    # Give the question an embedding far away from everything, so relevance ranking alone
    # would never surface the identity fact — it must be force-included regardless.
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: {
        "Name is Jack Harper.": [1.0, 0.0, 0.0],
        "Prefers dark mode UI.": [0.0, 1.0, 0.0],
        "something totally unrelated": [0.0, 0.0, 1.0],
    }.get(text.strip(), [0.5, 0.5, 0.5]))

    memory_utils.save_user_fact("jack", "Name is Jack Harper.", category="identity")
    memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    context = memory_utils.fetch_relevant_user_facts("jack", "something totally unrelated", limit=1)
    assert "Name is Jack Harper." in context


def test_fetch_relevant_user_facts_ranks_by_relevance_to_question(monkeypatch):
    vectors = {
        "Loves discussing Python programming.": [1.0, 0.0],
        "Enjoys hiking on weekends.": [0.0, 1.0],
        "tell me about python best practices": [1.0, 0.0],
    }
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: vectors.get(text.strip(), [0.5, 0.5]))

    memory_utils.save_user_fact("jack", "Enjoys hiking on weekends.", category="trait")
    memory_utils.save_user_fact("jack", "Loves discussing Python programming.", category="trait")

    context = memory_utils.fetch_relevant_user_facts("jack", "tell me about python best practices", limit=1)
    assert "Loves discussing Python programming." in context
    assert "Enjoys hiking on weekends." not in context


def test_fetch_relevant_user_facts_falls_back_to_recency_when_question_empty():
    memory_utils.save_user_fact("jack", "Older fact.", category="trait")
    memory_utils.save_user_fact("jack", "Newer fact.", category="trait")

    context = memory_utils.fetch_relevant_user_facts("jack", "", limit=1)
    assert "Newer fact." in context
    assert "Older fact." not in context


def test_facts_are_scoped_per_username():
    memory_utils.save_user_fact("jack", "Jack's fact.", category="preference")
    memory_utils.save_user_fact("alice", "Alice's fact.", category="preference")

    assert [f.fact for f in memory_utils.load_user_facts("jack")] == ["Jack's fact."]
    assert [f.fact for f in memory_utils.load_user_facts("alice")] == ["Alice's fact."]


def _make_fact(category="trait", confidence=1.0, days_old=0, fact_id="test-id", embedding=None,
                goal_status=None, last_nudged_at=None):
    now = datetime.now(timezone.utc)
    updated = (now - timedelta(days=days_old)).isoformat()
    return memory_utils.UserFact(
        id=fact_id, username="jack", category=category, fact="test fact", source="explicit",
        confidence=confidence, created_at=updated, updated_at=updated, active=True, embedding=embedding,
        goal_status=goal_status, last_nudged_at=last_nudged_at,
    )


def test_effective_confidence_no_decay_at_zero_days():
    fact = _make_fact(confidence=1.0, days_old=0)
    assert memory_utils._effective_confidence(fact, datetime.now(timezone.utc)) == pytest.approx(1.0, abs=0.01)


def test_effective_confidence_halves_at_one_half_life():
    fact = _make_fact(confidence=1.0, days_old=memory_utils.FACT_CONFIDENCE_HALF_LIFE_DAYS)
    assert memory_utils._effective_confidence(fact, datetime.now(timezone.utc)) == pytest.approx(0.5, rel=0.05)


def test_effective_confidence_quarters_at_two_half_lives():
    fact = _make_fact(confidence=1.0, days_old=2 * memory_utils.FACT_CONFIDENCE_HALF_LIFE_DAYS)
    assert memory_utils._effective_confidence(fact, datetime.now(timezone.utc)) == pytest.approx(0.25, rel=0.05)


def test_effective_confidence_identity_never_decays():
    fact = _make_fact(category="identity", confidence=1.0, days_old=365)
    assert memory_utils._effective_confidence(fact, datetime.now(timezone.utc)) == 1.0


def test_save_user_fact_duplicate_reinforces_confidence():
    seeded = _make_fact(confidence=0.5, fact_id="seed-id", embedding=memory_utils.embed_text("Prefers dark mode UI."))
    seeded.fact = "Prefers dark mode UI."
    memory_utils.save_user_facts("jack", [seeded])

    reinforced = memory_utils.save_user_fact("jack", "prefers dark mode ui.", category="trait")
    assert reinforced.id == "seed-id"
    assert reinforced.confidence == pytest.approx(0.5 + memory_utils.FACT_REINFORCEMENT_INCREMENT)


def test_save_user_fact_reinforcement_caps_at_one():
    seeded = _make_fact(confidence=0.95, fact_id="seed-id", embedding=memory_utils.embed_text("Prefers dark mode UI."))
    seeded.fact = "Prefers dark mode UI."
    memory_utils.save_user_facts("jack", [seeded])

    reinforced = memory_utils.save_user_fact("jack", "prefers dark mode ui.", category="trait")
    assert reinforced.confidence == 1.0


def test_save_user_fact_supersede_resets_confidence_to_baseline(monkeypatch):
    seeded = _make_fact(confidence=0.9, fact_id="seed-id", embedding=memory_utils.embed_text("Prefers dark mode UI."))
    seeded.fact = "Prefers dark mode UI."
    memory_utils.save_user_facts("jack", [seeded])

    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "supersede"})))
    )
    # Same normalized bucket as a stand-in for "semantically similar, but contradicting" (see
    # test_save_user_fact_contradicting_statement_supersedes_instead_of_duplicating) — the
    # mocked LLM judgment above is what actually decides "supersede" here, not the wording.
    superseded = memory_utils.save_user_fact("jack", "prefers dark mode ui.", category="trait", confidence=0.7)
    assert superseded.id == "seed-id"
    assert superseded.confidence == 0.7  # resets to the passed-in baseline, not accumulated


def test_fetch_relevant_user_facts_prefers_fresh_over_decayed_at_equal_similarity(monkeypatch):
    # Force equal similarity so the test isolates confidence/decay's effect on ranking.
    monkeypatch.setattr(memory_utils, "cosine_similarity", lambda a, b: 0.9)
    monkeypatch.setattr(memory_utils, "embed_text", lambda text: [1.0] if text else None)

    old_fact = _make_fact(fact_id="old-id", confidence=1.0, days_old=2000, embedding=[1.0])
    old_fact.fact = "Old but equally similar fact."
    fresh_fact = _make_fact(fact_id="fresh-id", confidence=1.0, days_old=0, embedding=[1.0])
    fresh_fact.fact = "Fresh equally similar fact."
    memory_utils.save_user_facts("jack", [old_fact, fresh_fact])

    context = memory_utils.fetch_relevant_user_facts("jack", "some question", limit=1)
    assert "Fresh equally similar fact." in context
    assert "Old but equally similar fact." not in context


def test_category_expansion_new_categories_round_trip():
    for category in ("career", "project", "goal", "relationship"):
        saved = memory_utils.save_user_fact("jack", f"A {category} fact.", category=category)
        assert saved.category == category


def test_pattern_category_is_valid():
    saved = memory_utils.save_user_fact("jack", "Consistently gravitates toward agent systems.", category="pattern")
    assert saved.category == "pattern"


# ---------------------------------------------------------------------------
# Fact-store cap — tested directly against _prune_excess_facts (pure function,
# no embeddings/DB involved) rather than through save_user_fact's full dedup
# pipeline, so it isn't at the mercy of hash-bucket collisions at scale. Reuses
# the _make_fact helper defined above (category/confidence/days_old/fact_id).

def test_prune_excess_facts_noop_under_cap(monkeypatch):
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 10)
    facts = [_make_fact(fact_id=str(i)) for i in range(5)]
    assert memory_utils._prune_excess_facts(facts) == facts


def test_prune_excess_facts_drops_lowest_confidence_first(monkeypatch):
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 2)
    high = _make_fact(fact_id="high", confidence=1.0)
    mid = _make_fact(fact_id="mid", confidence=0.5)
    low = _make_fact(fact_id="low", confidence=0.1)

    result = memory_utils._prune_excess_facts([low, mid, high])

    assert {f.id for f in result} == {"high", "mid"}


def test_prune_excess_facts_never_drops_identity(monkeypatch):
    # 2 identity facts + a cap of 3 leaves exactly 1 slot for non-identity facts.
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 3)
    identity1 = _make_fact(fact_id="id1", category="identity", confidence=0.01)
    identity2 = _make_fact(fact_id="id2", category="identity", confidence=0.01)
    high_pref = _make_fact(fact_id="pref-high", category="preference", confidence=1.0)
    low_pref = _make_fact(fact_id="pref-low", category="preference", confidence=0.2)

    result = memory_utils._prune_excess_facts([identity1, identity2, high_pref, low_pref])

    # Both identity facts survive even though that pushes the total over the cap — capacity
    # for non-identity facts shrinks accordingly (only the highest-confidence one fits).
    result_ids = {f.id for f in result}
    assert {"id1", "id2"} <= result_ids
    assert "pref-high" in result_ids
    assert "pref-low" not in result_ids


def test_prune_excess_facts_protects_the_just_saved_fact_even_if_low_confidence(monkeypatch):
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 2)
    just_saved = _make_fact(fact_id="new", category="preference", confidence=0.05)  # freshly created, barely any confidence yet
    older_high = _make_fact(fact_id="old-high", category="preference", confidence=1.0)
    older_mid = _make_fact(fact_id="old-mid", category="preference", confidence=0.5)

    result = memory_utils._prune_excess_facts([older_high, older_mid, just_saved], protect_id="new")

    result_ids = {f.id for f in result}
    assert "new" in result_ids  # protected regardless of its own confidence
    assert "old-high" in result_ids
    assert "old-mid" not in result_ids


def test_save_user_fact_prunes_when_cap_exceeded(monkeypatch):
    monkeypatch.setattr(memory_utils, "MAX_STORED_FACTS", 3)
    for i in range(5):
        memory_utils.save_user_fact("jack", f"Distinct fact number {i}.", category="preference")

    facts = memory_utils.load_user_facts("jack")
    assert len(facts) == 3
    # The most recently saved fact is always protected, so it must have survived.
    assert any(f.fact == "Distinct fact number 4." for f in facts)


# ---------------------------------------------------------------------------
# Goal/project lifecycle status + stale check-in nudge
# ---------------------------------------------------------------------------

def test_user_fact_goal_status_and_last_nudged_at_default_to_none():
    fact = _make_fact()
    assert fact.goal_status is None
    assert fact.last_nudged_at is None

    memory_utils.save_user_facts("jack", [fact])
    reloaded = memory_utils.load_user_facts("jack")[0]
    assert reloaded.goal_status is None
    assert reloaded.last_nudged_at is None


def test_save_user_fact_new_goal_and_project_default_to_active_status():
    goal = memory_utils.save_user_fact("jack", "Wants to learn Spanish.", category="goal")
    project = memory_utils.save_user_fact("jack", "Building a side app.", category="project")
    pref = memory_utils.save_user_fact("jack", "Prefers dark mode UI.", category="preference")

    assert goal.goal_status == "active"
    assert project.goal_status == "active"
    assert pref.goal_status is None


def test_save_user_fact_supersede_on_goal_marks_achieved(monkeypatch):
    seeded = _make_fact(
        category="goal", fact_id="goal-id",
        embedding=memory_utils.embed_text("Wants to learn Spanish."),
    )
    seeded.fact = "Wants to learn Spanish."
    seeded.goal_status = "active"
    memory_utils.save_user_facts("jack", [seeded])

    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(side_effect=[
            SimpleNamespace(content=json.dumps({"action": "supersede"})),
            SimpleNamespace(content=json.dumps({"status": "achieved"})),
        ]),
    )
    updated = memory_utils.save_user_fact("jack", "wants to learn spanish.", category="goal")

    assert updated.id == "goal-id"
    assert updated.goal_status == "achieved"


def test_save_user_fact_supersede_on_project_marks_abandoned(monkeypatch):
    seeded = _make_fact(
        category="project", fact_id="project-id",
        embedding=memory_utils.embed_text("Building a side app."),
    )
    seeded.fact = "Building a side app."
    seeded.goal_status = "active"
    memory_utils.save_user_facts("jack", [seeded])

    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(side_effect=[
            SimpleNamespace(content=json.dumps({"action": "supersede"})),
            SimpleNamespace(content=json.dumps({"status": "abandoned"})),
        ]),
    )
    updated = memory_utils.save_user_fact("jack", "building a side app.", category="project")

    assert updated.id == "project-id"
    assert updated.goal_status == "abandoned"


def test_save_user_fact_duplicate_on_goal_does_not_call_goal_status_judge(monkeypatch):
    seeded = _make_fact(
        category="goal", fact_id="goal-id",
        embedding=memory_utils.embed_text("Wants to learn Spanish."),
    )
    seeded.fact = "Wants to learn Spanish."
    seeded.goal_status = "active"
    memory_utils.save_user_facts("jack", [seeded])

    # Autouse fixture already stubs lite_llm.invoke to return "duplicate" by default.
    fake_judge = Mock()
    monkeypatch.setattr(memory_utils, "_judge_goal_status", fake_judge)

    memory_utils.save_user_fact("jack", "wants to learn spanish.", category="goal")

    fake_judge.assert_not_called()


def test_save_user_fact_supersede_on_non_goal_category_does_not_call_goal_status_judge(monkeypatch):
    seeded = _make_fact(fact_id="pref-id", embedding=memory_utils.embed_text("Prefers dark mode UI."))
    seeded.fact = "Prefers dark mode UI."
    memory_utils.save_user_facts("jack", [seeded])

    monkeypatch.setattr(
        memory_utils.lite_llm, "invoke",
        Mock(return_value=SimpleNamespace(content=json.dumps({"action": "supersede"})))
    )
    fake_judge = Mock()
    monkeypatch.setattr(memory_utils, "_judge_goal_status", fake_judge)

    memory_utils.save_user_fact("jack", "prefers light mode ui.", category="preference")

    fake_judge.assert_not_called()


def test_find_stale_goal_to_nudge_returns_none_when_nothing_qualifies():
    assert memory_utils.find_stale_goal_to_nudge("jack") is None

    memory_utils.save_user_facts("jack", [_make_fact(category="preference", days_old=100)])
    assert memory_utils.find_stale_goal_to_nudge("jack") is None


def test_find_stale_goal_to_nudge_respects_interval():
    interval = memory_utils.GOAL_NUDGE_INTERVAL_DAYS
    fresh = _make_fact(category="goal", fact_id="fresh", goal_status="active", days_old=interval - 1)
    stale = _make_fact(category="goal", fact_id="stale", goal_status="active", days_old=interval + 1)

    memory_utils.save_user_facts("jack", [fresh])
    assert memory_utils.find_stale_goal_to_nudge("jack") is None

    memory_utils.save_user_facts("jack", [stale])
    result = memory_utils.find_stale_goal_to_nudge("jack")
    assert result is not None
    assert result.id == "stale"


def test_find_stale_goal_to_nudge_respects_last_nudged_at_not_just_updated_at():
    now_iso = datetime.now(timezone.utc).isoformat()
    fact = _make_fact(category="goal", goal_status="active", days_old=30, last_nudged_at=now_iso)
    memory_utils.save_user_facts("jack", [fact])

    assert memory_utils.find_stale_goal_to_nudge("jack") is None


def test_find_stale_goal_to_nudge_excludes_achieved_and_abandoned():
    achieved = _make_fact(category="goal", fact_id="achieved", goal_status="achieved", days_old=100)
    abandoned = _make_fact(category="project", fact_id="abandoned", goal_status="abandoned", days_old=100)
    memory_utils.save_user_facts("jack", [achieved, abandoned])

    assert memory_utils.find_stale_goal_to_nudge("jack") is None


def test_find_stale_goal_to_nudge_excludes_non_goal_project_categories():
    old_pref = _make_fact(category="preference", fact_id="pref", days_old=100)
    memory_utils.save_user_facts("jack", [old_pref])

    assert memory_utils.find_stale_goal_to_nudge("jack") is None


def test_find_stale_goal_to_nudge_picks_most_overdue_when_multiple_qualify():
    less_stale = _make_fact(category="goal", fact_id="less-stale", goal_status="active", days_old=20)
    more_stale = _make_fact(category="project", fact_id="more-stale", goal_status="active", days_old=40)
    memory_utils.save_user_facts("jack", [less_stale, more_stale])

    result = memory_utils.find_stale_goal_to_nudge("jack")
    assert result.id == "more-stale"


def test_fetch_goal_nudge_context_empty_when_nothing_stale():
    assert memory_utils.fetch_goal_nudge_context("jack") == ""


def test_fetch_goal_nudge_context_returns_block_and_updates_last_nudged_at():
    stale = _make_fact(category="goal", fact_id="stale-id", goal_status="active",
                        days_old=memory_utils.GOAL_NUDGE_INTERVAL_DAYS + 1)
    stale.fact = "Wants to learn Spanish."
    memory_utils.save_user_facts("jack", [stale])

    context = memory_utils.fetch_goal_nudge_context("jack")

    assert "STALE GOAL CHECK-IN" in context
    assert "Wants to learn Spanish." in context

    reloaded = next(f for f in memory_utils.load_user_facts("jack") if f.id == "stale-id")
    assert reloaded.last_nudged_at is not None
    nudged_at = datetime.fromisoformat(reloaded.last_nudged_at)
    assert (datetime.now(timezone.utc) - nudged_at).total_seconds() < 5


def test_fetch_goal_nudge_context_never_raises_on_internal_error(monkeypatch):
    def _boom(username, interval_days=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(memory_utils, "find_stale_goal_to_nudge", _boom)

    assert memory_utils.fetch_goal_nudge_context("jack") == ""
