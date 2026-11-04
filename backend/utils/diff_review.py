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
from typing import Any, Dict, List, Optional, Tuple

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


# The plan-end reading (2026-11-05): a stronger model reads the whole of an approved plan's change against the plan itself. It looks for what a cheaper model that wrote the code tends to leave out,
# which Jack and Claude Code found by hand on the browser and datastore migrations (a counter nothing increments, a test that passes without the change, tools run at once that share a page, a name
# never imported). It sees the plan and the diff only, so it still says nothing about what the diff does not show.
PLAN_REVIEW_ENABLED = os.getenv("PLAN_REVIEW_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
PLAN_MAX_DIFF_CHARS = int(os.getenv("PLAN_REVIEW_MAX_CHARS", "40000"))
PLAN_MAX_REQUEST_CHARS = 2600
PLAN_MAX_FINDINGS = 5

PLAN_REVIEW_PROMPT = (
    "You are the senior reviewer of a code change that was made by following an approved plan. You see the plan and the whole diff, not the rest of the repository.\n"
    "Report ONLY problems that would make the change wrong, incomplete or failing in real use, and only when the plan and the diff themselves show it. Check exactly these:\n"
    "1. A step of the plan, or part of the goal, that the diff does not do.\n"
    "2. A test that would still pass if the change were taken out (it asserts only a name, a count of keys, or the shape of source text, or it mocks away the very thing the change does).\n"
    "3. A test whose input data is invented rather than the shape the real code returns.\n"
    "4. A counter, key, field or option that is added but that nothing in the diff ever sets or increments, or that is incremented but that no test checks.\n"
    "5. A field, kind or value that now crosses a boundary (backend to frontend, server to runner) with no change in the diff to what receives it.\n"
    "6. Calls that now run at the same moment but share one page, session, connection or list, with nothing making them run in order.\n"
    "7. A name the new code uses that nothing in the diff defines or imports.\n"
    "8. Counting or parsing that treats every line alike (a \"nothing found\" message counted as a result), or a test that hard-codes today's date when the code also reads the clock.\n"
    "If the diff does not let you tell, say nothing about it. No comments on style, naming or anything that is fine. At most five problems, the worst first.\n"
    "Answer with ONLY this JSON: {\"findings\": [{\"blocking\": true, \"file\": \"path\", \"problem\": \"under 30 words\", \"fix\": \"under 30 words\"}]}. "
    "Use {\"findings\": []} when there is nothing. blocking is true only when the change would be wrong or fail without the fix.\n"
)


def plan_request(plan: Optional[Dict[str, Any]]) -> str:
    """The approved plan as the request the reviewer reads: its goal, its steps (with the files each changes) and the tests it wanted. Pure; "" when there is no plan."""
    if not plan:
        return ""
    lines = [f"Goal: {plan.get('goal', '')}", "Steps:"]
    for step in plan.get("steps") or []:
        files = f" [{', '.join(step.get('files') or [])}]" if step.get("files") else ""
        lines.append(f"- {step.get('text', '')}{files}")
    if plan.get("tests"):
        lines += ["Tests the plan wanted:"] + [f"- {t}" for t in plan["tests"]]
    return "\n".join(lines)


def reader_for(executing_plan: bool, claude_may_read: bool, strong: Any, ordinary: Any) -> Tuple[str, Any]:
    """Which model reads an applied change: ("none", None) when this person's code may not go to Claude (they chose no Anthropic model for the code work); ("plan", Sonnet) while carrying out an approved plan,
    when the plan reading is on and Sonnet can be used; otherwise ("ordinary", Haiku), which may itself be None (then there is no review). Pure."""
    if not claude_may_read:
        return "none", None
    if executing_plan and PLAN_REVIEW_ENABLED and strong is not None:
        return "plan", strong
    return "ordinary", ordinary


def plan_counts(findings: Optional[List[Dict[str, Any]]]) -> Dict[str, int]:
    """The day's tally for a plan-end reading (self_counters.COMBO_KEYS): one run, and one blocked when a finding blocks. {} when the call failed (a failed review is no review). Pure."""
    if findings is None:
        return {}
    return {"plan_review_runs": 1, **({"plan_review_blocked": 1} if blocking(findings) else {})}


def trivial(files: List[Dict[str, Any]]) -> bool:
    """A change not worth a review: only prose, only deleted or moved files, or a couple of lines. Pure."""
    text_files = [f for f in files if f.get("format") not in ("docx", "docx_edit") and f.get("kind") not in ("delete", "rename")]
    if not text_files or all(str(f.get("path", "")).lower().endswith(_PROSE_SUFFIXES) for f in text_files):
        return True
    return sum(int(f.get("additions", 0) or 0) + int(f.get("deletions", 0) or 0) for f in text_files) < MIN_CHANGED_LINES


def diff_text(files: List[Dict[str, Any]], limit: Optional[int] = None) -> str:
    """The diff cards as one text, each file under its path, capped overall (MAX_DIFF_CHARS, or `limit`) with a note where it was cut."""
    cap = MAX_DIFF_CHARS if limit is None else limit
    parts, used = [], 0
    for card in files:
        if card.get("format") in ("docx", "docx_edit"):
            continue
        head = f"=== {card.get('path')} ({card.get('kind', 'edit')})\n"
        body = "" if card.get("kind") == "delete" else str(card.get("diff") or "")
        room = cap - used - len(head)
        if room <= 0:
            parts.append("=== (more files not shown)")
            break
        shown = body[:room] + ("\n[cut]" if len(body) > room else "")
        parts.append(head + shown)
        used += len(head) + len(shown)
    return "\n".join(parts)


def build_prompt(request: str, files: List[Dict[str, Any]], plan: bool = False) -> str:
    """The prompt for one reading. `plan` is the plan-end reading: the broader prompt, the plan as the request, and room for a whole plan's diff."""
    if plan:
        return f"{PLAN_REVIEW_PROMPT}\nTHE APPROVED PLAN:\n{(request or '').strip()[:PLAN_MAX_REQUEST_CHARS]}\n\nTHE DIFF:\n{diff_text(files, PLAN_MAX_DIFF_CHARS)}\n"
    return f"{REVIEW_PROMPT}\nTHE REQUEST:\n{(request or '').strip()[:MAX_REQUEST_CHARS]}\n\nTHE DIFF:\n{diff_text(files)}\n"


def parse(text: Any, limit: int = MAX_FINDINGS) -> List[Dict[str, Any]]:
    """The findings from the model's answer: at most `limit` (MAX_FINDINGS) dicts {blocking, file, problem, fix}, or [] for anything that is not the JSON asked for. Never raises. Pure."""
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
        if len(found) >= limit:
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


async def review(llm: Any, request: str, files: List[Dict[str, Any]], plan: bool = False) -> Optional[List[Dict[str, Any]]]:
    """Asks `llm` (one call) to review the diff; `plan` is the plan-end reading (build_prompt). The findings (possibly none), or None when the call failed or there is no model: a failed review is no
    review. Never raises."""
    if llm is None or not files:
        return None
    try:
        return parse(_text_of(await llm.ainvoke(build_prompt(request, files, plan))), PLAN_MAX_FINDINGS if plan else MAX_FINDINGS)
    except Exception:
        return None
