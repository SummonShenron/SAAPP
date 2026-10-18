"""Getting-to-know-you questions: when someone mentions a thing in their life Sonic knows nothing about yet (a project, a team, a person), Sonic may ask
ONE light question about how it fits ("is that for work, or your own thing?"), and the answer becomes a clear, linked memory.

Memory finds out what things are by listening, and it guesses which facts belong together (backend/utils/fact_links.py). A person can simply say. Asking
turns a guess into a statement: the answer is saved as one fact in the person's own words, and linked straight to the fact it belongs under (the employer, for a
work project) with no embedding guess in between.

How it stays a conversation and not a form:
- THE PERSON SETS THE AMOUNT. Account page, "Questions about you": less (never), balanced (the default) or more. Less means nothing is noted, nothing is asked, and
  nothing is left waiting. The "let Sonic bring something up on its own" switch also turns it off. A guest is never asked.
- ASKED ONCE, AND CAPPED. Each thing is asked about once, ever. Balanced allows one question a day and three a week; more, one every few hours and ten a week.
  The question takes the turn's one proactive slot, so it never stacks with a follow-up, a heads-up, a shelf item or the open question.
- NEVER WHEN A QUIET REPLY MATTERS MORE. Conversational route only, no safety context, energy tier open, not a closing message, not when they are answering a
  question Sonic just asked. It comes after the real answer, as one short clause.
- NOT SENSITIVE. Health, therapy, money trouble, legal matters, loss and feelings are never noted, by the prompt and again by a keyword filter. A question about a
  person or a place stays light ("who is Dana to you?" is for a friend or a coworker, never "how do you feel about her").
- IT MAY SKIP. The model writes the wording, may leave it out, and the usual reply checks still run. It is recorded as asked only if the reply really asked.
- THEIR DATA. What it noted is deleted by "clear all" and expires on its own; the saved fact is an ordinary memory they can edit or forget on the Memory page.

Capture is one lite-model call after the reply, off the response path, only for a message that talks about their own involvement in something, at most a few
an hour per person and paused in low-power mode. Storage is one small document per thing in `user_clarifications`, capped per user and expired by a TTL index, so
it cannot grow past MAX_DOCS_PER_USER * a few hundred bytes per person.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from backend.utils import profile_utils

logger = logging.getLogger("SASS Logger")

COLLECTION = "user_clarifications"
CLARIFY_MARK = "clarify_offered"        # on Sonic's own reply: the id of the thing it asked about

LESS, BALANCED, MORE = "less", "balanced", "more"
LEVELS = (LESS, BALANCED, MORE)
# (hours since the last question, questions in the last seven days)
CADENCE: Dict[str, Tuple[float, int]] = {BALANCED: (24.0, 3), MORE: (4.0, 10)}

PENDING, ASKED, ANSWERED, DROPPED = "pending", "asked", "answered", "dropped"
KINDS = ("project", "org", "person", "place", "group")
CATEGORY_FOR_KIND = {"project": "project", "org": "career", "person": "relationship", "place": "preference", "group": "relationship"}
PARENT_CATEGORIES = ("career", "project", "goal", "relationship")     # a general fact about the person (identity) is never what a thing belongs under
CONTEXT_CATEGORIES = ("identity", "career", "project", "goal", "relationship")

MAX_PENDING = 3
MAX_TRIES = 2                       # a question that was invited but not really asked is retried once, then dropped
MAX_DOCS_PER_USER = 60              # the oldest are forgotten past this
PENDING_DAYS = 7
ASKED_DAYS = 14
DECIDED_DAYS = 90                   # an answered or dropped thing is remembered this long, so it is not asked about again
MAX_ENTITY_CHARS = 60
MAX_WHAT_CHARS = 160
MAX_FACT_CHARS = 200
MAX_MESSAGE_CHARS = 1500
MIN_MESSAGE_WORDS = 6
MAX_CONTEXT_FACTS = 25
CAPTURE_CALLS_PER_HOUR = 6          # best effort: held in this process, so it bounds one server, not a fleet

_index_ready = False
_recent_calls: Dict[str, List[float]] = {}


def clarify_enabled() -> bool:
    """A kill switch (CLARIFY_QUESTIONS=false) for the whole feature."""
    return os.getenv("CLARIFY_QUESTIONS", "true").strip().lower() not in ("0", "false", "no", "off")


def level_of(profile: Optional[Dict[str, Any]]) -> str:
    """How many questions this person wants. Their "bring something up on its own" switch off means none, whatever the setting says."""
    if not profile_utils.offers_allowed(profile):
        return LESS
    choice = ((profile or {}).get("style") or {}).get("questions")
    return choice if choice in LEVELS else BALANCED


# ---------------------------------------------------------------------------------------------------------------
# The cheap gate in front of the model call
# ---------------------------------------------------------------------------------------------------------------

_INVOLVED_RE = re.compile(
    r"\b(?:i'?m|i am|i'?ve been|ive been|i have been|we'?re|we are|we'?ve been|working (?:on|with|for)|building|designing|developing|launching|helping|at work|for work|"
    r"(?:my|our) (?:team|project|client|boss|manager|company|friend|sister|brother|mom|mother|dad|father|partner|cousin|coworker|co-worker|colleague|roommate|"
    r"girlfriend|boyfriend|wife|husband|kid|son|daughter|group|club|class|startup|business))\b",
    re.IGNORECASE,
)


def worth_a_look(message: str) -> bool:
    """Whether a message talks about the person's own involvement in something, which is all that is worth a model call. Most messages do not."""
    text = (message or "").strip()
    if not text or len(text) > MAX_MESSAGE_CHARS or len(text.split()) < MIN_MESSAGE_WORDS:
        return False
    return bool(_INVOLVED_RE.search(text))


def _allow_call(username: str, now_ts: Optional[float] = None) -> bool:
    now_ts = time.time() if now_ts is None else now_ts
    recent = [t for t in _recent_calls.get(username, []) if now_ts - t < 3600]
    if len(recent) >= CAPTURE_CALLS_PER_HOUR:
        _recent_calls[username] = recent
        return False
    recent.append(now_ts)
    _recent_calls[username] = recent
    return True


# Ground that is never noted or asked about, on top of backend/utils/open_loops.py's list (health, therapy, legal, loss). It leaves out the word "financial" and
# the like on purpose: an employer is often "... Financial Group", and the thing to keep out is money TROUBLE (debt, bankruptcy), not a company's name.
_ALSO_SENSITIVE_RE = re.compile(
    r"\b(?:health|illness|ill|sick\w*|disease|medic\w*|depress\w*|anxi\w*|panic|mental|disorder|adhd|autis\w*|ptsd|trauma\w*|abus\w*|assault\w*|addict\w*|"
    r"alcohol\w*|drunk|sober|relaps\w*|drugs?|self[- ]harm|grief|griev\w*|mourn\w*|died|death|dead|miscarr\w*|fertil\w*|divorc\w*|breakup|broke up|"
    r"separat\w*|affair|cheat\w*|ex[- ](?:wife|husband|girlfriend|boyfriend|partner)|debt|loans?|fired|laid off|layoffs?|unemploy\w*|arrest\w*|"
    r"sex\w*|porn\w*|nsfw|religio\w*|politic\w*)\b",
    re.IGNORECASE,
)


def _is_sensitive(text: str) -> bool:
    from backend.utils.open_loops import is_sensitive as private

    return bool(private(text) or _ALSO_SENSITIVE_RE.search(str(text or "")))


def already_says(message: str, entity: str) -> bool:
    """Whether the message itself already says how the thing relates to them ("my sister Dana", "our client Northwind", "Dana, my coworker"): then there is
    nothing to ask. A backstop in code for a model that proposes it anyway; it errs toward not asking."""
    name = re.escape(" ".join((entity or "").split()))
    if not name:
        return False
    return bool(re.search(rf"\b(?:my|our)\s+(?:[\w'-]+\s+){{0,2}}{name}\b|\b{name}\s*(?:is|was|,)\s*(?:my|our)\b", message or "", re.IGNORECASE))


def entity_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


# ---------------------------------------------------------------------------------------------------------------
# What the model is shown, and what it may return
# ---------------------------------------------------------------------------------------------------------------

def candidate_facts(facts: Sequence[Any]) -> List[Any]:
    """The notes the model sees (newest first, a bounded few), so it can tell what is already known and what a thing belongs under. Nothing sensitive."""
    usable = [
        f for f in facts
        if getattr(f, "active", True) and getattr(f, "category", "") in CONTEXT_CATEGORIES and not _is_sensitive(getattr(f, "fact", ""))
    ]
    usable.sort(key=lambda f: getattr(f, "updated_at", "") or "", reverse=True)
    return usable[:MAX_CONTEXT_FACTS]


def facts_lines(candidates: Sequence[Any]) -> str:
    if not candidates:
        return "(nothing yet)"
    return "\n".join(f"{n}. {' '.join(str(c.fact).split())[:140]}" for n, c in enumerate(candidates, 1))


def _text_of(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def parse_json(raw: str) -> Dict[str, Any]:
    cleaned = (raw or "").replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _one_line(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))).strip()[:limit].strip()


def _pick(number: Any, candidates: Sequence[Any], categories: Optional[Sequence[str]] = None) -> Optional[Any]:
    """The fact a model's number points to, or None: the number must be a real one from the list shown (a bare integer, not a bool or text)."""
    if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(candidates):
        return None
    fact = candidates[number - 1]
    return fact if categories is None or fact.category in categories else None


def validate_thing(raw: Dict[str, Any], candidates: Sequence[Any], known_keys: Sequence[str]) -> Optional[Dict[str, Any]]:
    """A cleaned `{entity, kind, what, parent_fact_id}` for what the model proposed, or None. The model is never trusted with the details: the name and the
    sentence are short and plain, the kind is a known one, nothing is sensitive or an instruction, the parent is a real note from the list, and a thing
    already asked about (or declined) is not proposed again."""
    from backend.services.tool_agent_memory import reads_as_an_order

    if not isinstance(raw, dict) or not raw.get("entity"):
        return None
    entity = _one_line(raw.get("entity"), MAX_ENTITY_CHARS)
    what = _one_line(raw.get("what"), MAX_WHAT_CHARS)
    kind = str(raw.get("kind") or "").strip().lower()
    if not entity or len(entity.split()) > 8 or not what or kind not in KINDS:
        return None
    if re.search(r"[<>{}\[\]`|\\]|https?://|www\.", entity + what):
        return None
    if _is_sensitive(f"{entity} {what}") or reads_as_an_order(f"{entity} {what}"):
        return None
    key = entity_key(entity)
    if not key or key in set(known_keys):
        return None
    parent = _pick(raw.get("parent"), candidates, PARENT_CATEGORIES)
    return {"entity": entity, "kind": kind, "what": what, "key": key, "parent_fact_id": parent.id if parent else None}


def validate_answer(raw: Dict[str, Any], candidates: Sequence[Any]) -> Optional[Dict[str, Any]]:
    """`{fact, parent}` for a reply that really answered, or None. The fact is one short third-person sentence with nothing sensitive and no instruction in it."""
    from backend.services.tool_agent_memory import reads_as_an_order

    if not isinstance(raw, dict) or raw.get("answered") is not True:
        return None
    fact = _one_line(raw.get("fact"), MAX_FACT_CHARS + 1)
    if fact.lower().startswith("the user "):
        fact = fact[9:].lstrip()[:1].upper() + fact[9:].lstrip()[1:]       # facts are written "Is designing ...", as the other saved facts are
    if not fact or len(fact) > MAX_FACT_CHARS or len(fact.split()) < 3:
        return None
    if re.search(r"\b(?:I|I'm|I've|my|me|we|our)\b", fact) or re.search(r"[<>{}\[\]`|\\]|https?://|www\.", fact):
        return None
    if _is_sensitive(fact) or reads_as_an_order(fact):
        return None
    parent = _pick(raw.get("parent"), candidates, PARENT_CATEGORIES)
    return {"fact": fact, "parent_fact_id": parent.id if parent else None}


# ---------------------------------------------------------------------------------------------------------------
# Storage (one small document per thing; blocking, so call off the event loop)
# ---------------------------------------------------------------------------------------------------------------

def _ensure_indexes(db) -> None:
    global _index_ready
    if _index_ready:
        return
    db[COLLECTION].create_index("expires_at", expireAfterSeconds=0)
    db[COLLECTION].create_index("username")
    _index_ready = True


def list_docs(db, username: str) -> List[Dict[str, Any]]:
    return sorted(db[COLLECTION].find({"username": username}, {"_id": 0}), key=lambda d: d.get("created_at", ""))


def _expires(now: datetime, days: int) -> datetime:
    return now + timedelta(days=days)


def save_thing(db, username: str, thing: Dict[str, Any], now: datetime) -> bool:
    """Notes one thing to ask about later. Skips one already known (any status), and anything past the pending cap; forgets the oldest past the per-user cap."""
    _ensure_indexes(db)
    docs = list_docs(db, username)
    if any(d.get("key") == thing["key"] for d in docs):
        return False
    if sum(1 for d in docs if d.get("status") == PENDING) >= MAX_PENDING:
        return False
    for stale in docs[: max(0, len(docs) + 1 - MAX_DOCS_PER_USER)]:
        db[COLLECTION].delete_one({"username": username, "id": stale["id"]})
    db[COLLECTION].insert_one({
        "id": uuid.uuid4().hex, "username": username, "key": thing["key"], "entity": thing["entity"], "kind": thing["kind"], "what": thing["what"],
        "parent_fact_id": thing.get("parent_fact_id"), "status": PENDING, "tries": 0, "created_at": now.isoformat(), "asked_at": None,
        "expires_at": _expires(now, PENDING_DAYS),
    })
    return True


def set_status(db, username: str, doc_id: str, status: str, now: datetime, **fields: Any) -> bool:
    days = {ASKED: ASKED_DAYS, ANSWERED: DECIDED_DAYS, DROPPED: DECIDED_DAYS}.get(status, PENDING_DAYS)
    update: Dict[str, Any] = {"status": status, "expires_at": _expires(now, days), **fields}
    if status == ASKED:
        update["asked_at"] = now.isoformat()
    result = db[COLLECTION].update_one({"username": username, "id": doc_id}, {"$set": update})
    return getattr(result, "matched_count", 0) > 0


def bump_tries(db, username: str, doc_id: str, now: datetime) -> None:
    """The question was invited but the reply did not ask it: one more try at most, then it is dropped."""
    doc = next((d for d in list_docs(db, username) if d.get("id") == doc_id), None)
    if doc is None:
        return
    if int(doc.get("tries") or 0) + 1 >= MAX_TRIES:
        set_status(db, username, doc_id, DROPPED, now)
    else:
        db[COLLECTION].update_one({"username": username, "id": doc_id}, {"$set": {"tries": int(doc.get("tries") or 0) + 1}})


def forget_all(username: str) -> int:
    """Part of "forget everything"."""
    from backend.utils.db_utils import get_db

    db = get_db()
    return int(getattr(db[COLLECTION].delete_many({"username": username}), "deleted_count", 0) or 0) if db is not None else 0


# ---------------------------------------------------------------------------------------------------------------
# Choosing a question for this turn
# ---------------------------------------------------------------------------------------------------------------

def _parse_time(raw: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def cadence_clear(docs: Sequence[Dict[str, Any]], level: str, now: datetime) -> bool:
    """Whether this person's level allows another question now: long enough since the last one, and under the week's limit."""
    if level not in CADENCE:
        return False
    gap_hours, per_week = CADENCE[level]
    asked = [t for t in (_parse_time(d.get("asked_at")) for d in docs) if t is not None]
    if any(now - t < timedelta(hours=gap_hours) for t in asked):
        return False
    return sum(1 for t in asked if now - t < timedelta(days=7)) < per_week


def select_question(db, username: str, level: str, now: datetime) -> Optional[Dict[str, Any]]:
    """The one thing to ask about this turn (the oldest waiting), or None. The caller has applied every gate that depends on the conversation."""
    if level not in CADENCE:
        return None
    docs = list_docs(db, username)
    waiting = [d for d in docs if d.get("status") == PENDING and (_parse_time(d.get("expires_at")) or now) > now]
    if not waiting or not cadence_clear(docs, level, now):
        return None
    return waiting[0]


_FORM = {
    "project": "whether it is for work, for school, or something they do on their own",
    "org": "how they are connected to it (where they work, a client, a group they belong to)",
    "person": "who they are to them, kept light (a friend, a coworker, family)",
    "place": "what the place is to them (where they live or work, somewhere they go)",
    "group": "what it is to them (a hobby, something from work or school, a community)",
}


def build_question_block(doc: Optional[Dict[str, Any]], parent_text: Optional[str] = None) -> str:
    """The prompt section for a thing to ask about ("" when none). The model may use it or skip it."""
    if not doc:
        return ""
    known = ""
    if parent_text:
        known = (
            f" Something they have told you before: \"{' '.join(str(parent_text).split())[:140]}\". If the thing plausibly goes with that, a yes/no question that "
            "names it (\"is that for ...?\") is better than an open one."
        )
    return (
        "\nGETTING TO KNOW THEM (optional, this turn only): earlier they mentioned something about their own life: "
        f"\"{doc.get('what', '')}\" (a note in the third person: say it your own way, speaking to them as \"you\"; it is called \"{doc.get('entity', '')}\").{known} "
        "After you have fully answered what they asked, you may add ONE short, casual "
        f"question to understand where it fits in their life: {_FORM.get(doc.get('kind'), _FORM['project'])}. It is about where it fits, never how they feel about it. "
        "Write your own wording, as a friend would, in a clause or a sentence. Do not say you are curious, were wondering or want to know; do not mention notes, "
        "memory or that you remembered it; no list of options and no <<<FOLLOW_UP>>> suggestion. If they seem busy, upset or in the middle of something, or it would "
        "feel forced, skip it entirely.\n"
    )


# ---------------------------------------------------------------------------------------------------------------
# Capture and answer (run after the reply, off the response path)
# ---------------------------------------------------------------------------------------------------------------

def _known_keys(docs: Sequence[Dict[str, Any]]) -> List[str]:
    return [str(d.get("key")) for d in docs if d.get("key")]


def parent_text_for(facts: Sequence[Any], parent_fact_id: Optional[str]) -> Optional[str]:
    if not parent_fact_id:
        return None
    return next((f.fact for f in facts if f.id == parent_fact_id and getattr(f, "active", True)), None)


async def capture(
    username: str,
    message: str,
    *,
    level: str,
    previous_reply: str = "",
    risk_active: bool = False,
    now: Optional[datetime] = None,
    invoke: Optional[Callable[[str], Awaitable[Any]]] = None,
    get_db_fn: Optional[Callable[[], Any]] = None,
    load_facts: Optional[Callable[[str], List[Any]]] = None,
) -> bool:
    """Notes one thing worth asking about from this message, if there is one. Never raises (it runs as a background task); True when something was noted."""
    try:
        if not clarify_enabled() or level not in CADENCE or risk_active or not worth_a_look(message) or _is_sensitive(message):
            return False
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        db = get_db_fn()
        if db is None or not _allow_call(username):
            return False
        moment = now or datetime.now(timezone.utc)
        docs = await asyncio.to_thread(list_docs, db, username)
        if sum(1 for d in docs if d.get("status") == PENDING) >= MAX_PENDING:
            return False
        if load_facts is None:
            from backend.utils.memory_utils import load_user_facts as load_facts
        candidates = candidate_facts(await asyncio.to_thread(load_facts, username))

        from backend.components.constraints import CLARIFY_EXTRACTION_PROMPT

        prompt = CLARIFY_EXTRACTION_PROMPT.format(
            message=message.strip()[:MAX_MESSAGE_CHARS], previous=" ".join((previous_reply or "").split())[-400:] or "(none)",
            facts=facts_lines(candidates), entities=", ".join(sorted({str(d.get("entity")) for d in docs if d.get("entity")})) or "(none)",
        )
        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.ainvoke
        thing = validate_thing(parse_json(_text_of(await invoke(prompt))), candidates, _known_keys(docs))
        if thing is None or already_says(message, thing["entity"]):
            return False
        saved = await asyncio.to_thread(save_thing, db, username, thing, moment)
        if saved:
            from backend.utils.self_counters import record_turn_counts

            await asyncio.to_thread(record_turn_counts, {"clarify_noted": 1})
        return saved
    except Exception:
        logger.warning("[clarifications] could not note a thing to ask about.", exc_info=True)
        return False


def reads_as_a_reply(text: str) -> bool:
    """Whether a message could be an answer at all: not a sign-off, not a bare "ok", something to read."""
    from backend.utils.curiosity import is_lull
    from backend.utils.wrapup_utils import is_closing_message

    stripped = (text or "").strip()
    return bool(stripped) and len(stripped) <= 600 and not is_closing_message(stripped) and not is_lull(stripped)


async def apply_reply(
    username: str,
    doc_id: str,
    reply: str,
    *,
    risk_active: bool = False,
    now: Optional[datetime] = None,
    invoke: Optional[Callable[[str], Awaitable[Any]]] = None,
    get_db_fn: Optional[Callable[[], Any]] = None,
    load_facts: Optional[Callable[[str], List[Any]]] = None,
    save_fact: Optional[Callable[..., Any]] = None,
    link_facts: Optional[Callable[[str, str, str], bool]] = None,
    memory_vector_store: Any = None,
) -> str:
    """What the person said after being asked: "answered" (saved as a fact, linked to what it belongs under), "dropped" (a change of subject, a refusal, anything
    sensitive: it is not asked again) or "skipped" (nothing waiting for an answer). The question was asked once, so whatever happens it is not asked again. Never raises."""
    try:
        if get_db_fn is None:
            from backend.utils.db_utils import get_db as get_db_fn
        db = get_db_fn()
        if db is None:
            return "skipped"
        moment = now or datetime.now(timezone.utc)
        doc = next((d for d in await asyncio.to_thread(list_docs, db, username) if d.get("id") == doc_id), None)
        if doc is None or doc.get("status") != ASKED:
            return "skipped"
        if risk_active or not reads_as_a_reply(reply) or _is_sensitive(reply):
            await asyncio.to_thread(set_status, db, username, doc_id, DROPPED, moment)
            return "dropped"
        if load_facts is None:
            from backend.utils.memory_utils import load_user_facts as load_facts
        candidates = candidate_facts(await asyncio.to_thread(load_facts, username))
        asked_about = next((n for n, c in enumerate(candidates, 1) if c.id == doc.get("parent_fact_id")), None)

        from backend.components.constraints import CLARIFY_ANSWER_PROMPT

        prompt = CLARIFY_ANSWER_PROMPT.format(
            what=doc.get("what", ""), entity=doc.get("entity", ""), kind=doc.get("kind", ""), answer=reply.strip()[:600],
            facts=facts_lines(candidates), asked_about=asked_about if asked_about else "none",
        )
        if invoke is None:
            from backend.models.models import lite_llm

            invoke = lite_llm.ainvoke
        result = validate_answer(parse_json(_text_of(await invoke(prompt))), candidates)
        if result is None:
            await asyncio.to_thread(set_status, db, username, doc_id, DROPPED, moment)
            return "dropped"
        if save_fact is None:
            from backend.utils.memory_utils import save_user_fact as save_fact
        if link_facts is None:
            from backend.utils.memory_utils import link_user_facts as link_facts
        saved = await asyncio.to_thread(
            lambda: save_fact(username, result["fact"], category=CATEGORY_FOR_KIND.get(doc.get("kind"), "preference"), source="explicit",
                              memory_vector_store=memory_vector_store)
        )
        if result["parent_fact_id"] and saved.id != result["parent_fact_id"]:
            await asyncio.to_thread(link_facts, username, saved.id, result["parent_fact_id"])
        await asyncio.to_thread(
            set_status, db, username, doc_id, ANSWERED, moment, child_fact_id=saved.id, parent_fact_id=result["parent_fact_id"],
        )
        from backend.utils.self_counters import record_turn_counts

        await asyncio.to_thread(record_turn_counts, {"clarify_answered": 1})
        return "answered"
    except Exception:
        logger.warning("[clarifications] could not use an answer.", exc_info=True)
        return "skipped"
