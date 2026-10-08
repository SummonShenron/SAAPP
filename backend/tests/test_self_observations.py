import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from backend.utils import self_observations as so
from backend.utils.self_counters import summarize_counters

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)


def day(days_ago, **counts):
    return {"day": (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%d"), "counts": counts}


def summary(current=None, previous=None):
    docs = []
    if current:
        docs.append(day(1, **current))
    if previous:
        docs.append(day(10, **previous))
    return summarize_counters(docs, NOW, days=7)


BIG = dict(turns_total=200, reply_fit_checked=100)


# ---------------------------------------------------------------- proposals ----------------------------------------

def test_a_rate_above_its_threshold_with_enough_data_becomes_an_observation_with_its_evidence():
    s = summary(current={**BIG, "revised_emotional": 6}, previous={**BIG, "revised_emotional": 2})
    p = {x.key: x for x in so.build_proposals(s)}["emotional_fit"]
    assert "6.0%" in p.text and "up from 2.0%" in p.text
    assert p.evidence["rate"] == 0.06 and p.evidence["previous_rate"] == 0.02 and p.evidence["denominator"] == 100
    assert p.applies_to == ["emotional"]


def test_below_the_threshold_or_with_too_little_data_there_is_nothing_to_say():
    assert [x.key for x in so.build_proposals(summary(current={**BIG, "revised_emotional": 1}))] == []
    thin = dict(turns_total=10, reply_fit_checked=10, revised_emotional=5)
    assert so.build_proposals(summary(current=thin)) == []
    assert so.build_proposals(summary()) == []


def test_each_metric_has_its_own_observation_and_context():
    s = summary(current={
        **BIG, "revised_emotional": 5, "revised_closing": 4, "revised_identity": 3, "reward_evaluated": 40,
        "reward_failed_hallucination": 4, "turn_failed_quota": 4, "route_conversational": 120,
    })
    got = {x.key: x for x in so.build_proposals(s)}
    assert set(got) == {"emotional_fit", "closing_fishing", "identity_drift", "grounded_failures", "outright_failures", "work_mix"}
    assert "stating things the data doesn't support" in got["grounded_failures"].text
    assert "usage limit" in got["outright_failures"].text
    assert "plain conversation" in got["work_mix"].text
    assert got["closing_fishing"].applies_to == ["closing"] and "self_questions" in got["identity_drift"].applies_to


def test_the_trend_wording_covers_up_down_and_about_the_same():
    up = {x.key: x for x in so.build_proposals(summary({**BIG, "revised_closing": 6}, {**BIG, "revised_closing": 3}))}["closing_fishing"]
    down = {x.key: x for x in so.build_proposals(summary({**BIG, "revised_closing": 4}, {**BIG, "revised_closing": 8}))}["closing_fishing"]
    same = {x.key: x for x in so.build_proposals(summary({**BIG, "revised_closing": 4}, {**BIG, "revised_closing": 4}))}["closing_fishing"]
    assert "up from 3.0%" in up.text and "down from 8.0%" in down.text and "about the same as before" in same.text


# ---------------------------------------------------------------- validation ---------------------------------------

def test_validation_requires_every_figure_to_match_the_evidence():
    ev = {"rate": 0.06, "previous_rate": 0.02}
    assert so.validate_text("In about 6.0% of recent replies something happened, up from 2.0%.", ev)
    assert not so.validate_text("In about 9.0% of recent replies something happened.", ev)
    assert not so.validate_text("Something happened often.", ev)  # no figure at all


def test_validation_rejects_feelings_hooks_exclamations_and_runaway_length():
    ev = {"rate": 0.06, "previous_rate": None}
    assert not so.validate_text("About 6.0% of the time I love how this goes.", ev)
    assert not so.validate_text("About 6.0% of the time I missed you.", ev)
    assert not so.validate_text("About 6.0% of replies were off!", ev)
    assert not so.validate_text("About 6.0% " + "x" * 500, ev)
    assert not so.validate_text("", ev)


def test_every_generated_observation_passes_its_own_validation_and_the_identity_check():
    from backend.utils.identity_checks import identity_reply_issue
    s = summary(current={**BIG, "revised_emotional": 5, "revised_closing": 4, "revised_identity": 3, "reward_evaluated": 40,
                         "reward_failed_incomplete": 4, "turn_failed_overloaded": 4, "route_tool_output": 100})
    for p in so.build_proposals(s):
        assert so.validate_text(p.text, p.evidence), p.text
        assert identity_reply_issue(p.text) is None, p.text


# ---------------------------------------------------------------- lifecycle ----------------------------------------

def proposal(rate=0.06, key="emotional_fit"):
    return so.Proposal(key, f"In about {so._pct(rate)} of recent replies something.", {"rate": rate, "previous_rate": None, "denominator": 100}, ["emotional"])


def test_a_new_key_starts_pending_with_the_wording_waiting_for_approval():
    doc = so.merge_proposal(None, proposal(), NOW)
    assert doc["status"] == "pending" and doc["proposed_text"] and not doc.get("approved_text")
    assert not so.is_live(doc, NOW)


def test_only_approval_makes_wording_live_and_the_approved_text_is_what_is_used():
    doc = so.approve_doc(so.merge_proposal(None, proposal(), NOW), NOW)
    assert doc["status"] == "approved" and doc["approved_text"] and doc["proposed_text"] is None
    assert so.is_live(doc, NOW)
    assert so.live_observations([doc], NOW) == [doc]


def test_approval_refuses_wording_that_fails_validation_or_when_nothing_is_waiting():
    doc = so.merge_proposal(None, proposal(), NOW)
    doc["proposed_text"] = "I love this!"
    assert so.approve_doc(doc, NOW) is None
    assert so.approve_doc({"key": "x", "status": "pending"}, NOW) is None


def test_a_small_shift_keeps_the_approved_wording_and_just_reconfirms_it():
    approved = so.approve_doc(so.merge_proposal(None, proposal(0.06), NOW), NOW)
    later = NOW + timedelta(days=30)
    doc = so.merge_proposal(approved, proposal(0.065), later)
    assert doc["approved_text"] == approved["approved_text"] and doc["proposed_text"] is None
    assert doc["last_confirmed_at"] == later.isoformat()


def test_a_material_shift_waits_for_approval_while_the_old_wording_stays_live():
    approved = so.approve_doc(so.merge_proposal(None, proposal(0.06), NOW), NOW)
    doc = so.merge_proposal(approved, proposal(0.12), NOW + timedelta(days=30))
    assert doc["approved_text"] == approved["approved_text"] and "12%" in doc["proposed_text"]
    assert so.is_live(doc, NOW + timedelta(days=30))


def test_an_observation_expires_unless_a_later_reflection_confirms_it():
    approved = so.approve_doc(so.merge_proposal(None, proposal(), NOW), NOW)
    assert so.is_live(approved, NOW + timedelta(days=so.EXPIRY_DAYS - 1))
    assert not so.is_live(approved, NOW + timedelta(days=so.EXPIRY_DAYS + 1))


def test_a_retired_observation_is_not_live_and_starts_over_if_the_data_supports_it_again():
    approved = so.approve_doc(so.merge_proposal(None, proposal(), NOW), NOW)
    retired = so.retire_doc(approved, NOW)
    assert not so.is_live(retired, NOW)
    again = so.merge_proposal(retired, proposal(), NOW + timedelta(days=5))
    assert again["status"] == "pending" and not again.get("approved_text")


def test_live_observations_come_back_in_priority_order():
    def live(key, rate):
        return so.approve_doc(so.merge_proposal(None, proposal(rate, key), NOW), NOW)
    docs = [live("work_mix", 0.5), live("closing_fishing", 0.05), live("emotional_fit", 0.05)]
    assert [d["key"] for d in so.live_observations(docs, NOW)] == ["emotional_fit", "closing_fishing", "work_mix"]


# ---------------------------------------------------------------- selection and injection --------------------------

def approved(key, applies, rate=0.05, text=None):
    p = so.Proposal(key, text or f"In about {so._pct(rate)} of recent replies {key}.", {"rate": rate, "previous_rate": None, "denominator": 100}, applies)
    return so.approve_doc(so.merge_proposal(None, p, NOW), NOW)


def test_only_observations_that_apply_to_this_turns_contexts_are_chosen():
    docs = [approved("emotional_fit", ["emotional"]), approved("closing_fishing", ["closing"]), approved("work_mix", ["self_questions"])]
    assert so.select_observations(docs, NOW, contexts=["closing"]) == [docs[1]["approved_text"]]
    assert so.select_observations(docs, NOW, contexts=[]) == []
    assert len(so.select_observations(docs, NOW, contexts=["emotional", "closing", "self_questions"])) == 3


def test_at_most_three_lines_are_chosen():
    docs = [approved(k, ["self_questions"]) for k in ("emotional_fit", "closing_fishing", "identity_drift", "work_mix")]
    assert len(so.select_observations(docs, NOW, contexts=["self_questions"])) == so.MAX_LINES


def test_expired_and_unapproved_observations_are_never_chosen():
    stale = approved("emotional_fit", ["emotional"])
    stale["last_confirmed_at"] = (NOW - timedelta(days=200)).isoformat()
    pending = so.merge_proposal(None, proposal(0.05, "closing_fishing"), NOW)
    assert so.select_observations([stale, pending], NOW, contexts=["emotional", "closing"]) == []


@pytest.mark.parametrize("message,expected", [
    ("how reliable are you, honestly?", True), ("what are your weaknesses", True), ("can i trust you with this", True),
    ("do you make mistakes", True), ("what are you good at", True), ("tell me about yourself", True),
    ("how do i reverse a list in python", False), ("can you fix this bug", False), ("", False),
])
def test_asks_about_itself(message, expected):
    assert so.asks_about_itself(message) is expected


def test_contexts_follow_the_turn():
    assert so.active_contexts(asks_self=False, closing=False, grounded=False, emotional=False) == []
    assert so.active_contexts(asks_self=True, closing=True, grounded=True, emotional=True) == ["self_questions", "closing", "grounded", "emotional"]


def test_the_prompt_block_is_quiet_by_default_and_allows_stating_numbers_only_when_asked():
    assert so.build_self_knowledge_block([], False) == ""
    quiet = so.build_self_knowledge_block(["Line one."], asks_self=False)
    assert "Line one." in quiet and "Don't recite it" in quiet and "state the relevant parts" not in quiet
    asked = so.build_self_knowledge_block(["Line one."], asks_self=True)
    assert "state the relevant parts plainly, with their numbers" in asked and "without overselling or underselling" in asked
    assert "Say only what the lines above say" in asked and "If you don't have a number" in asked


class _Col:
    def __init__(self, docs):
        self.docs = {d["key"]: dict(d) for d in docs}

    def find(self, query, projection=None):
        status = (query or {}).get("status")
        return [dict(d) for d in self.docs.values() if status is None or d.get("status") == status]

    def find_one(self, query, projection=None):
        d = self.docs.get(query["key"])
        return dict(d) if d else None

    def replace_one(self, query, doc, upsert=False):
        self.docs[query["key"]] = dict(doc)


class _DB:
    def __init__(self, docs=(), counters=()):
        self.cols = {so.COLLECTION: _Col(docs), "sonic_counters": _CounterCol(counters)}

    def __getitem__(self, name):
        return self.cols[name]


class _CounterCol:
    def __init__(self, docs):
        self.docs = list(docs)

    def find(self, query, projection=None):
        return list(self.docs)


@pytest.fixture(autouse=True)
def _fresh_cache():
    so._cache["at"], so._cache["docs"] = 0.0, []


def test_observations_for_turn_reads_the_database_once_then_uses_the_cache(monkeypatch):
    doc = approved("closing_fishing", ["closing"])
    db = _DB([doc])
    reads = []
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: (reads.append(1), db)[1])
    kwargs = dict(source_type="conversational", closing=True, emotional_active=False, risk_active=False, now=NOW)
    assert so.observations_for_turn("thanks, bye", **kwargs) == [doc["approved_text"]]
    assert so.observations_for_turn("thanks, bye", **kwargs) == [doc["approved_text"]]
    assert len(reads) == 1


def test_nothing_is_used_under_a_safety_context_or_when_no_context_applies(monkeypatch):
    doc = approved("closing_fishing", ["closing"])
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: _DB([doc]))
    base = dict(source_type="conversational", emotional_active=False, now=NOW)
    assert so.observations_for_turn("bye", closing=True, risk_active=True, **base) == []
    assert so.observations_for_turn("what is a mutex", closing=False, risk_active=False, **base) == []


def test_a_database_problem_means_no_observations_never_an_error(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("backend.utils.db_utils.get_db", boom)
    assert so.observations_for_turn("how reliable are you", source_type="conversational", closing=False,
                                    emotional_active=False, risk_active=False, now=NOW) == []
    monkeypatch.setattr("backend.utils.db_utils.get_db", lambda: None)
    so._cache["at"] = 0.0
    assert so.observations_for_turn("how reliable are you", source_type="conversational", closing=False,
                                    emotional_active=False, risk_active=False, now=NOW) == []


# ---------------------------------------------------------------- database operations -------------------------------

def test_reflect_stores_proposals_and_reports_what_the_data_does_not_support():
    counters = [day(1, **BIG, revised_emotional=6), day(10, **BIG, revised_emotional=2)]
    db = _DB(counters=counters)
    out = so.reflect(db, 7, NOW)
    assert out["proposed"] == ["emotional_fit"] and out["confirmed"] == []
    assert "closing_fishing" in out["unsupported"] and "emotional_fit" not in out["unsupported"]
    stored = db[so.COLLECTION].find_one({"key": "emotional_fit"})
    assert stored["status"] == "pending" and stored["proposed_text"] and not stored.get("approved_text")


def test_approve_then_a_second_reflection_with_a_similar_rate_only_confirms():
    counters = [day(1, **BIG, revised_emotional=6)]
    db = _DB(counters=counters)
    so.reflect(db, 7, NOW)
    assert so.approve(db, "emotional_fit", NOW)
    again = so.reflect(db, 7, NOW + timedelta(days=1))
    assert again["confirmed"] == ["emotional_fit"] and again["proposed"] == []


def test_approve_and_retire_report_failure_for_unknown_keys_and_empty_proposals():
    db = _DB()
    assert not so.approve(db, "nope", NOW) and not so.retire(db, "nope", NOW)
    db[so.COLLECTION].docs["x"] = {"key": "x", "status": "pending"}
    assert not so.approve(db, "x", NOW)


def test_retire_stops_an_observation_from_being_live():
    db = _DB(counters=[day(1, **BIG, revised_emotional=6)])
    so.reflect(db, 7, NOW)
    so.approve(db, "emotional_fit", NOW)
    assert so.retire(db, "emotional_fit", NOW)
    assert not so.is_live(db[so.COLLECTION].find_one({"key": "emotional_fit"}), NOW)


def test_the_listing_shows_live_pending_and_proposed_updates():
    db = _DB(counters=[day(1, **BIG, revised_emotional=6)])
    so.reflect(db, 7, NOW)
    text = so.format_listing(so.load_all(db), NOW)
    assert "[PENDING] emotional_fit" in text and "needs `approve emotional_fit`" in text
    so.approve(db, "emotional_fit", NOW)
    assert "[LIVE] emotional_fit" in so.format_listing(so.load_all(db), NOW)
    assert "No observations yet" in so.format_listing([], NOW)


def test_the_app_selects_observations_and_injects_them_with_the_ask_flag():
    source = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    for needle in (
        "observations_for_turn,",
        "risk_active=risk_level != RISK_NONE",
        "self_knowledge_context=build_self_knowledge_block(",
        "asks_about_itself(final_state.get(\"original_question\", question))",
        'tally(turn_counts, "observations_injected")',
    ):
        assert needle in source, needle
