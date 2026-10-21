"""One-off repairs to stored memory. Every command is a DRY RUN unless given --apply.

    python -m backend.utils.memory_maintenance strip-labels               # count what would change
    python -m backend.utils.memory_maintenance strip-labels --apply       # do it
    python -m backend.utils.memory_maintenance compact-tier2 --username U [--apply]   # merge near-duplicate summaries now, archiving the originals
    python -m backend.utils.memory_maintenance restore-compacted [--run ID|latest] [--apply]   # list compaction runs / undo one (newest first)
    python -m backend.utils.memory_maintenance restore-pruned-facts --username U [--source explicit] [--show] [--apply]   # bring back pruned facts

strip-labels: until the label fix, about a third of the saved notes begin with the model's "SUMMARY:" (it copied the last word of
the prompt). That word was embedded into the note, which made unrelated notes look alike to the recall search. This removes the label
from the stored text and re-embeds the cleaned text (a small embedding call per note, no model call). Nothing else about a note changes:
not its date, owner, type or provenance. A note whose cleaned text would be empty is left alone. Prints counts only, never note text."""
import argparse
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence

from backend.services.memory_search import CHUNK_ARCHIVE_COLLECTION, USER_MEMORY_COLLECTION
from backend.utils.app_utils import strip_note_label

ARCHIVE_COLLECTION = CHUNK_ARCHIVE_COLLECTION


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


# ---- bringing back facts that pruning evicted --------------------------------------------------------------------------------------
# Until pruning learned to keep what the user stated over what a model inferred, it evicted facts people had told Sonic directly (oldest first).
# They were archived, not deleted; this puts them back. A fact is skipped when it is already live (same id), or is a near-duplicate of a live
# fact or of another fact being restored (the same bar save_user_fact uses for "this is the same fact"), because a newer version of it already
# exists or was merged. The restored facts keep their original ids, dates and wording. If the store is then over its cap, the normal pruning
# runs and evicts the weakest (the inferred ones first), archiving them as usual.
FACT_ARCHIVE_ONLY_FIELDS = ("_id", "archived_at", "restored_at")


def _normalized(text: str) -> str:
    return " ".join((text or "").lower().split()).strip(" .!?")


def plan_fact_restore(archived: Sequence[Dict[str, Any]], live: Sequence[Any], threshold: float, similarity: Callable[[Any, Any], float]) -> Dict[str, Any]:
    """Which archived facts to bring back. `archived` are archive documents, `live` the user's UserFacts, `similarity` compares two embeddings.
    Returns {"restore": [(archive_doc, best_similarity)], "skipped": {reason: count}}. Newest first, so when two archived facts are versions
    of the same thing the newer one wins. Pure."""
    live_ids = {getattr(f, "id", None) for f in live}
    live_texts = {_normalized(getattr(f, "fact", "")) for f in live}
    chosen: List[Dict[str, Any]] = []
    skipped = {"already live": 0, "same as a live fact": 0, "same as another restored fact": 0}
    restore: List[Any] = []
    for doc in sorted(archived, key=lambda d: d.get("updated_at") or "", reverse=True):
        if doc.get("id") in live_ids:
            skipped["already live"] += 1
            continue
        embedding = doc.get("embedding")
        text = _normalized(doc.get("fact", ""))
        best_live = max((similarity(embedding, f.embedding) for f in live if embedding and getattr(f, "embedding", None)), default=0.0)
        if text in live_texts or best_live >= threshold:
            skipped["same as a live fact"] += 1
            continue
        best_chosen = max((similarity(embedding, c.get("embedding")) for c in chosen if embedding and c.get("embedding")), default=0.0)
        if text in {_normalized(c.get("fact", "")) for c in chosen} or best_chosen >= threshold:
            skipped["same as another restored fact"] += 1
            continue
        chosen.append(doc)
        restore.append((doc, best_live))
    return {"restore": restore, "skipped": skipped}


def restore_pruned_facts(db: Any, username: str, source: str, now_iso: str) -> Dict[str, Any]:
    """Restores the archived facts of `source` for `username` that plan_fact_restore approves; returns its counts and how many facts the
    cap then evicted. Safe to repeat: restored rows are marked, and a restored fact is "already live" the next time."""
    from backend.utils import memory_utils as mu
    from backend.utils.embedding_utils import cosine_similarity

    archive = db[mu.FACT_ARCHIVE_COLLECTION]
    archived = list(archive.find({"username": username, "source": source, "restored_at": {"$exists": False}}))
    live = mu.load_user_facts(username)
    plan = plan_fact_restore(archived, live, mu.FACT_SIMILARITY_THRESHOLD, cosine_similarity)
    restored = [mu.UserFact(**{k: v for k, v in doc.items() if k not in FACT_ARCHIVE_ONLY_FIELDS}) for doc, _ in plan["restore"]]
    result: Dict[str, Any] = {"restored": len(restored), "skipped": plan["skipped"], "evicted_to_stay_under_cap": 0}
    if not restored:
        return result
    combined = live + restored
    kept = mu._prune_excess_facts(combined)  # evicts the weakest (inferred first) and archives them, exactly as a normal save would
    result["evicted_to_stay_under_cap"] = len(combined) - len(kept)
    mu.save_user_facts(username, kept)
    archive.update_many({"_id": {"$in": [doc["_id"] for doc, _ in plan["restore"]]}}, {"$set": {"restored_at": now_iso}})
    return result


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
    facts = sub.add_parser("restore-pruned-facts", help="bring back facts that pruning evicted (default: the ones the user stated)")
    facts.add_argument("--username", required=True)
    facts.add_argument("--source", default="explicit", help="which archived facts: explicit (stated by the user), inferred or pattern")
    facts.add_argument("--show", action="store_true", help="also print each fact's text (default: counts, categories and similarity only)")
    facts.add_argument("--apply", action="store_true", help="actually restore (default: only show what would come back)")
    domains = sub.add_parser("classify-domains", help="sort stored facts into job / own projects / personal / general (and fix facts that wrongly join the job and an own project)")
    domains.add_argument("--username", required=True)
    domains.add_argument("--employer", default="", help="one line about their job: the employer's name (and how they write it) and what they work on there")
    domains.add_argument("--own", default="", help="one line about their own projects: their names, including this assistant")
    domains.add_argument("--show", action="store_true", help="also print each fact with the domain it would get, and every proposed rewrite (default: counts only)")
    domains.add_argument("--redo", action="store_true", help="also re-sort facts that already have a domain")
    domains.add_argument("--rewrites", action="store_true", help="also let the model reword or split facts that join the job and an own project (off: it only tags, and its rewrites were wrong when tried)")
    domains.add_argument("--save-answers", default="", help="write the model's answers to this file, so a later --answers run applies exactly what was reviewed")
    domains.add_argument("--answers", default="", help="use the answers saved in this file instead of asking the model again")
    domains.add_argument("--apply", action="store_true", help="actually write the domains and rewrites, after saving a backup (default: only show the plan)")
    undo = sub.add_parser("restore-domains", help="undo a classify-domains run: put each fact back as it was")
    undo.add_argument("--username", required=True)
    undo.add_argument("--run", default="latest", help="a run id, or 'latest'")
    undo.add_argument("--apply", action="store_true", help="actually restore (default: only say what would change)")
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
    if args.command == "restore-pruned-facts":
        return _restore_facts_command(db, args.username, args.source, args.show, args.apply)
    if args.command == "classify-domains":
        return _classify_domains_command(db, args.username, args.employer, args.own, args.show, args.redo, args.apply, args.rewrites, args.save_answers, args.answers)
    if args.command == "restore-domains":
        return _restore_domains_command(db, args.username, args.run, args.apply)
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


def _restore_facts_command(db: Any, username: str, source: str, show: bool, apply: bool) -> int:
    from datetime import datetime, timezone

    from backend.utils import memory_utils as mu
    from backend.utils.embedding_utils import cosine_similarity

    archived = list(db[mu.FACT_ARCHIVE_COLLECTION].find({"username": username, "source": source, "restored_at": {"$exists": False}}))
    live = mu.load_user_facts(username)
    plan = plan_fact_restore(archived, live, mu.FACT_SIMILARITY_THRESHOLD, cosine_similarity)
    skipped = ", ".join(f"{count} {reason}" for reason, count in plan["skipped"].items() if count) or "none"
    over = max(0, len(live) + len(plan["restore"]) - mu.MAX_STORED_FACTS)
    print(f"{len(archived)} archived '{source}' fact(s); {len(plan['restore'])} would come back; skipped: {skipped}.")
    print(f"The store holds {len(live)} of {mu.MAX_STORED_FACTS}; restoring would leave it about {over} over, so about {over} weaker fact(s) (inferred first) would be archived.")
    for doc, best in plan["restore"]:
        line = f"  [{doc.get('category')}] updated {str(doc.get('updated_at'))[:10]}, closest live fact {best:.2f}"
        print(line + (f": {doc.get('fact')}" if show else ""))
    if not apply:
        print("Dry run: nothing changed. Add --apply to restore them (add --show to see the text first).")
        return 0
    if not plan["restore"]:
        return 0
    print(restore_pruned_facts(db, username, source, datetime.now(timezone.utc).isoformat()))
    return 0


def _classify_domains_command(db: Any, username: str, employer: str, own: str, show: bool, redo: bool, apply: bool, rewrites: bool = False, save_answers: str = "", answers_file: str = "") -> int:
    import json

    from backend.models.models import lite_llm
    from backend.utils import memory_domains as md
    from backend.utils import memory_utils as mu
    from backend.utils.embedding_utils import embed_text

    facts = [f for f in mu.load_user_facts(username) if f.active]
    wanted = [f for f in facts if (redo or f.domain is None) and not f.domain_locked]
    print(f"{len(facts)} stored fact(s); {len(wanted)} to sort.")
    if not wanted:
        return 0
    if answers_file:
        with open(answers_file, encoding="utf-8") as handle:
            answers = json.load(handle)
        print(f"using the {len(answers)} saved answer(s) in {answers_file}.")
    else:
        answers = md.classify(wanted, lite_llm.invoke, employer, own)
        if save_answers:
            with open(save_answers, "w", encoding="utf-8") as handle:
                json.dump(answers, handle)
            print(f"saved the model's answers to {save_answers}.")
    plan = md.plan_changes(wanted, answers, redo=redo, allow_rewrites=rewrites)
    summary = md.summarize(plan, wanted)
    print(f"would tag: {summary['domains']}; job facts by category {summary['categories']['work']}; own-project facts by category {summary['categories']['own']}")
    print(f"rewrites: {summary['rewrites']} accepted, {summary['rejected_rewrites']} rejected (they added a detail the fact did not hold); skipped: {summary['skipped']}")
    if show:
        by_id = {f.id: f for f in wanted}
        for rewrite in plan["rewrites"]:
            original = by_id[rewrite["id"]]
            print(f"\n  REWRITE [{original.category}] {original.fact}")
            for part in rewrite["parts"]:
                print(f"      -> ({part['domain']}) {part['fact']}")
        for rejected in plan["rejected_rewrites"]:
            print(f"\n  REJECTED REWRITE (tagged only) [{by_id[rejected].category}] {by_id[rejected].fact}")
        print()
        for tag in plan["tags"]:
            if tag["domain"] != "general":
                print(f"  ({tag['domain']}) [{by_id[tag['id']].category}] {by_id[tag['id']].fact[:150]}")
    if not apply:
        print("Dry run: nothing changed. Add --apply to write it (a backup is saved first, and restore-domains undoes it).")
        return 0

    def save_backup(doc: Dict[str, Any]) -> None:
        db[md.BACKUP_COLLECTION].replace_one({"run_id": doc["run_id"]}, doc, upsert=True)

    result = md.apply_plan(username, mu.load_user_facts(username), plan, save_facts=mu.save_user_facts, save_backup=save_backup, embed=embed_text)
    print(f"done: {result}. Undo with: restore-domains --username {username} --run {result['run_id']} --apply")
    return 0


def _restore_domains_command(db: Any, username: str, run: str, apply: bool) -> int:
    from datetime import datetime, timezone

    from backend.utils import memory_domains as md
    from backend.utils import memory_utils as mu

    query: Dict[str, Any] = {"username": username, "restored_at": {"$exists": False}}
    if run and run != "latest":
        query["run_id"] = run
    backups = list(db[md.BACKUP_COLLECTION].find(query).sort("at", -1).limit(1))
    if not backups:
        print("No run to restore.")
        return 0
    backup = backups[0]
    print(f"run {backup['run_id']} from {backup.get('at')}: {len(backup.get('changes', []))} fact(s) would go back to how they were.")
    if not apply:
        print("Dry run: nothing changed. Add --apply to restore it.")
        return 0
    result = md.restore_run(username, backup, mu.load_user_facts(username), save_facts=mu.save_user_facts)
    db[md.BACKUP_COLLECTION].update_one({"run_id": backup["run_id"]}, {"$set": {"restored_at": datetime.now(timezone.utc).isoformat()}})
    print(f"restored: {result}")
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
