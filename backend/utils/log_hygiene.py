"""Keeps user content out of logs and out of what is forwarded to errAgent.

Two leaks existed before this module:

1. Message strings that interpolated content: a saved memory fact, a memory summary, a rewritten question, a model reply, a
   tool observation, an emotional "gist". Those lines were fixed at the source, and backend/tests/test_log_hygiene.py now
   scans every logger call so a new one can't reintroduce it.
2. `logger.info("node executed", extra={"erragent_context": {"input": node_input, "output": node_output}})`. Every graph node
   attaches its WHOLE input and output state (the conversation, retrieved documents, memory facts), and erragent's handler
   forwards every INFO-and-above record to errAgent. So the full text of every turn left the process on every turn.

`StateRedactionFilter` is the single choke point for the second: installed on the logger, it rewrites the `input` and `output`
of any `erragent_context` into a summary (types, counts, lengths, sources, scalar flags) before any handler sees the record.
New nodes are covered automatically. A key it doesn't know is summarized, never passed through, so "safe" is the default.

To capture full state deliberately (local debugging), set ERRAGENT_CAPTURE_STATE=true. It is off unless asked for.
"""
import logging
import os
from typing import Any, Dict

CAPTURE_ENV = "ERRAGENT_CAPTURE_STATE"

# State keys whose scalar values are identifiers, routes or flags, not content. Kept as they are when short.
_KEEP_SCALARS = {
    "workflowName", "requestId", "session_id", "username", "relevance_grade", "rag_mode", "deep_thinking", "loop_count",
    "last_intent", "coordinator_intent", "write_action", "repo", "force_web_search", "source_type", "needs_retrieval",
    "intent", "grade", "step", "status", "tag", "level",
}
_MAX_KEPT_STR = 80


def capture_full_state() -> bool:
    return os.getenv(CAPTURE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _is_message(value: Any) -> bool:
    return hasattr(value, "content") and hasattr(value, "type")


def _is_document(value: Any) -> bool:
    return hasattr(value, "page_content") and hasattr(value, "metadata")


def _summarize_list(items: list) -> Dict[str, Any]:
    if items and all(_is_message(i) for i in items):
        return {
            "messages": len(items),
            "roles": [str(getattr(i, "type", "?")) for i in items][-12:],
            "chars": sum(len(str(getattr(i, "content", "") or "")) for i in items),
        }
    if items and all(_is_document(i) for i in items):
        sources = [str((getattr(i, "metadata", None) or {}).get("source", "?")) for i in items]
        return {"documents": len(items), "sources": sources[:10]}
    return {"list": len(items)}


def summarize_value(key: str, value: Any, depth: int = 0) -> Any:
    """A content-free description of one state value."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key in _KEEP_SCALARS and len(value) <= _MAX_KEPT_STR:
            return value
        return {"str": len(value)}
    if isinstance(value, (list, tuple)):
        return _summarize_list(list(value))
    if isinstance(value, dict):
        if value and all(isinstance(v, (bool, int, float)) or v is None for v in value.values()):
            return dict(value)  # a dict of flags or counts (reasoner_flags) carries no text
        if depth >= 1:
            return {"dict": len(value)}
        return {k: summarize_value(str(k), v, depth + 1) for k, v in value.items()}
    return {"type": type(value).__name__}


def redact_state(state: Any) -> Any:
    """A summary of a node's input or output state: shape and flags, never content."""
    if isinstance(state, dict):
        return {str(k): summarize_value(str(k), v) for k, v in state.items()}
    return summarize_value("state", state)


def describe_observation(text: Any) -> str:
    """What to log about a tool's result: the error text when the tool itself reported one (it starts with "ERROR" and is
    system-written, which is exactly what debugging needs), otherwise only how big it was. A successful result is the
    user's calendar, mail, documents or code, and doesn't belong in a log."""
    text = text if isinstance(text, str) else str(text or "")
    return text[:200] if text.startswith("ERROR") else f"{len(text)} chars"


def emotion_summary(state: Any) -> Dict[str, Any]:
    """The part of an emotional-state reading that is safe to log: how strong and which kind, never the gist (a short
    description of what the person is going through) or the contact (someone in their life)."""
    if not isinstance(state, dict):
        return {}
    intensity = state.get("intensity")
    return {
        "valence": state.get("valence"),
        "intensity": round(intensity, 2) if isinstance(intensity, (int, float)) else None,
        "need": state.get("need"),
        "risk": state.get("risk"),
    }


class StateRedactionFilter(logging.Filter):
    """Summarizes the `input` / `output` of any `erragent_context` before a record reaches a handler. Never raises, and never
    drops the record: if redaction itself fails, the context is replaced by a marker, never passed through."""

    def filter(self, record: logging.LogRecord) -> bool:
        if capture_full_state():
            return True
        context = getattr(record, "erragent_context", None)
        if not isinstance(context, dict):
            return True
        try:
            cleaned = dict(context)
            for key in ("input", "output"):
                if key in cleaned:
                    cleaned[key] = redact_state(cleaned[key])
            record.erragent_context = cleaned
        except Exception:
            record.erragent_context = {"redaction": "failed"}
        return True
