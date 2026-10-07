import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from backend.utils.safety_utils import (
    CRISIS_RESOURCE_LINE, KIND_OUTAGE_FALLBACK, RISK_NONE, detect_risk_language, effective_risk, log_safety_event,
)

logger = logging.getLogger(__name__)

KIND_QUOTA = "quota"
KIND_OVERLOADED = "overloaded"
KIND_OTHER = "other"

# Google answers a spent cap, a spent per-minute/day quota and a billing stop with a 429 / RESOURCE_EXHAUSTED
# (billing and permission stops surface as 403 with "billing"/"quota" in the text).
_QUOTA_RE = re.compile(
    r"\b429\b|RESOURCE_EXHAUSTED|ResourceExhausted|quota|rate.?limit|billing|spending cap|exceeded your current",
    re.IGNORECASE,
)
_OVERLOADED_RE = re.compile(r"\b50[234]\b|UNAVAILABLE|DEADLINE_EXCEEDED|Gateway Timeout|overloaded", re.IGNORECASE)

_QUOTA_REPLY = (
    "I can't answer right now: the AI service I run on has hit its usage limit, so this one is on my end, not "
    "something you did. Nothing you sent was lost. Please try again in a little while."
)
_OVERLOADED_REPLY = (
    "I couldn't get an answer through just now because the AI service I run on is busy or not responding. "
    "Nothing you sent was lost. Please try again in a minute."
)
_OTHER_REPLY = "Something went wrong on my end and I couldn't finish that. Nothing you sent was lost. Please try again."

# Used instead of the plain reply when the person has said something that signals risk. The support ladder
# (safety_utils) needs the model to run, so with the model down the one thing that must still happen is that
# they are pointed at a human, in code, with no dependence on a model call.
_CRISIS_PREFIX = (
    "I'm so sorry: I can't give you a proper answer right now because the service I run on is down, and I know "
    "this is the worst possible moment for that. Please don't wait on me."
)
_CRISIS_SUFFIX = " I'll be here when I'm back, and I'd rather you had a person with you in the meantime."


def classify_llm_failure(exc: Optional[BaseException]) -> str:
    """Whether a model failure is a spent quota/cap, a transient overload, or something else. Decided from the
    exception's type and text only, so it works for any provider's error."""
    if exc is None:
        return KIND_OTHER
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return KIND_OVERLOADED
    text = f"{type(exc).__name__}: {exc}"
    if _QUOTA_RE.search(text):
        return KIND_QUOTA
    if _OVERLOADED_RE.search(text):
        return KIND_OVERLOADED
    return KIND_OTHER


def in_crisis(recent_user_messages: Iterable[Any]) -> bool:
    """Whether any of these recent user messages signals risk. Pattern detection only (no model), since the
    point is to work while the model is unavailable."""
    return any(detect_risk_language(str(m or "")) != RISK_NONE for m in recent_user_messages)


def failure_reply(kind: str, crisis: bool = False) -> str:
    """What the user is shown when a turn fails: plain language, never the raw exception, and in a crisis a
    real human line instead of a bare 'try again'."""
    if crisis:
        return _CRISIS_PREFIX + CRISIS_RESOURCE_LINE + _CRISIS_SUFFIX
    return {KIND_QUOTA: _QUOTA_REPLY, KIND_OVERLOADED: _OVERLOADED_REPLY}.get(kind, _OTHER_REPLY)


@dataclass
class FailedTurn:
    kind: str
    crisis: bool
    reply: str


def handle_failed_turn(
    exc: BaseException,
    username: str,
    recent_user_messages: Iterable[Any],
    safety_state: Optional[dict] = None,
) -> FailedTurn:
    """Everything the chat stream does when a turn dies: classify the failure, decide whether this person had
    signalled risk (from their recent messages by pattern, or the conversation's own safety state if the
    reasoner had already run), tell the operator, and return the reply to show. Never raises."""
    try:
        kind = classify_llm_failure(exc)
        crisis = in_crisis(recent_user_messages) or effective_risk(safety_state, datetime.now(timezone.utc)) != RISK_NONE
        log_model_outage(kind, username, crisis, exc)
        if crisis:
            # So the operator's safety counts include the turns where the model was down and the fixed reply stood in.
            log_safety_event(username, None, kind, KIND_OUTAGE_FALLBACK)
        return FailedTurn(kind, crisis, failure_reply(kind, crisis))
    except Exception:
        logger.exception("handle_failed_turn itself failed; falling back to the plain reply")
        return FailedTurn(KIND_OTHER, False, failure_reply(KIND_OTHER))


def log_model_outage(kind: str, username: str, crisis: bool, exc: BaseException) -> None:
    """Tells the operator (errAgent ingests logger.error with an erragent_context) that users are being turned
    away, which a console budget alert alone does not say. Carries no message text."""
    if kind == KIND_QUOTA:
        headline = "LLM quota or spending cap reached: chat turns are failing for users"
    elif kind == KIND_OVERLOADED:
        headline = "LLM provider unavailable or timing out: chat turns are failing"
    else:
        headline = "Chat turn failed with an unexpected error"
    logger.error(
        "%s (user=%s crisis_turn=%s): %s", headline, username, crisis, type(exc).__name__,
        extra={
            "erragent_context": {
                "kind": kind,
                "crisisTurn": crisis,
                "errorType": type(exc).__name__,
                "environment": os.getenv("ENVIRONMENT", "production"),
            }
        },
    )
