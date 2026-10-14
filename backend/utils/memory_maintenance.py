"""One-off repairs to stored memory. Every command is a DRY RUN unless given --apply.

    python -m backend.utils.memory_maintenance strip-labels               # count what would change
    python -m backend.utils.memory_maintenance strip-labels --apply       # do it
    python -m backend.utils.memory_maintenance compact-tier2 --username U [--apply]   # merge near-duplicate summaries now, archiving the originals
    python -m backend.utils.memory_maintenance restore-compacted [--run ID|latest] [--apply]   # list compaction runs / undo one (newest first)

strip-labels: until the label fix, about a third of the saved notes begin with the model's "SUMMARY:" (it copied the last word of
the prompt). That word was embedded into the note, which made unrelated notes look alike to the recall search. This removes the label
from the stored text and re-embeds the cleaned text (a small embedding call per note, no model call). Nothing else about a note changes:
not its date, owner, type or provenance. A note whose cleaned text would be empty is left alone. Prints counts only, never note text."""
import argparse
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.services.memory_search import USER_MEMORY_COLLECTION
from backend.utils.app_utils import strip_note_label

ARCHIVE_COLLECTION = "user_memory_chunk_archive"  # the same name as memory_compaction.ARCHIVE_COLLECTION; a test keeps them equal


def plan_label_cleanup(rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows whose text starts with a label, each with its cleaned text. Pure."""
    changes = []
    for row in rows:
        text = (row.get("text") or "").strip()
        cleaned = strip_note_label(text)
        if cleaned and cleaned != text:
            changes.append({"_id": row["_id"], "text": cleaned, "source_type": row.get("source_type") or "unknown"})
    return changes


def apply_label_cleanup(collection: Any, changes: Sequence[Dict[str, Any]], embed: Callable[[str], Optional[List[float]]]) -> Dict[str, int]:
    """Writes the cleaned text and its fresh embedding to each row. A row whose embedding fails is skipped, not half-changed."""
    done = skipped = 0
    for change in changes:
        embedding = embed(change["text"])
        if embedding is None:
            skipped += 1
            continue
        collection.update_one({"_id": change["_id"]}, {"$set": {"text": change["text"], "embedding": embedding}})
        done += 1
    return {"changed": done, "skipped": skipped}


ARCHIVE_ONLY_FIELDS = ("archived_at", "compaction_tier", "run_id", "cluster_id", "restored_at")


def list_runs(archive_docs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The compaction runs that archived notes, newest first: {run_id, tier, archived_at, notes, clusters, restored}. Pure."""
    runs: Dict[str, Dict[str, Any]] = {}
    for doc in archive_docs:
        run = runs.setdefault(doc.get("run_id") or "unknown", {
            "run_id": doc.get("run_id") or "unknown", "tier": doc.get("compaction_tier"), "archived_at": doc.get("archived_at") or "",
            "notes": 0, "clusters": set(), "restored": 0,
        })
        run["notes"] += 1
        run["clusters"].add(doc.get("cluster_id"))
        run["restored"] += int(bool(doc.get("restored_at")))
    out = [{**run, "clusters": len(run["clusters"])} for run in runs.values()]
    return sorted(out, key=lambda run: run["archived_at"], reverse=True)


def restore_run(db: Any, run_id: str, now_iso: str) -> Dict[str, int]:
    """Undoes one compaction run: puts its archived notes back (with their original ids and dates), deletes the summaries that replaced
    them, and marks the archive rows restored. Undo the NEWEST run first: a summary that a later run merged again is no longer there to
    delete (counted as summaries_missing; the notes are still restored). Safe to repeat."""
    archive = db[ARCHIVE_COLLECTION]
    chunks = db[USER_MEMORY_COLLECTION]
    by_cluster: Dict[str, List[Dict[str, Any]]] = {}
    for doc in archive.find({"run_id": run_id, "restored_at": {"$exists": False}}):
        by_cluster.setdefault(doc.get("cluster_id") or "", []).append(doc)
    restored = removed = missing = 0
    for cluster_id, docs in by_cluster.items():
        for doc in docs:
            original = {key: value for key, value in doc.items() if key not in ARCHIVE_ONLY_FIELDS and key != "_id"}
            chunks.update_one({"_id": doc["_id"]}, {"$setOnInsert": original}, upsert=True)
            restored += 1
        deleted = chunks.delete_many({"username": docs[0].get("username"), "source_ref": {"$regex": f"#{cluster_id}$"}}).deleted_count
        removed += deleted
        missing += int(deleted == 0)
        archive.update_many({"run_id": run_id, "cluster_id": cluster_id}, {"$set": {"restored_at": now_iso}})
    return {"restored": restored, "summaries_removed": removed, "summaries_missing": missing}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="One-off repairs to stored memory (dry run unless --apply).")
    sub = parser.add_subparsers(dest="command", required=True)
    strip = sub.add_parser("strip-labels", help="remove the leading 'SUMMARY:' from saved notes and re-embed them")
    strip.add_argument("--apply", action="store_true", help="actually change the notes (default: only count them)")
    strip.add_argument("--username", default="", help="only this user's notes")
    tier2 = sub.add_parser("compact-tier2", help="run tier-2 compaction now for one user (archives what it merges)")
    tier2.add_argument("--username", required=True)
    tier2.add_argument("--apply", action="store_true", help="actually compact (default: only show what would be merged)")
    restore = sub.add_parser("restore-compacted", help="list compaction runs, or undo one (newest first)")
    restore.add_argument("--run", default="", help="a run id from the list, or 'latest'; omit to list the runs")
    restore.add_argument("--apply", action="store_true", help="actually restore (default: only count)")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    from backend.utils.db_utils import get_db
    from backend.utils.embedding_utils import embed_text

    db = get_db()
    if db is None:
        print("No database is configured.")
        return 2
    if args.command == "compact-tier2":
        return _compact_tier2(db, args.username, args.apply)
    if args.command == "restore-compacted":
        return _restore_command(db, args.run, args.apply)
    collection = db[USER_MEMORY_COLLECTION]
    query = {"username": args.username} if args.username else {}
    changes = plan_label_cleanup(list(collection.find(query, {"text": 1, "source_type": 1})))
    by_type: Dict[str, int] = {}
    for change in changes:
        by_type[change["source_type"]] = by_type.get(change["source_type"], 0) + 1
    print(f"{len(changes)} note(s) start with a label" + (f": {by_type}" if by_type else "."))
    if not args.apply:
        print("Dry run: nothing changed. Add --apply to clean them.")
        return 0
    result = apply_label_cleanup(collection, changes, embed_text)
    print(f"changed {result['changed']}, skipped {result['skipped']} (embedding failed; run again to retry)", file=sys.stderr)
    return 0


def _compact_tier2(db: Any, username: str, apply: bool) -> int:
    from backend.services import memory_compaction as mc

    pool = mc._fetch_chunks(db, username, {"$in": [mc.TIER1_SOURCE_TYPE, mc.TIER2_SOURCE_TYPE]})
    clusters = [c for c in mc._cluster_chunks(pool, threshold=mc.DEFAULT_META_SIMILARITY_THRESHOLD) if len(c) >= 2]
    absorbed = sum(len(c) for c in clusters)
    sizes = sorted((len(c) for c in clusters), reverse=True)
    print(f"tier-2 pool {len(pool)} notes; at similarity {mc.DEFAULT_META_SIMILARITY_THRESHOLD:.2f} (groups capped at {mc.MAX_CLUSTER_SIZE}): "
          f"{len(clusters)} merge group(s) absorbing {absorbed} notes -> pool of {len(pool) - absorbed + len(clusters)}; biggest groups {sizes[:5]}")
    if not apply:
        print("Dry run: nothing changed. Add --apply to compact (originals are archived first; undo with restore-compacted).")
        return 0
    import asyncio

    from backend.services.memory_search import get_user_memory_vector_store
    from backend.utils.embedding_utils import _get_embeddings_client

    store = get_user_memory_vector_store(db, _get_embeddings_client())
    result = asyncio.run(mc.compact_meta_memory(db, store, username))
    print(result)
    return 0


def _restore_command(db: Any, run: str, apply: bool) -> int:
    from datetime import datetime, timezone

    runs = list_runs(list(db[ARCHIVE_COLLECTION].find({}, {"run_id": 1, "compaction_tier": 1, "archived_at": 1, "cluster_id": 1, "restored_at": 1})))
    if not runs:
        print("No compaction runs have archived anything yet.")
        return 0
    if not run:
        print("run id        tier  archived at                        notes  merge groups  already restored")
        for r in runs:
            print(f"{r['run_id']:<13} {r['tier']!s:<5} {r['archived_at']:<34} {r['notes']:<6} {r['clusters']:<13} {r['restored']}")
        print("Undo one with: restore-compacted --run <id|latest> --apply   (newest first)")
        return 0
    run_id = runs[0]["run_id"] if run == "latest" else run
    target = next((r for r in runs if r["run_id"] == run_id), None)
    if target is None:
        print(f"No archived run {run_id!r}.")
        return 2
    print(f"run {run_id}: {target['notes']} archived note(s) in {target['clusters']} merge group(s), {target['restored']} already restored.")
    if not apply:
        print("Dry run: nothing changed. Add --apply to restore.")
        return 0
    print(restore_run(db, run_id, datetime.now(timezone.utc).isoformat()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
