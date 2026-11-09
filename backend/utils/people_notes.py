"""Finding the people notes that fit what someone just said, and putting them in the reply (the registry is backend/components/sonic_people_notes.py; the design is docs/people-understanding-plan.md).

WHY. Asked nothing, a hurting person was interviewed; a friend who understands says something instead. A note is understanding of what people commonly go through in a situation and what tends to help, in front of the reply at the moment it matters,
for every base model: it goes into the shared reply prompt, not into one model's closing reminder.

HOW. The same machinery as the working leanings (backend/utils/leanings.py): every approved note is embedded once from its situation plus its example messages, the message is embedded (the turn's question embedding is already computed for memory), and
the closest few above a threshold are used. No model call is made beyond that embedding. Embeddings are kept in `sonic_people_note_index`, so a restart embeds nothing and an edited note is re-embedded on its own.

WHEN. Only on a conversational reply, never under a safety plan (the support ladder's rules decide those turns, and a note cannot say a person is or is not at risk), never on a closing message, and only for a message long enough to say something.
A miss is better than a forced fit, so the threshold is high and the block tells the model to skip a note that does not fit. An emotional context does NOT turn it off: those are the turns it is for.

NOTHING PERSONAL. The message is embedded and compared, never stored. What is recorded is a count per note (aggregate counters, backend/utils/self_counters.py), so a note that never matches can be reworded or removed.

THE RULES IT KEEPS (docs/feelings-policy.md). A note is about people and about what helps, never an emotional stake of Sonic's; no lived experience ("I know how that feels", "I've seen this"); no label or diagnosis for the person; nothing stated as fact
about what another person feels or means; and no engagement hook. `check_registry` holds every note, approved or not, to them, with the identity check as fixed code.
"""
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.components import sonic_people_notes as registry
from backend.utils import leanings
from backend.utils.identity_checks import identity_reply_issue

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_people_note_index"
# Calibrated 2026-11-08 on real embeddings (gemini-embedding-001) with 60 made-up messages worded fresh for the notes and 25 unrelated or merely adjacent ones: the intended note ranked first for all 60 (scores 0.67 to 0.91); the
# best unrelated scores were vague low-mood messages ("I'm a bit sad today" 0.719 against the night note, "I'm so tired" 0.710 against the after-a-push note). At 0.72 the intended note is served for 56 of 60 (93%), no unrelated message and no
# wrong note; 0.70 serves 58 but lets three vague messages through; 0.74 serves 51. High on purpose: a miss is better than a forced fit, and a vague mood has no situation to be understood.
MIN_SCORE = float(os.getenv("PEOPLE_NOTES_MIN_SCORE", "0.72"))
PER_TURN = max(int(os.getenv("PEOPLE_NOTES_PER_TURN", "2")), 1)
MIN_REQUEST_CHARS = 12
MAX_REQUEST_CHARS = 700
MAX_BLOCK_CHARS = 1800
ALLOWED_ROUTES = frozenset({"conversational"})

_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}


def enabled() -> bool:
    return os.getenv("PEOPLE_NOTES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def live(notes: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The notes that may be used: only the ones a person approved. A draft is never put in a prompt. Pure."""
    return [note for note in (registry.NOTES if notes is None else notes) if note.get("approved") is True]


def view(note: Dict[str, Any]) -> Dict[str, Any]:
    """A note in the shape the leanings' index and ranking read: its situation is what a message resembles (`when`), its pattern the text (`line`), its example messages `asks`. Pure."""
    return {"id": note["id"], "when": note.get("situation", ""), "line": note.get("pattern", ""), "asks": list(note.get("asks") or []), "approved": note.get("approved") is True}


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    source_type: str = "conversational",
    risk_active: bool = False,
    closing: bool = False,
    vector: Optional[List[float]] = None,
    notes: Optional[List[Dict[str, Any]]] = None,
    threshold: Optional[float] = None,
    k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The notes for this message, best first ([] when the feature is off, a gate is closed, or nothing is close enough). Never raises. Blocking (at most one embedding)."""
    try:
        if not enabled() or risk_active or closing or source_type not in ALLOWED_ROUTES:
            return []
        approved = live(notes)
        text = (request or "").strip()[:MAX_REQUEST_CHARS]
        if not approved or len(text) < MIN_REQUEST_CHARS:
            return []
        views = [view(note) for note in approved]
        vectors = leanings.index_vectors(db, embed, views, fill=False, collection=COLLECTION, cache=_cache)
        if len(vectors) < len(views):
            warm(db, embed, approved)
        asked = vector or embed(text)
        if not asked or not vectors:
            return []
        by_id = {note["id"]: note for note in approved}
        ranked = leanings.rank(asked, views, vectors, threshold=MIN_SCORE if threshold is None else threshold, k=PER_TURN if k is None else k)
        return [by_id[entry["id"]] for _, entry in ranked if entry["id"] in by_id]
    except Exception:
        logger.warning("[people_notes] could not choose notes for this message.", exc_info=True)
        return []


_warm_lock = threading.Lock()
_warming = {"on": False}


def warm(db: Any, embed: Callable[[str], Optional[List[float]]], notes: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Builds the index in a background thread (embedding what is missing), one build at a time, so no turn ever waits for embeddings. A server restart calls it at startup. True when a build started. Never raises."""
    try:
        with _warm_lock:
            if _warming["on"]:
                return False
            _warming["on"] = True

        def build() -> None:
            try:
                leanings.index_vectors(db, embed, [view(note) for note in live(notes)], fill=True, collection=COLLECTION, cache=_cache)
            except Exception:
                logger.warning("[people_notes] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="people-notes-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


BLOCK_HEAD = (
    "WHAT PEOPLE IN THIS SPOT OFTEN GO THROUGH (understanding you can draw on: a read, never your own experience). Use one note to say something that shows you understand, as \"people often...\" or \"it is common to...\", "
    "not to ask a question. Never say you know how it feels or that you have seen it; never label the person; never state what another person feels or means as fact; skip a note that does not fit what they said.\n"
)


def build_block(chosen: List[Dict[str, Any]]) -> str:
    """The prompt block for the chosen notes, or "": each with its situation, what people often go through, what tends to help and what to avoid, within MAX_BLOCK_CHARS (a note that would pass it is left out). Pure."""
    parts: List[str] = []
    length = len(BLOCK_HEAD)
    for note in chosen:
        entry = (f"- When: {note.get('situation', '')}\n  Often: {note.get('pattern', '')}\n  Tends to help: {note.get('helps', '')}\n  Avoid: {note.get('avoid', '')}\n")
        if parts and length + len(entry) > MAX_BLOCK_CHARS:
            break
        parts.append(entry)
        length += len(entry)
    return (BLOCK_HEAD + "".join(parts)) if parts else ""


def tally_turn(counts: Dict[str, int], chosen: List[Dict[str, Any]], tally: Callable[..., None]) -> None:
    """Adds this turn's counters to `counts` with the counters' own `tally` (backend/utils/self_counters.py, which ignores any key that is not registered): one for the turn and one per note. Counts only."""
    if not chosen:
        return
    tally(counts, "people_notes_offered")
    for note in chosen:
        tally(counts, "people_note_used", note["id"])


# What check_registry refuses, in words people can read.
_FIRST_PERSON_RE = re.compile(r"\b(?:I|I'm|I've|I'd|I'll|me|my|mine|myself)\b", re.IGNORECASE)
_LABEL_RE = re.compile(
    r"\battachment style\b|\bdisorder\b|\bdiagnos\w*|\btrauma response\b|\bnarcissis\w*|\bborderline\b|\bbipolar\b|\bptsd\b|\bcodependen\w*|\banxiously attached\b|\bavoidant\b"
    r"|\b(?:you|they) (?:are|have|'re) (?:an? )?(?:anxious|depressed|depression)\b",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"[\"“”]")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_TEXT_FIELDS = (("situation", registry.MAX_SITUATION_CHARS), ("pattern", registry.MAX_PATTERN_CHARS), ("helps", registry.MAX_HELPS_CHARS), ("avoid", registry.MAX_AVOID_CHARS))


def check_registry(notes: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the notes, in words ([] when they are sound). Checks drafts too. Each note: a unique snake_case id; a situation, pattern, helps and avoid that are single sentences within their limits and say nothing as Sonic ("I", "my"),
    carry no quotation, label or diagnosis, and pass the identity check; three or four short example messages; evidence with a kind, a date and one plain sentence (a `record` note also names how many independent people it rests on, at least three);
    and an `approved` flag. Pure."""
    problems: List[str] = []
    seen = set()
    for note in registry.NOTES if notes is None else notes:
        note_id = str(note.get("id", ""))
        if not _ID_RE.match(note_id) or note_id in seen:
            problems.append(f"{note_id or '(no id)'}: the id must be unique snake_case")
        seen.add(note_id)
        for field, limit in _TEXT_FIELDS:
            text = str(note.get(field, "")).strip()
            if not text or not text.endswith(".") or len(text) > limit:
                problems.append(f"{note_id}: {field} must be one or more sentences ending in a full stop, at most {limit} characters")
                continue
            if _FIRST_PERSON_RE.search(text):
                problems.append(f"{note_id}: {field} speaks as Sonic (I, me, my): a note is about people")
            if _QUOTE_RE.search(text):
                problems.append(f"{note_id}: {field} carries a quotation: a note holds no one's words")
            if _LABEL_RE.search(text):
                problems.append(f"{note_id}: {field} labels or diagnoses")
            if identity_reply_issue(text):
                problems.append(f"{note_id}: {field} fails the identity check ({identity_reply_issue(text)})")
        asks = note.get("asks") or []
        if not 3 <= len(asks) <= 4 or any(not str(a).strip() or len(str(a)) > registry.MAX_ASK_CHARS for a in asks):
            problems.append(f"{note_id}: asks must be 3 or 4 short example messages of at most {registry.MAX_ASK_CHARS} characters")
        evidence = note.get("evidence") or []
        if not evidence:
            problems.append(f"{note_id}: it needs at least one evidence note")
        for item in evidence:
            kind, what = (item or {}).get("kind"), str((item or {}).get("what", "")).strip()
            if kind not in ("hand", "reading", "record"):
                problems.append(f"{note_id}: an evidence note's kind must be hand, reading or record")
            if not re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str((item or {}).get("on", ""))) or not what or len(what) > registry.MAX_EVIDENCE_CHARS:
                problems.append(f"{note_id}: each evidence note needs a date and one plain sentence of at most {registry.MAX_EVIDENCE_CHARS} characters")
            if kind == "record" and not (isinstance((item or {}).get("people"), int) and item["people"] >= 3):
                problems.append(f"{note_id}: a record note must name at least 3 independent people it rests on")
        if not isinstance(note.get("approved"), bool):
            problems.append(f"{note_id}: approved must be true or false")
    return problems
