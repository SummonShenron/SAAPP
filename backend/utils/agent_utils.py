import ast
import difflib
import json
import re
from datetime import datetime

from langchain_core.callbacks.manager import adispatch_custom_event


async def safe_emit_event(name: str, data: dict):
    """Safely emit a custom event, ignoring errors if called outside an active run context."""
    try:
        await adispatch_custom_event(name, data)
    except RuntimeError:
        # Safely ignored when called from fallback utilities or standalone scripts
        pass


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


def resolve_recent_mention(messages: list, extractor, skip_predicate=None):
    """Scans messages most-recent-first, applying `extractor` to each message's content and
    returning the first (i.e. most recent) truthy result. `skip_predicate`, if given, skips
    a message's content entirely without trying to extract from it (e.g. bare approval
    replies like "yes"/"ok" that never carry the real topic). Returns None if nothing in
    history matches — callers apply their own final fallback."""
    for m in reversed(messages or []):
        content = getattr(m, "content", "") if hasattr(m, "content") else (m.get("content", "") if isinstance(m, dict) else str(m))
        if skip_predicate and skip_predicate(content):
            continue
        result = extractor(content)
        if result:
            return result
    return None


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


# --- read_repo_file's truncation/paging knobs (moved from agent_workflow.py) ---
# A flat character-count cap on a large file (this repo has several 2000+ line files) silently
# cuts off before ever reaching a function defined further down — a real fabrication risk, since
# a model can mistake "I was given the start of the file" for "I read the file" and fill the gap
# it never saw with something plausible instead of real code. start_line lets a caller jump
# straight to a specific line once it knows where to look (from _build_definition_index below, or
# from search_code), the same way a normal editor would.
_READ_FILE_CHAR_CAP = 3500
_READ_FILE_DEFAULT_LINE_WINDOW = 150
# Always show at least this much raw content even when a large file's definition index needs
# most of the observation's budget (docs/coding-agent-roadmap.md, Section 12) — the index takes
# priority, but the snippet never shrinks to nothing.
_READ_FILE_MIN_SNIPPET_CHARS = 500
_DEFINITION_INDEX_WRAPPER = (
    "\n\n... [truncated — this file has {line_count} lines total, too long to show in full. "
    "Top-level definitions found in it:\n{index}\nCall read_repo_file again with start_line set "
    "to the one you actually need — do not assume the file's contents past this point from "
    "general knowledge of what a file like this usually contains.]"
)
_TOP_LEVEL_DEF_RE = re.compile(r'^(?:async\s+)?(def|class)\s+(\w+)')


def _build_definition_index(lines: list) -> str:
    """A lightweight table of contents for a large file: every top-level (module-level, not
    nested inside a class/function) def/class and the line it starts on. Lets the model jump
    straight to the real function it needs via start_line instead of guessing or reading from
    the top of a multi-thousand-line file and hoping the truncated slice happens to reach it."""
    entries = [
        f"  line {i}: {m.group(1)} {m.group(2)}"
        for i, line in enumerate(lines, start=1)
        if (m := _TOP_LEVEL_DEF_RE.match(line))
    ]
    return "\n".join(entries[:150])


# --- run_react_loop's own bookkeeping helpers (moved from agent_workflow.py) ---

def _parse_agent_json(raw_text: str) -> dict:
    """Defensive JSON extraction shared by every ReAct-loop tool: tries direct parsing,
    then a regex-located JSON object, then gives up and returns {} (the loop treats a
    decision with no recognizable "action" as a failed step, not a crash)."""
    clean_text = raw_text.strip()
    clean_text = re.sub(r"^```(?:json|python)?\s*", "", clean_text, flags=re.IGNORECASE)
    clean_text = re.sub(r"\s*```$", "", clean_text)

    try:
        parsed = json.loads(clean_text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        json_match = re.search(r"(\{.*\})", clean_text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass
    return {}


_EMPTY_OBSERVATION_VALUES = {
    "", "[]", "{}", "none", "null", "no results", "no results found",
    "no matches.", "no matches", "no diff context available.", "no similar file paths found.",
}


def _is_empty_observation(observation: str) -> bool:
    """An empty result (no matches, an empty list, an empty file listing) is not the same as
    "nothing exists" — it's very often a sign the query, path, repo, or collection was wrong,
    not proof of absence (this is exactly what happened with the repo-misresolution bug
    earlier: a wrong repo name didn't error, it just came back empty). Treated the same way
    an outright "ERROR: ..." is by the retry-nudge tracking in run_react_loop, since both are
    "this step didn't actually get you anywhere" — the model just can't tell that from the
    text alone without this check."""
    return observation.strip().lower() in _EMPTY_OBSERVATION_VALUES


def _mentions_unresolved_truncation(observation: str) -> bool:
    """A real production trace showed the model treat a truncated read_repo_file result as if
    it were the whole file: it read agent_workflow.py once (truncated at _READ_FILE_CHAR_CAP,
    nowhere near run_react_loop's actual body), never re-called with start_line despite the
    truncation note explicitly saying to, and confidently proposed a fully fabricated
    reimplementation instead — with 3 full steps of budget still unused, so this wasn't even
    budget pressure. The truncation note already tells it not to guess; this makes that
    mechanical instead of relying on it to comply. Reuses the exact same marker text _read_file
    emits (both no-start_line truncation variants share "truncated — this file has"), so a
    follow-up read_repo_file call with a genuinely different start_line — a different
    args_signature — clears it via the same unretried_inconclusive_tools machinery an ERROR or
    empty result already does, no new tracking dict needed.

    A second real trace immediately exposed a gap in this same fix: forced to retry, the model
    correctly called read_repo_file WITH a start_line — but that's a THIRD, different message
    shape ("N more lines below — re-call with a higher start_line"), not the first two, so it
    wasn't covered and the model declared "final" from lines 400-549 of a 4552-line file with
    the real code (line ~2350) still unread. Same fix, same reasoning, just the missing variant."""
    return "truncated — this file has" in observation or "more lines below" in observation


_MAX_OBSERVATION_CHARS = 4000


def _truncate_observation(observation) -> str:
    """Every other action in this loop self-limits its own output (_read_file caps at 3500
    chars, _list_tree caps at 400 paths) — an unbounded PyMongo query was the one path with no
    cap at all, and a raw find() over a collection that stores embedding vectors is easily
    hundreds of documents with long float arrays each. That observation gets JSON-dumped
    straight into both the next reasoning prompt (_format_react_attempts) and the final
    footer (_format_observation_for_footer), so one huge result set is enough to blow past
    Gemini's 1,048,576-token input limit on the final Voice Composer call. Truncating once,
    right where every tool's observation is captured, protects all of them generically instead
    of special-casing Mongo."""
    text = observation if isinstance(observation, str) else json.dumps(observation, default=str, indent=2)
    if len(text) <= _MAX_OBSERVATION_CHARS:
        return text
    return text[:_MAX_OBSERVATION_CHARS] + f"\n... [truncated — {len(text)} total characters]"


class _UnsafeActionRequested(Exception):
    """Raised by run_react_loop when is_unsafe() flags a step, so the calling node can
    build its own tool-specific approval-required response instead of the loop guessing."""
    def __init__(self, decision: dict):
        self.decision = decision


class _ClarificationNeeded(Exception):
    """Raised by run_react_loop when the model reports genuine uncertainty (action="clarify")
    instead of guessing — carries the question to ask and the attempts made so far, so the
    calling node can pause for a real answer and resume the loop from where it left off
    instead of starting over."""
    def __init__(self, question: str, attempts: list):
        self.question = question
        self.attempts = attempts


# Catches the exact shape of a real production failure: a confident, detailed "final" answer
# denying a capability that was sitting in that same turn's own action menu the whole time
# (browser_navigate — see docs/coding-agent-roadmap.md, "tool_agent_node had zero conversation
# history"). A prose rule already tells the model to check its own menu before denying a
# capability; this is the mechanical backstop for when it doesn't, checking the actual "final"
# TEXT against the actual menu rather than trusting the model caught its own contradiction. Only
# the denial-shaped phrase is generic/repo-agnostic here — WHICH capabilities to watch for is
# domain knowledge the caller (tool_agent_node) supplies via capability_denial_watchlist, the
# same config-driven shape as stuck_action_redirects.
_CAPABILITY_DENIAL_RE = re.compile(
    r"\b(i (?:don'?t|do not) (?:actually |currently )?have\b|i can'?t\b|i'?m not able to\b|"
    r"i lack\b|no access to\b|i (?:don'?t|do not) have access\b)",
    re.IGNORECASE,
)


# A different failure shape from the same production trace as the capability-denial backstop
# above (docs/coding-agent-roadmap.md, Section 7): three self-drive attempts in a row each
# fabricated a different kind of ground truth (invented code, a false "this doesn't exist" claim,
# and — the one this catches — a confidently-presented diff editing a data structure that was
# never actually verified to exist). Before a "final" containing a diff against an EXISTING file
# is accepted, at least one of that diff's own claimed pre-existing lines (context or removed,
# never a `+` line) must actually appear in a real read_repo_file observation for that same path
# recorded THIS turn — otherwise the diff was composed from a plausible guess, not derived from
# what was actually fetched. String/regex based, not a real diff parser — same accepted
# soft-failure-mode tradeoff already used by trace_symbol/find_file in this file.
_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/\S+", re.MULTILINE)
_DIFF_NEW_FILE_RE = re.compile(r"^new file mode")


def _extract_diff_file_grounding_lines(final_answer: str) -> dict:
    """Maps each existing-file path named in a `diff --git` block inside final_answer to the
    non-added lines (context or removed) inside its hunks — the lines the diff claims already
    existed in that file before this change. A brand-new file (a `new file mode` line before the
    next file header) is skipped entirely, since there's nothing pre-existing to verify."""
    files: dict = {}
    current_path = None
    skip_current = False
    for line in final_answer.splitlines():
        header_match = _DIFF_FILE_HEADER_RE.match(line)
        if header_match:
            current_path = header_match.group(1)
            skip_current = False
            files.setdefault(current_path, [])
            continue
        if current_path is None:
            continue
        if _DIFF_NEW_FILE_RE.match(line):
            skip_current = True
            files.pop(current_path, None)
            continue
        if skip_current or line.startswith(("+++", "---", "index ", "@@")) or line.startswith("+"):
            continue
        if line.startswith("-"):
            files[current_path].append(line[1:].strip())
        elif line.startswith(" "):
            files[current_path].append(line[1:].strip())
    return {path: [l for l in lines if l] for path, lines in files.items()}


def _final_diff_disagrees_with_fetched_content(final_answer: str, attempts: list) -> str | None:
    """Returns the file path of the first diff hunk whose claimed pre-existing lines never
    actually appeared in a real read_repo_file result for that path this turn — a strong signal
    the diff was composed from a guess instead of derived from real fetched content. None if every
    diffed file either has real corroborating evidence or the answer contains no diff at all."""
    for path, grounding_lines in _extract_diff_file_grounding_lines(final_answer).items():
        if not grounding_lines:
            continue
        fetched_text = "\n".join(
            a["observation"] for a in attempts
            if a.get("action_desc", "").startswith("read_repo_file(") and f"path={path}" in a["action_desc"]
        )
        if not fetched_text or not any(line in fetched_text for line in grounding_lines):
            return path
    return None


def _format_observation_for_footer(observation) -> str:
    if isinstance(observation, str):
        return observation
    return json.dumps(observation, default=str, indent=2)


def _format_react_attempts(attempts: list) -> str:
    if not attempts:
        return "(none yet — this is the first step)"
    return "\n\n".join(
        f"Attempt {i} — Purpose: {a['purpose']}\nAction: {a['action_desc']}\nObservation: {a['observation']}"
        for i, a in enumerate(attempts, 1)
    )


def _format_attempts_steps(attempts: list) -> str:
    """Renders attempts as 'Step N — purpose / Result' blocks for display — used both in a
    completed answer's footer and in a clarification pause message. Purely a display renderer:
    resuming a paused clarification reads attempts back from real checkpointed state
    (state["paused_clarification"]), not by re-parsing this rendered text."""
    return "\n\n".join(
        f"**Step {i} — {a['purpose']}:**\n```\n{a['action_desc']}\n```\n"
        f"**Result:**\n```\n{_format_observation_for_footer(a['observation'])}\n```"
        for i, a in enumerate(attempts, 1)
    )


# --- find_file's fuzzy matching + trace_symbol's write/read classification (moved) ---

_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,5}$")

# find_file's fuzzy matching, so a colloquial name ("navbar") can still surface a differently
# named real file (menu-navigator.tsx) even though they share no exact token — search_code only
# matches GitHub's literal keyword index, which finds nothing at all in that case. Splits both on
# non-alphanumeric characters AND camelCase boundaries so "MenuNavigator" and "menu-navigator"
# tokenize to the same {"menu", "navigator"} regardless of naming convention.
_PATH_TOKEN_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_FUZZY_MATCH_CUTOFF = 0.5
_FUZZY_MATCH_LIMIT = 15


def _tokenize_for_fuzzy_match(text: str) -> set:
    tokens = set()
    for fragment in _PATH_TOKEN_SPLIT_RE.split(text):
        if not fragment:
            continue
        for sub in _CAMEL_BOUNDARY_RE.split(fragment):
            if sub:
                tokens.add(sub.lower())
    return tokens


def _fuzzy_path_score(query_tokens: set, path: str) -> float:
    """Best similarity between any query token and any token in `path` (folder names and
    filename, extension stripped) — an exact token match short-circuits to 1.0, otherwise
    falls back to difflib's character-level ratio so near-misses (navbar/navigator, singular
    vs. plural) still score usefully instead of an all-or-nothing exact match."""
    path_tokens = _tokenize_for_fuzzy_match(_FILE_EXTENSION_RE.sub("", path))
    if not path_tokens:
        return 0.0
    best = 0.0
    for query_token in query_tokens:
        for path_token in path_tokens:
            if query_token == path_token:
                return 1.0
            ratio = difflib.SequenceMatcher(None, query_token, path_token).ratio()
            if ratio > best:
                best = ratio
    return best


# trace_symbol's write-vs-read classification — regex-based (not a real parser) by design, to
# stay consistent with find_file's own heuristic rather than pull in a per-language AST
# dependency for one tool. Covers both this repo's Python and TypeScript/React conventions:
# a plain assignment, an attribute/dict-style assignment, a def/class that IS the symbol, a
# React state setter call (setSymbol(...)), a useState/useReducer/useRef/useMemo destructuring
# that defines the symbol, and a function returning it — matching exactly the "write/decide
# site" categories described in docs/coding-agent-roadmap.md. Everything else that mentions the
# symbol (a read, a prop being consumed, a log line, a re-export) falls through to "read".
def _classify_symbol_line(symbol: str, line: str) -> str:
    stripped = line.strip()
    escaped = re.escape(symbol)

    if re.match(rf'^(export\s+)?(async\s+)?(def|function|class)\s+{escaped}\b', stripped):
        return "write"

    setter_name = f"set{symbol[0].upper()}{symbol[1:]}" if symbol else ""
    if setter_name and re.search(rf'\b{re.escape(setter_name)}\s*\(', stripped):
        return "write"

    if re.search(rf'\b{escaped}\b[^=]*=\s*use(State|Reducer|Ref|Memo|Context)\s*\(', stripped):
        return "write"

    if re.search(rf'(?<![=!<>]){escaped}\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'\.{escaped}\s*=(?!=)', stripped) or re.search(rf'\[["\']{escaped}["\']\]\s*=(?!=)', stripped):
        return "write"

    if re.search(rf'^return\b.*\b{escaped}\b', stripped):
        return "write"

    return "read"


# --- task-shape detectors (moved) ---

# A real production trace showed a static system-prompt paragraph telling the model to reach
# for browser_navigate on "a real page's current, real content or appearance" wasn't enough —
# asked to "inspect the actual page" to fix a z-index conflict, it did 15 steps of pure repo
# reading and never opened a browser once (see docs/coding-agent-roadmap.md, "Failure B").
# Rather than trust a general system-prompt rule to out-compete many turns of code-reading
# momentum, this detects the specific category of question (layout/rendering/visual state that
# literally cannot be confirmed from source alone) and injects a directive into THIS turn's own
# question text — much harder to deprioritize than a rule buried among many others.
_VISUAL_INSPECTION_RE = re.compile(
    r"\b(inspect|actual page|actual site|how (?:it|this|that) (?:looks|renders|appears)|"
    r"z-?index|stacking|overlap(?:ping)?|visually|on[- ]?screen|in the browser|"
    r"css (?:issue|bug|problem)|layout (?:issue|bug|problem)|rendering (?:issue|bug|problem))\b",
    re.IGNORECASE,
)


def _mentions_visual_inspection(text: str) -> bool:
    return bool(_VISUAL_INSPECTION_RE.search(text or ""))


# A real production trace showed the default step budget forced a premature, fabricated answer
# on a "scan the repo and plan this refactor" ask: it needs one search plus a confirmatory
# trace_symbol per candidate call site to actually be exhaustive, which the flat 7-step (or even
# 14-step deep) budget doesn't leave room for — so it pattern-completed the rest instead of
# admitting it ran out of steps. This detects that task shape from the user's own wording and
# grants it the same higher budget deep_thinking gets, regardless of whether deep_thinking is on.
_AUDIT_TASK_RE = re.compile(
    r"\b(scan the (?:whole |entire )?repo|every (?:file|place|call ?site|usage|occurrence)s?|"
    r"across the (?:whole |entire )?(?:repo|codebase)|cross-cutting|\baudit\b|"
    r"(?:refactor|migration|architecture) plan|which files (?:do we|need to|touch|are)|"
    r"how many files|plan (?:the|this|a) (?:refactor|migration|architecture))\b",
    re.IGNORECASE,
)


def _is_audit_style_task(text: str) -> bool:
    return bool(_AUDIT_TASK_RE.search(text or ""))


# search_literal exists specifically because search_code rides GitHub's hosted search index
# (capped at ~20 results, subject to indexing lag) and can silently miss real matches — but it's
# opt-in, so an audit-style task ("find every place X is used") can still reach for search_code
# out of habit and come back with a plausible-looking but incomplete answer (docs/coding-agent-
# roadmap.md, Section 4c). Prose guidance for this already existed in TOOL_AGENT_PROMPT and wasn't
# enough on its own — same lesson this whole file keeps re-learning — so this rides the same
# mechanical injection already proven for the architecture map below instead of adding a new one.
_AUDIT_TASK_SEARCH_NUDGE = (
    "AUDIT TASK DETECTED: prefer search_literal over search_code for exhaustive results this turn "
    "— search_code rides GitHub's hosted search index (capped, subject to indexing lag) and can "
    "miss real matches; search_literal greps the actual repo tree directly.\n"
)


# --- architecture map (moved) ---

# An auto-injected, repo-wide internal-import graph for audit-style tasks (docs/coding-agent-
# roadmap.md, Section 4c). search_literal/trace_symbol find where a SYMBOL is referenced; this
# shows which FILES depend on which other files, which is what actually answers "what else does
# this touch" for a cross-file refactor — the exact gap that produced the fabricated per-user-
# token plan in Section 4b. Injected straight into the prompt (like `schema` already is) rather
# than offered as a new opt-in tool_action, because this project has now independently shown
# three times (Sections 0, 0b, 4b) that a capability the model must remember to reach for gets
# skipped under pressure.
_ARCH_MAP_MAX_FILES_SCANNED = 200
_ARCH_MAP_MAX_FILE_BYTES = 200_000
_ARCH_MAP_MAX_ENTRIES = 150
_ARCH_MAP_PY_EXTENSION = ".py"
_ARCH_MAP_JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx")

_JS_IMPORT_RE = re.compile(
    r"""(?:^\s*import\s+['"]([^'"]+)['"])|(?:\bfrom\s+['"]([^'"]+)['"])|(?:\brequire\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)


def _extract_python_imports(content: str) -> list[str]:
    # Best-effort, same spirit as _classify_symbol_line's regex heuristic — a file with a real
    # (rare) syntax error just contributes no edges to the map instead of failing the whole thing.
    # Relative imports are kept as their literal dotted form (e.g. ".utils") rather than resolved
    # to a file path — the model can trivially map that back to a real path itself, and it avoids
    # building a second, error-prone module-resolution layer for comparatively little benefit.
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return []
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            dots = "." * (node.level or 0)
            if node.module:
                imports.append(f"{dots}{node.module}")
            else:
                # `from . import utils` — module is None, the actual reference (a sibling
                # module) lives in the imported names instead.
                imports.extend(f"{dots}{alias.name}" for alias in node.names)
    return imports


def _extract_js_imports(content: str) -> list[str]:
    # Regex, not a real parser — matches this codebase's own precedent (_classify_symbol_line)
    # for TS/JS specifically. Covers `import x from '...'`, bare `import '...'`, and
    # `require('...')`; does not cover dynamic `import(variable)` or path-aliased imports
    # (e.g. tsconfig `@/`) — an acceptable, documented gap rather than a bundler-grade resolver.
    imports = []
    for match in _JS_IMPORT_RE.finditer(content):
        imports.append(next(g for g in match.groups() if g))
    return imports


def _is_internal_python_import(import_str: str, top_level_segments: set) -> bool:
    if import_str.startswith("."):
        return True
    return import_str.split(".")[0] in top_level_segments


def _repo_top_level_segments(tree_items: list) -> set:
    # Derived from the real tree every time (never hardcoded) so this works identically on any
    # repo, not just this one — matches this session's own multi-repo/multi-user design goal.
    segments = set()
    for item in tree_items:
        path = item.get("path", "")
        parts = path.split("/")
        segments.add(parts[0])
        if len(parts) == 1 and parts[0].endswith(_ARCH_MAP_PY_EXTENSION):
            segments.add(parts[0][: -len(_ARCH_MAP_PY_EXTENSION)])
    return segments


def _build_architecture_map(tree_items: list, fetch_content) -> str:
    """Builds a compact FILE -> internal imports (and its inverse) map from real file content —
    fetch_content(path) must return (content, error) like _fetch_file_content does. Only internal
    (own-repo) imports are kept; a bare package import (os, react, requests) would otherwise
    dominate the reverse map with useless high fan-in and drown out the edges that actually matter
    for scoping a refactor's blast radius."""
    top_level_segments = _repo_top_level_segments(tree_items)
    candidates = [
        item for item in tree_items
        if item.get("path", "").endswith((_ARCH_MAP_PY_EXTENSION,) + _ARCH_MAP_JS_EXTENSIONS)
        and item.get("size", 0) <= _ARCH_MAP_MAX_FILE_BYTES
    ][:_ARCH_MAP_MAX_FILES_SCANNED]

    imports_by_file: dict = {}
    imported_by: dict = {}
    for item in candidates:
        path = item.get("path", "")
        content, error = fetch_content(path)
        if error or not isinstance(content, str):
            continue
        if path.endswith(_ARCH_MAP_PY_EXTENSION):
            internal = [i for i in _extract_python_imports(content) if _is_internal_python_import(i, top_level_segments)]
        else:
            internal = [i for i in _extract_js_imports(content) if i.startswith(".")]
        if not internal:
            continue
        imports_by_file[path] = sorted(set(internal))
        for imp in internal:
            imported_by.setdefault(imp, set()).add(path)

    if not imports_by_file:
        return ""

    forward_lines = [
        f"  {path} -> {', '.join(imports_by_file[path])}"
        for path in sorted(imports_by_file)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    reverse_lines = [
        f"  {imp} <- imported by: {', '.join(sorted(imported_by[imp]))}"
        for imp in sorted(imported_by)[:_ARCH_MAP_MAX_ENTRIES]
    ]
    return (
        "PRE-COMPUTED ARCHITECTURE MAP (real internal-import graph — exhaustive for the files "
        "scanned, not a guess; use this to find every file a change would touch instead of "
        "relying on search_code alone):\n"
        "FILE -> ITS INTERNAL IMPORTS:\n" + "\n".join(forward_lines) +
        "\n\nINTERNAL MODULE -> IMPORTED BY:\n" + "\n".join(reverse_lines)
    )