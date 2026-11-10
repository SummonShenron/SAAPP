"""An answer that hands the work back to the person while the loop still has steps (2026-11-09, three real runs in a row, Haiku, a coding task).

THE FAILURE. Run 1 ended with 15 steps left asking "Should I read SonicHealth.tsx next?"; run 2 with 1 step left asking "draft the TSX first, or wire last_step_*?"; run 3 (a correct four-file change) with 15 steps left telling the person
to run the tests themselves, and to revert the edits to watch the new tests fail. Steps were never the limit: the loop lets the model end a coding turn whenever it answers, and the model chose to hand the next step back. A runner was paired and
online in all of them, with a check named `tests` that takes files (so it can be run on just the files that changed).

THE FIX, mechanical (the same shape as the other answer send-backs, backend/utils/answer_checks.py): on a coding turn, an answer whose ENDING
- OFFERS TO CARRY ON ("Should I read X?", "Want me to update Y?") with something the model can do itself (read, search, update, add, run...), or
- TELLS THE PERSON TO RUN SOMETHING THEMSELVES ("on your end", "to verify, run pytest ...") after an edit was applied, when the model has a check tool, or
- SAYS THE WORK IS NOT FINISHED, or promises the next step in words ("I'll read X next", "not fully wired", "what needs to be finished": 2026-11-09, the Office Hours run: a tested foundation, then 18 steps unused), or
- ANSWERS RIGHT AFTER A CHECK THAT FAILED (2026-11-09: pytest was asked to run a JavaScript test file, answered "not found", and the turn ended with 17 steps left on a wrong reading of why),
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
# A choice put to the person in words (2026-11-10: "1. Match on reply ... 2. Judge the turn ... Which approach do you want?" when ask_choice was in the menu): two or more listed options and a question at the end.
_OPTION_LINE_RE = re.compile(r"(?m)^\s*(?:\d+[.)]|[-*•])\s+\S.{8,}")
_CHOOSE_RE = re.compile(r"\b(?:which|prefer|pick|choose|go with|want to go|would you like)\b", re.IGNORECASE)
_COMMANDISH_RE = re.compile(r"\b(?:pytest|npm (?:run )?test|npx tsc|tsc -b|python -m|node --test|the tests?|the type ?check|the checks?)\b", re.IGNORECASE)
CHECK_TOOLS = ("run_local_check", "run_local_command")
KINDS = ("offer", "verify", "failed", "unfinished", "choice")       # what `kind` can return: one send-back of any of them per turn (native_loop), counted as answer_sent_back_handback_<kind>
UNFINISHED_CHARS = 1200
# An answer that says, in so many words, that the work is not done, or that promises the next step instead of taking it. The whole last stretch is searched for the first (a list of "what needs to be finished" can
# sit above the closing line), the very end for the second.
_UNFINISHED_RE = re.compile(
    r"\b(?:not (?:fully |yet |quite |completely )?(?:wired(?: up)?|hooked up|connected|finished|done|complete|implemented|live)"
    r"|isn't (?:wired|hooked up|connected|finished|done|complete)|aren't (?:wired|hooked up|finished|done)"
    r"|(?:is|are) still (?:open|missing|to do|unfinished)|what (?:still )?needs to be (?:finished|done|wired|added)|what's (?:still )?left"
    r"|(?:still )?needs? to be (?:wired|hooked up|finished|done|added|updated)|remaining (?:work|steps|pieces)|(?:laid|built|put in) (?:the )?foundation)\b",
    re.IGNORECASE,
)
_PROMISE_RE = re.compile(
    r"\b(?:i'?ll|i will|i'm going to|i am going to|next,? i(?:'ll| will)|let me) (?:now |next |then |go ahead and )?(?:read|wire|hook|finish|continue|add|update|write|look|check|run|implement|make|fix|trace|search|open)\b",
    re.IGNORECASE,
)
_CHECK_CALL_PREFIXES = ("run_local_check(", "run_local_command(", "run_sandbox_tests(")
_FAILED_RE = re.compile(r"^LOCAL (?:CHECK|COMMAND)\b[^\n]*\bFAILED \(exit code", re.IGNORECASE)


def last_check_failed(attempts) -> bool:
    """Whether the most recent thing the turn did was a check that FAILED (a check, a command or sandbox tests whose result says "FAILED (exit code ..."). If it did anything else after the check (read, edited) the
    check has been dealt with. Pure."""
    for attempt in reversed(list(attempts or [])):
        if not str(attempt.get("action_desc", "")).startswith(_CHECK_CALL_PREFIXES):
            return False
        return bool(_FAILED_RE.match(str(attempt.get("observation", "")).strip()))
    return False


def says_unfinished(text: str) -> bool:
    """Whether a reply says the work is not finished or promises the next step in words (the same test the loop's send-back uses). Pure."""
    plain = _FOLLOW_UP_TAG_RE.sub("", str(text or ""))
    return bool(_UNFINISHED_RE.search(plain[-UNFINISHED_CHARS:]) or _PROMISE_RE.search(plain[-TAIL_CHARS:]))


def tail_of(answer: str) -> str:
    """The end of an answer without its follow-up tag: where a hand-back sits. Pure."""
    text = _FOLLOW_UP_TAG_RE.sub("", str(answer or "")).strip()
    return text[-TAIL_CHARS:]


def kind(answer: str, *, coding_turn: bool, edited: bool, offered: Iterable[str], steps_left: int, checked: bool = False, check_failed: bool = False) -> Optional[str]:
    """"failed", "offer", "verify", "unfinished" or None. `edited` is whether an edit was applied this turn, `checked` whether the turn already ran a check, `check_failed` whether the last thing it did was a failed check, `offered`
    the tool names this turn has, `steps_left` the tool steps still available. Pure."""
    if not coding_turn or steps_left < 1:
        return None
    if check_failed:
        return "failed"
    tail = tail_of(answer)
    if not tail:
        return None
    if edited and not checked and set(offered) & set(CHECK_TOOLS) and _HANDOFF_RE.search(tail) and (_COMMANDISH_RE.search(tail) or _CODE_FENCE_RE.search(tail)):
        return "verify"
    if "ask_choice" in set(offered) and len(_OPTION_LINE_RE.findall(tail)) >= 2 and "?" in tail[-250:] and _CHOOSE_RE.search(tail[-400:]):
        return "choice"
    if _OFFER_RE.search(tail) and "?" in tail[-250:]:
        return "offer"
    if _UNFINISHED_RE.search(_FOLLOW_UP_TAG_RE.sub("", str(answer or ""))[-UNFINISHED_CHARS:]) or _PROMISE_RE.search(tail):
        return "unfinished"
    return None


def notice(which: str, steps_left: int, offered: Iterable[str] = ()) -> str:
    """The note put in front of the model when its answer is sent back. Pure."""
    left = f"{steps_left} tool step{'s' if steps_left != 1 else ''}"
    if which == "failed":
        return (f"Your last check FAILED and you have {left} left, but you answered without dealing with it. Read what it printed. If the command was wrong for what you ran it on (pytest cannot run a JavaScript test file, "
                "and a check can be reported as not found when it simply cannot run that kind of file: use run_local_command with the project's own command for those), run the right one. If your code is wrong, fix it and run "
                "the check again. If the failure is not yours or cannot be fixed, say exactly why in your answer. Do not hand the person commands to run.")
    if which == "choice":
        return (f"Your answer asks the person to choose between options in words, and you have {left} left. Put it to them as a card instead: call ask_choice with one short question, the two to four options "
                "(a label and one sentence each) and `recommended` set to the option you would pick. Then carry on with what they choose. Do not list the options in your answer.")
    if which == "unfinished":
        return (f"Your answer says the work is not finished, or says what you will do next, and you have {left} left. Do it now with your tools: carry on with the rest of the request, check it, then answer with what is done. "
                "Stop early only if something really blocks you; then say exactly what, and if it needs a decision only the person can make, put it with ask_choice. Do not end a turn with a list of what still needs doing.")
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
