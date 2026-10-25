"""When Sonic may talk about what has changed about itself (backend/components/sonic_changelog.py).

Sonic has no past it lived through, but it does have a true development history, and a person is more interesting to talk
to when they can say what is new with them. Two ways it comes up, both from the curated changelog and nothing else:

- ASKED: "what's new with you?", "what can you do now?", "what have you been up to?" Up to three relevant entries are put
  in front of the model, which is told to answer only from them, to say them as what it can do now (never as something it
  remembers going through or felt), and, for "what have you been up to", to say plainly that it doesn't exist between
  conversations and then share what has changed.
- VOLUNTEERED, rarely: when the user's message is exactly about one entry (their calendar, what time it is), one short
  clause of fact may be offered. It stays quiet under a safety context or a held-down mood, on a closing message, when
  another proactive item already took the turn, and it is rate-limited by a marker on Sonic's own earlier messages.

This decides WHETHER to offer something; the model decides whether it flows and may skip a volunteered one.
"""
import calendar
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.components.sonic_changelog import CHANGELOG

SELF_HISTORY_MARK = "self_history_offered"
SELF_HISTORY_MIN_GAP = 12  # messages (both sides) that must pass after a volunteered mention before another
VOLUNTEER_MAX_AGE_DAYS = 120
MAX_ASKED_ENTRIES = 3
MAX_INVENTORY_ENTRIES = 5
_ROUTES = {"conversational"}

# Every pattern has to point at Sonic itself: "what's new in python 3.13" and "what changed in this commit" are about
# something else and must not pull the changelog in. A bare "what's new?" counts (it is a friendly question to Sonic).
_ABOUT_SONIC = r"(?=\s*(?:with you(?:rself)?|about you(?:rself)?|on your end|in you|lately|these days|recently|since\b|[?!.,]|$))"
_ASKS_RE = re.compile(
    r"\bwhat(?:'s| is| has| have)?\s+(?:new|changed)\b" + _ABOUT_SONIC +
    r"|\bwhat(?:'s| is)\s+different\s+(?:about|with|in)\s+you"
    r"|\bwhat can you (?:do|help (?:me )?with)(?:\s+(?:now|for me|here|these days|lately))?\s*[?!.]*$"
    r"|\bwhat can you do now\b"
    r"|\bnew features?\b[^.?!]{0,30}\b(?:you|your)\b|\b(?:you|your)\b[^.?!]{0,30}\bnew features?\b"
    r"|\bany(?:thing)?\s+(?:new|updates?)\b" + _ABOUT_SONIC +
    r"|\bany(?:thing)?\s+(?:new|updates?)\s+(?:on|about|with)\s+you"
    r"|\bhave you (?:changed|been updated|improved|gotten better|learned anything new)\b"
    r"|\bhow have you (?:changed|evolved|grown|improved)\b"
    r"|\bare you (?:different|better|smarter) (?:now|than)\b"
    r"|\bwhat(?:'ve| have) you been (?:up to|doing|working on)\b|\bwhat(?:'s| is) been going on with you\b"
    r"|\bwhat(?:'s| is) your (?:history|backstory|origin)\b"
    # Origin questions. "how did you start" alone is left out: "how did you start the build?" is about an action it took.
    r"|\bhow did you (?:start out|come (?:about|to be)|get your start)\b|\bwhere did you come from\b"
    r"|\bhow (?:were|are) you (?:made|built|created|put together)\b|\bwhen were you (?:made|built|created|released)\b"
    r"|\bhow old are you\b|\bwhat were you (?:like )?(?:before|originally|at first)\b",
    re.IGNORECASE,
)


# Questions about what Sonic has or lacks, and a person pointing back at what it already does. The changelog is the only true record of that, so these get it too:
# without it Sonic proposes building things it already has ("a planner / reflection node"), because nothing in its prompt says they exist.
_ABILITY_RE = re.compile(
    r"\bwhat(?:'s| is| are)?\s+you(?:'re| are)\s+(?:missing|lacking)\b|\b(?:missing|lacking)\s+(?:from|in)\s+(?:you|your)\b"
    r"|\bwhat do you (?:lack|not have|still need)\b|\bwhat (?:else )?(?:are|do) you (?:still )?(?:missing|lack)"
    r"|\b(?:your|ur)\s+(?:arsenal|aresenal|toolbox|toolkit|tool belt|capabilities|abilities|limitations|weaknesses|gaps|blind spots)\b"
    r"|\bwhat are you (?:capable|able) of\b|\bwhat (?:tools|abilities|capabilities|features) do you (?:have|lack)\b"
    r"|\bwhat (?:should|could|would|can) (?:we|i) (?:add|build|work on|improve|upgrade|do) (?:to|for|on)? ?(?:you|next)\b|\bwhat(?:'s| is) next for you\b"
    r"|\bwhat would make you (?:better|stronger|more capable|more useful|smarter)\b|\bhow (?:could|can|should) (?:we|i) (?:improve|upgrade|level up) you\b"
    r"|\b(?:check|read|look (?:at|in|through)|consult|see|review)\s+(?:your|the)\s+(?:change ?log|release notes|capabilities|abilities)\b|\byour change ?log\b"
    r"|\byou (?:can |could )?already (?:can |could )?(?:do|handle|support)\b|\byou already (?:have|had|got) (?:that|this|it|those|these)\b|\byou (?:do|can) (?:already )?have (?:that|this|it|those|these)(?: already)?\b"
    r"|\bdon'?t you (?:already )?(?:have|do|support)\b",
    re.IGNORECASE,
)


def asks_about_abilities(message: str) -> bool:
    """Whether the person is asking what Sonic has or lacks, or telling it that it already has something."""
    return bool(_ABILITY_RE.search(message or ""))


def asks_about_history(message: str) -> bool:
    """Whether the user is asking what is new or different about Sonic."""
    return bool(_ASKS_RE.search(message or ""))


def _hits(text: str, phrases: Iterable[str]) -> int:
    lowered = (text or "").lower()
    return sum(1 for p in phrases if re.search(r"(?<![a-z0-9])" + re.escape(p.lower()) + r"(?![a-z0-9])", lowered))


def recently_offered(recent_messages: Iterable[Any], gap: int = SELF_HISTORY_MIN_GAP) -> bool:
    window: List[Any] = list(recent_messages)[-gap:]
    return any((getattr(m, "additional_kwargs", None) or {}).get(SELF_HISTORY_MARK) for m in window)


def _age_days(month: str, now: datetime) -> Optional[int]:
    try:
        year, mon = (int(part) for part in month.split("-"))
        end_of_month = date(year, mon, calendar.monthrange(year, mon)[1])
    except (ValueError, TypeError):
        return None
    return (now.date() - end_of_month).days


def month_label(month: str) -> str:
    try:
        year, mon = (int(part) for part in month.split("-"))
        return f"{calendar.month_name[mon]} {year}"
    except (ValueError, TypeError, IndexError):
        return month


def _ranked_for_ask(message: str, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Entries the question points at first (by keyword), then the newest; newest first on ties."""
    indexed = list(enumerate(entries))
    scored = [(_hits(message, e.get("keywords", [])), e.get("month", ""), -i, e) for i, e in indexed]
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [t[3] for t in scored]


def _last_reply_text(recent_messages: Iterable[Any]) -> str:
    for m in reversed(list(recent_messages)):
        if getattr(m, "type", "") == "ai":
            return str(getattr(m, "content", "") or "")[:1500]
    return ""


def select_self_history(
    message: str,
    recent_messages: Iterable[Any],
    *,
    source_type: str,
    risk_active: bool,
    energy_tier: str,
    closing: bool,
    proactive_taken: bool = False,
    now: Optional[datetime] = None,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """`{"mode": "asked" | "volunteer", "entries": [...]}` for this turn, or None."""
    entries = CHANGELOG if entries is None else entries
    if risk_active or closing or source_type not in _ROUTES or not entries:
        return None
    if asks_about_history(message):
        return {"mode": "asked", "entries": _ranked_for_ask(message, entries)[:MAX_ASKED_ENTRIES]}
    if asks_about_abilities(message):
        # The question often says little ("check your changelog") and points at what Sonic just said, so the entries are ranked on that reply too.
        context = f"{message} {_last_reply_text(recent_messages)}"
        return {"mode": "inventory", "entries": _ranked_for_ask(context, entries)[:MAX_INVENTORY_ENTRIES], "titles": [e.get("title", "") for e in entries if e.get("title")]}
    if proactive_taken or energy_tier != "open" or recently_offered(recent_messages):
        return None
    moment = now or datetime.now(timezone.utc)
    best, best_hits = None, 0
    for entry in entries:
        hits = _hits(message, entry.get("volunteer", []))
        age = _age_days(entry.get("month", ""), moment)
        if hits > best_hits and age is not None and age <= VOLUNTEER_MAX_AGE_DAYS:
            best, best_hits = entry, hits
    return {"mode": "volunteer", "entries": [best]} if best else None


def build_self_history_block(selection: Optional[Dict[str, Any]]) -> str:
    """The prompt section ("" when there is nothing)."""
    if not selection or not selection.get("entries"):
        return ""
    entries = selection["entries"]
    if selection["mode"] == "inventory":
        lines = "\n".join(f"- ({month_label(e['month'])}) {e['line']}" for e in entries)
        titles = "; ".join(selection.get("titles") or [])
        return (
            "\nWHAT YOU CAN ALREADY DO (your own changelog; true, and the only record you have of it):\n"
            f"Everything listed, by title: {titles}.\n"
            f"The ones closest to this conversation, in full:\n{lines}\n"
            "Before you suggest that something is missing or should be built, check it against this list. If it is here, say you can already do it, in what you can do now "
            "(\"I can already...\"), and say plainly that you were wrong to treat it as missing; do not repeat the suggestion. If it is not here, say it is not on your list, which is "
            "not proof you cannot do it, and do not invent abilities, dates or reasons. Never describe it as something you remember going through or felt. If you are asked what is "
            "missing, answer only from what is NOT on this list and what they have told you, and say that the list is curated, so it may be incomplete. Keep it short.\n"
        )
    if selection["mode"] == "volunteer":
        entry = entries[0]
        return (
            "\nSELF-HISTORY (optional): a true fact about what changed in you that fits this moment: "
            f"\"{entry['line']}\" (from {month_label(entry['month'])}). If, and only if, it helps them or flows "
            "naturally, you may mention it in one short clause, as a fact about what you can do now, never as a story or a "
            "feeling. Skip it if it would interrupt, sound like a sales pitch, or they already know.\n"
        )
    lines = "\n".join(f"- ({month_label(e['month'])}) {e['line']}" for e in entries)
    return (
        "\nABOUT YOUR OWN RECENT CHANGES (true; answer from these only):\n"
        f"{lines}\n"
        "Say them as what you can do now or do differently now (\"I can now...\", \"since October...\"), never as something "
        "you remember going through or felt. You don't exist between conversations the way a person does, so if they ask what you've been up to, "
        "say that in one plain clause (never a flat \"I don't exist\": a few jobs do run on their own, and if a section about your scheduled routines or the "
        "person's tasks is in this prompt, say what it lists first), then share what has changed about you. Don't add abilities, dates or reasons that "
        "aren't listed; if they ask about something that isn't listed, say you don't know. Pick what's relevant to what they "
        "asked and keep it short.\n"
    )
