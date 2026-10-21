"""Honest writes: Sonic only saves a detail about a person if the person actually gave it.

THE FAILURE THIS CLOSES (2026-10-20). Sonic had "a date tonight with Ashlyn" but no time. It answered "time for that quick nap before heading downtown by 7:30" (an
invention), and one second later the note writer saved "Jack needs to take a nap before heading downtown by 7:30 PM" as if the person had said it. The note prompt
already says never to record a detail that appears only in the assistant's reply; a prompt is only prose. This is the mechanical check behind it, with no model call.

THE RULE. A note, fact or open loop may only carry a SPECIFIC (a time, a number or amount, a weekday, a month, or a name the assistant introduced) that is in the
person's own words. A sentence with a specific that is not is dropped; with nothing left, nothing is saved. When in doubt, the detail is not kept.

CONFIRMATION. "yep" confirms nothing by itself. Only when the assistant's last message was an explicit question ("7 at Tidos, right?") and the person answers
yes does the detail in THAT question count as theirs (`confirmed_text`).

No text is ever logged or counted here: only how many sentences were dropped and which kinds of detail they carried (time, number, weekday, month, name).
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

_WORD_NUMBERS: Dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "thousand": 1000,
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
# "may" is left out on purpose: it is also a verb, and a month name is only a detail when it is capitalised in a note, which "May" at a sentence start would break.
_MONTHS = ("january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december")

_TIME_COLON_RE = re.compile(r"\b(\d{1,2}):(\d{2})\s*(?:[ap]\.?m\b\.?)?", re.IGNORECASE)
_TIME_AMPM_RE = re.compile(r"\b(\d{1,2})\s*[ap]\.?m\b\.?", re.IGNORECASE)
_THOUSANDS_RE = re.compile(r"(?<![\w.])\$?(\d+(?:\.\d+)?)\s?k\b", re.IGNORECASE)
_NUMBER_RE = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)(?:st|nd|rd|th)?\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z']+")
_CAP_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CONFIRM_RE = re.compile(
    r"^\W*(?:yes|yep|yeah|yea|yup|correct|right|exactly|sure|ok|okay|absolutely|definitely|indeed|that'?s (?:right|correct|it)|sounds (?:right|good)|uh huh|mhm)\b",
    re.IGNORECASE,
)
MAX_CONFIRMATION_WORDS = 8

# Capitalised words that are not names of anything: the person's own pronoun and the product's own words, and what a model starts a note with.
_NOT_A_NAME = frozenset({"the", "this", "that", "they", "their", "user", "sonic", "assistant"}) | frozenset(_WEEKDAYS) | frozenset(_MONTHS)


@dataclass
class Grounded:
    """What came of checking a piece of text: the text to keep (None when nothing is left), how many sentences were dropped, and the KINDS of detail that were not the
    person's (never the details themselves)."""
    text: Optional[str]
    dropped: int = 0
    kinds: List[str] = field(default_factory=list)


def is_confirmation(message: str) -> bool:
    """A short yes ("yep", "yeah that's right"): a reply that could confirm what the assistant just asked. Anything longer is the person saying something of their own."""
    words = (message or "").split()
    return bool(words) and len(words) <= MAX_CONFIRMATION_WORDS and bool(_CONFIRM_RE.match(message or ""))


def confirmed_text(user_text: str, previous_reply: str) -> str:
    """The assistant's last QUESTION, when the person has just said yes to it; otherwise nothing. Only that sentence counts as confirmed, never the whole reply."""
    if not is_confirmation(user_text) or "?" not in (previous_reply or ""):
        return ""
    questions = [s for s in _SENTENCE_SPLIT_RE.split((previous_reply or "").strip()) if "?" in s]
    return questions[-1] if questions else ""


def _times_and_rest(text: str):
    """(times as "H:MM" strings, the text with the time spans taken out so their digits are not counted twice)."""
    times: Set[str] = set()

    def take_colon(match: "re.Match[str]") -> str:
        times.add(f"{int(match.group(1))}:{match.group(2)}")
        return " "

    def take_ampm(match: "re.Match[str]") -> str:
        times.add(f"{int(match.group(1))}:00")
        return " "

    rest = _TIME_COLON_RE.sub(take_colon, text or "")
    rest = _TIME_AMPM_RE.sub(take_ampm, rest)
    return times, rest


def _numbers(text: str, include_words: bool) -> Set[str]:
    found: Set[str] = set()
    for match in _THOUSANDS_RE.finditer(text):
        found.add(str(int(float(match.group(1)) * 1000)))
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        found.add(raw.rstrip("0").rstrip(".") if "." in raw else str(int(raw)))
    if include_words:
        for word in _WORD_RE.findall(text.lower()):
            if word in _WORD_NUMBERS:
                found.add(str(_WORD_NUMBERS[word]))
    return found


def _tokens(text: str) -> Set[str]:
    return {w.lower().strip("'") for w in _WORD_RE.findall(text or "")}


def _names(text: str) -> Set[str]:
    """Capitalised words that are not the first word of a sentence: what a note names (people, places, products)."""
    out: Set[str] = set()
    for sentence in _SENTENCE_SPLIT_RE.split((text or "").strip()):
        for match in _CAP_RE.finditer(sentence):
            if match.start() == 0:
                continue
            token = match.group(0).lower()
            if token not in _NOT_A_NAME:
                out.add(token)
    return out


def _name_in(token: str, words: Set[str]) -> bool:
    """A name is the person's if it is one of their words, or a spelling one letter off or a longer form of one (Ashlyn / Ashlynn)."""
    if token in words:
        return True
    return any(len(w) >= 4 and (w.startswith(token) or token.startswith(w)) and abs(len(w) - len(token)) <= 2 for w in words)


def ungrounded_kinds(candidate: str, *, user_text: str, assistant_text: str = "", confirmed: str = "", known_text: str = "") -> List[str]:
    """The kinds of detail in `candidate` that are not in the person's own words (and not in what they just confirmed): "time", "number", "weekday", "month", "name".
    `assistant_text` (the reply the note was written from) lets a NAME be recognised as coming from the assistant; with none given, names are not checked.
    `known_text` is what Sonic already knows about the person (their saved facts), so a name it already holds is not new."""
    source = f"{user_text or ''}\n{confirmed or ''}"
    source_times, source_rest = _times_and_rest(source)
    source_numbers = _numbers(source_rest, include_words=True)
    for stamp in source_times:                       # the pieces of a time are numbers the person gave too ("7:30" gives 7 and 30)
        hour, minute = stamp.split(":")
        source_numbers.update({str(int(hour)), str(int(minute))})
    source_words = _tokens(source)
    kinds: List[str] = []

    cand_times, cand_rest = _times_and_rest(candidate)
    for stamp in cand_times:
        hour, minute = stamp.split(":")
        # "7:00" is grounded by a "7" the person said; "7:30" needs 7:30 itself.
        grounded = stamp in source_times or (minute == "00" and str(int(hour)) in source_numbers)
        if not grounded and "time" not in kinds:
            kinds.append("time")
    if any(n not in source_numbers for n in _numbers(cand_rest, include_words=False)):
        kinds.append("number")
    cand_words = _tokens(candidate)
    if any(day in cand_words and day not in source_words for day in _WEEKDAYS):
        kinds.append("weekday")
    if any(month in cand_words and month not in source_words for month in _MONTHS):
        kinds.append("month")
    if assistant_text:
        said = _tokens(assistant_text)
        known = source_words | _tokens(known_text)
        if any(name in said and not _name_in(name, known) for name in _names(candidate)):
            kinds.append("name")
    return kinds


def ground_note(note: str, *, user_text: str, assistant_text: str = "", previous_reply: str = "", known_text: str = "") -> Grounded:
    """`note` with every sentence that carries a detail the person did not give taken out. Nothing left means nothing to save (`text` is None)."""
    confirmed = confirmed_text(user_text, previous_reply)
    kept: List[str] = []
    dropped = 0
    kinds: List[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split((note or "").strip()):
        if not sentence.strip():
            continue
        bad = ungrounded_kinds(sentence, user_text=user_text, assistant_text=assistant_text, confirmed=confirmed, known_text=known_text)
        if bad:
            dropped += 1
            kinds.extend(k for k in bad if k not in kinds)
        else:
            kept.append(sentence.strip())
    return Grounded(text=" ".join(kept) if kept else None, dropped=dropped, kinds=kinds)


def ground_fact(fact: str, *, user_text: str, previous_reply: str = "") -> Grounded:
    """A fact the model wrote from the person's message. If it added a detail the message does not hold, the fact is the person's own words instead (clipped), which
    are grounded by definition."""
    checked = ground_note(fact, user_text=user_text, previous_reply=previous_reply)
    if checked.dropped == 0:
        return Grounded(text=fact, kinds=[])
    own = " ".join((user_text or "").split())[:300]
    return Grounded(text=own or None, dropped=checked.dropped, kinds=checked.kinds)
