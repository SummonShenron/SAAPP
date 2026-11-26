"""New shared moments, noticed as they happen (stage 2 of backend/utils/shared_moments.py, 2026-11-25).

WHEN. After a turn, and only when the person's message carries a signal that something memorable just happened between them and Sonic (SIGNALS: a shared past, a milestone,
a verdict on Sonic). Measured on Jack's own month (2026-10-25 to 11-25): 17 messages matched, a median of one a day, at most three, and they were the real thing ("We did it!",
"50 cents for two asks you couldnt even complete", "seriously you forgot all of it?") plus some that were not ("it works for me most of the times"). So a signal only earns a
look: never under a safety plan, never for a guest, never when they are low or distressed (a hard moment is not something to bring up fondly), at most once every
CALL_GAP_HOURS per person, and only while fewer than MAX_WAITING moments wait for their OK.

WHAT. One small-model call reads the last few messages and returns one moment in Sonic's voice with cues, or nothing (the usual answer). Code then refuses anything that is
not grounded: the line may not carry a number, time or name the messages do not (backend/utils/memory_grounding.py), every cue must appear in the messages, and nothing
sensitive (backend/utils/interest_anchor.py). The day is today, set by code, never by the model. What survives is stored PROPOSED: the person approves it on the Memory page
before Sonic ever uses it. Counts only, no content, are recorded.
"""
import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from backend.utils import shared_moments

logger = logging.getLogger("SASS Logger")

SIGNALS = {
    "shared_past": re.compile(r"\bremember (?:when|that time|the time|how you|how we)\b|\bthat time (?:you|we)\b|\blast time (?:you|we)\b", re.I),
    "milestone": re.compile(r"\b(?:it works|it worked|we did it|finally (?:works|working|done|merged|shipped|fixed)|shipped it|got the (?:job|offer)|i got the job|nailed it|you nailed|we nailed|first time (?:you|we|it))\b", re.I),
    "verdict": re.compile(r"\b(?:you forgot|you broke|you saved me|cents for|best (?:reply|answer) (?:yet|ever)|you (?:finally|actually) (?:did|got) it|proud of (?:you|us)|that was (?:amazing|hilarious|perfect)|you did it)\b", re.I),
}
# Beside interest_anchor's sensitive topics: substances, which that list leaves to "drugs" and "alcohol" (one of Jack's real signal messages was "remember when we got you high").
_SUBSTANCES_RE = re.compile(r"\b(?:high|stoned|weed|joints?|edibles?|vap(?:e|ing)|smok\w*|bong|drinks?|drinking|hungover|cravings?|buzzed)\b", re.IGNORECASE)
LOW_VALENCES = ("low", "distressed")
CALL_GAP_HOURS = 6
MAX_WAITING = 5
MAX_MESSAGES = 8
MAX_CHARS_PER_MESSAGE = 600
_last_call: Dict[str, float] = {}


def signal_of(message: str) -> Optional[str]:
    """Which signal the message carries, or None. Pure."""
    for name, pattern in SIGNALS.items():
        if pattern.search(message or ""):
            return name
    return None


def worth_a_look(message: str, *, guest: bool, risk_active: bool, mood: Optional[Dict[str, Any]]) -> bool:
    """The cheap gates, before any database read or model call. Pure."""
    mood = mood or {}
    if guest or risk_active or not signal_of(message):
        return False
    return not (mood.get("read_this_turn") and str(mood.get("valence")) in LOW_VALENCES)


def _allow_call(username: str, now_ts: Optional[float] = None) -> bool:
    now_ts = time.time() if now_ts is None else now_ts
    last = _last_call.get(username)
    if last is not None and now_ts - last < CALL_GAP_HOURS * 3600:
        return False
    _last_call[username] = now_ts
    return True


def forget_calls() -> None:
    _last_call.clear()


def exchange_text(messages: List[Any]) -> str:
    """The last MAX_MESSAGES messages as "Person:" / "Sonic:" lines, oldest first. Pure."""
    lines = []
    for message in list(messages or [])[-MAX_MESSAGES:]:
        kind = getattr(message, "type", "")
        content = getattr(message, "content", "")
        if kind not in ("human", "ai") or not isinstance(content, str) or not content.strip():
            continue
        lines.append(f"{'Person' if kind == 'human' else 'Sonic'}: {' '.join(content.split())[:MAX_CHARS_PER_MESSAGE]}")
    return "\n".join(lines)


def parse(reply: Any) -> Optional[Dict[str, Any]]:
    """{"moment", "cues"} from the model's reply, or None. Pure."""
    text = reply.content if hasattr(reply, "content") else reply
    if isinstance(text, list):
        text = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in text)
    match = re.search(r"\{.*\}", str(text or ""), re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("moment"), str) or not data["moment"].strip():
        return None
    return {"moment": data["moment"], "cues": data.get("cues") if isinstance(data.get("cues"), list) else []}


# A cue has to be specific to the moment, or it fires on everything: on the first live run (2026-11-25) the model offered "it worked", "forgotten" and "huge milestone". A cue
# is kept only when one of its words is RARE in what this person has written in the conversation (in fewer than RARE_SHARE of their messages), judged once the conversation
# has RARITY_MIN_MESSAGES of theirs; a shorter one keeps every cue that is in the messages.
RARE_SHARE = 0.01
RARITY_MIN_MESSAGES = 100
_CUE_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'._/-]{2,}")


def specific_cues(cues: List[str], messages: List[Any]) -> List[str]:
    """The cues with at least one word that is rare in this person's own messages. Pure."""
    theirs = [str(getattr(m, "content", "")).lower() for m in messages or [] if getattr(m, "type", "") == "human" and isinstance(getattr(m, "content", None), str)]
    if len(theirs) < RARITY_MIN_MESSAGES:
        return list(cues)
    limit = RARE_SHARE * len(theirs)
    kept = []
    for cue in cues:
        words = _CUE_WORD_RE.findall(cue.lower())
        if any(sum(1 for text in theirs if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text)) < limit for word in words):
            kept.append(cue)
    return kept


def validate(found: Optional[Dict[str, Any]], messages: List[Any], today: str) -> Optional[Dict[str, Any]]:
    """The moment as it may be stored, or None: grounded in the messages, cues that really appear in them, nothing sensitive, dated today. Pure."""
    if not found:
        return None
    from backend.utils.interest_anchor import is_sensitive
    from backend.utils.memory_grounding import ground_note

    person = " ".join(str(getattr(m, "content", "")) for m in messages[-MAX_MESSAGES:] if getattr(m, "type", "") == "human")
    sonic = " ".join(str(getattr(m, "content", "")) for m in messages[-MAX_MESSAGES:] if getattr(m, "type", "") == "ai")
    line = " ".join(found["moment"].split())
    if is_sensitive(line) or is_sensitive(person) or _SUBSTANCES_RE.search(f"{line} {person}"):
        return None
    if ground_note(line, user_text=person, assistant_text=sonic, names_anywhere=True).dropped:
        return None                                       # a number, time, day or name the messages do not hold
    everything = f"{person} {sonic}"
    cues = [cue for cue in found["cues"] if isinstance(cue, str) and shared_moments.mentions(everything, cue)]
    cues = specific_cues(cues, messages)
    cleaned, errors = shared_moments.clean({"text": line, "when": today, "cues": cues})
    return cleaned if not errors else None


async def capture(
    username: str,
    messages: List[Any],
    *,
    guest: bool,
    risk_active: bool,
    mood: Optional[Dict[str, Any]],
    invoke: Optional[Callable[[str], Awaitable[Any]]] = None,
    get_db_fn: Optional[Callable[[], Any]] = None,
    record: Optional[Callable[[Dict[str, int]], None]] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Proposes one new shared moment from the latest exchange, if there is one. Runs as a background task: never raises. True when a moment was proposed."""
    try:
        latest = next((str(m.content) for m in reversed(messages or []) if getattr(m, "type", "") == "human" and isinstance(getattr(m, "content", None), str)), "")
        if not worth_a_look(latest, guest=guest, risk_active=risk_active, mood=mood):
            return False
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        if record is None:
            from backend.utils.self_counters import record_turn_counts as record
        db = get_db_fn()
        if db is None:
            return False
        waiting = [m for m in await asyncio.to_thread(shared_moments.list_for, db, username) if m.get("status") == shared_moments.PROPOSED]
        if len(waiting) >= MAX_WAITING or not _allow_call(username):
            return False
        from backend.components.constraints import MOMENT_CAPTURE_PROMPT

        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.ainvoke
        found = parse(await invoke(MOMENT_CAPTURE_PROMPT.format(exchange=exchange_text(messages))))
        moment = validate(found, list(messages or []), (now or datetime.now(timezone.utc)).date().isoformat())
        if moment is None:
            await asyncio.to_thread(record, {"moment_capture_asked": 1, **({"moment_capture_refused": 1} if found else {})})
            return False
        stored, _errors = await asyncio.to_thread(lambda: shared_moments.propose(db, username, moment, source="captured", now=now))
        await asyncio.to_thread(record, {"moment_capture_asked": 1, **({"moment_capture_proposed": 1} if stored else {})})
        return stored is not None
    except Exception:
        logger.warning("[moment_capture] could not look for a new shared moment.", exc_info=True)
        return False
