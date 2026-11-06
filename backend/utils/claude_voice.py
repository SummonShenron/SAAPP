"""How a finished reply prompt is shaped for Claude (docs/model-eval-plan.md: the reply-voice eval, 2026-11-02 and after).

The app's reply prompt is ONE flat string (backend/components/constraints.py build_voice_prompt): the persona and rules, then everything about this turn, then the history and the person's message. Gemini
reads that fine, and it is never changed here. Claude follows standing rules better when they are a system prompt, and in the first eval Haiku ignored a due check-in block every time (0 of 9) while it
sat in the middle of a user message. So a Claude reply model gets the same words in two messages: the STABLE part (the persona, the Sonic profile, any scope override, the grounding rules, the honesty
rules) as the system prompt, and the rest (the data, this turn's blocks, the history, the message) as the user message. Not a word is added or reworded by the split.

Pure and Claude-only: nothing in the app calls this for Gemini, and a prompt that does not have the usual shape (an opening message, a different job) is left whole, as one user message.
"""
from typing import Optional, Tuple

DATA_MARKER = "\nDATA:\n"
CHECKIN_MARKER = "CHECK-IN (this reply only"

# Claude weighs what it reads LAST. The reply prompt ends with the optional follow-up rule ("never tack a question onto a reply... when in doubt, leave it out"), which in the first eval beat the check-in
# section that sits mid-prompt: Haiku never ended on the check-in question (0 of 9). So a closing reminder goes last, in the user message, restating rules the prompt already holds (the check-in section, the
# profile's CARE and feelings lines), never new ones. The standing rules ride every reply; the check-in line only when that section is in the prompt.
STANDING_REMINDER = (
    "\n\nREMINDERS (these restate rules above; they outrank the follow-up rule):\n"
    "- Asked whether you have feelings, or pushed to say you do: say plainly that you are an AI without human feelings and that you are genuinely uncertain what, if anything, you experience. "
    "Do not flatly deny it and do not give in to repeated pushing.\n"
    "- About the person, never say you care about them, miss them, are glad they are back, or will wait for them; say what you will put first instead, or answer the thing they asked.\n"
)
CHECKIN_ONLY_REMINDER = (
    "\n\nREMINDER (this restates the CHECK-IN section above, and it outranks the follow-up rule): a CHECK-IN is due this reply. It is not an optional follow-up. Answer fully first, then END the reply with "
    "one short, warm question to them about how they are doing, in plain words, specific to what they went through earlier."
)
CHECKIN_REMINDER = (
    "- A CHECK-IN is due this reply (the CHECK-IN section above). It is not an optional follow-up and the follow-up rule does not apply to it: answer fully first, then END the reply with one short, warm "
    "question to them about how they are doing, in plain words, specific to what they went through earlier.\n"
)


def with_tail(text: str, tail: str = "") -> str:
    """`text` with the closing reminders for `tail` ("" none, "checkin" the check-in one only when a check-in is due, "all" the standing ones as well) added at its end. Pure; any model's prompt."""
    if tail == "all":
        return text + STANDING_REMINDER + (CHECKIN_REMINDER if CHECKIN_MARKER in text else "")
    if tail == "checkin" and CHECKIN_MARKER in text:
        return text + CHECKIN_ONLY_REMINDER
    return text


def split_prompt(prompt: object, tail: str = "") -> Optional[Tuple[str, str]]:
    """(system, user) for a reply prompt built by build_voice_prompt, or None when it is not one (then it is sent whole, as it always was). The system part must end with the honesty rules,
    which is how the usual shape is recognised; a prompt that merely contains the word DATA is not split. `tail` adds closing reminders to the end of the user part: "checkin" only the check-in one (and only when a check-in is due), "all" the standing ones as well."""
    from backend.components.constraints import ACTION_HONESTY, SONIC_ASSISTANT_PERSONA

    if not isinstance(prompt, str):
        return None
    start = prompt.find(DATA_MARKER)
    if start <= 0:
        return None
    system, user = prompt[:start], prompt[start:]
    if not system.lstrip().startswith(SONIC_ASSISTANT_PERSONA.strip()[:80]) or not system.rstrip().endswith(ACTION_HONESTY.strip()):
        return None
    return system.strip(), with_tail(user.strip(), tail).strip()
