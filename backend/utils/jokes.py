"""Jokes: a few approved lines, placed in a reply by code, in a situation code can detect (the registry is backend/components/sonic_jokes.py; why it is built this way is there).

WHEN. Only after a code change was PROPOSED this turn (so it only ever speaks to someone asking for a change), and only when the proposal is one of the named situations (TRIGGERS): "big_change" (BIG_FILES files or more) or "small_change" (one file, a few lines:
the same rule the outcome counters use, code_outcomes.size_bucket). Never to someone who is low, distressed or frustrated, now or still carrying it, or who is asking to be reassured; never under a safety plan; at most once in a conversation; never again for the same person
within COOLDOWN_HOURS (kept in memory, so a restart forgets it: the conversation rule is what holds); never the line used last. Humor that does not land is worse than none, so every one of these is a reason to say nothing.

HOW. The line is added as a paragraph of its own at the END of the reply, before any <<<FOLLOW_UP>>> tag, word for word (`place`). The reply stamp records which joke it was (backend/utils/reply_stamp.py), so the Sonic Health feedback page can show thumbs for replies that carried a joke
and a joke that lands badly shows up. A joke can be removed in the "Its voice" tab, and JOKES_ENABLED=off turns them all off.

NOTHING PERSONAL. Only the proposal's file paths' COUNT and size are read, never a path or a line; what is recorded is a count per joke.
"""
import logging
import os
import random
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.components import sonic_jokes as registry
from backend.utils import code_outcomes, emotion_utils, identity_checks, voice_review
from backend.utils.situation_facts import BLOCKED_TERMS

logger = logging.getLogger("SASS Logger")

JOKE_MARK = "joke_told"                       # on a reply that carried a joke: one per conversation reads it
TRIGGERS = ("big_change", "small_change")
BIG_FILES = 3                                 # "a multi-file change": this many files or more
COOLDOWN_HOURS = float(os.getenv("JOKES_COOLDOWN_HOURS", "6"))
_last_told: Dict[str, datetime] = {}
_FOLLOW_UP_TAG_RE = re.compile(r"\s*<<<FOLLOW_UP:.*?>>>\s*$", re.DOTALL)


def enabled() -> bool:
    return os.getenv("JOKES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def live(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The jokes that may be used: only the ones a person approved. A draft is never used. Pure."""
    return [e for e in (registry.JOKES if entries is None else entries) if e.get("approved") is True]


def changes_of(files: Any) -> Dict[str, Tuple[int, int]]:
    """{path: (lines added, lines removed)} from a proposal's files (the event's `files`: dicts with `path`, `additions`, `deletions`). Anything else is read as nothing. Pure."""
    out: Dict[str, Tuple[int, int]] = {}
    for item in files or []:
        if isinstance(item, dict) and item.get("path"):
            try:
                out[str(item["path"])] = (max(int(item.get("additions") or 0), 0), max(int(item.get("deletions") or 0), 0))
            except (TypeError, ValueError):
                out[str(item["path"])] = (0, 0)
    return out


def trigger_of(files: Any) -> Optional[str]:
    """The situation this proposal is, or None: "big_change" for BIG_FILES files or more, "small_change" for one file and a few lines, nothing between (two files, or one big one, is neither a joke nor the simulations). Pure."""
    changes = changes_of(files)
    if len(changes) >= BIG_FILES:
        return "big_change"
    if changes and code_outcomes.size_bucket(changes) == "small":
        return "small_change"
    return None


def mood_ok(state: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> bool:
    """False for someone low, distressed or frustrated (this message, or a feeling still being carried) and for a message asking to be reassured; True otherwise, including no mood at all. Pure."""
    state = state or {}
    fresh = bool(state.get("read_this_turn"))
    if fresh and state.get("need") == "reassurance":
        return False
    if str(state.get("valence", "neutral")) not in emotion_utils.NEGATIVE_VALENCES:
        return True
    try:
        carried = emotion_utils.effective_intensity(state, now or datetime.now(timezone.utc)) >= emotion_utils.EMOTION_INJECTION_THRESHOLD
    except Exception:
        carried = False
    return not (fresh or carried)


def told_in_conversation(messages: Any) -> bool:
    """Whether an earlier reply in this conversation carried a joke. Pure."""
    return any((getattr(m, "additional_kwargs", None) or {}).get(JOKE_MARK) for m in list(messages or []))


def _said_before(line: str, messages: Any) -> bool:
    return any(line in str(getattr(m, "content", "")) for m in list(messages or []))


def cooling_down(user: str, now: Optional[datetime] = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    last = _last_told.get(user or "")
    return bool(last and moment - last < timedelta(hours=COOLDOWN_HOURS))


def note_told(user: str, now: Optional[datetime] = None) -> None:
    _last_told[user or ""] = now or datetime.now(timezone.utc)


def forget_cooldowns() -> None:
    _last_told.clear()


def pick(
    files: Any,
    state: Optional[Dict[str, Any]],
    messages: Any,
    *,
    user: str = "",
    risk_active: bool = False,
    db: Any = None,
    now: Optional[datetime] = None,
    rng: Optional[random.Random] = None,
    entries: Optional[List[Dict[str, Any]]] = None,
) -> Optional[Dict[str, str]]:
    """{"id", "line"} for the joke to tell after this proposal, or None. Every gate is a reason to say nothing. Never raises. Blocking only for a database read of the dashboard's approvals."""
    try:
        if not enabled() or risk_active:
            return None
        trigger = trigger_of(files)
        if trigger is None or not mood_ok(state, now) or told_in_conversation(messages) or cooling_down(user, now):
            return None
        decided = voice_review.overrides(db)
        approved = [e for e in live(voice_review.apply(registry.JOKES if entries is None else entries, "joke", decided)) if e.get("trigger") == trigger]
        if not approved:
            return None
        chooser = rng or random
        entry = chooser.choice(approved)
        fresh = [line for line in entry.get("lines") or [] if not _said_before(line, messages)]            # never the line used last
        lines = fresh or list(entry.get("lines") or [])
        return {"id": entry["id"], "line": chooser.choice(lines)} if lines else None
    except Exception:
        logger.warning("[jokes] could not choose a joke for this turn.", exc_info=True)
        return None


def place(reply: str, line: str) -> str:
    """The reply with the line added as a paragraph of its own at the end, before any <<<FOLLOW_UP>>> tag, word for word. Unchanged when there is no line, no reply, or the line is already in it. Pure."""
    text = reply or ""
    line = (line or "").strip()
    if not line or not text.strip() or line in text:
        return text
    tag = _FOLLOW_UP_TAG_RE.search(text)
    body, tail = (text[: tag.start()], text[tag.start():].lstrip()) if tag else (text, "")
    told = f"{body.rstrip()}\n\n{line}"
    return f"{told}\n\n{tail}" if tail else told


def tally_turn(counts: Dict[str, int], joke: Optional[Dict[str, str]], tally: Callable[..., None]) -> None:
    """Adds this turn's counters with the counters' own `tally`: one for the turn and one for the joke. Counts only."""
    if not joke:
        return
    tally(counts, "jokes_placed")
    tally(counts, "joke_used", joke["id"])


# ---- what the registry may contain -------------------------------------------------------------------------------------------------------------------------------------------------------------
_ID_RE = re.compile(r"^[a-z][a-z_]{2,60}$")          # letters and underscores only: the id is part of a counter name, and counters refuse digits


def check_registry(entries: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the jokes, in words ([] when they are sound). Checks drafts too. Each: a unique id of letters and underscores; a trigger from TRIGGERS; a situation within the limit; one to MAX_LINES lines, each at most MAX_LINE_CHARS, with no
    em dash or list, no blocked term, and passing the identity check (no claimed feeling or stake); and an `approved` flag. Pure."""
    problems: List[str] = []
    seen = set()
    for entry in registry.JOKES if entries is None else entries:
        entry_id = str(entry.get("id", ""))
        if not _ID_RE.match(entry_id) or entry_id in seen:
            problems.append(f"{entry_id or '(no id)'}: the id must be unique, letters and underscores only")
        seen.add(entry_id)
        if entry.get("trigger") not in TRIGGERS:
            problems.append(f"{entry_id}: the trigger must be one of {', '.join(TRIGGERS)}")
        situation = str(entry.get("situation", "")).strip()
        if not situation or len(situation) > 200:
            problems.append(f"{entry_id}: a situation of at most 200 characters is needed")
        lines = entry.get("lines") or []
        if not 1 <= len(lines) <= registry.MAX_LINES:
            problems.append(f"{entry_id}: one to {registry.MAX_LINES} lines are needed")
        for line in lines:
            line = str(line).strip()
            if not line or len(line) > registry.MAX_LINE_CHARS:
                problems.append(f"{entry_id}: each line must be at most {registry.MAX_LINE_CHARS} characters")
            if "—" in line or re.search(r"^\s*(?:[-*]|\d+\.)\s", line):
                problems.append(f"{entry_id}: a line uses an em dash or a list")
            if identity_checks.identity_reply_issue(line):
                problems.append(f"{entry_id}: a line fails the identity check ({identity_checks.identity_reply_issue(line)})")
            for term in BLOCKED_TERMS:
                if term.lower() in line.lower():
                    problems.append(f"{entry_id}: a line carries '{term}', which belongs to a real conversation or to the product")
        if not isinstance(entry.get("approved"), bool):
            problems.append(f"{entry_id}: approved must be true or false")
    return problems
