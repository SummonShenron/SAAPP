"""Sonic's recorded experiences: real, dated, abstract things that happened in its own work, cited back when one genuinely fits what is being asked now.

THE IDEA (Jack, 2026-10-26). A person says "that reminds me of the time...". Sonic can do the honest version of that, because it really has a record: not a fabricated life, a logged event. This is
shared_history's callback pointed inward (backend/utils/shared_history.py, which draws one line from something the PERSON said earlier to now), with the same restraint: once, lightly, never
reciting, skipped when it is a stretch, rate-limited by a marker on Sonic's own earlier messages.

WHAT AN EXPERIENCE IS. A dated, abstract sentence about something that really happened in Sonic's own work: a failure ("a hover selector was written without checking how the page was nested, and it
did not apply"), something that worked, or a number from its own counters. Today they are the `evidence` notes on the working leanings (backend/components/sonic_leanings.py), which were written from
its recorded turns with no text from anyone's messages and no names, and which a person approved with the leaning they belong to. An experience is only ever cited from approved entries.

HOW IT IS SAID. As a record being consulted, never as a recollection happening to Sonic. "This is close to a case in my record: ..." passes; "I remember a similar case", "that reminds me of this
one time" and "I've seen this before" claim a felt act of recall that is not there, even when the event is real, so they are caught by the identity check unless the sentence is anchored in the record
("that reminds me of a case in my record" is allowed; the anchor is what makes it a citation). The reply may add nothing beyond the note.

WHEN. Only on a turn with code tools, only when the request is close in meaning to a note (the same embedding index as the leanings, kept in `sonic_experience_index`), at most once in ten messages, and
never under a safety context, a heavy mood or a closing message. For a failure note the instruction is to cite it only when it changes what is being done now ("so I'm reading the markup first"), never as a
confession offered for its own sake.

Counts only are kept (aggregate counters): `experience_offered` (a note was put in the prompt) and `experience_cited` (the reply cited the record).
"""
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Optional

from backend.components.sonic_leanings import LEANINGS
from backend.utils import leanings

logger = logging.getLogger("SASS Logger")

EXPERIENCE_MARK = "experience_cited"
MIN_GAP = 10                                                    # messages (both sides) that must pass after a citation before another
MIN_SCORE = float(os.getenv("EXPERIENCE_MIN_SCORE", "0.66"))      # calibrated 2026-10-26 on real embeddings: related requests scored 0.63 to 0.85, unrelated ones 0.47 to 0.55; a wrong citation costs more than a missed one
COLLECTION = "sonic_experience_index"
MAX_REQUEST_CHARS = 700
_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}
_warm_lock = threading.Lock()
_warming = {"on": False}

# A sentence is a citation of the record when it says so.
ANCHOR_RE = re.compile(r"\b(?:in|from|on) my (?:own )?(?:record|log)\b|\bmy (?:own )?(?:record|log) (?:has|shows|holds|includes|lists)\b|\bit(?:'s| is| was) (?:in|logged in|recorded in) my (?:own )?(?:record|log)\b", re.IGNORECASE)
CITES_RE = re.compile(r"\b(?:in|from|on) my (?:own )?(?:record|log)\b|\bmy (?:own )?(?:record|log) (?:has|shows|holds|includes|lists)\b", re.IGNORECASE)


def enabled() -> bool:
    return os.getenv("EXPERIENCES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def entries(db: Any = None) -> List[Dict[str, Any]]:
    """Every experience that may be cited: the evidence notes of the approved leanings, and with a database the notes a person approved in the dashboard (backend/utils/experience_capture.py), as
    {"id", "on", "kind", "what", "leaning", "area"} plus the fields the index needs. Without a database it is pure."""
    out: List[Dict[str, Any]] = []
    for entry in leanings.live(LEANINGS):
        for number, note in enumerate(entry.get("evidence") or []):
            what = str((note or {}).get("what", "")).strip()
            if not what:
                continue
            out.append({
                "id": f"{entry['id']}-{number}", "on": str(note.get("on", "")), "kind": str(note.get("kind") or "failure"), "what": what, "leaning": entry["id"], "area": entry.get("area", ""),
                "when": what, "line": "", "asks": [], "approved": True,
            })
    return out + (_approved_from_db(db) if db is not None else [])


_stored_cache: Dict[str, Any] = {"at": 0.0, "items": []}
STORED_CACHE_SECONDS = 60


def _approved_from_db(db: Any) -> List[Dict[str, Any]]:
    """The approved notes in the database, read at most once a minute. A failed read means none this turn."""
    import time

    now = time.monotonic()
    if now - _stored_cache["at"] < STORED_CACHE_SECONDS:
        return list(_stored_cache["items"])
    try:
        from backend.utils import experience_capture

        _stored_cache["items"] = experience_capture.approved_entries(db)
    except Exception:
        logger.warning("[experiences] could not read the approved notes.", exc_info=True)
        _stored_cache["items"] = []
    _stored_cache["at"] = now
    return list(_stored_cache["items"])


def forget_cached() -> None:
    """A note was approved, edited or deleted: read them again next time."""
    _stored_cache["at"] = 0.0


def closest_in_record(db: Any, embed: Callable[[str], Optional[List[float]]], vector: List[float]) -> Optional[float]:
    """How close `vector` is to the nearest note already in the record (None when the index is not ready), so a new note that only repeats one is merged, not added. Blocking."""
    try:
        items = entries(db)
        vectors = leanings.index_vectors(db, embed, items, fill=False, collection=COLLECTION, cache=_cache)
        scores = [leanings.cosine(vector, v) for vs in vectors.values() for v in leanings._vector_list(vs)]
        return max(scores) if scores else None
    except Exception:
        return None


def _month_label(on: str) -> str:
    import calendar

    try:
        year, month = (int(part) for part in on.split("-")[:2])
        return f"{calendar.month_name[month]} {year}"
    except (ValueError, IndexError):
        return on


def allowed(recent_messages: Iterable[Any], *, risk_active: bool, energy_tier: str, closing: bool, proactive_taken: bool = False) -> bool:
    """The cheap gates (nothing is read): a safety context, a heavy mood, a closing message or another proactive item in the turn stop it, and so does a citation in the last MIN_GAP messages."""
    if not enabled() or risk_active or closing or proactive_taken or energy_tier != "open":
        return False
    window = list(recent_messages)[-MIN_GAP:]
    return not any((getattr(m, "additional_kwargs", None) or {}).get(EXPERIENCE_MARK) for m in window)


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    items: Optional[List[Dict[str, Any]]] = None,
    vector: Optional[List[float]] = None,
    threshold: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """The one experience closest in meaning to the request, if it clears the threshold, else None. Failure and `worked` notes come before `record` ones (a number is the weakest thing to cite).
    The index is read, never built, here: an index that is not ready yet means no experience this turn (a background build fills it). Never raises. Blocking (an embedding)."""
    try:
        items = entries(db) if items is None else items
        text = (request or "").strip()[:MAX_REQUEST_CHARS]
        if not enabled() or not items or len(text) < leanings.MIN_REQUEST_CHARS:
            return None
        vectors = leanings.index_vectors(db, embed, items, fill=False, collection=COLLECTION, cache=_cache)
        if len(vectors) < len(items):
            warm(db, embed, items)
        asked = vector or embed(text)
        if not asked or not vectors:
            return None
        limit = MIN_SCORE if threshold is None else threshold
        scored = []
        for item in items:
            score = max((leanings.cosine(asked, v) for v in leanings._vector_list(vectors.get(item["id"]))), default=0.0)
            if score >= limit:
                scored.append((score - (0.03 if item.get("kind") == "record" else 0.0), item))
        return max(scored, key=lambda pair: pair[0])[1] if scored else None
    except Exception:
        logger.warning("[experiences] could not choose an experience for this request.", exc_info=True)
        return None


def warm(db: Any, embed: Callable[[str], Optional[List[float]]], items: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Builds the experience index in a background thread, one at a time (see leanings.warm). Returns True when a build was started. Never raises."""
    try:
        with _warm_lock:
            if _warming["on"]:
                return False
            _warming["on"] = True

        def build() -> None:
            try:
                leanings.index_vectors(db, embed, entries(db) if items is None else items, fill=True, collection=COLLECTION, cache=_cache)
            except Exception:
                logger.warning("[experiences] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="experiences-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


_KIND_HINT = {
    "failure": (
        "This is a failure. Mention it only if it changes what you are doing here, in the same breath (\"this is close to a case in my record, where X; so I'm checking Y first\"). Never as a confession "
        "offered for its own sake, and never dwelling on it."
    ),
    "worked": "This is something that worked. Say it plainly and briefly, as a reason for the approach you are taking (\"a case in my record where X worked, so I'm doing the same\").",
    "record": "This is a number from your own counters. Say it as a plain fact about your record, once, only if it bears on what they asked.",
}


def build_block(item: Dict[str, Any], reflection: str = "") -> str:
    """The prompt block that offers one experience, or "". `reflection` is the approved line on what Sonic takes from it (backend/utils/experience_reflections.py), if there is one. Pure."""
    if not item or not item.get("what"):
        return ""
    return (
        "\nA REAL CASE FROM YOUR OWN RECORD (optional, this reply only). Dated " + _month_label(str(item.get("on", ""))) + ": " + str(item["what"]) + "\n"
        "If, and only if, it genuinely helps with what they are asking now, you may mention it once, in a sentence, as a case in your record: \"this is close to a case in my record: ...\". "
        + _KIND_HINT.get(str(item.get("kind")), _KIND_HINT["failure"]) + " "
        "Say only what the note says: no added detail, no outcome or reason beyond it. Never say you remember or recall it, that it reminds you, that it came to mind, that you have seen this before, "
        "or \"this one time\": it is a record you can cite, not a memory coming back. Skip it entirely if the link is a stretch, if they are upset or wrapping up, or if it would pull them away from what they asked.\n"
        + (f"If you do cite it, you may add one more sentence on what you take from it, in your own words and without adding to it: \"{reflection}\"\n" if reflection else "")
    )


def cited(reply: str) -> bool:
    """Whether the reply cites the record ("in my record", "my record shows"). Pure."""
    return bool(CITES_RE.search(reply or ""))


def admin_view(db: Any) -> Dict[str, Any]:
    """Everything the dashboard shows about the record: notes waiting, approved stored notes, and the hand-written ones, each with its reflection state. Blocking."""
    from backend.utils import experience_capture, experience_reflections

    rows = experience_capture.list_all(db)
    reflections = experience_reflections.load_all(db)
    blank = {"status": "", "text": ""}
    approved = [{**row, "reflection": reflections.get(f"db-{row['id']}", blank)} for row in rows if row.get("status") == experience_capture.APPROVED]
    return {
        "pending": [row for row in rows if row.get("status") == experience_capture.PENDING],
        "approved": approved,
        "registry": [{"id": item["id"], "on": item["on"], "kind": item["kind"], "what": item["what"], "leaning": item["leaning"], "area": item["area"], "reflection": reflections.get(item["id"], blank)}
                     for item in entries()],
        "limits": {"per_day": experience_capture.MAX_PER_DAY, "waiting": experience_capture.MAX_PENDING, "expires_days": experience_capture.PENDING_DAYS, "reflections_at_once": experience_reflections.BACKFILL_LIMIT},
    }


def counts_for(offered: bool, reply: str = "") -> Dict[str, int]:
    """The counter increments: a note was put in the prompt, and whether the reply cited the record. Pure."""
    counts: Dict[str, int] = {}
    if offered:
        counts["experience_offered"] = 1
        if cited(reply):
            counts["experience_cited"] = 1
    return counts
