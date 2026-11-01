"""A list of steps Sonic proposes for the person to run themselves, one click at a time (the `propose_steps` tool; the card is local/src/components/CommandStepsCard.tsx).

WHY. A multi-step job ("commit this and push it") used to be one long command line on one card, or one card per step with Sonic's turn waiting on each. This is the third shape: Sonic lays the steps out
with a short title each, and the person runs them in their own time, in order or one by one, from a single card, the way a person runs the commands of a README.

THE RULES (the same as for any command the runner is asked to run, backend/utils/runner_commands.py):
- EVERY LINE IS CHECKED HERE with the runner's own rules, so a step the runner would refuse is never shown; the model is told which step was refused and why, and nothing is shown at all until the whole list
  is acceptable (a list with a hole in it is worse than none).
- A STEP IS ONLY TEXT UNTIL THE PERSON PRESSES RUN. Pressing it makes the same command job the Terminal makes (POST /api/runner/jobs) and approves it; a line that changes things still needs the stronger
  confirmation, and the runner decides AGAIN on the person's own computer (and asks in its own window for a change). This module creates and approves nothing.
- NOTHING IS CLAIMED. Sonic is told that no step has run, so it cannot say one did; how the steps went comes back through the person (the card can put a summary in their message box).
"""
import re
from typing import Any, Dict, List, Tuple

from backend.utils import runner_commands

MAX_STEPS = 8
MAX_TITLE_CHARS = 80
MAX_LINE_CHARS = 600
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")


def _title(text: Any) -> str:
    return re.sub(r"\s+", " ", _CONTROL_RE.sub(" ", str(text or ""))).strip()[:MAX_TITLE_CHARS].rstrip()


def clean_steps(raw: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The steps ready for a card, and the reasons any step was refused. `raw` is the model's list of {"title", "line"}. With any reason, the list is not shown. Each step comes back as
    {"id", "title", "line" (as it will run, rendered by the runner's own rules), "klass" ("read", "run" or "change"), "notes"}. Pure apart from the shared rules file."""
    if not runner_commands.AVAILABLE:
        return [], ["Commands are not available on this server right now."]
    if not isinstance(raw, list) or not raw:
        return [], ["Give a list of steps, each with a short title and one command line."]
    if len(raw) > MAX_STEPS:
        return [], [f"At most {MAX_STEPS} steps in one card: split the job in two."]
    steps: List[Dict[str, Any]] = []
    problems: List[str] = []
    for index, item in enumerate(raw, 1):
        title = _title(item.get("title") if isinstance(item, dict) else "")
        line = item.get("line") if isinstance(item, dict) else None
        if not title:
            problems.append(f"Step {index} has no title.")
            continue
        if not isinstance(line, str) or not line.strip() or len(line) > MAX_LINE_CHARS:
            problems.append(f"Step {index} ({title}) needs one command line of at most {MAX_LINE_CHARS} characters.")
            continue
        try:
            plan = runner_commands.classify(line)
        except runner_commands.commands.CommandError as problem:
            problems.append(f"Step {index} ({title}) was refused: {problem}")
            continue
        steps.append({"id": f"s{index}", "title": title, "line": runner_commands.commands.render(plan), "klass": plan.klass, "notes": list(plan.notes[:4])})
    return (steps, []) if not problems else ([], problems)


def summary_for_model(steps: List[Dict[str, Any]]) -> str:
    """What the tool tells the model once the card is shown: the steps are only proposed, and none has run. Pure."""
    listing = "; ".join(f"{s['id']} {s['title']}" for s in steps)
    changing = [s["id"] for s in steps if s["klass"] == "change"]
    note = f" {', '.join(changing)} change(s) things, so the person confirms them more strongly." if changing else ""
    return (f"SHOWN: {len(steps)} steps are on a card for the person to run themselves, one click at a time ({listing}).{note} NOTHING HAS RUN: do not say any step ran or worked. "
            "Give a short final answer on what the steps do and in what order; they can tell you how it went afterwards.")
