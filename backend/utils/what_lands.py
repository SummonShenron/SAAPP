"""What tends to land with a person: each kind of thing a reply did is counted against how their next message read, so the next choice can lean on it.

WHY (2026-11-27). Sonic chose its moves for a low moment in a fixed order and never learned from what followed. The app already reads, on every message, whether the mood lifted
(`lift_this_turn`), and nothing connected that back to what the reply before it had done.

WHAT. A reply carries MARK: the kinds of things it did (KINDS: a low move by its kind, a stated view, a stance, a placed story or record line, a question at the end). When the person's next
message arrives, each kind gets one try and, when that message showed a lift, one lift; a message of some length that is not a closing counts as engaged. Stored per person in `what_lands`
as numbers only, one document a person with flat fields (`read_tries`, `read_lifts`, `read_engaged`, ...). No message, topic or word is kept.

USE. `order(stats, kinds)` puts the kinds this person has lifted after most often first, once a kind has MIN_TRIES tries; with too little to go on the caller's own order stands. The low
moves use it (backend/utils/low_moves.py `prefer`). It is one person's numbers used for that person only, erased with everything else of theirs, and never for a guest.

No model call; nothing logged.
"""
import logging
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger("SASS Logger")

COLLECTION = "what_lands"
MARK = "move_kinds"
KINDS = ("push_back", "pattern", "read", "company", "view", "stance", "story", "record", "question")
MIN_TRIES = 4
ENGAGED_WORDS = 8
OUTCOMES = ("lift", "engaged", "flat")
FIELDS = ("tries", "lifts", "engaged")


def kinds_of(*, low_move: Optional[str] = None, view: bool = False, stance: bool = False, story: bool = False, record: bool = False, question: bool = False) -> List[str]:
    """The kinds a reply did, for its mark. Pure."""
    found = [low_move] if low_move in KINDS else []
    for present, kind in ((view, "view"), (stance, "stance"), (story, "story"), (record, "record"), (question, "question")):
        if present and kind not in found:
            found.append(kind)
    return found


def marked(message: Any) -> List[str]:
    """The kinds on a reply's mark, cleaned. Pure."""
    raw = (getattr(message, "additional_kwargs", None) or {}).get(MARK)
    return [kind for kind in (raw if isinstance(raw, (list, tuple)) else []) if kind in KINDS]


def last_reply_kinds(messages: Iterable[Any]) -> List[str]:
    """The kinds on Sonic's most recent reply before the person's latest message, or []. Pure."""
    for message in reversed(list(messages or [])):
        if getattr(message, "type", "") == "ai":
            return marked(message)
    return []


def outcome(state: Optional[Dict[str, Any]], message: str, closing: bool) -> str:
    """How the person's next message read: "lift" (the mood read showed one), "engaged" (they wrote on at some length and are not leaving) or "flat". Pure."""
    if (state or {}).get("lift_this_turn"):
        return "lift"
    if not closing and len(str(message or "").split()) >= ENGAGED_WORDS:
        return "engaged"
    return "flat"


def record(db: Any, username: str, kinds: List[str], result: str) -> bool:
    """Adds one try (and a lift or an engaged) to each kind for this person. True when written. Blocking; never raises."""
    kinds = [kind for kind in kinds or [] if kind in KINDS]
    if db is None or not username or not kinds or result not in OUTCOMES:
        return False
    inc: Dict[str, int] = {}
    for kind in kinds:
        inc[f"{kind}_tries"] = 1
        if result == "lift":
            inc[f"{kind}_lifts"] = 1
        if result in ("lift", "engaged"):
            inc[f"{kind}_engaged"] = 1
    try:
        db[COLLECTION].update_one({"username": username}, {"$inc": inc}, upsert=True)
        return True
    except Exception:
        logger.warning("[what lands] could not record an outcome.", exc_info=True)
        return False


def load(db: Any, username: str) -> Dict[str, Dict[str, int]]:
    """This person's numbers by kind, or {}. Blocking; never raises."""
    if db is None or not username:
        return {}
    try:
        doc = db[COLLECTION].find_one({"username": username}) or {}
    except Exception:
        logger.warning("[what lands] could not read the numbers.", exc_info=True)
        return {}
    found: Dict[str, Dict[str, int]] = {}
    for kind in KINDS:
        stats = {field: int(doc[f"{kind}_{field}"]) for field in FIELDS if isinstance(doc.get(f"{kind}_{field}"), (int, float))}
        if stats:
            found[kind] = stats
    return found


def rate(stats: Optional[Dict[str, int]]) -> Optional[float]:
    """The share of tries that were followed by a lift, or None with fewer than MIN_TRIES tries. Pure."""
    tries = int((stats or {}).get("tries") or 0)
    if tries < MIN_TRIES:
        return None
    return int((stats or {}).get("lifts") or 0) / tries


def order(stats: Optional[Dict[str, Dict[str, int]]], kinds: Iterable[str]) -> List[str]:
    """`kinds` with those this person has lifted after most often first. A kind without enough tries keeps its place among the others like it, after every kind that has a rate above
    zero, so a new kind is still tried before one that has been tried and never helped. Pure."""
    listed = list(kinds)
    rated = {kind: rate((stats or {}).get(kind)) for kind in listed}
    helped = sorted((kind for kind in listed if rated[kind]), key=lambda kind: (-rated[kind], listed.index(kind)))
    untried = [kind for kind in listed if rated[kind] is None]
    never = [kind for kind in listed if rated[kind] == 0]
    return helped + untried + never
