"""When Sonic can change the person's files, it changes them; it does not write the change out in its answer. Pure, no model call, nothing logged.

WHY. With a folder connected, a change is made by calling propose_local_edits (the person sees a diff and presses Apply). Writing the code or "here is what to change" into the answer instead costs a whole
model call, helps nobody (they cannot Apply prose) and leaves the work undone: Jack, 2026-10-31, "theres genuinely no reason it should EVER be giving pseudocode when it has the ability to make direct edits".

`wrote_code_instead_of_editing(text)` finds a fenced block of real code (three or more lines, not a shell command or program output) in an answer. The loop sends such an answer back to make the edit
(backend/services/native_loop.py), only on a turn that is about making a change, and only when no edit was proposed yet this turn: quoting existing code while explaining is not this.
"""
import re

_FENCE = re.compile(r"```([A-Za-z0-9_+.-]*)[^\n]*\n(.*?)```", re.S)
_NOT_CODE = {"bash", "sh", "shell", "zsh", "powershell", "ps1", "cmd", "console", "terminal", "text", "txt", "output", "log", "plaintext"}
MIN_CODE_LINES = 3


def wrote_code_instead_of_editing(text: str) -> bool:
    """True when the answer contains a fenced block of code (at least MIN_CODE_LINES non-empty lines, and not a shell command or output). Pure."""
    for language, body in _FENCE.findall(str(text or "")):
        if language.lower() in _NOT_CODE:
            continue
        if sum(1 for line in body.splitlines() if line.strip()) >= MIN_CODE_LINES:
            return True
    return False


SEND_BACK_NOTICE = (
    "ACT, DO NOT DESCRIBE: your answer writes the change out as code. Their folder is connected, so make the change: call propose_local_edits now with the exact edits (old text and new text from the "
    "files you read). Do not paste code, pseudocode or a list of changes into your answer, and do not ask whether to apply it."
)

ACT_NOW_NOTICE = (
    "The reading budget is used up, and the change is not made yet. Make it NOW: call propose_local_edits with the edits you can write from what you already read. Do not write the code in your "
    "answer and do not describe what you would change: only the tool call counts. If you truly cannot write one exact edit, call it for the part you can."
)
