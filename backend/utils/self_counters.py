"""Aggregate counters about how Sonic itself is behaving: the raw material for a grounded picture of its own
tendencies (see backend/components/sonic_profile.py for who it is; this is what it measurably does).

One document per day, `{"day": "2026-10-07", "counts": {"turns_total": 41, "revised_identity": 2, ...}}`, updated
with a single `$inc` per turn. By construction it cannot hold anything about a person or a conversation:

- there is no user id, session id or text field anywhere in it;
- a count is only ever recorded under a key from a fixed list (below), so a model-written tag or any free text
  is dropped or mapped to "other" before it gets near the database.

Read it back with:

    python -m backend.utils.self_counters             # the last 7 days, against the 7 before
    python -m backend.utils.self_counters --days 30
"""
import argparse
import datetime
import logging
from datetime import timezone
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger("SASS Logger")

COUNTERS_COLLECTION = "sonic_counters"
RETENTION_SECONDS = 400 * 24 * 3600
_index_ready = False

# What the key groups may contain. Anything else is dropped (or, for the reward tag, mapped to "other").
ROUTES = ("kb_strict", "kb_open", "web", "tool_output", "conversational")
REVISION_KINDS = ("safety", "emotional", "identity", "encouragement", "closing")
REWARD_TAGS = ("hallucination", "incorrect_filter", "formatting", "incomplete", "other")
FAILURE_KINDS = ("quota", "overloaded", "other")
from backend.components.sonic_leanings import LEANINGS  # noqa: E402  (plain data)
from backend.utils.feeling_questions import KINDS as FEELING_KINDS  # noqa: E402  (a plain tuple of names; that module imports nothing from here)
from backend.utils.code_plan import PLAN_STOP_REASONS  # noqa: E402  (a plain tuple of names)

_GROUPS: Dict[str, tuple] = {
    "route": ROUTES,
    "revised": REVISION_KINDS,
    "reward_failed": REWARD_TAGS,
    "turn_failed": FAILURE_KINDS,
    # Questions about Sonic's own feelings, by kind, and the identity rewrites on turns with and without one (backend/utils/feeling_questions.py).
    "feeling_asked": FEELING_KINDS,
    # Which working leanings were put in a tool turn's prompt (backend/utils/leanings.py): one count per entry, so one that never matches can be reworded or removed.
    "leaning_used": tuple(entry["id"] for entry in LEANINGS),
    # What happened after a reply that used a leaning (backend/utils/leanings.py): a message followed, it read as pushback, it praised, or the turn hit its step limit. Evidence of which leanings earn their place, never a target.
    "leaning_followed": tuple(entry["id"] for entry in LEANINGS),
    "leaning_pushback": tuple(entry["id"] for entry in LEANINGS),
    "leaning_praise": tuple(entry["id"] for entry in LEANINGS),
    "leaning_capped": tuple(entry["id"] for entry in LEANINGS),
    "feeling_revised": FEELING_KINDS + ("other",),
    # Why maybe_open_conversation (backend/utils/initiation.py) declined to open a conversation: one count per reason, and no_anchor for nothing to follow up on.
    # Why a turn carrying out an approved plan ended (backend/utils/code_plan.py PLAN_STOP_REASONS): one count per reason, "plan_done" being the only good one.
    "code_plan_stop": PLAN_STOP_REASONS,
    "initiation_refused": ("guest", "disabled", "recent_risk", "ignored", "too_soon", "new_thread", "already_waiting", "recent_activity", "unknown_gap", "no_anchor", "opener_off", "reach_none", "low_power"),
}
_PLAIN_KEYS = ("turns_total", "self_model_used", "experience_captured", "experience_capture_merged", "experience_capture_invalid", "experience_offered", "experience_cited", "native_runs", "native_calls", "native_chars_sent", "native_chars_results", "native_chars_instructions", "native_chars_tools", "native_chars_preferences", "native_chars_architecture", "native_chars_memory", "native_chars_history", "native_chars_leanings", "leaning_turns", "native_chars_environment", "native_chars_request", "code_plan_turns", "code_plan_exec_tool_steps", "code_plan_exec_early", "code_plan_fed_next", "code_plan_turn_cap_hit", "code_plan_submitted", "code_plan_invalid", "code_plan_approved", "code_plan_cancelled", "code_plan_exec_turns", "code_plan_exec_cap_hit", "code_plan_exec_edited", "code_plan_steps_done", "code_plan_prefetched", "code_plan_deviation", "code_pushback", "code_frustrated", "code_proposals", "code_proposal_rework", "code_edit_calls", "code_edit_errors", "code_test_runs", "code_edit_applied", "code_edit_declined", "code_edit_undone", "code_edit_applied_small", "code_edit_applied_large", "code_edit_declined_small", "code_edit_declined_large", "code_edit_undone_small", "code_edit_undone_large", "working_notes_updated", "working_notes_item_added", "working_notes_item_dropped", "working_notes_injected", "working_notes_chat_read", "reply_fit_checked", "reward_evaluated", "relatable_offered", "self_mention", "observations_injected", "encouragement_offered", "open_loop_captured", "open_loop_offered", "open_loop_resolved", "callback_offered", "self_history_offered", "initiation_offered", "initiation_fallback", "initiation_answered", "initiation_interest_offered", "initiation_interest_answered", "duration_offered", "curiosity_offered", "curiosity_taken", "shelf_offered", "shelf_taken", "shelf_listed", "finding_selected", "lean_offered", "guardrail_applied", "routines_offered", "followups_offered", "task_results_offered", "meeting_turns", "headsup_offered", "clarify_noted", "clarify_asked", "clarify_answered", "memory_note_dropped", "memory_note_trimmed", "status_offered", "offer_made", "offer_taken", "offer_passed", "offer_held_back", "code_turns", "code_cap_hit", "code_edited", "code_verified", "code_followed", "code_redo")
# The rhythm of a coding turn (backend/utils/loop_rhythm.py): turns it was followed on, turns that acted at all, reads before the first act (summed), edits applied, applied edits checked in the next
# step, and reading-streak notices shown. Counts only.
RHYTHM_KEYS = ("code_rhythm_turns", "code_rhythm_acted_turns", "code_rhythm_reads_before_first_act", "code_rhythm_applies", "code_rhythm_applies_checked", "code_rhythm_streak_notices")
# Sonic's place tools (backend/services/maps_tools.py): turns that were given them, calls of each, and calls that came back as errors. Counts only: no place, no query, no person.
MAPS_KEYS = ("maps_turns", "maps_find_places", "maps_travel_times", "maps_errors")
# How much of a coding turn's reading was the right material (backend/utils/reading_waste.py): turns that read, distinct files read, re-reads, searches and empty ones, whole-tree listings, find_file calls, and
# in turns where an edit was applied: how many files were read and how many of those were the ones edited. Counts only.
READING_KEYS = ("reading_turns", "reading_files_read", "reading_rereads", "reading_searches", "reading_empty_searches", "reading_tree_listings", "reading_find_files", "reading_edit_turns",
                "reading_edit_turn_files_read", "reading_edit_turn_files_used")
# Turns that were given the connected folder's own guidance file (backend/utils/project_guidance.py).
GUIDANCE_KEYS = ("project_guidance_turns",)
# A saved plan changed because they said it moved or was cancelled (backend/utils/plan_changes.py): rewritten with the new day, removed, and turns where the model was asked and changed nothing.
PLAN_CHANGE_KEYS = ("plan_change_updated", "plan_change_cancelled", "plan_change_unchanged")
_PLAIN_KEYS = _PLAIN_KEYS + RHYTHM_KEYS + MAPS_KEYS + READING_KEYS + GUIDANCE_KEYS + PLAN_CHANGE_KEYS
KEYS = frozenset(
    list(_PLAIN_KEYS) + [f"{group}_{name}" for group, names in _GROUPS.items() for name in names]
)


def new_turn_counts() -> Dict[str, int]:
    """The tally for one turn, which starts as one turn."""
    return {"turns_total": 1}


def revision_kind(tag: Optional[str]) -> str:
    """The kind of reply-fit rewrite a revision tag belongs to. The emotional check has several tags; they are
    one kind here, so the key list stays closed."""
    if tag == "safety":
        return "safety"
    if tag == "identity":
        return "identity"
    if tag == "encouragement":
        return "encouragement"
    if tag == "closing_engagement":
        return "closing"
    return "emotional"


def tally(counts: Dict[str, int], group: str, name: Optional[str] = None) -> None:
    """Adds one to a counter on this turn's tally. `tally(c, "reply_fit_checked")` for a plain key,
    `tally(c, "route", source_type)` for a grouped one. An unknown key is ignored, and an unknown reward or failure
    value is counted as "other", so nothing outside the fixed list is ever recorded."""
    try:
        if group in _GROUPS:
            if name not in _GROUPS[group]:
                # A failure with a missing or unrecognized label still happened, so it is counted as "other".
                if group in ("reward_failed", "turn_failed"):
                    name = "other"
                else:
                    return
            key = f"{group}_{name}"
        else:
            key = group
        if key in KEYS:
            counts[key] = counts.get(key, 0) + 1
    except Exception:
        logger.warning("[self counters] could not tally %r.", group, exc_info=True)


def record_turn_counts(counts: Dict[str, int], now: Optional[datetime.datetime] = None) -> None:
    """Writes one turn's tally with a single upsert. Blocking (a database write), so call it off the event loop.
    Never raises: counting must not be able to break a reply."""
    global _index_ready
    try:
        clean = {key: int(value) for key, value in (counts or {}).items() if key in KEYS and int(value) > 0}
        if not clean:
            return
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is None:
            return
        now = now or datetime.datetime.now(timezone.utc)
        if not _index_ready:
            db[COUNTERS_COLLECTION].create_index("day_dt", expireAfterSeconds=RETENTION_SECONDS)
            db[COUNTERS_COLLECTION].create_index("day", unique=True)
            _index_ready = True
        day = now.strftime("%Y-%m-%d")
        db[COUNTERS_COLLECTION].update_one(
            {"day": day},
            {
                "$inc": {f"counts.{key}": value for key, value in clean.items()},
                "$setOnInsert": {"day_dt": now.replace(hour=0, minute=0, second=0, microsecond=0)},
            },
            upsert=True,
        )
    except Exception:
        logger.warning("[self counters] could not record this turn's counts.", exc_info=True)


def _totals(docs: Iterable[Dict[str, Any]], start_day: str, end_day: str) -> Dict[str, int]:
    """Counts summed over the days start_day <= day < end_day."""
    totals: Dict[str, int] = {}
    for doc in docs:
        day = doc.get("day")
        if not isinstance(day, str) or not (start_day <= day < end_day):
            continue
        for key, value in (doc.get("counts") or {}).items():
            if key in KEYS and isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + int(value)
    return totals


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _rates(totals: Dict[str, int]) -> Dict[str, Optional[float]]:
    checked = totals.get("reply_fit_checked", 0)
    turns = totals.get("turns_total", 0)
    evaluated = totals.get("reward_evaluated", 0)
    rates: Dict[str, Optional[float]] = {}
    for kind in REVISION_KINDS:
        rates[f"revised_{kind}"] = _rate(totals.get(f"revised_{kind}", 0), checked)
    rates["reward_failed"] = _rate(sum(totals.get(f"reward_failed_{t}", 0) for t in REWARD_TAGS), evaluated)
    rates["turn_failed"] = _rate(sum(totals.get(f"turn_failed_{k}", 0) for k in FAILURE_KINDS), turns)
    rates["relatable_offered"] = _rate(totals.get("relatable_offered", 0), turns)
    rates["self_mention"] = _rate(totals.get("self_mention", 0), turns)
    rates["open_loop_captured"] = _rate(totals.get("open_loop_captured", 0), turns)
    rates["open_loop_offered"] = _rate(totals.get("open_loop_offered", 0), turns)
    rates["callback_offered"] = _rate(totals.get("callback_offered", 0), turns)
    # How often a message Sonic started was answered, and how often the template had to stand in for the model.
    rates["initiation_answered"] = _rate(totals.get("initiation_answered", 0), totals.get("initiation_offered", 0))
    rates["initiation_fallback"] = _rate(totals.get("initiation_fallback", 0), totals.get("initiation_offered", 0))
    # The same answer rate for just the openers about something they're into, so that kind can be judged on its own.
    rates["initiation_interest_answered"] = _rate(totals.get("initiation_interest_answered", 0), totals.get("initiation_interest_offered", 0))
    rates["curiosity_offered"] = _rate(totals.get("curiosity_offered", 0), turns)
    # How often the one open question was picked up.
    rates["curiosity_taken"] = _rate(totals.get("curiosity_taken", 0), totals.get("curiosity_offered", 0))
    rates["lean_offered"] = _rate(totals.get("lean_offered", 0), turns)
    rates["shelf_offered"] = _rate(totals.get("shelf_offered", 0), turns)
    rates["shelf_taken"] = _rate(totals.get("shelf_taken", 0), totals.get("shelf_offered", 0))
    # The welcome goal's signal (backend/utils/goals.py): of the offers whose answer was not a sign-off, the share picked up. offer_made / offer_held_back are its volume.
    rates["offer_taken"] = _rate(totals.get("offer_taken", 0), totals.get("offer_taken", 0) + totals.get("offer_passed", 0))
    rates["offer_made"] = _rate(totals.get("offer_made", 0), turns)
    # How coding work ends (backend/utils/code_outcomes.py): runs that hit the step cap, edits that were also tested, and next messages that said it did not work.
    rates["code_cap_hit"] = _rate(totals.get("code_cap_hit", 0), totals.get("code_turns", 0))
    rates["code_verified"] = _rate(totals.get("code_verified", 0), totals.get("code_edited", 0))
    rates["code_redo"] = _rate(totals.get("code_redo", 0), totals.get("code_followed", 0))
    # Plan mode (backend/utils/code_plan.py): runs that ended in a plan, the share the person approved, and how execution turns end against coding turns overall (code_cap_hit above).
    rates["code_plan_approved"] = _rate(totals.get("code_plan_approved", 0), totals.get("code_plan_submitted", 0))
    rates["code_plan_exec_cap_hit"] = _rate(totals.get("code_plan_exec_cap_hit", 0), totals.get("code_plan_exec_turns", 0))
    rates["code_plan_turn_cap_hit"] = _rate(totals.get("code_plan_turn_cap_hit", 0), totals.get("code_plan_turns", 0))
    # Working notes for coding conversations (backend/utils/working_state.py): how often the notes were updated, how often a coding turn was given them, and the share of what the
    # model proposed that the checks threw out (a quote that was not word for word, a detail nobody said).
    rates["working_notes_updated"] = _rate(totals.get("working_notes_updated", 0), turns)
    rates["working_notes_injected"] = _rate(totals.get("working_notes_injected", 0), totals.get("code_turns", 0))
    rates["working_notes_chat_read"] = _rate(totals.get("working_notes_chat_read", 0), turns)
    rates["working_notes_dropped"] = _rate(totals.get("working_notes_item_dropped", 0), totals.get("working_notes_item_added", 0) + totals.get("working_notes_item_dropped", 0))
    # Questions about its own feelings: how many turns asked each kind, and the share of them whose reply the identity check sent back.
    for kind in FEELING_KINDS:
        rates[f"feeling_asked_{kind}"] = _rate(totals.get(f"feeling_asked_{kind}", 0), turns)
        rates[f"feeling_revised_{kind}"] = _rate(totals.get(f"feeling_revised_{kind}", 0), totals.get(f"feeling_asked_{kind}", 0))
    rates["feeling_revised_other"] = _rate(totals.get("feeling_revised_other", 0), turns)
    rates["self_history_offered"] = _rate(totals.get("self_history_offered", 0), turns)
    for route in ROUTES:
        rates[f"route_{route}"] = _rate(totals.get(f"route_{route}", 0), turns)
    return rates


def summarize_counters(
    docs: Iterable[Dict[str, Any]], now: Optional[datetime.datetime] = None, days: int = 7
) -> Dict[str, Any]:
    """This period's totals and rates, the previous period's, and the change in each rate: the 'trend' that makes
    a number worth saying (4% means little; 4%, up from 2%, does)."""
    now = now or datetime.datetime.now(timezone.utc)
    docs = list(docs)
    end = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    mid = (now - datetime.timedelta(days=days - 1)).strftime("%Y-%m-%d")
    start = (now - datetime.timedelta(days=2 * days - 1)).strftime("%Y-%m-%d")
    current, previous = _totals(docs, mid, end), _totals(docs, start, mid)
    current_rates, previous_rates = _rates(current), _rates(previous)
    change = {
        key: (round(current_rates[key] - previous_rates[key], 4)
              if current_rates[key] is not None and previous_rates[key] is not None else None)
        for key in current_rates
    }
    return {
        "days": days,
        "totals": current,
        "previous_totals": previous,
        "rates": current_rates,
        "previous_rates": previous_rates,
        "change": change,
    }


def fetch_counter_docs(db: Any, days: int, now: Optional[datetime.datetime] = None) -> list:
    """The daily documents covering this period and the one before it."""
    now = now or datetime.datetime.now(timezone.utc)
    start = (now - datetime.timedelta(days=2 * days)).strftime("%Y-%m-%d")
    return list(db[COUNTERS_COLLECTION].find({"day": {"$gte": start}}, {"_id": 0, "day": 1, "counts": 1}))


def _pct(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _delta(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{'+' if value >= 0 else ''}{value * 100:.1f} pts"


def format_summary(summary: Dict[str, Any]) -> str:
    totals, rates, previous, change = summary["totals"], summary["rates"], summary["previous_rates"], summary["change"]
    lines = [
        f"Sonic's own behavior, last {summary['days']} days (against the {summary['days']} before)",
        f"  Turns: {totals.get('turns_total', 0)}  (before: {summary['previous_totals'].get('turns_total', 0)})",
        f"  Replies checked for fit: {totals.get('reply_fit_checked', 0)}",
        "  Share of checked replies that needed a rewrite:",
    ]
    for kind in REVISION_KINDS:
        key = f"revised_{kind}"
        lines.append(f"    {kind:<10}{_pct(rates[key]):>8}   was {_pct(previous[key])}   {_delta(change[key])}")
    lines.append(
        f"  Answers the reward check failed: {_pct(rates['reward_failed'])}   was {_pct(previous['reward_failed'])}   {_delta(change['reward_failed'])}"
    )
    lines.append(
        f"  Turns that failed outright: {_pct(rates['turn_failed'])}   was {_pct(previous['turn_failed'])}   {_delta(change['turn_failed'])}"
    )
    lines.append(
        f"  Turns where a relatable line was offered: {_pct(rates['relatable_offered'])}   "
        f"replies that mentioned a leaning of its own: {_pct(rates['self_mention'])}"
    )
    lines.append("  Where turns were routed: " + ", ".join(f"{r} {_pct(rates[f'route_{r}'])}" for r in ROUTES))
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate counters about Sonic's own behavior.")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv()
    from backend.utils.db_utils import get_db

    db = get_db()
    if db is None:
        raise SystemExit("No database is configured, so there are no counters to read.")
    print(format_summary(summarize_counters(fetch_counter_docs(db, args.days), days=args.days)))


if __name__ == "__main__":
    main()
