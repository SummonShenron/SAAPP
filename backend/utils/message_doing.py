"""What a message is DOING, beside the mood it carries: joking through something, winding down, asking a question, or telling news.

WHY (2026-11-27). The mood read gives a valence and a need for the message, and every low message then got the same shape of reply: how they feel first, at length. But "lol of course she
cancelled again", "ok I'm going to bed", "did I do the right thing?" and "she finally texted back" are four different acts in the same mood, and a friend answers each differently.

WHAT. `kind(message)` reads the person's own words, by fixed patterns, and returns one of KINDS or None; the first that applies wins, in the order joking, winding_down, asking, news.
`build_block(kind)` is one line for the reply prompt saying what to do FIRST. It never overrides the emotional guidance (which follows it in the prompt) or a safety plan (the caller leaves
it out there), and it says nothing about how long a reply to someone low should be, except for someone who is leaving for the night.

Pure, no model call, nothing logged.
"""
import re
from typing import Optional

KINDS = ("joking", "winding_down", "asking", "news")
WINDING_DOWN_MAX_WORDS = 30

_JOKING_RE = re.compile(r"\b(?:lol|lmao|lmfao|rofl|haha+|hehe+|jk|just kidding|kidding)\b|😂|🤣|😅|💀", re.IGNORECASE)
_WINDING_DOWN_RE = re.compile(
    r"\b(?:going|heading|off|headed) to (?:bed|sleep)\b|\bgonna (?:sleep|crash|head to bed|go to bed|try (?:to|and) sleep)\b|\bneed to (?:sleep|get some sleep|go to bed)\b"
    r"|\bcalling it (?:a night|for tonight)\b|\bi should (?:go|sleep|get to bed)\b|\b(?:gotta|got to|have to) (?:go|run|sleep)\b|\btry(?:ing)? to (?:get some )?sleep\b",
    re.IGNORECASE,
)
_NEWS_TIME_RE = re.compile(r"\b(?:just|finally|today|tonight|this (?:morning|afternoon|evening)|earlier|yesterday|last night|a (?:minute|second|moment|while) ago)\b", re.IGNORECASE)
_NEWS_EVENT_RE = re.compile(
    r"\b(?:got|heard|texted|called|said|told|went|came|sent|replied|emailed|messaged|happened|found out|finished|passed|failed|quit|started|signed|booked|cancell?ed|showed up|"
    r"asked|offered|rejected|accepted|broke|fixed|moved|met|saw)\b",
    re.IGNORECASE,
)

_BLOCKS = {
    "joking": "they are joking through it. Meet the joke first, in one light clause of your own, before anything heavier; do not explain it or turn it into a concern.",
    "winding_down": "they are winding down for the night. Keep this one to a few sentences, ask nothing and open nothing new: let them go.",
    "asking": "they asked you something. Answer it in your first sentence, before anything about how they feel.",
    "news": "they are telling you something that happened. Respond to the news itself first, specifically: what happened and what it changes. Not a summary of it, and not a check on their feelings.",
}


def kind(message: Optional[str]) -> Optional[str]:
    """One of KINDS for what the message is doing, or None when none of the patterns applies. Pure."""
    text = str(message or "").strip()
    if not text:
        return None
    if _JOKING_RE.search(text):
        return "joking"
    if "?" not in text and len(text.split()) <= WINDING_DOWN_MAX_WORDS and _WINDING_DOWN_RE.search(text):
        return "winding_down"
    if "?" in text:
        return "asking"
    if _NEWS_TIME_RE.search(text) and _NEWS_EVENT_RE.search(text):
        return "news"
    return None


def build_block(found: Optional[str]) -> str:
    """The prompt line for what the message is doing ("" when nothing was read). Pure."""
    if found not in _BLOCKS:
        return ""
    return f"\nWHAT THIS MESSAGE IS DOING (read from their own words; do this first, and the guidance below still holds): {_BLOCKS[found]}\n"
