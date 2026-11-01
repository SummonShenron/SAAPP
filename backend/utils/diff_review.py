"""A second pass over a change that was just applied, before Sonic says it is done.

Jack, 2026-11-01: when Claude Code finishes a change it reads the diff and checks it against the real thing, and Sonic ends with its own tests. Both of today's mistakes (a test built from an invented
tool result, and a backend change with nothing on the frontend to receive it) passed Sonic's own checks. This is that second reading, kept cheap: Jack's constraint is that even one cent a review is too
much, so it is ONE small Haiku call (about a tenth of a cent) that only looks for the project's own rules (backend/utils/lesson_proposals.py, .sonic/AGENTS.md), plus free checks in code.

WHEN. After an applied edit that is not a step of a plan with more steps to go, for a change that is more than a couple of lines and is not only prose. Never on a turn that applied nothing.
WHAT IT SAYS. Only problems the diff itself shows, at most three, each marked blocking or not; "I can't tell from the diff" is silence. A BLOCKING finding sends Sonic back to fix it and gives the turn steps
back (backend/utils/combo.py: the first review that blocks earns the full refund, the second half, and there are at most MAX_ROUNDS of them), so it can act on the finding instead of running out of steps.
A review that finds nothing blocking ends there. A call that fails is no review at all: no refund, no message.
WHAT IT SENDS. The request and the diff cards (the same text the person is shown on the card), capped, to the same Haiku the coding loop already uses. Nothing is logged but counts.
"""
import json
import os
import re
from typing import Any, Dict, List, Optional

ENABLED = os.getenv("DIFF_REVIEW_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
MAX_ROUNDS = int(os.getenv("DIFF_REVIEW_MAX_ROUNDS", "2"))
MAX_DIFF_CHARS = int(os.getenv("DIFF_REVIEW_MAX_CHARS", "12000"))
MAX_REQUEST_CHARS = 1500
MAX_FINDINGS = 3
MIN_CHANGED_LINES = 3
_PROSE_SUFFIXES = (".md", ".txt", ".rst")
_TEST_PATH = re.compile(r"(?:^|/)(?:tests?/|test_[^/]*$)|\.test\.[a-z]+$", re.IGNORECASE)
_FIXED_DATE = re.compile(r"""datetime\(\s*20\d\d\s*,|new Date\(\s*['"]20\d\d-|\bdate\(\s*20\d\d\s*,""")

REVIEW_PROMPT = (
    "You are a second reader checking a code change that was just applied, against what the person asked. You see only the request and the diff, not the rest of the repository.\n"
    "Report ONLY problems that would make the change wrong, incomplete or failing in real use, and only when the diff itself shows it. Check exactly these:\n"
    "1. A test whose input data is invented rather than the shape the real code returns (a made-up result, or a different format from what the code it tests produces).\n"
    "2. A field, kind or value that now crosses a boundary (backend to frontend, server to runner) with no change in the diff to what receives it.\n"
    "3. Counting or parsing that treats every line alike (all non-blank lines counted as items, or a \"nothing found\" message counted as a result).\n"
    "4. A test that hard-codes today's date or time when the code under test also reads the clock.\n"
    "5. Something the request asked for that the diff does not do.\n"
    "If the diff does not let you tell, say nothing about it. No comments on style, naming or anything that is fine. At most three problems.\n"
    "Answer with ONLY this JSON: {\"findings\": [{\"blocking\": true, \"file\": \"path\", \"problem\": \"under 25 words\", \"fix\": \"under 25 words\"}]}. "
    "Use {\"findings\": []} when there is nothing. blocking is true only when the change would be wrong or fail without the fix.\n"
)


def trivial(files: List[Dict[str, Any]]) -> bool:
    """A change not worth a review: only prose, only deleted or moved files, or a couple of lines. Pure."""
    text_files = [f for f in files if f.get("format") not in ("docx", "docx_edit") and f.get("kind") not in ("delete", "rename")]
    if not text_files or all(str(f.get("path", "")).lower().endswith(_PROSE_SUFFIXES) for f in text_files):
        return True
    return sum(int(f.get("additions", 0) or 0) + int(f.get("deletions", 0) or 0) for f in text_files) < MIN_CHANGED_LINES


def diff_text(files: List[Dict[str, Any]]) -> str:
    """The diff cards as one text, each file under its path, capped overall with a note where it was cut."""
    parts, used = [], 0
    for card in files:
        if card.get("format") in ("docx", "docx_edit"):
            continue
        head = f"=== {card.get('path')} ({card.get('kind', 'edit')})\n"
        body = "" if card.get("kind") == "delete" else str(card.get("diff") or "")
        room = MAX_DIFF_CHARS - used - len(head)
        if room <= 0:
            parts.append("=== (more files not shown)")
            break
        shown = body[:room] + ("\n[cut]" if len(body) > room else "")
        parts.append(head + shown)
        used += len(head) + len(shown)
    return "\n".join(parts)


def build_prompt(request: str, files: List[Dict[str, Any]]) -> str:
    return f"{REVIEW_PROMPT}\nTHE REQUEST:\n{(request or '').strip()[:MAX_REQUEST_CHARS]}\n\nTHE DIFF:\n{diff_text(files)}\n"


def parse(text: Any) -> List[Dict[str, Any]]:
    """The findings from the model's answer: at most MAX_FINDINGS dicts {blocking, file, problem, fix}, or [] for anything that is not the JSON asked for. Never raises. Pure."""
    raw = str(text or "")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start:end + 1])
    except ValueError:
        return []
    rows = data.get("findings") if isinstance(data, dict) else None
    found: List[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("problem"), str) or not row["problem"].strip():
            continue
        found.append({
            "blocking": row.get("blocking") is True,
            "file": " ".join(str(row.get("file") or "").split())[:200],
            "problem": " ".join(row["problem"].split())[:240],
            "fix": " ".join(str(row.get("fix") or "").split())[:240],
        })
        if len(found) >= MAX_FINDINGS:
            break
    return found


def fixed_date_notes(files: List[Dict[str, Any]]) -> List[str]:
    """Free check, no model: a test file that gained a line with a fixed calendar date. Only a note (many tests pass the time in explicitly), but it is the mistake that broke 13 tests on 2026-11-01."""
    notes = []
    for card in files:
        path = str(card.get("path") or "")
        if not _TEST_PATH.search(path) or card.get("kind") == "delete":
            continue
        if any(line.startswith("+") and not line.startswith("+++") and _FIXED_DATE.search(line) for line in str(card.get("diff") or "").splitlines()):
            notes.append(f"{path}: a test line now hard-codes a calendar date. If the code under test also reads the clock, build the time from datetime.now() instead, or the test will fail once that date passes.")
    return notes


def blocking(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [f for f in findings if f.get("blocking")]


def note(findings: List[Dict[str, Any]], advisories: Optional[List[str]] = None) -> str:
    """The message for the loop: what the review found, to be fixed before finishing. Fixed wording around the findings."""
    lines = []
    for f in findings:
        where = f"{f['file']}: " if f.get("file") else ""
        lines.append(f"- {where}{f['problem']}" + (f" Fix: {f['fix']}" if f.get("fix") else "") + ("" if f.get("blocking") else " (not blocking)"))
    lines += [f"- {text}" for text in (advisories or [])]
    return (
        "REVIEW OF THE CHANGE YOU JUST APPLIED (a second reading of the diff, against your rules). "
        + ("Fix what is blocking now, then run the check again and finish. " if blocking(findings) else "Nothing here blocks. Mention it only if it matters. ")
        + "If you believe a finding is wrong, say why in your answer instead of changing the code:\n" + "\n".join(lines)
    )


def _text_of(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


async def review(llm: Any, request: str, files: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Asks `llm` (one cheap call) to review the diff. The findings (possibly none), or None when the call failed or there is no model: a failed review is no review. Never raises."""
    if llm is None or not files:
        return None
    try:
        return parse(_text_of(await llm.ainvoke(build_prompt(request, files))))
    except Exception:
        return None
