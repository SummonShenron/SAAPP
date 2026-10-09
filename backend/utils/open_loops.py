"""Open loops: things the user said are coming up ("a date tomorrow", "interview Friday"), so the next conversation
after the day has passed can ask how it went. Memory gives Sonic a past; this gives it a near future.

How it stays on the right side of the identity rules (backend/components/sonic_profile.py):

- It is anchored to something the user actually said, never to Sonic's own wish to hear from them. The follow-up
  block forbids "I was thinking about you / waiting to hear", and the identity check backstops the wording.
- It asks once. A loop is marked asked when it is offered and never offered again, and it expires a week after its day.
- Sensitive things (health, legal, money, loss, therapy) are never stored as loops, by the prompt and again by a
  keyword filter. If the user raises those themselves, ordinary memory handles it.
- It stays quiet when the emotional layer is holding the energy down, under any safety context, on a closing message,
  mid-conversation (only at the start of one or after a real pause), and on any route but plain conversation.
- It is the user's own data: listed on the Memory page, deletable one by one, and wiped by "Clear all".

Capture costs nothing on most turns: a cheap keyword gate looks for future-time language, and only a hit makes one
lite-model call, off the response path.

Storage is one document per loop in `user_open_loops` (not another array on the facts document), capped per user and
expired by a TTL index.
"""
import asyncio
import json
import logging
import os
import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from backend.utils.time_utils import gap_seconds, local_now

logger = logging.getLogger("SASS Logger")

COLLECTION = "user_open_loops"
MAX_OPEN_LOOPS = 12
MAX_TEXT_CHARS = 140
MAX_HORIZON_DAYS = 60
EXPIRE_DAYS_AFTER_DUE = 7
# Offered only at the start of a conversation, or when they are back after at least this long.
FOLLOW_UP_MIN_GAP_SECONDS = 2 * 3600
MAX_MESSAGE_CHARS = 2000

OPEN, ASKED, RESOLVED = "open", "asked", "resolved"
# "sensitive" is something the model may answer with but is never stored.
ALLOWED_KINDS = ("social", "work", "travel", "errand", "event")

_index_ready = False


def open_loops_enabled() -> bool:
    """A kill switch (OPEN_LOOPS_ENABLED=false) for the whole feature."""
    return os.getenv("OPEN_LOOPS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------------------------
# The cheap gate in front of the model call
# ---------------------------------------------------------------------------------------------------------------

_WEEKDAY = r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"  # full names only: "sat", "sun", "wed" are ordinary words
_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_FUTURE_CUE_RE = re.compile(
    r"\b(?:"
    r"tomorrow|tonight|later today|later this week|coming up|upcoming"
    r"|this (?:morning|afternoon|evening|weekend)"
    r"|next (?:week|month|weekend)"
    r"|" + _WEEKDAY +  # "interview Friday", "on Friday", "next Friday": a weekday name alone is enough to ask the model
    r"|in (?:a|an|\d+|a couple of|a few|two|three|four|five|six|seven) (?:days?|weeks?)"
    r"|" + _MONTH + r"\.? \d{1,2}"
    r"|\d{1,2}/\d{1,2}"
    r")\b",
    re.IGNORECASE,
)
# A message that reports on something that already happened ("it went great", "got the job"): only worth a model call
# when there is a loop due that it might close.
_PAST_REPORT_RE = re.compile(
    r"\b(?:went|was|were|did|got|had|yesterday|last night|turned out|ended up|how it)\b", re.IGNORECASE
)


def should_extract(message: str, has_due_loop: bool = False) -> bool:
    text = (message or "").strip()
    if not text or len(text) > MAX_MESSAGE_CHARS:
        return False
    if _FUTURE_CUE_RE.search(text):
        return True
    return has_due_loop and bool(_PAST_REPORT_RE.search(text))


_SENSITIVE_RE = re.compile(
    r"\b(?:doctor|dr\.|surgery|surgeon|diagnos\w*|therap\w*|psychiatr\w*|psycholog\w*|counsel+ing|hospital|biopsy|chemo\w*|"
    r"oncolog\w*|pregnan\w*|scan results?|test results?|medication|prescription|rehab|detox|hospice|funeral|memorial|burial|"
    r"autopsy|passed away|court|lawyer|attorney|custody|divorce|lawsuit|sentenc\w*|bankrupt\w*|eviction|foreclos\w*|"
    r"immigration|abortion|miscarriage|cancer|overdose|suicid\w*)\b",
    re.IGNORECASE,
)


def is_sensitive(text: str) -> bool:
    """Whether text touches health, legal, money trouble, loss or therapy, which is never kept as an open loop."""
    return bool(_SENSITIVE_RE.search(text or ""))


# ---------------------------------------------------------------------------------------------------------------
# Validating what the model returned
# ---------------------------------------------------------------------------------------------------------------

def validate_loop(raw: Any, today: date) -> Optional[Dict[str, str]]:
    """A cleaned `{text, due_date, kind}` for one model-proposed loop, or None. The date must be real and today or
    later (up to about two months out), the text short and not sensitive, the kind a known one. The model is never
    trusted to have done the date arithmetic: a date that is wrong in an obvious way is dropped, not guessed."""
    if not isinstance(raw, dict):
        return None
    text = " ".join(str(raw.get("what") or "").split())
    kind = str(raw.get("kind") or "").strip().lower()
    if not text or len(text) > MAX_TEXT_CHARS or kind not in ALLOWED_KINDS or is_sensitive(text):
        return None
    try:
        due = date.fromisoformat(str(raw.get("due_date") or "").strip())
    except ValueError:
        return None
    if due < today or due > today + timedelta(days=MAX_HORIZON_DAYS):
        return None
    return {"text": text, "due_date": due.isoformat(), "kind": kind}


def _tokens(text: str) -> set:
    return {word for word in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(word) > 2}


def is_duplicate(loop: Dict[str, Any], others: Iterable[Dict[str, Any]]) -> bool:
    """The same thing on the same day, worded differently ("has a first date" / "has a date tomorrow night")."""
    mine = _tokens(loop.get("text", ""))
    for other in others:
        if other.get("due_date") != loop.get("due_date"):
            continue
        theirs = _tokens(other.get("text", ""))
        if not mine or not theirs:
            continue
        if len(mine & theirs) / len(mine | theirs) >= 0.4:
            return True
    return False


# ---------------------------------------------------------------------------------------------------------------
# Storage (one document per loop; blocking, so call off the event loop)
# ---------------------------------------------------------------------------------------------------------------

def _ensure_indexes(db) -> None:
    global _index_ready
    if _index_ready:
        return
    db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    db[COLLECTION].create_index("username")
    _index_ready = True


def list_loops(db, username: str, statuses: Iterable[str] = (OPEN,)) -> List[Dict[str, Any]]:
    wanted = set(statuses)
    docs = db[COLLECTION].find({"username": username}, {"_id": 0})
    return sorted((d for d in docs if d.get("status") in wanted), key=lambda d: d.get("due_date", ""))


def load_open_loops(username: str) -> List[Dict[str, Any]]:
    """The user's open loops, or [] with no database (blocking, so call off the event loop)."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return list_loops(db, username) if db is not None else []


def upcoming_for_page(username: str) -> List[Dict[str, Any]]:
    """The Memory page's list: open loops only, soonest first."""
    return [public_view(doc) for doc in load_open_loops(username)]


def forget_loop(username: str, loop_id: str) -> bool:
    from backend.utils.db_utils import get_db

    db = get_db()
    return delete_loop(db, username, loop_id) if db is not None else False


def forget_all_loops(username: str) -> int:
    """Part of "forget everything": loops are the user's data too."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return delete_all_loops(db, username) if db is not None else 0


def mark_asked(username: str, loop_id: str, now: Optional[datetime] = None) -> bool:
    """Records that a loop was offered, so it is never offered again. Never raises."""
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is None:
            return False
        return set_status(db, username, loop_id, ASKED, now or datetime.now(timezone.utc))
    except Exception:
        logger.warning("[open loops] could not mark a loop as asked.", exc_info=True)
        return False


def save_loops(db, username: str, loops: List[Dict[str, str]], now: datetime) -> int:
    """Stores new loops, skipping duplicates and anything past the per-user cap. Returns how many were saved."""
    _ensure_indexes(db)
    existing = list_loops(db, username, (OPEN, ASKED))
    open_count = sum(1 for d in existing if d.get("status") == OPEN)
    saved = 0
    for loop in loops:
        if open_count + saved >= MAX_OPEN_LOOPS or is_duplicate(loop, existing):
            continue
        due = date.fromisoformat(loop["due_date"])
        doc = {
            "id": uuid.uuid4().hex,
            "username": username,
            "text": loop["text"],
            "due_date": loop["due_date"],
            "kind": loop["kind"],
            "status": OPEN,
            "created_at": now.isoformat(),
            "asked_at": None,
            # Gone from the database a day after the week-long window closes.
            "expires_at": datetime.combine(due + timedelta(days=EXPIRE_DAYS_AFTER_DUE + 1), time.min, tzinfo=timezone.utc),
        }
        db[COLLECTION].insert_one(doc)
        existing.append(doc)
        saved += 1
    return saved


def set_status(db, username: str, loop_id: str, status: str, now: datetime) -> bool:
    fields: Dict[str, Any] = {"status": status}
    if status == ASKED:
        fields["asked_at"] = now.isoformat()
    result = db[COLLECTION].update_one({"username": username, "id": loop_id}, {"$set": fields})
    return getattr(result, "matched_count", 0) > 0


def delete_loop(db, username: str, loop_id: str) -> bool:
    return getattr(db[COLLECTION].delete_one({"username": username, "id": loop_id}), "deleted_count", 0) > 0


def delete_all_loops(db, username: str) -> int:
    return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0) or 0)


def public_view(doc: Dict[str, Any]) -> Dict[str, Any]:
    """What the Memory page is shown for a loop: its own words and its day, nothing internal."""
    return {key: doc.get(key) for key in ("id", "text", "due_date", "kind", "status")}


# ---------------------------------------------------------------------------------------------------------------
# Choosing a follow-up for this turn
# ---------------------------------------------------------------------------------------------------------------

def _due_for_follow_up(loop: Dict[str, Any], today: str) -> bool:
    """Open, its day has passed, and still inside the week it is worth asking in."""
    if loop.get("status") != OPEN:
        return False
    try:
        due = date.fromisoformat(str(loop.get("due_date")))
        today_date = date.fromisoformat(today)
    except ValueError:
        return False
    return due < today_date <= due + timedelta(days=EXPIRE_DAYS_AFTER_DUE)


def select_followup(
    load_loops: Callable[[], List[Dict[str, Any]]],
    *,
    today: str,
    now: datetime,
    last_message_at: Optional[datetime],
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
) -> Optional[Dict[str, Any]]:
    """The one loop to ask about this turn, or None. Every cheap gate is checked before `load_loops` (a database
    read) is called, so most turns never touch the store."""
    if risk_active or closing or energy_tier != "open" or source_type != "conversational":
        return None
    seconds = gap_seconds(last_message_at, now)
    if last_message_at is not None and (seconds is None or seconds < FOLLOW_UP_MIN_GAP_SECONDS):
        return None  # mid-conversation: only the start of one, or a real pause, is the moment to ask
    try:
        loops = load_loops() or []
    except Exception:
        logger.warning("[open loops] could not read the loops.", exc_info=True)
        return None
    due = [loop for loop in loops if _due_for_follow_up(loop, today)]
    if not due:
        return None
    return max(due, key=lambda loop: loop.get("due_date", ""))


def describe_when(due_date: str, today: str) -> str:
    try:
        due = date.fromisoformat(due_date)
        today_date = date.fromisoformat(today)
    except ValueError:
        return "recently"
    if due == today_date - timedelta(days=1):
        return "yesterday"
    return f"on {due.strftime('%A, %B')} {due.day}"


def build_open_loop_block(loop: Optional[Dict[str, Any]], today: str) -> str:
    """The prompt section for an offered loop ("" when none). The model may use it or skip it."""
    if not loop:
        return ""
    return (
        "\nOPEN LOOP (optional, once): "
        f"{describe_when(loop.get('due_date', ''), today)} the user mentioned something coming up: "
        f"\"{loop.get('text', '')}\". If, and only if, it fits naturally, ask once how it went, as one short, casual "
        "clause. Answer what they actually asked first, and don't make it the point of the reply. If they've already "
        "told you how it went, don't ask: respond to what they said. Never say you were thinking about them, waiting, "
        "wondering, or looking forward to hearing; never mention that you keep track of things or remembered it on "
        "purpose; it's just something they told you that you recall. If the moment isn't right, skip it entirely.\n"
    )


# ---------------------------------------------------------------------------------------------------------------
# Capture (runs after the reply, off the response path)
# ---------------------------------------------------------------------------------------------------------------

def _response_text(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def parse_extraction(raw_text: str) -> Dict[str, Any]:
    """The model's JSON answer as a dict ({} if it isn't usable)."""
    cleaned = (raw_text or "").replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _existing_lines(loops: List[Dict[str, Any]]) -> str:
    if not loops:
        return "(none)"
    return "\n".join(f"{d.get('id')}: {d.get('text')} (due {d.get('due_date')})" for d in loops)


async def capture_open_loops(
    username: str,
    message: str,
    tz_name: Optional[str],
    *,
    risk_active: bool = False,
    now: Optional[datetime] = None,
    invoke: Optional[Callable[[str], Awaitable[Any]]] = None,
    get_db_fn: Optional[Callable[[], Any]] = None,
) -> Dict[str, int]:
    """Notes any upcoming thing in this message and closes any loop it reports on. Never raises (it runs as a
    background task); returns `{"captured": n, "resolved": m}` for tests and logging."""
    result = {"captured": 0, "resolved": 0}
    try:
        if not open_loops_enabled() or risk_active or is_sensitive(message):
            return result
        # Most messages mention nothing about time at all, so the store isn't even read for them.
        if not (_FUTURE_CUE_RE.search(message or "") or _PAST_REPORT_RE.search(message or "")):
            return result
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        db = get_db_fn()
        if db is None:
            return result
        moment = now or datetime.now(timezone.utc)
        local = local_now(tz_name, moment)
        today = local.date()
        existing = await asyncio.to_thread(list_loops, db, username, (OPEN,))
        # A loop whose day has arrived or passed might be what a past-tense message is reporting on.
        has_due = any(d.get("due_date", "") <= today.isoformat() for d in existing)
        if not should_extract(message, has_due):
            return result

        from backend.components.constraints import OPEN_LOOP_EXTRACTION_PROMPT

        prompt = OPEN_LOOP_EXTRACTION_PROMPT.format(
            today=today.isoformat(), weekday=local.strftime("%A"),
            existing=_existing_lines(existing), message=message.strip()[:MAX_MESSAGE_CHARS],
        )
        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.ainvoke
        parsed = parse_extraction(_response_text(await invoke(prompt)))

        loops = [v for v in (validate_loop(raw, today) for raw in (parsed.get("loops") or [])[:3]) if v]
        known_ids = {d.get("id") for d in existing}
        resolved_ids = [i for i in (parsed.get("resolved_ids") or []) if isinstance(i, str) and i in known_ids]

        if loops:
            result["captured"] = await asyncio.to_thread(save_loops, db, username, loops, moment)
        for loop_id in resolved_ids:
            if await asyncio.to_thread(set_status, db, username, loop_id, RESOLVED, moment):
                result["resolved"] += 1

        counts = {"open_loop_captured": result["captured"], "open_loop_resolved": result["resolved"]}
        if any(counts.values()):
            from backend.utils.self_counters import record_turn_counts

            await asyncio.to_thread(record_turn_counts, counts)
    except Exception:
        logger.warning("[open loops] capture failed for this turn.", exc_info=True)
    return result
