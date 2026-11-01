"""Turning a correction into a rule for the project's guidance file, with the person's approval.

Jack, 2026-11-01: when Claude Code is corrected it saves the lesson, and Sonic did not, so the same mistake came back the next day. The project's guidance file (`.sonic/AGENTS.md`, see
backend/utils/project_guidance.py) is read at the start of every coding turn, so a rule there is a lesson Sonic keeps. This only asks Sonic to PROPOSE one:

- No extra model call and no new detector: when the message follows a coding reply (backend/utils/code_outcomes.followup), the turn's prompt gets a short note, and the model itself decides whether this
  message really corrected a mistake that a rule would have prevented (it is told what does not count: taste, one-off slips, rules already in the file).
- Only for `.sonic/AGENTS.md` (the file this project owns for Sonic), never for an AGENTS.md or CLAUDE.md that other tools read, and only when the whole file fits what is read.
- It is an ordinary edit card. Changes to that file ALWAYS ask, even at the automatic approval levels (local/src/editApproval.ts), and the server refuses a change that would grow the file past what is read
  (backend/services/local_edits.py), so a rule can never be cut off at the end.
- Nothing is logged but counts. The note itself is fixed text: it carries the size of the file and nothing the person wrote.
"""
from typing import Any, Dict, Optional

from backend.utils import project_guidance

GUIDANCE_PATH = project_guidance.FILES[0]          # ".sonic/AGENTS.md": the one file lessons are proposed for


def should_offer(code_followup: Optional[str], guidance: Optional[Dict[str, Any]]) -> bool:
    """Whether this turn's prompt carries the note: the message follows a coding reply, and the guidance read is our own file, complete (a cut file has no room to add to). Pure."""
    return bool(code_followup) and bool(guidance) and guidance.get("path") == GUIDANCE_PATH and not guidance.get("cut") and bool(guidance.get("text"))


def note(guidance: Dict[str, Any]) -> str:
    """The note for the prompt. Fixed wording plus the file's current size and the limit."""
    used, limit = len(guidance.get("text") or ""), project_guidance.MAX_CHARS
    return (
        "\n\nLESSONS FOR THIS PROJECT. The message you are answering follows a coding reply of yours. If it tells you that something you did was wrong, or that you missed something about how this "
        "project works, and a short rule would have prevented it (not a matter of taste, not a one-off slip), then once you have fixed what they pointed out, ALSO propose ONE new rule for the "
        f"project's guidance file with propose_local_edits: a single bullet line in the section of {GUIDANCE_PATH} where it belongs (the rule, then what went wrong, briefly). Read that "
        "region of the file first and copy old_string from it exactly. "
        f"The file is read only up to {limit} characters and is {used} now, and a change that makes it longer than that is refused: if the line does not fit, replace the least useful line instead of "
        "adding. Do not add a rule that is already in PROJECT GUIDANCE above, do not propose the same one again if they decline, and say in one sentence in your answer which rule you proposed and why. "
        "If the message was not a correction of that kind, propose nothing and do not mention this.\n"
    )
