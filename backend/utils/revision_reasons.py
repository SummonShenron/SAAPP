"""Why a reply was rewritten, as one word from a closed list, so the rewrite counts say WHAT was taken out (self_counters group `revised_reason`).

The reply-fit check rewrites a reply that crosses a line (app.py: safety, emotional, identity, encouragement, closing). Until 2026-11-22 only the kind was counted: 85 identity and 79 emotional
rewrites in 30 days, with no way to tell whether they were removing invented feelings (right) or Sonic's own legitimate substance (a problem). The identity check alone has about forty
descriptions; they are grouped here by what they are about. Counts only: no reply text, no description text, is ever stored.
"""
from typing import Optional

# The emotional check's own tags (backend/utils/emotion_checks.py), kept as they are.
EMOTIONAL_TAGS = ("probing_question", "two_questions", "template_phrase", "thin_comfort", "hedged", "generalizing", "restating", "venting_advice", "celebration_no_question",
                  "repeated_presence", "repeated_opening", "repeated_permission", "recycled_point", "against_their_goal", "stacked_validation")
# What an identity rewrite was about.
IDENTITY_REASONS = (
    "feeling",            # claims a feeling, love, joy, loneliness, a reaction, a human state
    "attachment",         # about the person: misses them, cares, glad they came back, waiting or thinking about them, they are special to it
    "invented_past",      # a memory, a story or experience from its own past, "I've been there", played or watched something
    "human_life",         # a routine, a practice or a daily-life detail of its own
    "concern_no_signal",  # concern for the person with no emotional signal on the table
    "stake",              # a stake in its own numbers, a reward or penalty
    "talk_length",        # a feeling or milestone about how long they have talked
    "shelf",              # an invented read or a verdict on a reading-shelf piece (reading_shelf.shelf_reply_issue)
    "event_time",         # named a time for an event it was never told the time of (event_fields.reply_issue)
    "status",             # said all is well while the status on record shows a problem (self_report.reply_issue)
    "routine_time",       # a scheduled job said to run at a different time than listed (routines.reply_issue)
    "identity_other",
)
OTHER_KINDS = ("safety", "encouragement", "closing", "other")
REASONS = EMOTIONAL_TAGS + IDENTITY_REASONS + OTHER_KINDS
# Which identity-check source raised the issue: the identity check itself, or one of the others that share the identity rewrite.
SOURCES = {"shelf": "shelf", "event": "event_time", "status": "status", "routine": "routine_time"}

# Checked in order: the first group with a word in the description wins (so "claims a feeling about how long they talked" is talk_length, not feeling).
_IDENTITY_WORDS = (
    ("talk_length", ("how long they have talked", "how long they talked")),
    ("concern_no_signal", ("no emotional signal",)),
    ("stake", ("stake in its own", "reward or penalty")),
    ("human_life", ("routine of its own", "practice of its own", "daily-life detail")),
    ("invented_past", ("remember", "reminded", "story from its past", "past experience", "memory of its own", "been through", "played or watched", "years of experience",
                       "know how it feels", "favorite work", "as if they had been there")),
    ("attachment", ("missed them", "special to it", "matters to it", "means a lot", "cares about", "glad they came back", "come back", "return", "looks forward", "waiting",
                    "thinking about them", "wondering about them", "worries about them", "devotion", "commitment", "treasure", "attachment", "asks for company",
                    "not to leave", "to stay")),
    ("feeling", ("feeling", "love or enjoy", "satisfaction", "joy", "loneliness", "human state", "reaction", "dislike")),
)


def identity_reason(description: Optional[str], source: str = "identity") -> str:
    """The IDENTITY_REASONS word for an identity rewrite: by its source when another check raised it, else by the words of the identity check's description. Pure."""
    if source in SOURCES:
        return SOURCES[source]
    text = (description or "").lower()
    for reason, words in _IDENTITY_WORDS:
        if any(word in text for word in words):
            return reason
    return "identity_other"


def reason_for(tag: Optional[str], identity_description: Optional[str] = None, identity_source: str = "identity") -> str:
    """The REASONS word for a rewrite with this revision tag (app.py's revision_tag). Pure."""
    if tag in EMOTIONAL_TAGS:
        return tag
    if tag == "identity":
        return identity_reason(identity_description, identity_source)
    if tag == "closing_engagement":
        return "closing"
    if tag in ("safety", "encouragement"):
        return tag
    return "other"
