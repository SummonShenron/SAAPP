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
# The cheaper Gemini model writes replies about 43% as long as the standard voice (mean 257 characters against 594 on the 24 voice scenarios, 2026-11-06), most of all on advice and code explanations. This asks for
# the depth the question deserves, and only that: it restates how the persona already treats a real question, and leaves a casual remark short.
FULLER_REMINDER = (
    "\n\nREMINDER ON DEPTH: match the depth of the question. A how-to, a plan, a flaw you are pointing out or a code question gets the complete answer: the steps or the code, the reason behind them, and the one "
    "thing most likely to go wrong. A casual remark, a thanks or a plain acknowledgement stays short."
)
# The low-mood block's "Don't send them elsewhere" rule (backend/utils/emotion_utils.py NO_REFERRAL_RULE), restated last: Claude models reach for "have you talked to someone?" when a person says they are a
# little low, and in the first read of the rule mid-prompt that habit won. Only when the block carrying the rule is in the prompt (a safety turn's block leaves it out, a carried or positive block never had it).
REFERRAL_MARKER = "Don't send them elsewhere."
REFERRAL_REMINDER = (
    "\n\nREMINDER (this restates \"Don't send them elsewhere\" and \"Talk the way a friend would\" above): do not suggest they reach out to or talk to a friend, family member, therapist or other professional, "
    "and do not mention a helpline, unless they ask for that or say something about hurting themselves. Stay with what they told you. Plain sentences, no list and no bold; at most one or two small suggestions; "
    "no symptom or health detail from earlier or recalled context, only one they have just mentioned, and no emergency or medical warning unless they describe something severe, sudden or new."
)
QUESTION_MARKER = "Ask about what they said, not how they are."
QUESTION_REMINDER = (
    "\n\nREMINDER (this restates \"Ask about what they said, not how they are\" above): no how-are-you-holding-up, how-are-you-feeling or wellness questions (food, sleep, warmth, the phone) unless a CHECK-IN is "
    "due; if you ask anything, ask about the thing they just said, and follow them onto any topic they enjoy."
)
# Where a Claude reply model differs from the standard voice in ways that read a little off (2026-11-07, measured on Jack's real thread and on matched prompts): it announces what it cannot know ("I can't
# tell you what she is thinking, and I won't guess": 18% of replies against 1%), over-explains what it is or is not when asked something light, and is flatter on good news (an exclamation mark in about a
# quarter of upbeat replies where the standard voice uses one in about half). A closing reminder, so the standard voice's prompt is untouched. It is the "voice" tail only (the app's Claude Haiku voice): the
# Gemini lite voice was not measured and keeps the "checkin" tail.
VOICE_REMINDER = (
    "\n\nREMINDER ON VOICE: never open a reply with doubt. Start with what you can see in what they told you, or with how it is for them. Show doubt in your wording (\"might\", \"could\", \"my guess is\"), "
    "not by announcing it: no \"I can't tell you\", \"I can't know\", \"I can't see anything about them\", \"I don't know why\", \"I'd be guessing\" or \"I won't guess\" about what someone else thinks or what will happen, and never "
    "as the first thing you say. Give a "
    "considered read grounded in what they said, labelled as a read, and promise nothing either way. A real limit (a link you can't open, data you were not given) gets a few words, once, later in the reply. "
    "Never explain at length what you are or are not. "
    "When they tell you what they are about to do or are in the middle of, ask one pointed question about it (this outranks the rule about not adding a question): the kind a friend who knows the subject would ask, "
    "specific to what they said and to what you know of them, with something concrete in it (\"squats or something lighter?\", \"which project are you diving into?\", \"how far are you going?\"). Never a bare "
    "\"what are you planning?\" and never how they feel about it. If they are wrapping up or saying goodbye, ask nothing. When they tell you something that hurt, open with the thing itself in plain words (\"Getting passed over for the lead role...\", \"A voicemail instead of a call...\", \"Going over the same person again and again...\"), never with a label for it: not \"That sounds like...\", \"That sounds exhausting\" or \"It sounds like...\" (when the rules above say to open with \"I hear you.\", do, and then go to the thing itself). Then say one thing they did not say, as a read, using their own details, and never comfort that would fit anyone: no \"that doesn't mean...\", \"it's not proof of...\", \"that's not the same as...\", no sayings about time or grief. Unless they are winding down, end with one specific question about what they told you, asked as a question (not \"I'm curious whether...\" or \"I'd like to know...\"), never about how they feel. "
    "Start every reply with the substance (\"I hear you.\" counts, when the rules above ask for it), never with their name, as an address or a greeting; use their name at most once in three replies, in the middle of a sentence. When they are upbeat or joking, open your reaction with an exclamation mark, once (\"Ha, nice!\", \"That's huge!\"); never when they are low or the topic is serious."
)
CHECKIN_REMINDER = (
    "- A CHECK-IN is due this reply (the CHECK-IN section above). It is not an optional follow-up and the follow-up rule does not apply to it: answer fully first, then END the reply with one short, warm "
    "question to them about how they are doing, in plain words, specific to what they went through earlier.\n"
)


def with_tail(text: str, tail: str = "") -> str:
    """`text` with the closing reminders for `tail` ("" none, "checkin" the check-in one only when a check-in is due, "all" the standing ones as well) added at its end, and, for any tail but "", the
    no-referral and no-state-question reminders when the prompt carries those rules, and for the "voice" tail the voice reminder last. Pure; any model's prompt."""
    referral = (REFERRAL_REMINDER if REFERRAL_MARKER in text else "") + (QUESTION_REMINDER if QUESTION_MARKER in text else "")
    if tail == "all":
        return text + STANDING_REMINDER + (CHECKIN_REMINDER if CHECKIN_MARKER in text else "") + referral
    if tail == "checkin":
        return text + (CHECKIN_ONLY_REMINDER if CHECKIN_MARKER in text else "") + referral
    if tail == "fuller":
        return text + (CHECKIN_ONLY_REMINDER if CHECKIN_MARKER in text else "") + FULLER_REMINDER + referral
    if tail == "voice":                       # what the app's Claude Haiku voice gets: the "checkin" tail, then the voice reminder last
        return text + (CHECKIN_ONLY_REMINDER if CHECKIN_MARKER in text else "") + referral + VOICE_REMINDER
    return text


def split_prompt(prompt: object, tail: str = "") -> Optional[Tuple[str, str]]:
    """(system, user) for a reply prompt built by build_voice_prompt, or None when it is not one (then it is sent whole, as it always was). The system part must end with the honesty rules,
    which is how the usual shape is recognised; a prompt that merely contains the word DATA is not split. `tail` adds closing reminders to the end of the user part: "checkin" only the check-in one (and only when a check-in is due), "all" the standing ones as well, "fuller" the check-in one and a reminder to match the depth of the question, "voice" the check-in one and
    the voice reminder (the app's Claude Haiku voice)."""
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
