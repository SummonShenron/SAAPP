"""A one-off cleanup of someone's stored facts and notes that can be undone (scripts/fix_stored_facts.py runs it).

WHY (2026-11-23 memory review). Stored facts had gone wrong in ways recall cannot fix: goals about days long past still read as plans ("has a scheduled interview ... on
October 27"), a relative date stayed relative ("completed a four-round onsite interview last Monday"), a company was stored as a person ("the interview with Casey"), and a
compacted note carried a date that was never said. These are corrected by hand, with the person's approval, here.

HOW. Every change archives the fact as it was first (FACT_ARCHIVE_COLLECTION, with the embedding and the run's id), then rewrites it (new wording, a new embedding, an optional goal
status) or removes it. A note is never rewritten, only flagged stale (left out of recall, as a superseded note already is) with the run's id. `restore` puts every fact of a run back as
it was and un-flags its notes. A rewrite whose new embedding cannot be made is skipped (never new words on an embedding of the old ones). Nothing is logged but counts.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger("SASS Logger")

ACTIONS = ("rewrite", "archive")
GOAL_STATUSES = ("active", "achieved", "abandoned")
NOTES_COLLECTION = "user_memory_chunks"


def _check(changes: Sequence[Dict[str, Any]]) -> None:
    seen = set()
    for change in changes:
        if change.get("action") not in ACTIONS:
            raise ValueError("unknown action")
        if not change.get("id") or change["id"] in seen:
            raise ValueError("each change needs its own fact id")
        seen.add(change["id"])
        if change["action"] == "rewrite" and not " ".join(str(change.get("text") or "").split()):
            raise ValueError("a rewrite needs new wording")
        if change.get("goal_status") is not None and change["goal_status"] not in GOAL_STATUSES:
            raise ValueError("unknown goal status")


def apply_changes(
    username: str,
    changes: Sequence[Dict[str, Any]],
    run_id: str,
    *,
    db: Any,
    load: Callable[[str], List[Any]],
    save: Callable[[str, List[Any]], None],
    embed: Callable[[str], Optional[List[float]]],
) -> Dict[str, Any]:
    """Applies the changes ({"id", "action": "rewrite"|"archive", "text"?, "goal_status"?}) to one person's facts, archiving each original first. Returns
    {"rewritten", "archived", "missing": [ids], "skipped": [ids whose embedding failed]}. Raises ValueError on a malformed change before touching anything."""
    if not run_id:
        raise ValueError("a run needs an id")
    _check(changes)
    facts = load(username)
    by_id = {f.id: f for f in facts}
    now = datetime.now(timezone.utc).isoformat()
    result = {"rewritten": 0, "archived": 0, "missing": [], "skipped": []}
    originals, removed, rewrites = [], set(), []
    for change in changes:
        fact = by_id.get(change["id"])
        if fact is None:
            result["missing"].append(change["id"])
            continue
        if change["action"] == "rewrite":
            text = " ".join(str(change["text"]).split())
            embedding = embed(text)
            if embedding is None:
                result["skipped"].append(change["id"])
                continue
            rewrites.append((fact, text, embedding, change.get("goal_status")))
        else:
            removed.add(fact.id)
        originals.append({**fact.model_dump(), "archived_at": now, "cleanup_run": run_id, "cleanup_action": change["action"]})
    if not originals:
        return result
    db["user_memory_fact_archive"].insert_many(originals)          # the originals are safe before anything changes
    for fact, text, embedding, status in rewrites:
        fact.fact, fact.embedding, fact.updated_at = text, embedding, now
        if status is not None and fact.category in ("goal", "project"):
            fact.goal_status = status
        result["rewritten"] += 1
    kept = [f for f in facts if f.id not in removed]
    for fact in kept:
        if removed & set(fact.related or []):
            fact.related = [linked for linked in fact.related if linked not in removed]
    result["archived"] = len(removed)
    save(username, kept)
    logger.info("[FactCleanup] Run applied: %d rewritten, %d archived, %d missing, %d skipped.", result["rewritten"], result["archived"], len(result["missing"]), len(result["skipped"]))
    return result


def flag_notes(db: Any, username: str, note_ids: Sequence[Any], run_id: str) -> int:
    """Flags these notes of theirs stale (left out of recall) under the run's id. Returns how many were flagged."""
    if not run_id or not note_ids:
        return 0
    found = db[NOTES_COLLECTION].update_many(
        {"username": username, "_id": {"$in": list(note_ids)}, "stale": {"$ne": True}},
        {"$set": {"stale": True, "stale_reason": "cleanup", "stale_at": datetime.now(timezone.utc).isoformat(), "stale_run": run_id}},
    )
    return int(getattr(found, "modified_count", 0) or 0)


def restore(
    username: str,
    run_id: str,
    *,
    db: Any,
    load: Callable[[str], List[Any]],
    save: Callable[[str, List[Any]], None],
    make_fact: Callable[[Dict[str, Any]], Any],
) -> Dict[str, int]:
    """Undoes a run: every fact it archived goes back as it was (replacing its rewritten form, or added again if it was removed), and its notes are un-flagged.
    Returns {"facts", "notes"}."""
    if not run_id:
        raise ValueError("a run needs an id")
    archived = list(db["user_memory_fact_archive"].find({"username": username, "cleanup_run": run_id, "restored_at": {"$exists": False}}))
    facts = load(username)
    if archived:
        back = {}
        for doc in archived:
            fields = {k: v for k, v in doc.items() if k not in ("_id", "archived_at", "cleanup_run", "cleanup_action", "restored_at")}
            back[fields["id"]] = make_fact(fields)
        facts = [back.pop(f.id, f) for f in facts] + list(back.values())
        save(username, facts)
        db["user_memory_fact_archive"].update_many(
            {"username": username, "cleanup_run": run_id, "restored_at": {"$exists": False}}, {"$set": {"restored_at": datetime.now(timezone.utc).isoformat()}},
        )
    notes = db[NOTES_COLLECTION].update_many({"username": username, "stale_run": run_id}, {"$set": {"stale": False}, "$unset": {"stale_reason": "", "stale_at": "", "stale_run": ""}})
    return {"facts": len(archived), "notes": int(getattr(notes, "modified_count", 0) or 0)}
