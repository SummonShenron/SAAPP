"""Corrections the person made in this conversation, carried into every later reply so Sonic never says the old version again.

THE FAILURE (Jack's chat review, 2026-11-23). He wrote "I was the one that asked to talk on the phone, not her". Sonic apologized and took it back, and three replies later said again that she had
asked him "whether you wanted to talk". The reply only sees a window of the conversation and its own earlier replies, and those still held the wrong version.

THE RULE. A message that corrects something (at its start: "I was the one...", "no, that's wrong", "she didn't actually...", "you got that wrong") is kept, in the person's own words, and the
last MAX_CARRIED of them from the last WINDOW_MESSAGES messages go into the reply prompt as facts that override anything said earlier, Sonic's own replies included. Found by a pattern on
the messages already in memory: no model call, nothing stored, nothing logged.

Tuned on the owner's real thread (2026-11-23): the pattern found 10 corrections in about 5,450 messages; the one false match came from a phrase deep inside a long message, so only the start
of a message (the first MATCH_WITHIN characters) is read.
"""
import re
from typing import Any, Iterable, List

WINDOW_MESSAGES = 60
MAX_CARRIED = 4
MATCH_WITHIN = 40
MAX_CHARS = 220
CORRECTION_RE = re.compile(
    r"\b(?:i was the one|it was me(?: not| who)|that'?s not (?:right|true|what (?:i|she|he|they) (?:said|meant))|that'?s wrong|you got (?:that|it) wrong|you'?re wrong"
    r"|no,? (?:she|he|they|i) (?:didn'?t|did|was|wasn'?t|said)|(?:she|he|they) didn'?t actually|i didn'?t say|i never said|not her,? (?:me|i)|you misread|you mixed (?:that|it) up"
    r"|that was (?:me|her|him) not)\b",
    re.IGNORECASE,
)


def is_correction(text: str) -> bool:
    """True when the message corrects something near its start. Pure."""
    head = " ".join((text or "").split())[: MATCH_WITHIN + 40]
    match = CORRECTION_RE.search(head)
    return bool(match) and match.start() <= MATCH_WITHIN


def recent_corrections(messages: Iterable[Any]) -> List[str]:
    """The person's corrections in the last WINDOW_MESSAGES messages, oldest first, at most MAX_CARRIED, each cut to MAX_CHARS. Pure."""
    window = list(messages or [])[-WINDOW_MESSAGES:]
    found = []
    for message in window:
        if getattr(message, "type", "") != "human" or not isinstance(getattr(message, "content", None), str):
            continue
        if is_correction(message.content):
            text = " ".join(message.content.split())
            found.append(text if len(text) <= MAX_CHARS else text[:MAX_CHARS].rstrip() + "…")
    return found[-MAX_CARRIED:]


def build_block(corrections: List[str]) -> str:
    """The prompt section, or "" when there is nothing to carry. Pure."""
    if not corrections:
        return ""
    lines = "\n".join(f'- "{text}"' for text in corrections)
    return (
        "\nCORRECTIONS THEY MADE EARLIER IN THIS CONVERSATION (their own words; true; they override anything said before, your own earlier replies included):\n"
        f"{lines}\n"
        "Never state the old, corrected version again. If one of your earlier replies said it, that reply was wrong.\n"
    )
