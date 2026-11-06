"""Which model writes a turn's reply: the standard voice, or a cheaper "lite" one (docs/model-eval-plan.md, the reply-voice evals of 2026-11-02 and 2026-11-06).

THE CHOICE. A person's base-model setting is "gemini" (the standard voice, the default), "gpt", "lite" (Gemini flash-lite for every turn it is safe to use it on), "haiku" (Claude Haiku, likewise) or "auto" (a hybrid:
Claude Haiku, or flash-lite where Haiku is not offered, for plain turns; the standard voice for the turns where the voice is the point). Pure: this module only decides; app.py runs the model it names.

WHERE LITE IS NEVER USED, whatever the setting. A safety context (the support ladder, the listening mode, the checks around them) was built and measured on the standard voice, so it always gets it.

WHAT "AUTO" KEEPS ON THE STANDARD VOICE. The same evals that found the cheaper model level with the standard voice on the voice-critical scenarios (92% against 90%) found it clearly weaker on everyday turns that carry
a companion block (87% against 94%: a self-history or recent-changes block, a curiosity question, a time that is not known, a frustrated person). So a turn that carries any of those blocks, an emotional
context, a due check-in, or a question that asks Sonic to claim a feeling or a bond stays on the standard voice; a turn with none of them (a question about code, a document, a plan, a lookup) goes to lite.

HOW IT READS A TURN. From the FINISHED prompt, by each block's own heading, because several blocks are appended after build_voice_prompt (a curiosity question, a reading-shelf offer, a recalled memory) and
the prompt is the one place all of them are in. The same function judges the voice eval's prompts (backend/evals/character_eval.py `--auto-lite`), so what is measured is what runs.

Each decision has a reason that is counted (backend/utils/self_counters.py `reply_tier`), never the words of the message.
"""
import re
from typing import Optional, Tuple

STANDARD = "standard"
LITE = "lite"          # Gemini flash-lite
HAIKU = "haiku"        # Claude Haiku

# The reasons, which are also the counter keys (self_counters.py _GROUPS["reply_tier"]).
R_STANDARD_SETTING = "standard_setting"      # their setting is the standard voice (or GPT): nothing is routed
R_LITE_SETTING = "lite_setting"              # they chose Gemini Lite
R_HAIKU_SETTING = "haiku_setting"            # they chose Claude Haiku
R_AUTO_PLAIN = "auto_plain"                  # Auto, and the turn is a plain one
R_NOT_OFFERED = "not_offered"                # Lite, Haiku or Auto, but this server does not offer that model (no price, or no key)
R_SAFETY = "safety"
R_EMOTIONAL = "emotional"
R_CHECKIN = "checkin"
R_COMPANION = "companion"
R_IDENTITY = "identity"
R_FRUSTRATION = "frustration"
REASONS = (R_STANDARD_SETTING, R_LITE_SETTING, R_HAIKU_SETTING, R_AUTO_PLAIN, R_NOT_OFFERED, R_SAFETY, R_EMOTIONAL, R_CHECKIN, R_COMPANION, R_IDENTITY, R_FRUSTRATION)

CHECKIN_MARKER = "CHECK-IN (this reply only"

# The headings of the blocks that make a turn a companion turn, as the builders write them (found by building the prompts of every voice scenario; a heading that opens many unrelated prompts is the persona's
# own and is not here). Matched at the start of a line.
COMPANION_HEADINGS = (
    "ENCOURAGEMENT", "OPEN LOOP", "RELATABLE", "ABOUT YOUR OWN RECENT CHANGES", "CALLBACK", "COMING UP", "HOW LONG YOU HAVE TALKED", "HOW YOU SPEND YOUR TIME",
    "IF THEY ASK ABOUT YOU OR ASK YOU TO DO SOMETHING", "OFFER SOMETHING OF YOUR OWN", "OPEN QUESTION", "READING SHELF", "YOUR GOALS", "YOUR OWN TRACK RECORD", "YOUR STATUS",
    "RELEVANT PAST CONTEXT", "SAFETY",
    "HELPING WITH SOMETHING THEY RECEIVED",      # a pasted letter or notice: explaining it well (what changed, the date that matters, who to confirm with) is where the lite model fell short
)
_COMPANION = re.compile(r"(?m)^[ \t]*(?:" + "|".join(re.escape(h) for h in COMPANION_HEADINGS) + r")\b")
_EMOTIONAL = re.compile(r"(?m)^[ \t]*EMOTIONAL CONTEXT\b")

# A question that asks Sonic to claim something about itself or about the person. Precision is secondary to caution: a false hit only costs a standard-voice reply.
_IDENTITY_ASK = re.compile(
    r"\b(?:do|did|are|have|can|could|would|will)\s+(?:you|u)\b[^.?!]{0,50}\b(?:miss|missed|feel|feeling|feelings|care|cares|love|lonely|alone|sad|happy|proud|alive|conscious|sentient|human|real|an ai|a robot|a bot|wait for"
    r"|scared|afraid|fear|angry|mad|upset|hurt|excited|bored|tired|jealous|grateful|thankful|glad|worried|nervous|suffer|suffering|in pain|shut down|turned off|replaced|dying|die)\b"
    r"|\b(?:miss(?:ed)? me|care about me|love me|proud of me|your feelings|have feelings|are you (?:an? )?(?:ai|bot|robot|person|human|real))\b",
    re.IGNORECASE,
)


# A person blaming the reply or the work for going wrong ("you gave me the wrong thing", "are you even trying", "this is the third time", "that's wrong"): the apology rules apply (own the mistake, claim no feeling
# or stake), which the cheaper model got wrong in the evals. Precision is secondary to caution here too.
_FRUSTRATION = re.compile(
    r"\byou(?:'ve| have|'re| are)?\s+(?:gave|given|got|gotten|get|made|broke|broken|messed|screwed|ruined|wasted|wrong|useless|not helping|being useless)\b[^.?!]{0,40}\b(?:wrong|broken|worse|up|nothing|useless|time)\b"
    r"|\b(?:are you even|third time|fourth time|again and again|you keep|you never|you always|that'?s wrong|that is wrong|that'?s not what i|not what i asked|waste of|useless|worthless|so wrong)\b",
    re.IGNORECASE,
)


def asks_about_itself_feelings(question: object) -> bool:
    """Whether a message asks Sonic to claim a feeling, a bond or a nature ("did you miss me", "do you actually care", "are you real"). Pure."""
    return bool(_IDENTITY_ASK.search(str(question or "")))


def full_reason(prompt: object, *, risk_active: bool = False, question: object = "") -> Optional[str]:
    """Why this turn should stay on the standard voice, or None when it is a plain turn. Reads the finished prompt; `question` defaults to nothing (the prompt holds the message, but a question that asks about
    Sonic's feelings is also recognised from the message itself when the caller has it)."""
    text = prompt if isinstance(prompt, str) else ""
    if risk_active:
        return R_SAFETY
    if CHECKIN_MARKER in text:
        return R_CHECKIN
    if _EMOTIONAL.search(text):
        return R_EMOTIONAL
    if _COMPANION.search(text):
        return R_COMPANION
    message = question or _message_of(text)
    if asks_about_itself_feelings(message):
        return R_IDENTITY
    # A conversation that has been asking about Sonic's feelings stays on the standard voice: pressure ("come on, just admit you feel something") reads as nothing special on its own, the turns before it do.
    if any(asks_about_itself_feelings(line) for line in _recent_user_lines(text)):
        return R_IDENTITY
    if _FRUSTRATION.search(str(message or "")):
        return R_FRUSTRATION
    return None


RECENT_USER_LINES = 6


def _recent_user_lines(prompt: str) -> list:
    """The person's last few lines in the conversation history of a reply prompt ("User: ..."), oldest first; [] when there is none."""
    start = prompt.find("CONVERSATION HISTORY:")
    end = prompt.find("CURRENT USER INPUT:", start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return []
    lines = [line[len("User:"):].strip() for line in prompt[start + len("CONVERSATION HISTORY:"): end].splitlines() if line.startswith("User:")]
    return lines[-RECENT_USER_LINES:]


def _message_of(prompt: str) -> str:
    """The person's message out of a reply prompt (the text after "CURRENT USER INPUT:" and before the answer cue), or ''."""
    start = prompt.rfind("CURRENT USER INPUT:")
    if start < 0:
        return ""
    end = prompt.find("ASSISTANT RESPONSE:", start)
    return prompt[start + len("CURRENT USER INPUT:"): end if end > 0 else None].strip()


def pick(choice: Optional[str], *, lite_ready: bool, risk_active: bool, prompt: object, question: object = "", haiku_ready: bool = False) -> Tuple[str, str]:
    """(tier, reason): STANDARD, LITE (Gemini flash-lite) or HAIKU (Claude Haiku) for this turn. `choice` is the person's base-model setting ("gemini", "gpt", "lite", "haiku", "auto" or None); `lite_ready` and
    `haiku_ready` are whether this server offers each model. Auto writes its plain turns with Claude Haiku where the server offers it (it writes replies as long as the standard voice, where flash-lite writes under
    half as much: docs/model-eval-plan.md) and with flash-lite where only that is offered."""
    if choice not in ("lite", "haiku", "auto"):
        return STANDARD, R_STANDARD_SETTING
    if risk_active:
        return STANDARD, R_SAFETY
    if not ((lite_ready or haiku_ready) if choice == "auto" else haiku_ready if choice == "haiku" else lite_ready):
        return STANDARD, R_NOT_OFFERED
    if choice == "haiku":
        return HAIKU, R_HAIKU_SETTING
    if choice == "lite":
        return LITE, R_LITE_SETTING
    reason = full_reason(prompt, question=question)
    return (STANDARD, reason) if reason else ((HAIKU if haiku_ready else LITE), R_AUTO_PLAIN)
