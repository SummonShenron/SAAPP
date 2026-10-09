"""Sonic speaking first: a message waiting in the conversation when the user comes back.

Everything proactive so far rides inside a reply to something the user sent. This is the first time Sonic writes a message
nobody asked for, so it carries a stricter bar than anything else, and every rule below is mechanical:

- OPT-IN. Off until the user turns it on (a menu setting), and a user whose settings say otherwise is never messaged.
- ANCHORED. Only to something concrete the user already gave Sonic: an open loop whose day has passed (see open_loops.py)
  or a goal that has gone quiet (memory_utils.find_stale_goal_to_nudge). Never to a wish of Sonic's own: the wording is
  checked for "I wanted to check in", "I was curious", "I've been wondering", and the identity check runs on it too.
- RARE. At most one every MIN_HOURS_BETWEEN hours, never while its last message in the thread is still unanswered, never
  more than MAX_UNANSWERED in a row, and never for a week after a safety escalation.
- QUIET. Only in an existing conversation, and only after a real pause since the last message.
- ON RETURN, NOT IN THE BACKGROUND. It is decided when the user opens a conversation (POST /api/chat/opening), so there is no
  scheduler, no push, and no message written while nobody was there. The timestamp is the moment it was written, so it never
  pretends otherwise, and Sonic must never say it looked into something "while you were away": nothing ran.
- VISIBLE. The message is stored with an `initiated` mark and shown as "Sonic started this".

If the model can't write an opener that passes the checks (one retry), a plain template is used instead, so the feature never
depends on the model behaving.
"""
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from backend.utils import open_loops as ol
from backend.utils.identity_checks import identity_reply_issue
from backend.utils.time_utils import build_time_context, gap_seconds, local_today, sent_at, stamp

logger = logging.getLogger("SASS Logger")

SETTINGS_COLLECTION = "user_settings"
INITIATED_KEY = "initiated"
KIND_KEY = "initiated_kind"

MIN_HOURS_BETWEEN = 20
MAX_UNANSWERED = 3
RISK_QUIET_DAYS = 7
MIN_PAUSE_SECONDS = ol.FOLLOW_UP_MIN_GAP_SECONDS  # a real pause since the thread's last message
MAX_WORDS = 40
MAX_ANCHOR_CHARS = 140


def proactive_opening_enabled() -> bool:
    """A server-side kill switch (PROACTIVE_OPENING_ENABLED=false) for the whole feature, whatever users have chosen."""
    return os.getenv("PROACTIVE_OPENING_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------------------------
# Per-user state (lives on the same per-user settings document as the other toggles)
# ---------------------------------------------------------------------------------------------------------------

def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def load_state(db, username: str) -> Dict[str, Any]:
    doc = db[SETTINGS_COLLECTION].find_one(
        {"username": username},
        {"_id": 0, "proactive_opening": 1, "last_initiated_at": 1, "unanswered_initiations": 1, "no_initiation_until": 1},
    ) or {}
    return {
        "enabled": doc.get("proactive_opening") is True,
        "last_initiated_at": _parse(doc.get("last_initiated_at")),
        "unanswered": int(doc.get("unanswered_initiations") or 0),
        "quiet_until": _parse(doc.get("no_initiation_until")),
    }


def set_enabled(db, username: str, enabled: bool) -> bool:
    enabled = bool(enabled)
    db[SETTINGS_COLLECTION].update_one({"username": username}, {"$set": {"proactive_opening": enabled}}, upsert=True)
    return enabled


def record_initiation(db, username: str, now: datetime) -> None:
    db[SETTINGS_COLLECTION].update_one(
        {"username": username},
        {"$set": {"last_initiated_at": now.isoformat()}, "$inc": {"unanswered_initiations": 1}},
        upsert=True,
    )


def reset_unanswered(db, username: str) -> None:
    """The user replied in the thread right after one: it was answered, so the run of unanswered ones starts over. A
    conditional update, so it writes only when there is something to reset."""
    db[SETTINGS_COLLECTION].update_one(
        {"username": username, "unanswered_initiations": {"$gt": 0}}, {"$set": {"unanswered_initiations": 0}}
    )


def note_risk(db, username: str, now: datetime) -> None:
    """A safety escalation happened: no opening message for a week, whatever else is due. `$max` only ever extends it."""
    until = (now + timedelta(days=RISK_QUIET_DAYS)).isoformat()
    db[SETTINGS_COLLECTION].update_one({"username": username}, {"$max": {"no_initiation_until": until}}, upsert=True)


def _with_db(fn: Callable, *args, default=None):
    """Runs a state function against the real database, or returns `default` with none. Never raises."""
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        return fn(db, *args) if db is not None else default
    except Exception:
        logger.warning("[initiation] state operation failed.", exc_info=True)
        return default


def get_enabled(username: str) -> bool:
    return bool(_with_db(lambda db, u: load_state(db, u)["enabled"], username, default=False))


def update_enabled(username: str, enabled: bool) -> bool:
    return bool(_with_db(set_enabled, username, enabled, default=False))


def answered(username: str) -> None:
    _with_db(reset_unanswered, username)


def risk_escalated(username: str, now: Optional[datetime] = None) -> None:
    _with_db(note_risk, username, now or datetime.now(timezone.utc))


# ---------------------------------------------------------------------------------------------------------------
# Whether to speak first at all
# ---------------------------------------------------------------------------------------------------------------

def _type_of(message: Any) -> str:
    return str(getattr(message, "type", "") or "")


def is_initiated(message: Any) -> bool:
    return bool((getattr(message, "additional_kwargs", None) or {}).get(INITIATED_KEY))


def refusal_reason(
    state: Dict[str, Any], transcript: List[Any], now: datetime, *, is_guest: bool = False
) -> Optional[str]:
    """Why Sonic must NOT open this conversation, or None if every gate passes."""
    if is_guest:
        return "guest"
    if not state["enabled"]:
        return "disabled"
    if state["quiet_until"] and now < state["quiet_until"]:
        return "recent_risk"
    if state["unanswered"] >= MAX_UNANSWERED:
        return "ignored"
    last = state["last_initiated_at"]
    if last and (now - last) < timedelta(hours=MIN_HOURS_BETWEEN):
        return "too_soon"
    if not any(_type_of(m) == "human" for m in transcript):
        return "new_thread"  # only an existing conversation, so a thread is never started by Sonic's own message
    final = transcript[-1]
    if _type_of(final) == "ai" and is_initiated(final):
        return "already_waiting"  # never twice in a row
    seconds = gap_seconds(sent_at(final), now)
    if seconds is None:
        return "unknown_gap"  # an unstamped (older) thread: an unknown time is never guessed
    if seconds < MIN_PAUSE_SECONDS:
        return "recent_activity"
    return None


def pick_anchor(
    username: str,
    today: str,
    now: datetime,
    last_message_at: Optional[datetime],
    load_loops: Callable[[], List[Dict[str, Any]]],
    find_goal: Callable[[], Any],
) -> Optional[Dict[str, Any]]:
    """What the opener is about: the most recent due open loop, else a goal that has gone quiet. Nothing else qualifies."""
    loop = ol.select_followup(
        load_loops, today=today, now=now, last_message_at=last_message_at,
        source_type="conversational", risk_active=False, energy_tier="open", closing=False,
    )
    if loop:
        return {"kind": "loop", "id": loop["id"], "text": loop["text"], "due_date": loop["due_date"]}
    try:
        fact = find_goal()
    except Exception:
        logger.warning("[initiation] could not look for a quiet goal.", exc_info=True)
        return None
    if fact is not None:
        return {"kind": "goal", "id": fact.id, "text": str(fact.fact)[:MAX_ANCHOR_CHARS]}
    return None


# ---------------------------------------------------------------------------------------------------------------
# Writing it, and checking what was written
# ---------------------------------------------------------------------------------------------------------------

def _task(anchor: Dict[str, Any], today: str) -> str:
    if anchor["kind"] == "loop":
        when = ol.describe_when(anchor.get("due_date", ""), today)
        return f"ask how this went. {when.capitalize()} they mentioned something coming up: \"{anchor['text']}\""
    return f"ask how this is going. They told you about it and haven't mentioned it in a while: \"{anchor['text']}\""


def build_opening_prompt(anchor: Dict[str, Any], today: str, tz_name: Optional[str], now: datetime, issue: str = "") -> str:
    from backend.components.constraints import SONIC_ASSISTANT_PERSONA
    from backend.components.sonic_profile import profile_prompt_block

    prompt = (
        SONIC_ASSISTANT_PERSONA + profile_prompt_block() + build_time_context(tz_name, now)
        + "\nYOU ARE OPENING THIS CONVERSATION. The user hasn't typed anything; this is the first thing they will see when "
        f"they come back. Write ONE short message, at most {MAX_WORDS} words, in your own voice, that does exactly this: "
        f"{_task(anchor, today)}.\n"
        "Rules: ask exactly one question. Refer to what they told you plainly. Don't say you were thinking about them, "
        "wondering, curious, waiting, or that you wanted to check in; you didn't exist between conversations, and the only "
        "reason to write is that they mentioned this. Don't say 'welcome back' or remark on how long they've been away. No "
        "emoji, no exclamation marks, no preamble or sign-off, and don't mention settings or that you keep track of things.\n"
    )
    if issue:
        prompt += f"\nYour previous attempt was rejected because it {issue}. Write it again without that problem.\n"
    return prompt + "\nOPENING MESSAGE:\n"


_DESIRE_RES = [
    (re.compile(r"\bI(?:'d| would| just)?\s*(?:just\s+)?wanted to (?:check in|see how|touch base|reach out|ask)\b", re.I), "says it wanted to check in"),
    (re.compile(r"\bI(?:'ve| have| was)?\s*(?:been\s+)?(?:wondering|curious|thinking about|worried)\b", re.I), "claims it was wondering or curious"),
    (re.compile(r"\b(?:couldn'?t|can'?t) stop thinking\b|\bon my mind\b|\bkept thinking\b", re.I), "claims it kept thinking about it"),
    (re.compile(r"\bwelcome back\b|\bgood to see you\b|\b(?:it'?s been|been) (?:a while|a bit|so long)\b|\blong time\b|\bI missed\b", re.I), "remarks on their absence"),
    (re.compile(r"\bwhile you(?:'ve| have| were)? (?:been )?(?:away|gone|out)\b|\bsince you(?:'ve| have)? (?:been )?(?:gone|away)\b", re.I), "talks about the time they were away"),
    (re.compile(r"\b(?:I looked|I checked|I went|I did some|I dug)\b[^.?!]{0,40}\b(?:into|up|through)\b", re.I), "claims work it did while they were away"),
]
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_STOPWORDS = {"the", "and", "has", "have", "with", "for", "that", "this", "from", "your", "about", "their", "they", "them",
              "was", "were", "will", "going", "coming", "mentioned", "something", "thing"}


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower()) if len(w) > 3 and w not in _STOPWORDS}


def opening_issue(text: str, anchor: Dict[str, Any]) -> Optional[str]:
    """What is wrong with a proposed opener, phrased to follow "it ", or None when it is fine."""
    body = (text or "").strip()
    if not body:
        return "was empty"
    words = len(body.split())
    if words > MAX_WORDS + 5:
        return f"ran to {words} words"
    if body.count("?") != 1:
        return "did not ask exactly one question"
    if "!" in body or _EMOJI_RE.search(body):
        return "used an exclamation mark or emoji"
    if "<<<" in body or "\n\n" in body:
        return "was more than one short message"
    identity = identity_reply_issue(body)
    if identity:
        return f"was flagged by the identity check ({identity})"
    for pattern, label in _DESIRE_RES:
        if pattern.search(body):
            return label
    anchor_words = _content_words(anchor.get("text", ""))
    if anchor_words and not (anchor_words & _content_words(body)):
        return "never referred to what they actually mentioned"
    return None


def fallback_opening(anchor: Dict[str, Any]) -> str:
    """A plain, always-acceptable opener, used when the model can't write one that passes the checks."""
    text = anchor["text"][:MAX_ANCHOR_CHARS].rstrip(" .")
    if anchor["kind"] == "loop":
        return f"You mentioned this was coming up: \"{text}\". How did it go?"
    return f"You mentioned this goal a while back: \"{text}\". How is it going?"


def _response_text(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return re.sub(r"<<<FOLLOW_UP:.*?>>>", "", str(content or ""), flags=re.S).strip().strip('"').strip()


async def write_opening(
    invoke: Callable[[str], Awaitable[Any]], anchor: Dict[str, Any], today: str, tz_name: Optional[str], now: datetime
) -> Dict[str, Any]:
    """`{"text": ..., "fallback": bool}`: the model's opener if it passes the checks (one retry), else the template."""
    issue = ""
    for _ in range(2):
        try:
            text = _response_text(await invoke(build_opening_prompt(anchor, today, tz_name, now, issue)))
        except Exception:
            logger.warning("[initiation] the model failed to write an opener.", exc_info=True)
            break
        issue = opening_issue(text, anchor) or ""
        if not issue:
            return {"text": text, "fallback": False}
    return {"text": fallback_opening(anchor), "fallback": True}


# ---------------------------------------------------------------------------------------------------------------
# The whole decision, run when a conversation is opened
# ---------------------------------------------------------------------------------------------------------------

def _stamp_goal_nudged(username: str, fact_id: str, now: datetime) -> None:
    from backend.utils.memory_utils import load_user_facts, save_user_facts

    facts = load_user_facts(username)
    for fact in facts:
        if fact.id == fact_id:
            fact.last_nudged_at = now.isoformat()
    save_user_facts(username, facts)


async def maybe_open_conversation(
    username: str,
    session_id: str,
    transcript: List[Any],
    *,
    tz_name: Optional[str],
    invoke: Callable[[str], Awaitable[Any]],
    save: Callable[[str, str, list], Any],
    now: Optional[datetime] = None,
    is_guest: bool = False,
    db=None,
) -> Optional[Dict[str, Any]]:
    """Appends and saves one opening message to `transcript` and returns it as `{type, content, sent_at, initiated}`, or
    returns None (the usual case) when any gate says no or nothing qualifies. Never raises: a failure here must not stop
    a conversation from opening."""
    import asyncio

    try:
        if not proactive_opening_enabled():
            return None
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            return None
        moment = now or datetime.now(timezone.utc)
        state = await asyncio.to_thread(load_state, db, username)
        reason = refusal_reason(state, transcript, moment, is_guest=is_guest)
        if reason:
            logger.debug("[initiation] not opening %s::%s: %s", username, session_id, reason)
            return None

        today = local_today(tz_name, moment)
        last_message_at = sent_at(transcript[-1])

        def find_goal():
            from backend.utils.memory_utils import find_stale_goal_to_nudge

            return find_stale_goal_to_nudge(username)

        anchor = await asyncio.to_thread(
            pick_anchor, username, today, moment, last_message_at, lambda: ol.list_loops(db, username), find_goal,
        )
        if not anchor:
            return None

        written = await write_opening(invoke, anchor, today, tz_name, moment)
        from langchain_core.messages import AIMessage

        message = stamp(AIMessage(content=written["text"], additional_kwargs={INITIATED_KEY: True, KIND_KEY: anchor["kind"]}), moment)
        transcript.append(message)
        await asyncio.to_thread(save, username, session_id, transcript)

        # Offered once: the loop or goal is closed for good, and the run of unanswered openings counts it.
        if anchor["kind"] == "loop":
            await asyncio.to_thread(ol.set_status, db, username, anchor["id"], ol.ASKED, moment)
        else:
            await asyncio.to_thread(_stamp_goal_nudged, username, anchor["id"], moment)
        await asyncio.to_thread(record_initiation, db, username, moment)

        from backend.utils.self_counters import record_turn_counts

        counts = {"initiation_offered": 1}
        if written["fallback"]:
            counts["initiation_fallback"] = 1
        await asyncio.to_thread(record_turn_counts, counts)
        return {"type": "ai", "content": written["text"], "sent_at": message.additional_kwargs.get("sent_at"), INITIATED_KEY: True}
    except Exception:
        logger.warning("[initiation] could not open the conversation.", exc_info=True)
        return None


async def open_for_request(
    username: str,
    session_id: str,
    chat_sessions: Dict[str, Any],
    *,
    load: Callable[[str, str], List[Any]],
    sync: Callable[[str, str, List[Any]], Any],
    get_timezone: Callable[[str], str],
    get_llm: Callable[[str], Any],
    save: Callable[[str, str, list], Any],
    is_guest: bool = False,
    now: Optional[datetime] = None,
    db=None,
) -> Optional[Dict[str, Any]]:
    """The body of POST /api/chat/opening: loads the conversation the way a chat turn does (so an opener lands in the same
    in-memory transcript the next turn will use) and decides. For a user who hasn't opted in, nothing is loaded at all."""
    import asyncio

    from backend.utils.user_settings_utils import TOGGLE_LOCKED_USERS

    session_id = (session_id or "").strip()
    if not session_id or is_guest or username in TOGGLE_LOCKED_USERS:
        return None
    if not await asyncio.to_thread(get_enabled, username):
        return None
    key = f"{username}::{session_id}"
    if key not in chat_sessions:
        chat_sessions[key] = load(username, session_id)
    else:
        sync(username, session_id, chat_sessions[key])
    return await maybe_open_conversation(
        username, session_id, chat_sessions[key],
        tz_name=await asyncio.to_thread(get_timezone, username),
        invoke=get_llm(username).ainvoke, save=save, now=now, is_guest=is_guest, db=db,
    )
