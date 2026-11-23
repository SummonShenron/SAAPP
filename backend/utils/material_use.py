"""Whether a finished reply really used a piece of material it was given (one of Sonic's track-record observations, a take, a line from its record), so "offered" and "used" can be
counted apart. Measured 2026-11-22: observations went into 1,232 prompts in 30 days with no way to tell whether a single reply used one.

The test is the reading shelf's (reading_shelf.reply_used_item), made a little stricter because these lines are short and share common words with any reply: the reply must share at least
MIN_SHARED distinctive words (MIN_WORD letters or more, not on the stop list) with ONE line, or a number of two digits or more from that line and at least one distinctive word. A paraphrase
that keeps no word is missed, so this undercounts; it never counts a reply that shares nothing. Pure, no logging.
"""
import re
from typing import Iterable

MIN_SHARED = 3
MIN_WORD = 4
_WORD_RE = re.compile(r"[a-z][a-z'-]+")
_NUMBER_RE = re.compile(r"\b\d[\d,.%]*\d\b|\b\d{2,}\b")
_STOP = frozenset("""
about above after again against also always because been before being below between both could does doing down during each every from further have having here
into itself just like made make more most much must never only other over same should some such than that their them then there these they this those through
under until very want what when where which while will with would your yours you're it's i'm i've don't didn't doesn't can't won't isn't aren't wasn't weren't
really things thing something someone anything everything nothing time times week weeks days today tell said says know think thought feel felt good well even still
""".split())


def _words(text: str) -> set:
    return {w.strip("'-") for w in _WORD_RE.findall((text or "").lower()) if len(w.strip("'-")) >= MIN_WORD and w.strip("'-") not in _STOP}


def used_line(reply: str, line: str) -> bool:
    """True when the reply shares MIN_SHARED distinctive words with the line, or one of its numbers and a distinctive word. Pure."""
    reply_words = _words(reply)
    shared = _words(line) & reply_words
    if len(shared) >= MIN_SHARED:
        return True
    numbers = set(_NUMBER_RE.findall(line or ""))
    return bool(shared) and any(number in (reply or "") for number in numbers)


def used_any(reply: str, lines: Iterable[str]) -> bool:
    """True when the reply used at least one of the lines. Pure."""
    return any(used_line(reply, line) for line in lines or [] if isinstance(line, str) and line.strip())
