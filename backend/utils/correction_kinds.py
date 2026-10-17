"""What a stored piece of feedback (a document in the `corrections` collection) IS: a thumbs-up or a thumbs-down, and who gave it.

Feedback comes from two places and has been stored in more than one shape:

- a person's thumbs (the Chat page sends `rating` as "like" or "dislike", and older versions sent no rating at all, only a tag such as
  "positive" or "thumbs_up" for a thumbs-up and one of the dislike reasons for a thumbs-down);
- the answer checker (the reward evaluator), which writes "negative" when it makes Sonic redo an answer.

fetch_relevant_corrections (backend/utils/app_utils.py) turns these into what the model is shown: a thumbs-down becomes a guardrail ("do NOT answer
like this") and a thumbs-up becomes a preferred example. It used to compare the rating with the exact word "positive", so a thumbs-up stored as "like"
or with no rating was shown to the model as something to AVOID. Everything that needs to know which kind a record is (the guardrails, the Sonic
Health page) now asks here, so old records and new ones are read the same way.
"""
from typing import Any, Mapping

POSITIVE = "positive"
NEGATIVE = "negative"

USER = "user"
EVALUATOR = "evaluator"

_POSITIVE_RATINGS = frozenset({"positive", "like", "up", "thumbs_up", "helpful"})
_NEGATIVE_RATINGS = frozenset({"negative", "dislike", "down", "thumbs_down"})
# Records from before the rating was stored: the tag the thumbs-up button used to send.
_POSITIVE_TAGS = frozenset({"positive", "thumbs_up"})


def normalize_rating(rating: Any) -> str:
    """The rating to store for what the page sent: "positive" or "negative". Anything unrecognised is negative, as it always has been."""
    return POSITIVE if str(rating or "").strip().lower() in _POSITIVE_RATINGS else NEGATIVE


def polarity(correction: Mapping[str, Any]) -> str:
    """"positive" (a thumbs-up) or "negative" (a thumbs-down, or the answer checker making Sonic redo an answer) for a stored record."""
    rating = str(correction.get("rating") or "").strip().lower()
    if rating in _POSITIVE_RATINGS:
        return POSITIVE
    if rating in _NEGATIVE_RATINGS:
        return NEGATIVE
    return POSITIVE if str(correction.get("tag") or "").strip().lower() in _POSITIVE_TAGS else NEGATIVE


def source(correction: Mapping[str, Any]) -> str:
    """Who gave it: "user" (a thumb on an answer) or "evaluator" (the answer checker). Records saved before `source` was stored are told apart by the
    rating the two paths wrote: the checker always wrote "negative"; the page wrote "like", "dislike" or nothing."""
    stored = str(correction.get("source") or "").strip().lower()
    if stored in (USER, EVALUATOR):
        return stored
    return EVALUATOR if str(correction.get("rating") or "").strip().lower() == "negative" else USER
