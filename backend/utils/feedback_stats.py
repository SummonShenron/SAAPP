"""Feedback and the knowledge graph, as counts, for the Sonic Health page.

FEEDBACK is the `corrections` collection (backend/utils/app_utils.py: save_correction). It is not just a report card: each record is used again.
When a later question from the same person is close in meaning to one that got feedback, the model is shown a thumbs-down as a guardrail
("do NOT answer like this") and a thumbs-up as a preferred example (fetch_relevant_corrections). So feedback can change what Sonic says, and this
page shows how much of it there is, which kind, where it came from (a person's thumb, or the answer checker making Sonic redo an answer), and how
many turns it was actually shown on. What kind a record is comes from backend/utils/correction_kinds.py, which also reads records stored before the
rating was kept properly.

KNOWLEDGE GRAPH is the `graph_edges` collection (backend/state/graph_db.py): the relationships retrieval adds to an answer, built when a document
is ingested. The point of showing it is that an empty or missing graph looks exactly like a graph that found nothing, which is how it stayed empty
for months: this page lists each knowledge base's edges and every document that has chunks but no edges.

Nothing here contains a person's words, a prompt, a response or a name: only counts, tag names from a fixed list, the ids of the shared knowledge
bases and the file names of documents in them (which an admin already sees on the Self Service page). Personal knowledge bases (one per person, id
"kb_...") are never listed: they are folded into a single row of counts, with no id and no file names, because what a person uploads to their own
knowledge base is theirs.
"""
import datetime
from collections import Counter
from datetime import timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils import billing_time, correction_kinds

CORRECTIONS_COLLECTION = "corrections"
GRAPH_EDGES_COLLECTION = "graph_edges"
DOCUMENTS_COLLECTION = "documents"
KNOWN_TAGS = ("hallucination", "incorrect_filter", "formatting", "incomplete", "other")
MAX_MISSING_NAMES = 8
PERSONAL_KB_PREFIX = "kb_"          # see isolation_kb_utils.make_personal_kb_id
PERSONAL_ROW = "personal"


def _as_utc(value: Any) -> Optional[datetime.datetime]:
    """created_at is stored as an ISO string by save_correction (older rows may hold a datetime)."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _tag(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    return text if text in KNOWN_TAGS else "other"


def fetch_corrections(db: Any) -> List[Dict[str, Any]]:
    """Every feedback record, without its text or its embedding (only whether it has one). Blocking: call off the event loop."""
    pipeline = [{"$project": {
        "_id": 0, "username": 1, "tag": 1, "rating": 1, "source": 1, "created_at": 1,
        "has_embedding": {"$cond": [{"$isArray": "$embedding"}, True, False]},
    }}]
    return list(db[CORRECTIONS_COLLECTION].aggregate(pipeline))


def summarize_feedback(rows: Iterable[Dict[str, Any]], applied_turns: int = 0, turns_total: int = 0,
                       now: Optional[datetime.datetime] = None, days: int = 7) -> Dict[str, Any]:
    """Counts over the last `days` days, plus what is stored in all (which is what can still be shown to the model)."""
    now = now or datetime.datetime.now(timezone.utc)
    since = now - datetime.timedelta(days=days)
    stored: Counter = Counter()
    period: Counter = Counter()
    tags: Counter = Counter()
    per_day: Dict[str, Counter] = {}
    people = set()
    stored_people = set()
    old_rule_misread = 0
    for row in rows:
        kind = correction_kinds.polarity(row)
        who = correction_kinds.source(row)
        stored[kind] += 1
        stored["with_embedding" if row.get("has_embedding") else "without_embedding"] += 1
        if row.get("username"):
            stored_people.add(row["username"])
        # The rule that read anything but the exact word "positive" as negative: how many thumbs-ups it would have shown as things to avoid.
        if kind == correction_kinds.POSITIVE and str(row.get("rating") or "").strip().lower() != "positive":
            old_rule_misread += 1
        at = _as_utc(row.get("created_at"))
        if at is None or at < since or at > now:
            continue
        period[kind] += 1
        period[f"{who}_{kind}"] += 1
        if row.get("username"):
            people.add(row["username"])
        if kind == correction_kinds.NEGATIVE:
            tags[_tag(row.get("tag"))] += 1
        day = billing_time.local(at).date().isoformat()
        per_day.setdefault(day, Counter())[kind] += 1
    given = period[correction_kinds.POSITIVE] + period[correction_kinds.NEGATIVE]
    today = billing_time.local(now).date()
    daily = []
    for offset in range(max(days, 1) - 1, -1, -1):
        day = (today - datetime.timedelta(days=offset)).isoformat()
        counts = per_day.get(day, Counter())
        daily.append({"day": day, "positive": counts[correction_kinds.POSITIVE], "negative": counts[correction_kinds.NEGATIVE]})
    return {
        "period": {
            "positive": period[correction_kinds.POSITIVE],
            "negative": period[correction_kinds.NEGATIVE],
            "user_positive": period["user_positive"],
            "user_negative": period["user_negative"],
            "evaluator_negative": period["evaluator_negative"],
            "people": len(people),
            "negative_share": round(period[correction_kinds.NEGATIVE] / given, 3) if given else None,
        },
        "by_tag": [{"tag": tag, "count": count} for tag, count in tags.most_common()],
        "daily": daily,
        "stored": {
            "total": stored[correction_kinds.POSITIVE] + stored[correction_kinds.NEGATIVE],
            "positive": stored[correction_kinds.POSITIVE],
            "negative": stored[correction_kinds.NEGATIVE],
            # Matching a new question to old feedback needs the embedding; a record without one can only be matched by identical wording.
            "matchable": stored["with_embedding"],
            "unmatchable": stored["without_embedding"],
            "people": len(stored_people),
            "positives_old_rule_misread": old_rule_misread,
        },
        "applied": {"turns": applied_turns, "share": round(applied_turns / turns_total, 3) if turns_total else None},
    }


def summarize_graph(db: Any) -> Dict[str, Any]:
    """Edges per knowledge base, and every indexed document that has no edges (a graph that was never built for it). Blocking."""
    edge_rows = list(db[GRAPH_EDGES_COLLECTION].aggregate([
        {"$group": {"_id": {"affiliate": "$affiliate", "source": "$source"}, "edges": {"$sum": 1}}},
    ]))
    document_rows = list(db[DOCUMENTS_COLLECTION].aggregate([
        {"$group": {"_id": {"affiliate": "$affiliate", "source": "$source"}, "chunks": {"$sum": 1}}},
    ]))
    edges_by_file: Dict[tuple, int] = {(r["_id"].get("affiliate"), r["_id"].get("source")): r["edges"] for r in edge_rows}
    documents_by_file: Dict[tuple, int] = {(r["_id"].get("affiliate"), r["_id"].get("source")): r["chunks"] for r in document_rows}
    def is_personal(affiliate: Any) -> bool:
        return str(affiliate or "").startswith(PERSONAL_KB_PREFIX)

    shared = sorted({a for a, _ in list(edges_by_file) + list(documents_by_file) if a and not is_personal(a)})
    rows = []
    for affiliate in shared:
        files = sorted(s for a, s in documents_by_file if a == affiliate and s)
        missing = [s for s in files if edges_by_file.get((affiliate, s), 0) == 0]
        rows.append({
            "affiliate": affiliate,
            "documents": len(files),
            "chunks": sum(documents_by_file[(affiliate, s)] for s in files),
            "edges": sum(n for (a, _), n in edges_by_file.items() if a == affiliate),
            "documents_without_edges": len(missing),
            "missing": missing[:MAX_MISSING_NAMES],
        })
    # Every person's own knowledge base, together, as counts only.
    personal_files = [(a, s) for a, s in documents_by_file if is_personal(a) and s]
    if personal_files:
        rows.append({
            "affiliate": PERSONAL_ROW,
            "documents": len(personal_files),
            "chunks": sum(documents_by_file[key] for key in personal_files),
            "edges": sum(n for (a, _), n in edges_by_file.items() if is_personal(a)),
            "documents_without_edges": sum(1 for key in personal_files if edges_by_file.get(key, 0) == 0),
            "missing": [],
        })
    return {
        "edges_total": sum(r["edges"] for r in rows),
        "documents_total": sum(r["documents"] for r in rows),
        "documents_without_edges": sum(r["documents_without_edges"] for r in rows),
        "by_affiliate": rows,
    }
