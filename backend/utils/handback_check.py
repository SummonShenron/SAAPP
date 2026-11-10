"""An answer that hands the work back to the person while the loop still has steps (2026-11-09, three real runs in a row, Haiku, a coding task).

THE FAILURE. Run 1 ended with 15 steps left asking "Should I read SonicHealth.tsx next?"; run 2 with 1 step left asking "draft the TSX first, or wire last_step_*?"; run 3 (a correct four-file change) with 15 steps left telling the person
to run the tests themselves, and to revert the edits to watch the new tests fail. Steps were never the limit: the loop lets the model end a coding turn whenever it answers, and the model chose to hand the next step back. A runner was paired and
online in all of them, with a check named `tests` that takes files (so it can be run on just the files that changed).

THE FIX, mechanical (the same shape as the other answer send-backs, backend/utils/answer_checks.py): on a coding turn, an answer whose ENDING
- OFFERS TO CARRY ON ("Should I read X?", "Want me to update Y?") with something the model can do itself (read, search, update, add, run...), or
- TELLS THE PERSON TO RUN SOMETHING THEMSELVES ("on your end", "to verify, run pytest ...") after an edit was applied, when the model has a check tool,
is sent back ONCE, while a step remains, with the step count and the way to do it: do it now, and if it is a real choice only the person can make, put it with ask_choice instead of asking in the answer. A turn
that really cannot do the thing (no tool offered) is not sent back for the "run it yourself" kind. Pure: no model call, no I/O.

THE TRACE NOTE. Whether a coding turn could run tests at all was invisible, so a hand-back could not be told from "no tool was offered". `verify_note` says which ways to check were offered this turn.
"""
import re
from typing import Iterable, Optional, Tuple

TAIL_CHARS = 700
_FOLLOW_UP_TAG_RE = re.compile(r"<<<.*?>>>", re.DOTALL)
_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)

# Things a model can do with its own tools: reading, searching, changing, checking.
_DOABLE = r"(?:read|look at|look into|open|search|find|check|inspect|trace|update|add|wire|write|draft|fix|apply|make|change|edit|create|propose|implement|run|test|verify|compare|fetch|hook up|finish|continue|carry on|go ahead)"
_OFFER_RE = re.compile(
    r"(?:\b(?:should i|shall i|do you want me to|want me to|would you like me to|would you like for me to)\b[^?.!\n]{0,160}\b" + _DOABLE + r"\b"
    r"|\blet me know (?:if|and) you(?:'d| would)? (?:like|want) me to\b[^?.!\n]{0,120}\b" + _DOABLE + r"\b"
    r")",
    re.IGNORECASE,
)
# Telling the person to run or verify something themselves, in a command-shaped way.
_HANDOFF_RE = re.compile(
    r"\bon your (?:end|side|machine|computer)\b|\bto verify\b[^.?!\n]{0,100}\b(?:run|use)\b|\b(?:please |you(?:'ll| will| can| could| should)? (?:want to )?)(?:run|execute)\b[^.?!\n]{0,60}\b(?:test|pytest|npm|npx|tsc|check)\b"
    r"|\b(?:run|execute) (?:the )?(?:following|these|this)\b|\bper your workflow\b[^.?!\n]{0,80}\brevert\b|\brevert the edits\b",
    re.IGNORECASE,
)
_COMMANDISH_RE = re.compile(r"\b(?:pytest|npm (?:run )?test|npx tsc|tsc -b|python -m|node --test|the tests?|the type ?check|the checks?)\b", re.IGNORECASE)
CHECK_TOOLS = ("run_local_check", "run_local_command")


def tail_of(answer: str) -> str:
    """The end of an answer without its follow-up tag: where a hand-back sits. Pure."""
    text = _FOLLOW_UP_TAG_RE.sub("", str(answer or "")).strip()
    return text[-TAIL_CHARS:]


def kind(answer: str, *, coding_turn: bool, edited: bool, offered: Iterable[str], steps_left: int, checked: bool = False) -> Optional[str]:
    """"offer", "verify" or None. `edited` is whether an edit was applied this turn, `checked` whether the turn already ran a check, `offered` the tool names this turn has, `steps_left` the tool steps
    still available. Pure."""
    if not coding_turn or steps_left < 1:
        return None
    tail = tail_of(answer)
    if not tail:
        return None
    if edited and not checked and set(offered) & set(CHECK_TOOLS) and _HANDOFF_RE.search(tail) and (_COMMANDISH_RE.search(tail) or _CODE_FENCE_RE.search(tail)):
        return "verify"
    if _OFFER_RE.search(tail) and "?" in tail[-250:]:
        return "offer"
    return None


def notice(which: str, steps_left: int, offered: Iterable[str] = ()) -> str:
    """The note put in front of the model when its answer is sent back. Pure."""
    left = f"{steps_left} tool step{'s' if steps_left != 1 else ''}"
    if which == "verify":
        available = set(offered)
        how = ("run_local_check, with `files` set to the test files you changed or added" if "run_local_check" in available else "run_local_command") if available & set(CHECK_TOOLS) else "your check tool"
        return (f"Your answer tells the person to run something themselves, but you have a way to run it and {left} left. Run it yourself now: {how}. Run only the tests for what you changed, not the whole suite, "
                "and for a part of the project the named check does not cover (the page, the types) use run_local_command. Read the result and report what passed or failed. If you truly cannot, say so in one sentence "
                "instead of handing them commands. Do not tell them to revert your changes to watch a test fail: if that matters, do it yourself and say what you saw.")
    return (f"Your answer ends by asking whether to carry on with something you can do yourself, and you have {left} left. Do it now with your tools, then answer with what you found or changed. "
            "If it is a real choice only the person can make, put it to them with ask_choice (one question, a recommended option) instead of asking in your answer. Do not ask permission for the next step of work you were asked to do.")


def verify_note(check_names: Iterable[str], commands_ok: bool) -> Tuple[str, str, str]:
    """(title, detail, counter key) for the trace line saying whether this coding turn can run checks itself. Pure."""
    names = [str(n) for n in check_names if str(n).strip()]
    parts = []
    if names:
        parts.append(f"the project's checks ({', '.join(names[:4])}) with run_local_check")
    if commands_ok:
        parts.append("any command from the allowed list with run_local_command")
    if parts:
        return ("Checks available", "It can verify its own work this turn: " + " and ".join(parts) + ". The person approves each run on a card.", "coding_turns_can_verify")
    return ("No test runner connected", "It cannot run tests or checks this turn, so it should say what it did not verify instead of handing the person commands.", "coding_turns_no_runner")
