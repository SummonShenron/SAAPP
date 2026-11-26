"""Shared moments: real things that happened between a person and Sonic, which Sonic may bring up the way a friend does ("like the night the plan loop cost you fifty cents
for two asks"). The 2026-11-25 entityhood review: Sonic had a record of its own, leanings, takes, people notes and jokes, but almost nothing that came from its history WITH
the person, which is the one thing no other assistant has.

WHAT ONE IS. A short line in Sonic's words (`text`), the day it happened (`when`, a real date), and a few cue words that make it relevant (`cues`). One document each in
COLLECTION. Every moment starts PROPOSED and is used only once the person APPROVED it (the Memory page): a moment is about them, so they decide which ones Sonic may bring up.

WHEN IT IS USED. Code decides, never the model: the person's message names one of its cues (whole words), the turn is conversational and calm (no safety context, not a
closing, a mood that is open, nothing else proactive already in the reply), no moment was offered in the last GAP_MESSAGES messages, and this moment was not used in the last
COOLDOWN_DAYS. Then the reply is told it may refer to it in one light clause, and to leave it out if it does not fit. Whether the reply really used it is counted (`used`).

Nothing of a moment is logged. Pure apart from the storage functions, which take the database.
"""
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

COLLECTION = "shared_moments"
PROPOSED, APPROVED, DISMISSED = "proposed", "approved", "dismissed"
STATUSES = (PROPOSED, APPROVED, DISMISSED)
MARK = "shared_moment"
GAP_MESSAGES = 30
COOLDOWN_DAYS = 14
MAX_TEXT = 200
MAX_CUES = 6
MAX_PER_PERSON = 60
_STOP = frozenset("that this with have been just like what your they them there their about from would could should really still very much when then than into also some more were will the and for you our".split())


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']{4,}", (text or "").lower()) if w not in _STOP}


def clean(raw: Any, today: Optional[date] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """({text, when, cues}, []) for a moment that can be stored, or (None, the reasons). The date must be a real day, not in the future. Pure."""
    errors: List[str] = []
    raw = raw if isinstance(raw, dict) else {}
    text = " ".join(str(raw.get("text") or "").split())
    if not 8 <= len(text) <= MAX_TEXT:
        errors.append(f"text: one line of 8 to {MAX_TEXT} characters")
    when = str(raw.get("when") or "").strip()
    try:
        day = date.fromisoformat(when)
        if day > (today or datetime.now(timezone.utc).date()):
            errors.append("when: the day has not happened yet")
    except ValueError:
        errors.append("when: a real date, YYYY-MM-DD")
    cues = []
    for cue in raw.get("cues") or []:
        cue = " ".join(str(cue or "").lower().split())
        if len(cue) >= 3 and cue not in cues:
            cues.append(cue)
    if not cues:
        errors.append("cues: at least one word or phrase (three letters or more) that makes it relevant")
    if errors:
        return None, errors
    return {"text": text, "when": when, "cues": cues[:MAX_CUES]}, []


def mentions(message: str, cue: str) -> bool:
    """Whether the message names the cue as a whole word or phrase (case-insensitive; an apostrophe or none, as people type it). Pure."""
    norm = lambda t: (t or "").lower().replace("’", "'")  # noqa: E731
    pattern = r"(?<![a-z0-9])" + re.escape(norm(cue)).replace("'", "'?") + r"(?![a-z0-9])"
    return bool(re.search(pattern, norm(message)))


def recently_offered(recent_messages: Iterable[Any], gap: int = GAP_MESSAGES) -> bool:
    return any((getattr(m, "additional_kwargs", None) or {}).get(MARK) for m in list(recent_messages or [])[-gap:])


def pick(moments: Iterable[Dict[str, Any]], message: str, recent_messages: Iterable[Any], *, now: datetime, source_type: str, risk_active: bool,
         closing: bool, energy_tier: str, proactive_taken: bool) -> Optional[Dict[str, Any]]:
    """The approved moment this message calls up, or None. The one naming the most cues wins, then the one used longest ago. Pure."""
    if source_type != "conversational" or risk_active or closing or energy_tier != "open" or proactive_taken or recently_offered(recent_messages):
        return None
    best, best_key = None, None
    for moment in moments or []:
        if moment.get("status") != APPROVED:
            continue
        last = moment.get("last_used_at")
        if last:
            try:
                used = datetime.fromisoformat(str(last))
                if (now - (used if used.tzinfo else used.replace(tzinfo=timezone.utc))) < timedelta(days=COOLDOWN_DAYS):
                    continue
            except ValueError:
                pass
        hits = sum(1 for cue in moment.get("cues") or [] if mentions(message, cue))
        if not hits:
            continue
        key = (hits, -(datetime.fromisoformat(str(last)).timestamp() if last else 0))
        if best_key is None or key > best_key:
            best, best_key = moment, key
    return best


def _day(iso: str) -> str:
    try:
        return date.fromisoformat(iso).strftime("%b %d, %Y").replace(" 0", " ")
    except ValueError:
        return ""


def build_block(moment: Optional[Dict[str, Any]]) -> str:
    """The reply prompt's section, or "". Pure."""
    if not moment:
        return ""
    when = _day(str(moment.get("when") or ""))
    return (
        "\nSOMETHING YOU AND THEY SHARE (a real moment from your history together, which they approved you bringing up): "
        f"{moment['text']}" + (f" ({when})" if when else "") + ".\n"
        "If it genuinely connects to what they just said, refer to it in one light clause, the way a friend brings up something you both remember. "
        "Do not explain it, do not quote this line word for word, do not make it the point of the reply, and do not add details that are not in it. If it does not fit, leave it out.\n"
    )


def used(reply: str, moment: Optional[Dict[str, Any]]) -> bool:
    """Whether the reply really brought the moment up: it shares two content words with the moment's line, or names one of its cues. Pure."""
    if not moment or not reply:
        return False
    return len(_words(reply) & _words(moment.get("text", ""))) >= 2 or any(mentions(reply, cue) for cue in moment.get("cues") or [])


# ---- storage --------------------------------------------------------------------------------------------------------------------------------

def list_for(db: Any, username: str) -> List[Dict[str, Any]]:
    """Every moment of this person, newest day first (no database: none). Blocking."""
    if db is None or not username:
        return []
    rows = list(db[COLLECTION].find({"username": username}, {"_id": 0}))
    return sorted(rows, key=lambda m: (str(m.get("when") or ""), str(m.get("created_at") or "")), reverse=True)


def approved_for(db: Any, username: str) -> List[Dict[str, Any]]:
    return [m for m in list_for(db, username) if m.get("status") == APPROVED]


def propose(db: Any, username: str, raw: Any, *, source: str = "suggested", now: Optional[datetime] = None) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Stores a moment as PROPOSED (it is not used until approved). The same text twice is one moment, and a person has at most MAX_PER_PERSON. Blocking."""
    now = now or datetime.now(timezone.utc)
    cleaned, errors = clean(raw, now.date())
    if cleaned is None:
        return None, errors
    existing = list_for(db, username)
    if any(m.get("text", "").lower() == cleaned["text"].lower() for m in existing):
        return None, ["this moment is already there"]
    if len(existing) >= MAX_PER_PERSON:
        return None, [f"at most {MAX_PER_PERSON} moments; remove some first"]
    moment = {**cleaned, "id": uuid.uuid4().hex, "username": username, "status": PROPOSED, "source": source, "created_at": now.isoformat(), "uses": 0, "last_used_at": None}
    db[COLLECTION].insert_one(dict(moment))
    return moment, []


def set_status(db: Any, username: str, moment_id: str, status: str) -> bool:
    """Approves or dismisses one of this person's moments. False for an unknown status or a moment that is not theirs. Blocking."""
    if status not in (APPROVED, DISMISSED) or db is None:
        return False
    result = db[COLLECTION].update_one({"username": username, "id": moment_id}, {"$set": {"status": status}})
    return bool(getattr(result, "matched_count", 0))


def delete(db: Any, username: str, moment_id: str) -> bool:
    if db is None:
        return False
    result = db[COLLECTION].delete_one({"username": username, "id": moment_id})
    return bool(getattr(result, "deleted_count", 0))


def note_used(db: Any, username: str, moment_id: str, now: Optional[datetime] = None) -> None:
    """Records that a reply brought the moment up (starts its cooldown). Blocking; never raises."""
    try:
        db[COLLECTION].update_one({"username": username, "id": moment_id},
                                  {"$set": {"last_used_at": (now or datetime.now(timezone.utc)).isoformat()}, "$inc": {"uses": 1}})
    except Exception:
        pass


def public(moment: Dict[str, Any]) -> Dict[str, Any]:
    """What the Memory page shows of a moment. Pure."""
    return {k: moment.get(k) for k in ("id", "text", "when", "cues", "status", "source", "uses", "last_used_at")}
