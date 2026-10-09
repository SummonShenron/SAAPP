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
        "you remember going through or felt. You don't exist between conversations, so if they ask what you've been up to, "
        "say that briefly and plainly and then share what has changed about you. Don't add abilities, dates or reasons that "
        "aren't listed; if they ask about something that isn't listed, say you don't know. Pick what's relevant to what they "
        "asked and keep it short.\n"
    )
