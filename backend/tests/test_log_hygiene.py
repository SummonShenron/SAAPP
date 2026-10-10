"""Logs must not carry user content.

SAAPP's logs go to Render's stdout and, through erragent's handler, to errAgent. An admin tool is about to read them, and
logs also outlive conversations. So the rule is mechanical, not a habit: a logger call may interpolate identifiers, counts,
lengths, tool names, flags and system error text, never user text, model text, tool output, documents, memory, or an
emotional description. This test scans every logger call in the backend and fails, naming the line, when one does.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
LEVELS = {"debug", "info", "warning", "error", "critical", "exception"}

# Names that hold user or model content in this codebase. Logging their length is fine (`len(question)`); logging them is not.
CONTENT_NAMES = {
    "question", "original_question", "rewrite_clean", "fact_text", "summary_text", "user_msg", "rejected_draft", "full_response",
    "final_answer", "observation", "state", "node_input", "node_output", "answer_text", "draft", "transcript", "history",
    "emotional_state", "safety_state", "memory_context", "answer", "reply", "response_text", "email_body", "body",
    "original_response", "rejected", "corrected",
}
# Constant dictionary keys that hold content: `verdict["reason"]` is a model-written explanation that quotes the reply.
CONTENT_KEYS = {"reason", "answer", "observation", "fact", "content", "gist", "contact", "text", "question", "body"}
# Attribute names that hold content: `result.fact`, `doc.page_content`, `msg.content`, `payload.reason`.
CONTENT_ATTRS = {"fact", "page_content", "content", "reason", "gist", "contact"}


def _files():
    for path in [REPO / "app.py", *sorted((REPO / "backend").rglob("*.py"))]:
        if "tests" in path.parts or "evals" in path.parts:
            continue
        yield path


def _logger_calls(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in LEVELS
                and isinstance(node.func.value, ast.Name) and node.func.value.id in {"logger", "log"}):
            yield node


# Functions whose result carries no content, so their arguments may be content: a length, a type, or the helpers in
# backend/utils/log_hygiene.py that keep only a safe part (emotion_summary drops the gist; describe_observation keeps a
# tool's own error text and otherwise only a size).
SAFE_WRAPPERS = {"len", "bool", "type", "emotion_summary", "describe_observation", "redact_state"}


def _callee_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


# Keys of the graph state that hold content. `state.get("last_intent")` or `state.get("reasoner_flags")` are scalars and fine;
# `state.get("pending_action")` holds a drafted email, `paused_clarification` holds the user's question.
STATE_CONTENT_KEYS = {
    "pending_action", "paused_clarification", "paused_code_plan", "messages", "documents", "memory_facts", "emotional_state",
    "safety_state", "raw_generation", "content_to_format", "insight_answer", "original_question", "attempts", "voice_payload",
}


def _state_key(node):
    """The constant key of `state.get("k")` / `state["k"]`, or None when the node isn't such an access."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "state" and node.args
            and isinstance(node.args[0], ast.Constant)):
        return node.args[0].value
    if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "state"
            and isinstance(node.slice, ast.Constant)):
        return node.slice.value
    return None


def _content_references(call):
    """Names/attributes in a logger call's arguments that hold content, ignoring those only used inside len(...)."""
    found = []

    def visit(node, inside_len=False):
        if isinstance(node, ast.Call) and _callee_name(node) in SAFE_WRAPPERS:
            for arg in node.args:
                visit(arg, inside_len=True)
            return
        key = _state_key(node)
        if key is not None:
            if key in STATE_CONTENT_KEYS and not inside_len:
                found.append(f"state[{key!r}]")
            return  # a specific, non-content key of the state is fine; don't treat `state` itself as the whole thing
        if (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value in CONTENT_KEYS
                and not inside_len):
            found.append(f"[{node.slice.value!r}]")
        if isinstance(node, ast.Name) and node.id in CONTENT_NAMES and not inside_len:
            found.append(node.id)
        if isinstance(node, ast.Attribute) and node.attr in CONTENT_ATTRS and not inside_len:
            found.append(f".{node.attr}")
        for child in ast.iter_child_nodes(node):
            visit(child, inside_len)

    for arg in call.args[1:] if not isinstance(call.args[0], ast.JoinedStr) else call.args:
        visit(arg)
    if call.args and isinstance(call.args[0], ast.JoinedStr):
        pass  # an f-string's interpolations are its own children: already visited above
    return found


def test_no_logger_call_interpolates_user_or_model_content():
    offenders = []
    for path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _logger_calls(tree):
            if not call.args:
                continue
            refs = _content_references(call)
            if refs:
                offenders.append(f"{path.relative_to(REPO)}:{call.lineno} logs {sorted(set(refs))}")
    assert not offenders, "logger calls that put content in the logs:\n  " + "\n  ".join(offenders)


def test_the_scan_actually_sees_a_violation_when_there_is_one():
    bad = ast.parse('logger.info("saved %s", result.fact)\nlogger.info(f"asked {question}")\nlogger.error("lost %s", state)')
    ok = ast.parse('logger.info("saved %d chars for %s", len(result.fact), username)\nlogger.info("tool %s", tool_name)')
    assert [len(_content_references(c)) for c in _logger_calls(bad)] == [1, 1, 1]
    assert [_content_references(c) for c in _logger_calls(ok)] == [[], []]
