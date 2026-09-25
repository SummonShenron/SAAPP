import json
import re
from datetime import datetime

def format_error_payload(error_message: str) -> str:
    """Serializes an error message into an SSE-compatible JSON format."""
    error_data = {
        "status": "error",
        "message": error_message,
        "timestamp": str(import_datetime().now()) # or your preferred timestamp
    }
    return f"data: {json.dumps(error_data)}\n\n"

# Helper for the timestamp inside the utility
def import_datetime():
    return datetime

def format_final_payload(data: dict) -> str:
    """Serializes data to a server-sent event (SSE) format."""
    import json
    return f"data: {json.dumps(data)}\n\n"

def update_chat_history(history: str, role: str, message: str) -> str:
    """Appends new messages to the history transcript string."""
    entry = f"{role.upper()}: {message}\n"
    return (history or "") + entry


# --- run_react_loop helpers (backend/services/agent_workflow.py's tool_agent_node loop) ---
# New standalone logic for the ReAct loop goes here rather than as another closure inside
# agent_workflow.py — agent_workflow.py stays the graph nodes, plain functions accumulate here,
# working toward eventually splitting them the way app.py/app_utils.py already are.

_DEFINITION_INDEX_BLOCK_RE = re.compile(
    r"Top-level definitions found in it:\n(.*?)\nCall read_repo_file", re.DOTALL
)
_DEFINITION_INDEX_LINE_RE = re.compile(r"^\s*line (\d+): (?:def|class) (\w+)", re.MULTILINE)


def parse_definition_index_from_observation(observation: str) -> dict:
    """A read_repo_file truncation response (agent_workflow._build_definition_index) embeds a
    real, line-numbered table of every top-level def/class in the file — a genuine answer to
    "which line is X on," not a guess. Extracts it into {symbol_name: line_number} so a later
    start_line choice for a named symbol can be checked against that ground truth. Returns {}
    when the observation isn't a truncated-without-start_line read_repo_file result at all."""
    block_match = _DEFINITION_INDEX_BLOCK_RE.search(observation or "")
    if not block_match:
        return {}
    return {
        m.group(2): int(m.group(1))
        for m in _DEFINITION_INDEX_LINE_RE.finditer(block_match.group(1))
    }


def find_mismatched_start_line_note(
    purpose: str, path: str, start_line, line_count, default_window: int, index_for_path: dict
) -> str | None:
    """Real, evidenced gap (docs/coding-agent-roadmap.md, Section 10): a production trace ran
    two extra search tools trying to relocate a symbol it had already read the definition index
    for a few steps earlier, then still guessed the wrong start_line — the two search tools it
    reached for (search_code, trace_symbol) don't even return line numbers, so the guess was
    inevitable once it stopped consulting the index it already had.

    Returns a corrective note when `purpose` names a symbol this same path's own earlier
    definition index already placed at a real line number, but the requested start_line/
    line_count window won't actually reach it. None when there's nothing to flag — including
    when index_for_path is empty (no earlier index seen for this path this turn) or start_line
    wasn't given at all (a fresh, unwindowed read has nothing to compare against yet)."""
    if not index_for_path or not start_line:
        return None
    try:
        start_line = int(start_line)
    except (TypeError, ValueError):
        return None
    try:
        window = int(line_count) if line_count else default_window
    except (TypeError, ValueError):
        window = default_window
    for symbol, real_line in index_for_path.items():
        if symbol in (purpose or "") and not (start_line <= real_line < start_line + window):
            return (
                f"(Note: your purpose mentions '{symbol}', which an earlier read of {path} "
                f"already placed at line {real_line} — this read's window (lines "
                f"{start_line}-{start_line + window - 1}) doesn't reach it. Re-call "
                f"read_repo_file with start_line={real_line} to actually see it, instead of "
                "guessing a location.)"
            )
    return None