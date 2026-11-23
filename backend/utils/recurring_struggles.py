"""Recurring struggles: how often someone has come to Sonic upset about the same person, counted in code, so a later step-back question can rest on a fact instead of a guess.

WHY. A friend who hears about the same painful relationship for the ninth time eventually asks the bigger question (what do you actually want? is this making you happy?). Sonic never
did: its mood reading lives in one stretch of one conversation, and the reply prompt forbids guessing "a pattern in their life they did not describe" (emotion_utils.SUBSTANCE_RULE), which is
right for a model on its own. Measured 2026-11-22 (read-only, counts only): in the one long thread that holds almost all of the owner's history, one saved person came up in 9 separate upset
stretches, and of 35 replies to upset messages about a saved person, 2 had any bigger-picture wording. This module is step 1 of that design: it COUNTS, and changes nothing Sonic says.

WHAT COUNTS. A turn is an upset turn about a person when the reasoner read THIS message as low, distressed or frustrated at UPSET_MIN or more, and the message is about one of their saved
people (conversation_people: a name from their relationship facts, named in the message, or the person the conversation has clearly been about when the message names nobody). Upset turns
about the same person less than EPISODE_GAP_HOURS apart are one EPISODE (one sitting); a later one starts a new episode. A person is RECURRING when they have at least
RECURRING_MIN_EPISODES episodes within WINDOW_DAYS that span at least RECURRING_MIN_SPAN_DAYS: the point at which a step-back question could be considered (step 2, not built).

WHAT IS STORED. One small document per person-who-uses-Sonic: for each saved person, the name as written in their facts and the start time of each episode, and (step 3, below) when a
step-back question was asked, until when it is dropped, and their own answer to it (at most ANSWER_MAX_CHARS). No other message text, no feeling, no reply. Episodes older than WINDOW_DAYS are dropped, a document untouched for EXPIRE_DAYS deletes itself, and "Clear All" on the Memory page deletes it (backend/utils/memory_erase.py).
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
        copy = {**person, "key": person.get("key"), "name": person.get("name"), "last_seen": person.get("last_seen"),
                "episodes": [e for e in (person.get("episodes") or []) if (_parse(e) or cutoff) > cutoff]}      # the step-back fields (asked_at, dropped_until, answer) ride along
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
    out = [p for p in out if p["episodes"] or p is entry or _parse(p.get("dropped_until")) is not None and _parse(p.get("dropped_until")) > now]
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


# ---- step 2 and 3: the step-back question, and dropping or pressing it (2026-11-22, on Jack's "do 1-6" with the defaults I recommended) ---------------------------------------
# WHEN. An upset message about a person who is RECURRING, and not in acute hurt (this message's intensity below ACUTE_INTENSITY): one question about the bigger thing (what they want from it,
# whether it is giving them what they need), never a verdict and never the count said aloud. At most once per person every ASK_GAP_DAYS.
# HOW FAR, by the person's reach dial (backend/utils/reach.py), so no new setting: "none" never asks; "low" and "default" ask now and then; "more" may also PRESS, by holding up what they
# themselves answered last time ("you said you wanted ...") next to what keeps happening.
# AFTER. The message after an asked step-back is read as its answer: a deflection ("I don't want to get into that") drops it for that person for DROP_DAYS; an engaged answer is kept, in
# their words (at most ANSWER_MAX_CHARS), for a later press. That answer is the one piece of their text this record keeps; Clear All and the record's expiry delete it.
STEP_BACK_MARK = "step_back_asked"          # on Sonic's own reply: the key of the person the step-back was about
ACUTE_INTENSITY = float(os.getenv("STEP_BACK_ACUTE_INTENSITY", "0.75"))
ASK_GAP_DAYS = 14
DROP_DAYS = 30
ANSWER_MIN_CHARS = 25
ANSWER_MAX_CHARS = 300
_DEFLECT_RE = re.compile(
    r"\b(?:don'?t|do not|rather not|not)\s+(?:want|wanna|ready|going)\s+(?:to\s+)?(?:talk|get into|go into|think about|discuss|answer|go there)\b"
    r"|\b(?:drop it|leave it|let it go|not now|not today|can we not|stop asking|change the subject|that'?s not the point|i don'?t know|idk|no idea|whatever)\b",
    re.IGNORECASE,
)


def classify_answer(message: str) -> str:
    """"deflected", "engaged" (at least ANSWER_MIN_CHARS and no deflection) or "unclear". Pure."""
    text = " ".join((message or "").split())
    if _DEFLECT_RE.search(text):
        return "deflected"
    return "engaged" if len(text) >= ANSWER_MIN_CHARS else "unclear"


def step_back_due(person: Optional[Dict[str, Any]], now: datetime, reach_level: str) -> Optional[Dict[str, Any]]:
    """{"key", "name", "press": their earlier answer or ""} when a step-back question may be asked about this person now, else None. Pure."""
    if not person or reach_level == "none" or not is_recurring(person, now):
        return None
    dropped = _parse(person.get("dropped_until"))
    if dropped is not None and dropped > now:
        return None
    asked = _parse(person.get("asked_at"))
    if asked is not None and now - asked < timedelta(days=ASK_GAP_DAYS):
        return None
    press = str(person.get("answer") or "") if reach_level == "more" else ""
    return {"key": person.get("key"), "name": person.get("name"), "press": press}


def step_back_for_turn(db: Any, username: str, message: str, messages: Sequence[Any], emotional_state: Any, load_facts: Callable[[str], Sequence[Any]], reach_level: str,
                       now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """The step-back question due on this turn, or None: an upset (not acute) message about a recurring person whose cadence allows it. Blocking (reads). Never raises."""
    try:
        if db is None or not username or reach_level == "none" or not upset_this_turn(emotional_state):
            return None
        if float((emotional_state or {}).get("intensity", 0.0)) >= ACUTE_INTENSITY:
            return None
        name = person_of_turn(message, messages, load_facts(username) or [])
        if not name:
            return None
        doc = db[COLLECTION].find_one({"username": username}, {"_id": 0, "people": 1}) or {}
        person = next((p for p in doc.get("people") or [] if p.get("key") == name.casefold()), None)
        return step_back_due(person, now or datetime.now(timezone.utc), reach_level)
    except Exception:
        logger.warning("[recurring struggles] could not decide on a step-back question.", exc_info=True)
        return None


def step_back_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section for a due step-back question, or "". Pure."""
    if not entry:
        return ""
    press = " ".join(str(entry.get("press") or "").split())
    return (
        f"\nA BIGGER QUESTION (true, from your records, found by the app: they have come to you upset about {entry['name']} many separate times over the past weeks, not only today): "
        "after you have answered what they said now, you may ask ONE question about the bigger picture, the way a friend eventually would: what they actually want from this, or whether it "
        "is giving them what they need. It is a question, not advice and not a verdict: never tell them to stay or leave, never diagnose the relationship or the person, never say \"people often\". "
        "Do not say how many times, and do not make it sound like you have been keeping score: \"this keeps coming back\" is the most you say about it. If anything in what they just said is "
        "raw or new, stay with that instead and leave the question for another time.\n"
        + (f"Last time you asked, they said, in their own words: \"{press}\". You may hold that up gently next to what keeps happening, once, as their words, not yours.\n" if press else "")
    )


def _set_fields(db: Any, username: str, key: str, fields: Dict[str, Any], now: datetime) -> bool:
    doc = db[COLLECTION].find_one({"username": username}, {"_id": 0, "people": 1}) or {}
    people = doc.get("people") or []
    if not any(p.get("key") == key for p in people):
        return False
    people = [({**p, **fields} if p.get("key") == key else p) for p in people]
    db[COLLECTION].update_one({"username": username}, {"$set": {"people": people, "updated_at": now}}, upsert=True)
    return True


def note_asked(db: Any, username: str, key: str, now: Optional[datetime] = None) -> bool:
    """Records that a step-back question about this person was really asked. Blocking. Never raises."""
    try:
        moment = now or datetime.now(timezone.utc)
        return bool(db is not None and key) and _set_fields(db, username, key, {"asked_at": moment.isoformat()}, moment)
    except Exception:
        logger.warning("[recurring struggles] could not record a step-back question.", exc_info=True)
        return False


def note_answer(db: Any, username: str, key: str, message: str, now: Optional[datetime] = None) -> Dict[str, int]:
    """Reads the message after an asked step-back as its answer: a deflection drops the question for DROP_DAYS, an engaged answer is kept for a later press. Returns its counters. Blocking. Never raises."""
    try:
        if db is None or not key:
            return {}
        moment = now or datetime.now(timezone.utc)
        kind = classify_answer(message)
        if kind == "deflected":
            _set_fields(db, username, key, {"dropped_until": (moment + timedelta(days=DROP_DAYS)).isoformat(), "answer": ""}, moment)
            return {"step_back_deflected": 1}
        if kind == "engaged":
            _set_fields(db, username, key, {"answer": " ".join((message or "").split())[:ANSWER_MAX_CHARS], "answer_at": moment.isoformat()}, moment)
            return {"step_back_engaged": 1}
        return {}
    except Exception:
        logger.warning("[recurring struggles] could not record a step-back answer.", exc_info=True)
        return {}


def record_answer(db: Any, username: str, key: str, message: str) -> None:
    """note_answer, then its counters added to the day's totals. For a background task. Never raises."""
    counts = note_answer(db, username, key, message)
    if counts:
        try:
            from backend.utils.self_counters import record_turn_counts

            record_turn_counts(counts)
        except Exception:
            logger.warning("[recurring struggles] could not record the counts.", exc_info=True)


def record_turn(db: Any, username: str, message: str, messages: Sequence[Any], emotional_state: Any, load_facts: Callable[[str], Sequence[Any]]) -> None:
    """note_turn, then its counters added to the day's totals. For a background task after the reply. Never raises."""
    counts = note_turn(db, username, message, messages, emotional_state, load_facts)
    if counts:
        try:
            from backend.utils.self_counters import record_turn_counts

            record_turn_counts(counts)
        except Exception:
            logger.warning("[recurring struggles] could not record the counts.", exc_info=True)
