"""Recurring struggles: how often someone has come to Sonic upset about the same person, counted in code, so a later step-back question can rest on a fact instead of a guess.

WHY. A friend who hears about the same painful relationship for the ninth time eventually asks the bigger question (what do you actually want? is this making you happy?). Sonic never
did: its mood reading lives in one stretch of one conversation, and the reply prompt forbids guessing "a pattern in their life they did not describe" (emotion_utils.SUBSTANCE_RULE), which is
right for a model on its own. Measured 2026-11-22 (read-only, counts only): in the one long thread that holds almost all of the owner's history, one saved person came up in 9 separate upset
stretches, and of 35 replies to upset messages about a saved person, 2 had any bigger-picture wording. This module is step 1 of that design: it COUNTS, and changes nothing Sonic says.

WHAT COUNTS. A turn is an upset turn about a person when the reasoner read THIS message as low, distressed or frustrated at UPSET_MIN or more, and the message is about one of their saved
people (conversation_people: a name from their relationship facts, named in the message, or the person the conversation has clearly been about when the message names nobody). Upset turns
about the same person less than EPISODE_GAP_HOURS apart are one EPISODE (one sitting); a later one starts a new episode. A person is RECURRING when they have at least
RECURRING_MIN_EPISODES episodes within WINDOW_DAYS that span at least RECURRING_MIN_SPAN_DAYS: the point at which a step-back question could be considered (step 2, not built).

WHAT IS STORED. One small document per person-who-uses-Sonic: for each saved person, the name as written in their facts and the start time of each episode. No message text, no feeling,
no reply. Episodes older than WINDOW_DAYS are dropped, a document untouched for EXPIRE_DAYS deletes itself, and "Clear All" on the Memory page deletes it (backend/utils/memory_erase.py).
Guests are never tracked. No name is ever logged; the counters (self_counters STRUGGLE_KEYS) are counts only.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from backend.utils import conversation_people
from backend.utils.emotion_utils import NEGATIVE_VALENCES

logger = logging.getLogger("SASS Logger")

COLLECTION = "user_recurring_struggles"
UPSET_MIN = float(os.getenv("STRUGGLE_UPSET_MIN", "0.3"))                    # how strong this message's negative reading must be to count
EPISODE_GAP_HOURS = float(os.getenv("STRUGGLE_EPISODE_GAP_HOURS", "6"))      # upset turns closer than this are one sitting
RECURRING_MIN_EPISODES = int(os.getenv("STRUGGLE_RECURRING_MIN_EPISODES", "4"))
RECURRING_MIN_SPAN_DAYS = float(os.getenv("STRUGGLE_RECURRING_MIN_SPAN_DAYS", "7"))
WINDOW_DAYS = 60
EXPIRE_DAYS = 90
PRESENCE_MIN = 0.5             # a message that names nobody counts for the person the conversation has clearly been about (conversation_people presence)
MAX_PEOPLE = 30
MAX_EPISODES = 40

_ready: set = set()


def upset_this_turn(emotional_state: Any) -> bool:
    """True when the reasoner read THIS message (not a feeling carried from earlier) as low, distressed or frustrated, at least UPSET_MIN strong. A message read as a small lift while an
    earlier low is carried keeps the earlier valence in the merged state (emotion_utils.merge_emotional_state), so `lift_this_turn` rules it out. Pure."""
    state = emotional_state if isinstance(emotional_state, dict) else {}
    try:
        return (bool(state.get("read_this_turn")) and not state.get("lift_this_turn") and str(state.get("valence")) in NEGATIVE_VALENCES
                and float(state.get("intensity", 0.0)) >= UPSET_MIN)
    except (TypeError, ValueError):
        return False


def person_of_turn(message: str, messages: Sequence[Any], facts: Sequence[Any]) -> Optional[str]:
    """The saved person this message is about, as written in their facts, or None. A name in the message wins (the most present one if it names several); a message that names nobody
    is about the person the conversation has clearly been about (presence at least PRESENCE_MIN). Pure."""
    present = dict(conversation_people.people_in_conversation(messages, facts))
    named = [name for name in conversation_people.people_vocabulary(facts) if re.search(r"\b" + re.escape(name) + r"\b", message or "", re.IGNORECASE)]
    if named:
        return max(named, key=lambda name: present.get(name, 0.0))
    if present:
        name, presence = max(present.items(), key=lambda item: item[1])
        if presence >= PRESENCE_MIN:
            return name
    return None


def _parse(value: Any) -> Optional[datetime]:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def update(people: List[Dict[str, Any]], name: str, now: datetime) -> Tuple[List[Dict[str, Any]], bool]:
    """The people list after an upset turn about `name` at `now`, and whether it started a new episode. Episodes older than WINDOW_DAYS are dropped; at most MAX_EPISODES per person and
    MAX_PEOPLE people (the least recently seen go first). Pure: the input is not changed."""
    key = name.casefold()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    out: List[Dict[str, Any]] = []
    entry: Optional[Dict[str, Any]] = None
    for person in people or []:
        copy = {"key": person.get("key"), "name": person.get("name"), "last_seen": person.get("last_seen"),
                "episodes": [e for e in (person.get("episodes") or []) if (_parse(e) or cutoff) > cutoff]}
        if copy["key"] == key:
            entry = copy
        out.append(copy)
    if entry is None:
        entry = {"key": key, "name": name, "last_seen": None, "episodes": []}
        out.append(entry)
    last_seen = _parse(entry.get("last_seen"))
    started = last_seen is None or now - last_seen >= timedelta(hours=EPISODE_GAP_HOURS) or not entry["episodes"]
    if started:
        entry["episodes"] = (entry["episodes"] + [now.isoformat()])[-MAX_EPISODES:]
    entry["last_seen"] = now.isoformat()
    entry["name"] = name
    out = [p for p in out if p["episodes"] or p is entry]
    out.sort(key=lambda p: _parse(p.get("last_seen")) or cutoff, reverse=True)
    return out[:MAX_PEOPLE], started


def is_recurring(person: Dict[str, Any], now: datetime) -> bool:
    """At least RECURRING_MIN_EPISODES episodes within WINDOW_DAYS, spanning at least RECURRING_MIN_SPAN_DAYS. Pure."""
    cutoff = now - timedelta(days=WINDOW_DAYS)
    times = sorted(t for t in (_parse(e) for e in (person.get("episodes") or [])) if t is not None and t > cutoff)
    return len(times) >= RECURRING_MIN_EPISODES and (times[-1] - times[0]) >= timedelta(days=RECURRING_MIN_SPAN_DAYS)


def recurring(people: Sequence[Dict[str, Any]], now: datetime) -> List[Dict[str, Any]]:
    """The recurring people, most episodes first: [{"name", "episodes", "first", "last"}]. What step 2 would read. Pure."""
    cutoff = now - timedelta(days=WINDOW_DAYS)
    found = []
    for person in people or []:
        if not is_recurring(person, now):
            continue
        times = sorted(t for t in (_parse(e) for e in person.get("episodes") or []) if t is not None and t > cutoff)
        found.append({"name": person.get("name"), "episodes": len(times), "first": times[0].isoformat(), "last": times[-1].isoformat()})
    return sorted(found, key=lambda p: p["episodes"], reverse=True)


def counts_for(upset_about_someone: bool, started: bool, now_recurring: bool) -> Dict[str, int]:
    """The turn's counters (self_counters STRUGGLE_KEYS). Pure."""
    if not upset_about_someone:
        return {}
    return {"struggle_turns": 1, **({"struggle_episodes": 1} if started else {}), **({"struggle_recurring": 1} if now_recurring else {})}


def _ensure_indexes(db: Any) -> None:
    if id(db) in _ready:
        return
    db[COLLECTION].create_index("username", unique=True)
    db[COLLECTION].create_index("updated_at", expireAfterSeconds=EXPIRE_DAYS * 86400)
    _ready.add(id(db))


def note_turn(db: Any, username: str, message: str, messages: Sequence[Any], emotional_state: Any, load_facts: Callable[[str], Sequence[Any]],
              now: Optional[datetime] = None) -> Dict[str, int]:
    """Records this turn if it was an upset turn about a saved person, and returns its counters ({} when it was not). Blocking (database): call it off the event loop. Never raises."""
    try:
        if db is None or not username or not upset_this_turn(emotional_state):
            return {}
        name = person_of_turn(message, messages, load_facts(username) or [])
        if not name:
            return {}
        moment = now or datetime.now(timezone.utc)
        _ensure_indexes(db)
        doc = db[COLLECTION].find_one({"username": username}, {"_id": 0, "people": 1}) or {}
        people, started = update(doc.get("people") or [], name, moment)
        db[COLLECTION].update_one({"username": username}, {"$set": {"people": people, "updated_at": moment}}, upsert=True)
        person = next(p for p in people if p["key"] == name.casefold())
        return counts_for(True, started, is_recurring(person, moment))
    except Exception:
        logger.warning("[recurring struggles] could not record a turn.", exc_info=True)
        return {}


def record_turn(db: Any, username: str, message: str, messages: Sequence[Any], emotional_state: Any, load_facts: Callable[[str], Sequence[Any]]) -> None:
    """note_turn, then its counters added to the day's totals. For a background task after the reply. Never raises."""
    counts = note_turn(db, username, message, messages, emotional_state, load_facts)
    if counts:
        try:
            from backend.utils.self_counters import record_turn_counts

            record_turn_counts(counts)
        except Exception:
            logger.warning("[recurring struggles] could not record the counts.", exc_info=True)
