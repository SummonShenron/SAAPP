"""A draft that lives inside the tool loop, and the halfway nudge that asks for one.

THE FAILURE THIS ANSWERS (2026-10-22/23, coding turns). The tool loops read, read, read and write the change only in their final answer, so a turn that needs five reads has no
step left to check what it wrote, and a turn that runs out of steps has nothing written at all. A person editing code writes a change, looks again, and fixes it; Sonic's change
is its final answer (the write tools are held back on purpose), so there was nothing to look at and no reason to keep working.

THE FIX has two parts, both advisory and with no model call of their own:
- A DRAFT the model may attach to any step ("draft" in the old loop's JSON, a `save_draft` call in the native one). It costs no extra step: it rides along with the step's
  tool calls. It is kept for the rest of the turn and shown back at every later step, so the model can check it against the files it read and send a corrected whole draft. It is
  never shown to the person unless the model returns it as its answer, and if the loop is cut off the draft is what is returned (labelled as unfinished) rather than nothing.
- A HALFWAY NUDGE: when about half the tool steps are used, some reading has been done and there is still no draft, the model is asked to write one if this is a request to
  change code. It is told to ignore the nudge when it is not, so a question that only needs reading is not pushed into inventing a draft.
"""
from typing import Any, Optional

MAX_DRAFT_CHARS = 12_000


def take_draft(value: Any) -> Optional[str]:
    """The draft text out of a decision's "draft" (or a save_draft call's text), trimmed and capped; None when there is none."""
    if isinstance(value, dict):
        value = value.get("draft") if "draft" in value else value.get("text")
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:MAX_DRAFT_CHARS] if text else None


def halfway_step(budget: int) -> int:
    """The 0-based step at which the nudge first appears: about half of the tool steps (the last step of a budget is the forced answer), never the first step."""
    return max(1, (max(budget, 2) - 1) // 2)


def draft_block(draft: Optional[str], *, how: str = 'send a corrected whole "draft" with your next step') -> str:
    """The draft as it is shown back to the model at every step after it was written, with what to do with it."""
    if not draft:
        return ""
    return (
        "\n\nYOUR DRAFT SO FAR (you wrote it earlier this turn; the person does not see it unless you return it as your answer):\n"
        f"{draft}\n"
        f"Check it against the files you actually read: every name, path and signature in it must exist as you wrote it. If something is wrong, {how}; if it is right, "
        "answer with it."
    )


def halfway_notice(step: int, budget: int, attempts_made: int, has_draft: bool, forced_final: bool, *, how: str = 'in the "draft" field of this step') -> Optional[str]:
    """The nudge, or None. `step` is the 0-based step about to run. Shown from the halfway step on while there is no draft, after some reading, and never on the forced last step."""
    if has_draft or forced_final or attempts_made < 1 or step < halfway_step(budget):
        return None
    tool_steps = max(budget - 1, 1)
    return (
        f"\n\n(Halfway: you have used {step} of {tool_steps} tool steps and have no draft yet. If this is a request to write or change code and you already know roughly what to "
        f"change, write your draft now {how}: the code, in full. It costs no extra step, and you can spend the steps that remain checking it against the files you read and "
        "correcting it. If this is not a request to change code, ignore this and answer as soon as you can.)"
    )


def forced_final_addition(draft: Optional[str]) -> str:
    """What the forced last step is told when a draft exists: start from it, not from nothing."""
    if not draft:
        return ""
    return "\n\nYou have a draft (shown above). Start your answer from it: correct anything you could not verify and say in one short line what you could not check."


UNFINISHED_LABEL = "I ran out of steps before I could check this, so treat it as an unfinished draft:"


def fallback_answer(draft: Optional[str]) -> Optional[str]:
    """The draft as an answer when the loop could not produce one of its own, labelled so nobody takes it for a checked result; None when there is no draft."""
    return f"{UNFINISHED_LABEL}\n\n{draft}" if draft else None
