"""A timeline of something ongoing, for a question about its history ("what interview steps have I had with Casey's?", "where do things stand with the apartment?").

THE FAILURE (2026-11-23). Asked about the Casey's interview process, Sonic forgot almost every step. Memory held 34 facts and 54 notes that mention Casey's, but the reply only ever gets the 5 facts and 3
notes closest in meaning, and those were the vaguest ("Jack is following the progress of the interview with Casey"). The real steps (an interview on Oct 27, a coding exam, a four-round onsite on
Nov 16) were each in a separate note, never shown together.

WHAT THIS DOES. On a question that asks for history or a list, and names something memory mentions at least MIN_ITEMS times (the rarest such word in the question: "caseys" over "interview"),
every fact and note that names it is gathered, near-duplicates dropped, the specific ones (a date, a number, a step word) kept first, at most MAX_ITEMS, and shown OLDEST FIRST with the date it was
told. The reply is told to answer from it in order, to say when a step is not in it, and never to invent a date. No model call: words matched in the stored text. Notes flagged stale are left out.

A possessive name is matched as such ("caseys" or "Casey's" in the question matches "Casey's" and "Caseys", not the person "Casey"), which also keeps out the facts that mixed the company up
with a person.
"""
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MIN_ITEMS = 3
MAX_ITEMS = 12
MAX_TERM_ITEMS = 80            # a word in more than this many items says nothing about which matter (work, today, project)
MAX_ITEM_CHARS = 220
SIMILAR = 0.7                  # word overlap at or above which two items say the same thing

HISTORY_RE = re.compile(
    r"\b(?:what|which) (?:steps?|stages?|rounds?|interviews?|happened|have i (?:had|done|told you)|did i (?:have|do|tell you))\b|\bhow many\b|\ball (?:the|of the|my)\b|\bevery\b"
    r"|\bso far\b|\bup to now\b|\buntil now\b|\btimeline\b|\bhistory\b|\brecap\b|\bcatch me up\b|\brun ?down\b|\bwhere (?:am i|are we|do i stand|do things stand|things stand)\b"
    r"|\bremind me\b|\bwalk me through\b|\bwhat(?:'s| has) happened\b|\bsteps? (?:i'?ve|ive|i have)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’]{2,}")
_STOP = frozenset("""
what which have had done does with that this they them their there from about been were will would could should tell told remind steps step stages stage rounds round
many every much more most some much just really still since until timeline history recap where things stand far now all the and for you your youve ive i'm im my me
how far walk through happened happen catch run down so up any know remember did doing going been being when
""".split())
_STEP_RE = re.compile(r"\b(?:round|onsite|on-site|exam|test|screen|interview(?:ed)?|call|offer|reject\w*|scheduled|completed|finished|missed|resched\w*|applied|started|ended|signed|moved)\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")


def asks_for_history(message: str) -> bool:
    """True when the message asks for a history, a list or where things stand. Pure."""
    return bool(HISTORY_RE.search(message or ""))


def term_pattern(word: str) -> "re.Pattern":
    """The pattern for a word from the question: a possessive form ("caseys", "Casey's") matches only the possessive in stored text; a plain word matches itself and its possessive. Pure."""
    base = word.lower().replace("’", "'")
    if base.endswith("'s"):
        stem = base[:-2]
        return re.compile(r"\b" + re.escape(stem) + r"(?:'|’)?s\b", re.IGNORECASE)
    if base.endswith("s") and len(base) > 4:
        stem = base[:-1]
        return re.compile(r"\b" + re.escape(stem) + r"(?:(?:'|’)s|s)\b", re.IGNORECASE)
    return re.compile(r"\b" + re.escape(base) + r"(?:(?:'|’)s)?\b", re.IGNORECASE)


def candidate_words(message: str) -> List[str]:
    """The words of the question that could name a matter: four letters or more, not on the stop list, in order, no repeats. Pure."""
    seen, words = set(), []
    for word in _WORD_RE.findall(message or ""):
        key = word.lower().replace("’", "'")
        if len(key.strip("'")) < 4 or key.strip("'") in _STOP or key in seen:
            continue
        seen.add(key)
        words.append(word)
    return words


def _items(facts: Sequence[Any], notes: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    items = []
    for fact in facts or []:
        text = str(getattr(fact, "fact", "") or (fact.get("fact") if isinstance(fact, dict) else "") or "")
        active = getattr(fact, "active", True) if not isinstance(fact, dict) else fact.get("active", True)
        if text and active:
            items.append({"text": text, "at": str(getattr(fact, "created_at", "") or (fact.get("created_at") if isinstance(fact, dict) else "") or "")})
    for note in notes or []:
        if note.get("text") and not note.get("stale"):
            items.append({"text": str(note["text"]), "at": str(note.get("created_at") or "")})
    return items


MAX_TERMS = 3


def choose_terms(message: str, items: Sequence[Dict[str, str]]) -> List[Tuple[str, "re.Pattern"]]:
    """The (at most MAX_TERMS) rarest words of the question that each name at least MIN_ITEMS items and at most MAX_TERM_ITEMS: the matter being asked about. Several, because one question can name
    a matter twice ("the Casey's AI Center of Excellence": "center" alone found 3 items and left the onsite out). Pure."""
    scored = []
    for word in candidate_words(message):
        pattern = term_pattern(word)
        count = sum(1 for item in items if pattern.search(item["text"]))
        if MIN_ITEMS <= count <= MAX_TERM_ITEMS:
            scored.append((count, word, pattern))
    scored.sort(key=lambda entry: entry[0])
    return [(word, pattern) for _count, word, pattern in scored[:MAX_TERMS]]


def _words(text: str) -> set:
    return {w.lower() for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 3}


def _specific(text: str) -> bool:
    return bool(_DIGIT_RE.search(text) or _STEP_RE.search(text))


def build(message: str, facts: Sequence[Any], notes: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"term", "lines": [(date, text)]} for a history question about a matter memory knows, oldest first, or None. Pure."""
    if not asks_for_history(message):
        return None
    items = _items(facts, notes)
    chosen = choose_terms(message, items)
    if not chosen:
        return None
    word = " and ".join(w for w, _p in chosen)
    matched = [item for item in items if any(p.search(item["text"]) for _w, p in chosen)]
    # Which ones to keep: those naming more of the question's words first (Casey's AND interview before interview alone: with only recency, generic interview notes crowded out the October
    # Casey's steps), then specific before vague, then newest first (stable sorts, last key applied first). Anything that says what a kept item says is dropped.
    matched.sort(key=lambda item: item["at"], reverse=True)
    matched.sort(key=lambda item: 0 if _specific(item["text"]) else 1)
    matched.sort(key=lambda item: -sum(1 for _w, p in chosen if p.search(item["text"])))
    kept: List[Dict[str, str]] = []
    for item in matched:
        words = _words(item["text"])
        if any(words and len(words & _words(k["text"])) / max(len(words | _words(k["text"])), 1) >= SIMILAR for k in kept):
            continue
        kept.append(item)
        if len(kept) >= MAX_ITEMS:
            break
    if len(kept) < MIN_ITEMS:
        return None
    kept.sort(key=lambda item: item["at"])
    lines = []
    for item in kept:
        text = " ".join(item["text"].split())
        lines.append((item["at"][:10], text if len(text) <= MAX_ITEM_CHARS else text[:MAX_ITEM_CHARS].rstrip() + "…"))
    return {"term": word, "lines": lines}


MAX_NOTES_READ = 400


def fetch_notes(db: Any, username: str, message: str) -> List[Dict[str, Any]]:
    """Their notes that contain any candidate word of the question (text, date and the stale flag only, never the embeddings), at most MAX_NOTES_READ. Blocking. [] without a database or on a
    failure. Never raises."""
    words = [re.escape(w.lower().replace("’", "'").rstrip("'s").rstrip("s") or w.lower()) for w in candidate_words(message)]
    if db is None or not username or not words:
        return []
    try:
        query = {"username": username, "text": {"$regex": r"\b(?:" + "|".join(words) + ")", "$options": "i"}}
        return list(db["user_memory_chunks"].find(query, {"_id": 0, "text": 1, "created_at": 1, "stale": 1}).limit(MAX_NOTES_READ))
    except Exception:
        return []


def _day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%b %d").replace(" 0", " ")
    except ValueError:
        return "date unknown"


def build_block(timeline: Optional[Dict[str, Any]]) -> str:
    """The reply prompt's section, or "". Pure."""
    if not timeline:
        return ""
    lines = "\n".join(f"- {_day(at)}: {text}" for at, text in timeline["lines"])
    return (
        f"\nTIMELINE: WHAT THEY HAVE TOLD YOU ABOUT {timeline['term'].upper()}, OLDEST FIRST (from your memory; each date is when they told you, not always when it happened; an early line "
        "may since have changed):\n" + lines + "\n"
        "They asked about the history or where things stand. Answer from this list, in order, with the steps and dates it gives. If a step they mention is not here, say you don't have it "
        "rather than guessing, and never invent a date or a step.\n"
    )
