"""A saved plan follows the person when they say it moved or was cancelled.

THE FAILURE (2026-10-30): a saved fact said "a date with Yashu on Friday (2026-10-30)". They said the date was cancelled and moved to Sunday. Open loops (the follow-up store) caught it, but
the saved fact did not change, so that evening Sonic told them the date was "today, Friday" and, corrected, said "I have the facts right here".

THE RULE, cautious because a wrong rewrite is worse than a stale fact:
- A plain-code gate first: the message has to say something moved, was cancelled or called off. Most messages never reach the model.
- Candidates are saved facts that carry a real date near today and are about someone the conversation has been about (backend/utils/conversation_people.py) or share a word with the message.
- One small model call decides, per candidate, update / cancel / keep (default keep). An update may change only the day or time: it is checked in code against the person's own words
  (no new time, number, name or day they did not say), carries no numeric date of the model's making, and gets its real date from date_resolve, not from the model.
- An update is written as the person's own correction (full confidence); a cancel removes the fact. The reply shows one "Noted:" line saying what was changed, so it can be corrected on the spot.
- Never for a message that touches health, loss, money trouble or therapy (open_loops.is_sensitive). The log line holds counts only, never the text.
"""
import asyncio
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from backend.utils import conversation_people, date_resolve, memory_grounding
from backend.utils.name_aliases import _within_one_edit
from backend.utils.time_utils import local_now

logger = logging.getLogger("SASS Logger")

MAX_CANDIDATES = 3
MAX_MESSAGE_CHARS = 600
MAX_RECENT = 4
MAX_RECENT_CHARS = 200
WINDOW_PAST_DAYS = 7           # a plan a week ago is already history; one this far back may still be what they are correcting
WINDOW_AHEAD_DAYS = 60
MIN_SHARED_WORD = 4

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CHANGE_RE = re.compile(
    r"\b(?:cancell?ed|cancell?ing|called (?:it )?off|postponed|pushed (?:it |that )?(?:back|to|till|until|out)|moved (?:it |that )?(?:back|up|out)|moved (?:it |that )?to (?:" + "|".join(_WEEKDAYS) + r"|tomorrow|tonight|next|the \d|\d)|"
    r"got moved|was moved|been moved|reschedul\w+|switched (?:it |that )?(?:to|over to) (?:" + "|".join(_WEEKDAYS) + r"|tomorrow|tonight|next|\d)|fell through|not happening|(?:is|it's|its) now (?:on|for|at))\b",
    re.IGNORECASE,
)
_CANCEL_RE = re.compile(r"\b(?:cancell?ed|called (?:it )?off|fell through|not happening|off the table|no longer happening)\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_DAY_WORDS = _WEEKDAYS + ("today", "tonight", "tomorrow")
_WORD_RE = re.compile(r"[A-Za-z']+")
_CAP_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_FILLER = frozenset({
    "that", "this", "with", "have", "been", "user", "from", "what", "they", "them", "then", "when", "will", "just", "some", "about", "their", "there", "would", "could", "should",
    "were", "your", "like", "into", "over", "also", "cancelled", "canceled", "moved", "pushed", "plan", "plans",
})


def looks_like_change(message: str) -> bool:
    """Whether the message says something moved, was cancelled or called off. Plain code, no model."""
    return bool(_CHANGE_RE.search(message or ""))


def _words(text: str) -> set:
    return {w.casefold() for w in _WORD_RE.findall(text or "")}


def _dated_in_window(text: str, today: date) -> bool:
    for found in _ISO_RE.findall(text or ""):
        try:
            when = date.fromisoformat(found)
        except ValueError:
            continue
        if today - timedelta(days=WINDOW_PAST_DAYS) <= when <= today + timedelta(days=WINDOW_AHEAD_DAYS):
            return True
    return False


def candidate_facts(facts: Sequence[Any], message: str, messages: Sequence[Any], today: date) -> List[Any]:
    """The saved facts the message could be about, newest first: an active fact with a real date near today that names someone the conversation has been about, or shares a word of
    four letters or more with the message. At most MAX_CANDIDATES."""
    people = [name for name, presence in conversation_people.people_in_conversation(messages, facts) if presence >= 0.25]
    message_words = {w for w in _words(message) if len(w) >= MIN_SHARED_WORD and w not in _FILLER}
    chosen = []
    for fact in facts:
        text = getattr(fact, "fact", "") or ""
        if not getattr(fact, "active", True) or not _dated_in_window(text, today):
            continue
        names_person = any(re.search(r"\b" + re.escape(name) + r"\b", text, re.IGNORECASE) for name in people)
        shares_word = bool(message_words & {w for w in _words(text) if len(w) >= MIN_SHARED_WORD and w not in _FILLER})
        if names_person or shares_word:
            chosen.append(fact)
    chosen.sort(key=lambda f: str(getattr(f, "updated_at", "") or ""), reverse=True)
    return chosen[:MAX_CANDIDATES]


def _recent_user_lines(messages: Sequence[Any], current: str) -> str:
    lines = []
    for m in reversed(list(messages or [])):
        content = getattr(m, "content", None)
        if getattr(m, "type", "") == "human" and isinstance(content, str) and content.strip() and content.strip() != current.strip():
            lines.append("- " + " ".join(content.split())[:MAX_RECENT_CHARS])
        if len(lines) >= MAX_RECENT:
            break
    return "\n".join(reversed(lines)) or "(none)"


def build_prompt(message: str, messages: Sequence[Any], facts: Sequence[Any], today: date) -> str:
    from backend.components.constraints import PLAN_CHANGE_PROMPT

    return PLAN_CHANGE_PROMPT.format(
        today=today.isoformat(), weekday=today.strftime("%A"),
        facts="\n".join(f"{f.id}: {f.fact}" for f in facts),
        recent=_recent_user_lines(messages, message), message=" ".join((message or "").split())[:MAX_MESSAGE_CHARS],
    )


def parse(raw: str) -> List[Dict[str, str]]:
    """The model's changes as [{id, action, text}], dropping anything malformed."""
    text = (raw or "").replace("```json", "").replace("```", "").strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return []
    items = parsed.get("changes") if isinstance(parsed, dict) else None
    out = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("action") in ("update", "cancel", "keep"):
            out.append({"id": item["id"], "action": item["action"], "text": str(item.get("text") or "")})
    return out


def _said(word: str, message_words: set) -> bool:
    """A day word is the person's if they wrote it, allowing one typo ("sunay")."""
    return word in message_words or any(len(w) >= 4 and _within_one_edit(word, w) for w in message_words)


def acceptable_update(new_text: str, old_text: str, message: str) -> bool:
    """Whether a rewritten fact only changes the day or time to what the person said. False for anything that adds a detail they did not give."""
    new_text = " ".join((new_text or "").split())
    if not new_text or new_text == old_text or len(new_text) > len(old_text) + 60:
        return False
    day_words = [w for w in _words(new_text) if w in _DAY_WORDS]
    if len(day_words) > 1:
        return False
    message_words = _words(message)
    if any(not _said(w, message_words) for w in day_words):
        return False
    if day_words and _ISO_RE.search(new_text):
        return False                                             # a new day with the old numeric date still beside it is the old date
    if any(found not in _ISO_RE.findall(old_text) for found in _ISO_RE.findall(new_text)):
        return False                                             # a numeric date is only ever the old one or the one code adds
    source = f"{message}\n{old_text}"
    if [k for k in memory_grounding.ungrounded_kinds(new_text, user_text=source) if k != "weekday"]:
        return False                                             # a time, number or month nobody gave
    known = {w.casefold() for w in _CAP_RE.findall(source)} | _words(source)
    if any(name.casefold() not in known and name.casefold() not in _DAY_WORDS for name in _CAP_RE.findall(new_text)):
        return False                                             # a name nobody gave
    return True


async def apply_plan_changes(
    username: str,
    message: str,
    messages: Sequence[Any],
    tz_name: Optional[str],
    *,
    invoke: Callable[[str], Awaitable[Any]],
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Updates or removes the saved plans this message says changed. Returns {"updated": n, "cancelled": m, "unchanged": k, "noted": [lines]}. Never raises: it runs as a background task."""
    result: Dict[str, Any] = {"updated": 0, "cancelled": 0, "unchanged": 0, "noted": []}
    try:
        from backend.utils import memory_utils
        from backend.utils.open_loops import is_sensitive

        if not looks_like_change(message) or is_sensitive(message):
            return result
        moment = now or datetime.now(timezone.utc)
        today = local_now(tz_name, moment).date()
        facts = await asyncio.to_thread(memory_utils.load_user_facts, username)
        candidates = candidate_facts(facts, message, messages, today)
        if not candidates:
            return result
        reply = await invoke(build_prompt(message, messages, candidates, today))
        raw = reply.content if hasattr(reply, "content") else reply
        if isinstance(raw, list):
            raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
        by_id = {f.id: f for f in candidates}
        changed = 0
        for change in parse(str(raw))[:MAX_CANDIDATES]:
            fact = by_id.get(change["id"])
            if fact is None or change["action"] == "keep":
                continue
            if change["action"] == "cancel":
                if _CANCEL_RE.search(message) and await asyncio.to_thread(memory_utils.delete_user_fact, username, fact.id):
                    result["cancelled"] += 1
                    result["noted"].append("Noted: removed a saved plan that you said is off.")
                    changed += 1
                continue
            if not acceptable_update(change["text"], fact.fact, message):
                continue
            new_text = date_resolve.annotate_dates(" ".join(change["text"].split()), tz_name, moment)
            saved = await asyncio.to_thread(memory_utils.edit_user_fact, username, fact.id, new_text)
            if saved is not None:
                result["updated"] += 1
                result["noted"].append(f"Noted: updated what I had saved, now: {saved.fact}")
                changed += 1
        if not changed:
            result["unchanged"] = 1
        counts = {"plan_change_updated": result["updated"], "plan_change_cancelled": result["cancelled"], "plan_change_unchanged": result["unchanged"]}
        if any(counts.values()):
            from backend.utils.self_counters import record_turn_counts

            await asyncio.to_thread(record_turn_counts, counts)
        result["noted"] = result["noted"][:2]
    except Exception:
        logger.warning("[plan changes] could not apply a plan change for this turn.", exc_info=True)
    return result
