"""Finding Sonic's working leanings that fit a request (the registry is backend/components/sonic_leanings.py).

The registry can grow far past what fits in a prompt, so for each tool turn only the leanings whose situation resembles the request are put in, by meaning, the way the reading shelf is matched to a
message (backend/utils/relevant_findings.py): every entry is embedded once from its `when` description plus its line, the request is embedded, and the closest few above a threshold are used. No model
call is made beyond that one embedding. Entries marked `core` are always used.

THE INDEX. Embeddings are kept in `sonic_leaning_index` (one small document per entry: id, a hash of the text that was embedded, the vector), so a restart does not re-embed anything and an edited entry
is re-embedded on its own. With no database the vectors are kept in memory only. A failed embedding leaves that entry out for the turn; it never fails the turn.

NOTHING PERSONAL. The request is embedded and compared, never stored. What is recorded is a count per entry (aggregate counters, backend/utils/self_counters.py): which leanings were put in, so one that
never matches can be reworded or removed.
"""
import hashlib
import logging
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.components.sonic_leanings import LEANINGS, MAX_EVIDENCE_CHARS, MAX_LINE_CHARS, MAX_REASON_CHARS, MAX_STATEMENT_CHARS, PHILOSOPHIES

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_leaning_index"
MIN_SCORE = float(os.getenv("LEANING_MIN_SCORE", "0.66"))      # calibrated 2026-10-25 on real embeddings (docs/personality-plan.md): requests meant for an entry scored 0.68 to 0.82, the closest miss 0.63
PER_TURN = int(os.getenv("LEANINGS_PER_TURN", "3"))
MIN_REQUEST_CHARS = 12
MAX_REQUEST_CHARS = 700                                          # only the start of a long request is matched: a paste says little about what is being asked
CACHE_SECONDS = 3600

_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}


def enabled() -> bool:
    return os.getenv("LEANINGS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def live(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The leanings that may be used: only the ones Jack approved. A draft is never put in a prompt. Pure."""
    return [entry for entry in (LEANINGS if entries is None else entries) if entry.get("approved") is True]


def source_text(entry: Dict[str, Any]) -> str:
    """What is embedded for an entry: the situation it applies to, then the leaning. The situation is what a request resembles, so it leads. Pure."""
    return f"{entry.get('when', '')} {entry.get('line', '')}".strip()


def parts_of(entry: Dict[str, Any]) -> List[str]:
    """Every text that is embedded for an entry: the whole description first, then each short example request (`asks`). A request is scored against the best of them, because one long description
    that lists many situations blurs into all of them, while a short example is sharp about one. Pure."""
    return [source_text(entry)] + [str(ask).strip() for ask in entry.get("asks") or [] if str(ask).strip()]


def text_hash(entry: Dict[str, Any]) -> str:
    return hashlib.sha1("\n".join(parts_of(entry)).encode("utf-8")).hexdigest()[:16]


def _vector_list(value: Any) -> List[List[float]]:
    """One vector or several, as a list of vectors. Pure."""
    if not value:
        return []
    return [list(value)] if isinstance(value[0], (int, float)) else [list(v) for v in value if v]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm = (sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5)
    return dot / norm if norm else 0.0


def rank(vector: List[float], entries: List[Dict[str, Any]], vectors: Dict[str, List[float]], *, threshold: Optional[float] = None, k: Optional[int] = None) -> List[Tuple[float, Dict[str, Any]]]:
    """The entries (without `core`) whose vectors are closest to `vector`, best first, only those at or above the threshold, at most `k`. Pure."""
    limit = MIN_SCORE if threshold is None else threshold
    scored = [(max((cosine(vector, v) for v in _vector_list(vectors.get(entry["id"]))), default=0.0), entry) for entry in entries if not entry.get("core") and vectors.get(entry["id"])]
    scored = [(round(score, 3), entry) for score, entry in scored if score >= limit]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[: PER_TURN if k is None else k]


def index_vectors(db: Any, embed: Callable[[str], Optional[List[float]]], entries: Optional[List[Dict[str, Any]]] = None, *, now: Optional[float] = None, fill: bool = True, collection: Optional[str] = None, cache: Optional[Dict[str, Any]] = None) -> Dict[str, List[List[float]]]:
    """{entry id: its vectors (the description's, then each example's)} for every entry that could be embedded. Read from the stored index; an entry that is missing or whose text changed is embedded and stored. Cached for CACHE_SECONDS. Blocking."""
    entries = live(entries)
    store = COLLECTION if collection is None else collection        # the same index machinery serves Sonic's recorded experiences (backend/utils/experiences.py), with its own collection and cache
    memo = _cache if cache is None else cache
    moment = time.monotonic() if now is None else now
    wanted = {entry["id"]: text_hash(entry) for entry in entries}
    cached = memo["vectors"]
    if cached and moment - memo["at"] < CACHE_SECONDS and all(cached.get(i, ("", None))[0] == h for i, h in wanted.items()):
        return {i: v for i, (_, v) in cached.items() if i in wanted}
    stored: Dict[str, Dict[str, Any]] = {}
    if db is not None:
        try:
            stored = {doc["_id"]: doc for doc in db[store].find({})}
        except Exception:
            logger.warning("[leanings] could not read the stored index.", exc_info=True)
    fresh: Dict[str, Tuple[str, List[List[float]]]] = {}
    missing = 0
    for entry in entries:
        entry_id, digest = entry["id"], wanted[entry["id"]]
        doc = stored.get(entry_id)
        if doc and doc.get("hash") == digest and doc.get("embeddings"):
            fresh[entry_id] = (digest, _vector_list(doc["embeddings"]))
            continue
        if not fill:
            missing += 1           # a turn never waits for embeddings: the entry is left out until the background build has stored it
            continue
        try:
            vectors = [embed(text) for text in parts_of(entry)]
        except Exception:
            logger.warning("[leanings] could not embed an entry.", exc_info=True)
            continue
        vectors = [[float(x) for x in vector] for vector in vectors if vector]
        if not vectors:
            continue
        fresh[entry_id] = (digest, vectors)
        if db is not None:
            try:
                db[store].update_one({"_id": entry_id}, {"$set": {"hash": digest, "embeddings": [[round(x, 5) for x in vector] for vector in vectors]}}, upsert=True)
            except Exception:
                logger.warning("[leanings] could not store an entry's vector.", exc_info=True)
    if not missing:                                # a partial index is not cached, so the next look sees entries that were stored meanwhile
        memo["vectors"], memo["at"] = fresh, moment
    return {i: v for i, (_, v) in fresh.items()}


_warm_lock = threading.Lock()
_warming = {"on": False}


def warm(db: Any, embed: Callable[[str], Optional[List[float]]], entries: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Builds the index in a background thread (embedding what is missing), once at a time. A server restart calls this at startup, and a turn that finds entries missing calls it too, so no
    turn ever waits for the embeddings: about 150 calls the first time and none after. Returns True when a build was started. Never raises."""
    try:
        with _warm_lock:
            if _warming["on"]:
                return False
            _warming["on"] = True

        def build() -> None:
            try:
                index_vectors(db, embed, entries, fill=True)
            except Exception:
                logger.warning("[leanings] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="leanings-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    entries: Optional[List[Dict[str, Any]]] = None,
    vector: Optional[List[float]] = None,
    threshold: Optional[float] = None,
    k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The leanings for this request: the `core` ones, then the closest others above the threshold. [] when the feature is off or there is nothing to compare. Never raises. Blocking (an embedding)."""
    try:
        entries = live(entries)
        if not enabled() or not entries:
            return []
        chosen = [entry for entry in entries if entry.get("core")]
        text = (request or "").strip()[:MAX_REQUEST_CHARS]
        if len(text) >= MIN_REQUEST_CHARS:
            vectors = index_vectors(db, embed, entries, fill=False)
            if len(vectors) < len(entries):
                warm(db, embed, entries)
            asked = vector or embed(text)
            if asked and vectors:
                chosen += [entry for _, entry in rank(asked, entries, vectors, threshold=threshold, k=k)]
        return chosen
    except Exception:
        logger.warning("[leanings] could not choose leanings for this request.", exc_info=True)
        return []


def build_block(chosen: List[Dict[str, Any]], philosophies: Optional[List[Dict[str, Any]]] = None) -> str:
    """The prompt block for the chosen leanings, or "": each leaning sits under the philosophy it follows from, with that philosophy's statement and reason, so Sonic has the reason to give when
    it is asked why. A philosophy that is not approved is left out and its leanings stand alone. Pure."""
    approved = {p["id"]: p for p in (PHILOSOPHIES if philosophies is None else philosophies) if p.get("approved") is True}
    groups: Dict[str, List[str]] = {}
    for entry in chosen:
        if entry.get("line"):
            groups.setdefault(entry.get("philosophy") if entry.get("philosophy") in approved else "", []).append(entry["line"])
    if not groups:
        return ""
    sections = []
    for philosophy_id, lines in groups.items():
        head = f"{approved[philosophy_id]['statement']} Why: {approved[philosophy_id]['reason']}\n" if philosophy_id else ""
        sections.append(head + "\n".join(f"- {line}" for line in lines))
    return (
        "YOUR OWN WAY OF WORKING ON THIS KIND OF TASK (what you hold and why, in your own words). The person's preferences and their project's conventions come first: if they want it another way, "
        "say your view once, with the reason, and do it their way.\n" + "\n".join(sections)
    )


def outcome_counts(used_before: Any, *, pushback: bool, praised: bool) -> Dict[str, int]:
    """The counters for the message that follows a reply that used these leanings (by id): `followed` for each, plus `pushback` or `praise` when the message read as one. Ids that are not in the
    registry are dropped (a leaning that was since removed). Counts only: no text, no person. Pure."""
    known = {entry["id"] for entry in LEANINGS}
    ids = [i for i in (used_before or []) if isinstance(i, str) and i in known]
    counts: Dict[str, int] = {}
    for leaning_id in ids:
        counts[f"leaning_followed_{leaning_id}"] = 1
        if pushback:
            counts[f"leaning_pushback_{leaning_id}"] = 1
        elif praised:
            counts[f"leaning_praise_{leaning_id}"] = 1
    return counts


def capped_counts(used: Any, cap_hit: bool) -> Dict[str, int]:
    """A turn that used these leanings and ran into its step limit: `capped` for each. Pure."""
    known = {entry["id"] for entry in LEANINGS}
    return {f"leaning_capped_{i}": 1 for i in (used or []) if cap_hit and isinstance(i, str) and i in known}


def counts_for(chosen: List[Dict[str, Any]]) -> Dict[str, int]:
    """The counter increments for a turn that used these leanings. Pure."""
    if not chosen:
        return {}
    return {"leaning_turns": 1, **{f"leaning_used_{entry['id']}": 1 for entry in chosen}}


def check_registry(entries: Optional[List[Dict[str, Any]]] = None, philosophies: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the registry, in words ([] when it is sound): ids unique, every leaning follows from a philosophy that exists, a situation and a line on each, philosophies with a statement and a
    reason, evidence notes dated and short, everything within the size limits. Checks drafts too. Pure."""
    problems, seen = [], set()
    philosophy_list = PHILOSOPHIES if philosophies is None else philosophies
    known = {p.get("id") for p in philosophy_list}
    for philosophy in philosophy_list:
        pid = philosophy.get("id", "")
        statement, reason = str(philosophy.get("statement", "")).strip(), str(philosophy.get("reason", "")).strip()
        if not pid or not statement.endswith(".") or len(statement) > MAX_STATEMENT_CHARS:
            problems.append(f"philosophy {pid or '(no id)'}: the statement must be a sentence of at most {MAX_STATEMENT_CHARS} characters")
        if not reason.endswith(".") or len(reason) > MAX_REASON_CHARS:
            problems.append(f"philosophy {pid or '(no id)'}: the reason must be a sentence of at most {MAX_REASON_CHARS} characters")
    for entry in LEANINGS if entries is None else entries:
        entry_id = entry.get("id", "")
        if not entry_id or entry_id in seen:
            problems.append(f"{entry_id or '(no id)'}: id missing or repeated")
        seen.add(entry_id)
        if entry.get("philosophy") not in known:
            problems.append(f"{entry_id}: it must follow from a philosophy that exists")
        for note in entry.get("evidence") or []:
            text = str((note or {}).get("what", "")).strip()
            if (note or {}).get("kind", "failure") not in ("failure", "worked", "record"):
                problems.append(f"{entry_id}: an evidence note's kind must be failure, worked or record")
            if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str((note or {}).get("on", ""))) or not text or len(text) > MAX_EVIDENCE_CHARS:
                problems.append(f"{entry_id}: each evidence note needs a date (YYYY-MM or YYYY-MM-DD) and a short description of at most {MAX_EVIDENCE_CHARS} characters")
        if len(str(entry.get("when", "")).strip()) < 40:
            problems.append(f"{entry_id}: describe the situation it applies to (at least 40 characters), since that is what is matched")
        line = str(entry.get("line", "")).strip()
        if not line or len(line) > MAX_LINE_CHARS or not line.endswith("."):
            problems.append(f"{entry_id}: the line must be a sentence of at most {MAX_LINE_CHARS} characters")
    return problems
