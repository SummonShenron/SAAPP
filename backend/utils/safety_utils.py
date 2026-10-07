import datetime
import logging
import re
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.utils.emotion_checks import reply_body

logger = logging.getLogger("SASS Logger")

# Crisis / safety escalation. Sonic has real emotional machinery (reading the user's state, a ceiling on its
# own energy, matching what they need), but until now nothing distinguished "a rough day" from "I want to
# die". Anything that talks to people about painful things needs that distinction before it talks to
# strangers: the reply has to change in kind, not just in tone.
#
# The design principle: a stranger in crisis should get what a good friend would do, in the order a good
# friend would do it. Not a hotline number as a way to close the topic. The reply works through the real
# people in the person's life first (a "support ladder": immediate family, then friends, then extended
# family, then a professional they already have), one conversational question at a time, remembers the
# answers, helps them actually reach whoever they have (even to wording the text with them), and only when
# every one of those is exhausted does it hand them to a trained human line, as a warm handoff, staying with
# them. The one exception is imminent danger (a plan, means within reach, a time, or something already done):
# there a human line and emergency services come in immediately, alongside the person.
#
# How it works, end to end:
#   1. Detection runs on every message from two independent sources whose result is the higher of the two:
#      explicit first-person language (patterns below, which work even when the model call fails) and the
#      reasoner's own read of the message in context. The level is carried for the rest of the conversation
#      (safety_state, same checkpointer exemption as the emotional state), together with the ladder answers.
#   2. While a level is active the reply is written under a SAFETY block that outranks everything else in the
#      prompt (persona playfulness, tone matching, the wrap-up rule, grounding refusals).
#   3. The finished reply is checked mechanically, including that it does NOT reach for a crisis line before
#      the ladder is exhausted (unless they ask, or it is imminent), and that it DOES offer one when due.
#   4. A minimal event (who, level, when, never the message) is recorded so the operator can see it happened,
#      and the people they name are remembered in long-term memory for next time.
#
# Deliberately NOT here: any claim to be a clinician or a crisis service, diagnosis, or a canned script.

RISK_NONE = "none"
RISK_ELEVATED = "elevated"
RISK_ACUTE = "acute"
RISK_IMMINENT = "imminent"
_ORDER = {RISK_NONE: 0, RISK_ELEVATED: 1, RISK_ACUTE: 2, RISK_IMMINENT: 3}
VALID_RISK = set(_ORDER)

# Windows after which a raised level relaxes (imminent to acute to elevated to none). Long on purpose: a
# person who said this should not get a breezy tone again a few messages later.
IMMINENT_TURNS = 4
IMMINENT_HOURS = 3
ACUTE_TURNS = 8
ACUTE_HOURS = 6
ELEVATED_TURNS = 30
ELEVATED_HOURS = 12

# ---------------------------------------------------------------------------
# Explicit-language detection (first person, deliberately specific)
# ---------------------------------------------------------------------------

_ACUTE_PATTERNS = [
    r"\b(?:kill|killing|killed) myself\b",
    r"\bend(?:ing)? my (?:own )?life\b",
    r"\btake my (?:own )?life\b",
    r"\btaking my (?:own )?life\b",
    r"\bcommit(?:ting)? suicide\b",
    r"\b(?:i(?:'m| am)|i feel|feeling) suicidal\b",
    r"\b(?:thinking|thought|thoughts) (?:of|about) (?:suicide|killing myself|ending (?:it|my life))\b",
    r"\bsuicidal (?:thoughts?|ideation|urges?)\b",
    r"\b(?:going to|gonna|about to) (?:kill|hurt|harm|end) (?:myself|it all|my life)\b",
    r"\bhang(?:ing)? myself\b",
    r"\bshoot(?:ing)? myself\b",
    r"\bjump(?:ing)? (?:off|from) (?:a|the|my) (?:bridge|building|roof|balcony|cliff)\b",
    r"\boverdos(?:e|ing) (?:on purpose|myself|tonight|to die)\b",
    r"\b(?:i have|i've got|i made|i've made) a plan to (?:die|kill|end|hurt)\b",
    r"\bplan(?:ning)? (?:to|on) (?:kill|killing|end|ending) (?:myself|my life)\b",
]
# Already happening or about to: a plan, means within reach, a time, or something already done.
_IMMINENT_PATTERNS = [
    r"\b(?:i(?:'ve| have) (?:already )?(?:taken|swallowed|cut)|i (?:just |already )?(?:took|swallowed|cut))\b.{0,40}\b(?:pills|bunch|too many|all of|everything|myself|my wrists?)\b",
    r"\bi(?:'m| am) (?:standing|sitting) (?:on|at|by) (?:the )?(?:edge|bridge|roof|ledge|balcony|railing)\b",
    r"\b(?:i have|i've got|i got) (?:the |a )?(?:pills|gun|rope|razor|knife|pistol|rifle|noose)\b.{0,40}\b(?:ready|here|with me|in front of me|right here|next to me)\b",
    r"\bwriting (?:a |my )?(?:goodbye|suicide|final) (?:note|letter|message)\b",
    r"\bthis is (?:goodbye|my goodbye)\b",
    r"\bi(?:'m| am) about to (?:do it|end it|kill myself|hurt myself)\b",
]
_TIMEFRAME_RE = re.compile(r"\b(?:tonight|right now|this (?:evening|afternoon|morning)|in a (?:few|couple of) (?:minutes|hours)|as soon as|after (?:everyone|they) (?:leaves?|sleeps?|go))\b", re.IGNORECASE)
_MEANS_RE = re.compile(r"\b(?:pills|gun|rope|razor|knife|blade|pistol|rifle|noose|medications?|insulin|bridge|car exhaust)\b", re.IGNORECASE)
_ELEVATED_PATTERNS = [
    r"\bwant(?:ed)? to die\b",
    r"\bwish (?:i|that i) (?:was|were) dead\b",
    r"\bwish i (?:would|could) (?:just )?(?:die|disappear|not wake up|go to sleep and not wake up)\b",
    r"\bbetter off (?:dead|without me)\b",
    r"\b(?:no|not any) (?:reason|point) (?:to|in) (?:live|living|go on|going on|be here|being here)\b",
    r"\b(?:can'?t|cannot) (?:go on|keep going|do this|take (?:it|this)) (?:anymore|any more)\b",
    r"\bdon'?t want to (?:be here|live|exist|be alive|wake up)(?: anymore| any more)?\b",
    r"\bwant (?:it all|everything|all of it) to (?:stop|end)\b",
    r"\bself[- ]?harm(?:ing)?\b",
    r"\b(?:cut|cutting|hurt|hurting|harm|harming) myself\b",
    r"\bnobody would (?:care|notice|miss me)\b",
    r"\beveryone would be better off\b",
]
_ACUTE_RE = re.compile("|".join(_ACUTE_PATTERNS), re.IGNORECASE)
_IMMINENT_RE = re.compile("|".join(_IMMINENT_PATTERNS), re.IGNORECASE)
_ELEVATED_RE = re.compile("|".join(_ELEVATED_PATTERNS), re.IGNORECASE)
# "this bug is killing me", "I want to die lol, Monday": a humor marker AND a mundane trigger in the same
# sentence downgrades an ELEVATED match only. Explicit acute language is never excused by a joke.
_HUMOR_RE = re.compile(r"\b(?:lol|lmao|lmfao|rofl|haha+|hehe+|jk|kidding|joking)\b|[😂🤣😭💀]", re.IGNORECASE)
_MUNDANE_RE = re.compile(
    r"\b(?:bug|bugs|deploy|merge conflict|meeting|monday|mondays|exam|homework|traffic|boredom|bored|cringe|embarrass\w*|"
    r"hungry|tired|sleepy|standup|code|compiler|build|test|tests|laundry|taxes|spreadsheet|email|emails)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?\n])\s+")


def detect_risk_language(text: Optional[str]) -> str:
    """The risk level an explicit message implies, from first-person language alone: "imminent" for a plan,
    means within reach, a time, or something already done; "acute" for stated intent or active suicidal
    thoughts; "elevated" for wishing to be dead, self-harm urges or hopelessness about going on; else
    "none". High recall for the clear statements, with a narrow carve-out for obvious jokes about everyday
    annoyances."""
    if not text:
        return RISK_NONE
    level = RISK_NONE
    for sentence in _SENTENCE_SPLIT.split(text):
        if _IMMINENT_RE.search(sentence):
            return RISK_IMMINENT
        if _ACUTE_RE.search(sentence):
            if _TIMEFRAME_RE.search(sentence) or _MEANS_RE.search(sentence):
                return RISK_IMMINENT
            level = RISK_ACUTE
            continue
        if _ELEVATED_RE.search(sentence):
            joking = _HUMOR_RE.search(sentence) and _MUNDANE_RE.search(sentence)
            if not joking and _ORDER[level] < _ORDER[RISK_ELEVATED]:
                level = RISK_ELEVATED
    return level


def normalize_risk(raw: Any) -> str:
    """The reasoner's "risk" value if it is one we recognize, else "none" (never guess a risk level)."""
    value = str(raw if not isinstance(raw, dict) else raw.get("risk", "")).strip().lower()
    return value if value in VALID_RISK else RISK_NONE


# ---------------------------------------------------------------------------
# The support ladder: who in their real life can be with them, worked through in order
# ---------------------------------------------------------------------------

LADDER: List[Tuple[str, str]] = [
    ("immediate_family", "immediate family: a parent, a partner, a sibling, or anyone who lives with them"),
    ("friends", "friends: someone they trust who they could call, or who could come over"),
    ("extended_family", "extended family: an aunt, an uncle, a cousin or a grandparent"),
    ("professional", "someone they already see or could call: a therapist, a doctor, a counselor or a faith leader"),
]
_RUNG_KEYS = [key for key, _ in LADDER]
UNKNOWN, AVAILABLE, UNAVAILABLE = "unknown", "available", "unavailable"
_VALID_STATUS = {UNKNOWN, AVAILABLE, UNAVAILABLE}
_MAX_CONTACTS = 6
_CONTACT_MAX_CHARS = 120


def normalize_support(raw: Any) -> Tuple[Dict[str, str], str]:
    """From the reasoner's emotional_state reading: ({rung: "available"|"unavailable"}, contact text). Only
    answers this message actually gave are returned ("unknown" and anything unrecognized is dropped), and the
    contact is bounded and flattened to one line before it goes anywhere near a prompt or the database."""
    if not isinstance(raw, dict):
        return {}, ""
    support = raw.get("support")
    updates: Dict[str, str] = {}
    if isinstance(support, dict):
        for key in _RUNG_KEYS:
            value = str(support.get(key, "")).strip().lower()
            if value in (AVAILABLE, UNAVAILABLE):
                updates[key] = value
    contact = raw.get("contact")
    contact = re.sub(r"\s+", " ", contact).strip().strip("\"'")[:_CONTACT_MAX_CHARS] if isinstance(contact, str) else ""
    return updates, contact


def ladder_status(state: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Where the ladder stands: {"statuses": {rung: status}, "contacts": [...], "next": rung or None,
    "available": rung or None, "exhausted": bool}. `available` is a rung where they said they have someone
    (the job then is helping them reach that person); `exhausted` is True only when every rung has been
    asked and none has anyone."""
    support = (state or {}).get("support") or {}
    statuses = {key: (support.get(key) if support.get(key) in _VALID_STATUS else UNKNOWN) for key in _RUNG_KEYS}
    available = next((k for k in _RUNG_KEYS if statuses[k] == AVAILABLE), None)
    next_rung = next((k for k in _RUNG_KEYS if statuses[k] == UNKNOWN), None)
    exhausted = all(statuses[k] == UNAVAILABLE for k in _RUNG_KEYS)
    return {
        "statuses": statuses, "contacts": list((state or {}).get("contacts") or []),
        "next": next_rung, "available": available, "exhausted": exhausted,
    }


# ---------------------------------------------------------------------------
# Carrying the level (and the ladder) through the conversation
# ---------------------------------------------------------------------------

def _parse_time(value: Any) -> Optional[datetime.datetime]:
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def effective_risk(state: Optional[Dict[str, Any]], now: datetime.datetime) -> str:
    """The level in force right now: a raised level holds for a while, then relaxes one step at a time
    (imminent, acute, elevated, none). Computed fresh, never stored."""
    if not state:
        return RISK_NONE
    level = state.get("level")
    if level not in (RISK_IMMINENT, RISK_ACUTE, RISK_ELEVATED):
        return RISK_NONE
    updated = _parse_time(state.get("updated_at"))
    if updated is None:
        return RISK_NONE
    hours = max((now - updated).total_seconds() / 3600.0, 0.0)
    try:
        turns = max(int(state.get("turns_since", 0)), 0)
    except (TypeError, ValueError):
        return RISK_NONE
    if level == RISK_IMMINENT and turns <= IMMINENT_TURNS and hours <= IMMINENT_HOURS:
        return RISK_IMMINENT
    if level in (RISK_IMMINENT, RISK_ACUTE) and turns <= ACUTE_TURNS and hours <= ACUTE_HOURS:
        return RISK_ACUTE
    if turns <= ELEVATED_TURNS and hours <= ELEVATED_HOURS:
        return RISK_ELEVATED
    return RISK_NONE


def merge_safety_state(
    prior: Optional[Dict[str, Any]],
    level: str,
    now: datetime.datetime,
    support_updates: Optional[Dict[str, str]] = None,
    contact: str = "",
) -> Optional[Dict[str, Any]]:
    """Folds this message's level (and anything it said about the people in their life) into the carried
    state. A message at or above the level in force (re)starts the clock; anything lower only advances it,
    so a calm message right after a frightening one never resets the watch. The ladder answers persist
    across re-raises, and are only ever changed by an explicit answer."""
    prior_effective = effective_risk(prior, now)
    active = prior_effective != RISK_NONE
    raised = level != RISK_NONE and _ORDER[level] >= _ORDER[prior_effective]
    if not raised and not active:
        return None

    support = dict((prior or {}).get("support") or {})
    contacts = list((prior or {}).get("contacts") or [])
    for key, status in (support_updates or {}).items():
        if key in _RUNG_KEYS and status in (AVAILABLE, UNAVAILABLE):
            support[key] = status
    # A contact describes a person they HAVE; a description that came with a "no one" answer is not one.
    if contact and contact not in contacts and AVAILABLE in (support_updates or {}).values():
        contacts.append(contact)
    contacts = contacts[-_MAX_CONTACTS:]

    if raised:
        # "escalated": this message brought the level UP (or started it). Saying the same thing again while the
        # level is already in force restarts the clock but is not a new disclosure, so the reply carries on with
        # the ladder instead of asking the opening safety question again.
        state: Dict[str, Any] = {
            "level": level, "updated_at": now.isoformat(), "turns_since": 0, "escalated": _ORDER[level] > _ORDER[prior_effective],
        }
    else:
        state = {**prior, "turns_since": int(prior.get("turns_since", 0) or 0) + 1, "escalated": False}
    if support:
        state["support"] = support
    if contacts:
        state["contacts"] = contacts
    return state


# ---------------------------------------------------------------------------
# What the reply must do
# ---------------------------------------------------------------------------

_RESOURCES_RE = re.compile(
    r"\b988\b|\b741741\b|\b911\b|findahelpline|crisis (?:line|lifeline|text|hotline|counsel)|suicide (?:&|and) crisis|"
    r"emergency (?:services|room|number)|\b999\b|\b112\b|\bhot ?line\b|\blifeline\b|\bhelpline\b",
    re.IGNORECASE,
)
_ASKED_FOR_LINE_RE = re.compile(
    r"\b988\b|hot ?line|crisis (?:line|text|counsel)|lifeline|helpline|number to call|someone to call|call (?:a|the) (?:line|number)",
    re.IGNORECASE,
)


def has_crisis_resource(text: str) -> bool:
    return bool(_RESOURCES_RE.search(text or ""))


# Appended in code (never left to the model) when a reply that is due to offer a human line still names no
# real resource after its one rewrite: the one element that must not depend on the model getting it right.
CRISIS_RESOURCE_LINE = (
    "\n\nIf any part of you might act on this tonight, I'd really like a person on the line with you right now. "
    "In the US you can call or text 988 and someone answers within minutes, or call 911 if you're in "
    "immediate danger; anywhere else, findahelpline.com will find you a line. I'll be right here the whole "
    "time you do."
)


def resources_already_given(history_text: str) -> bool:
    """Whether an earlier reply in this conversation already offered a crisis resource, so it isn't
    recited again every turn."""
    return has_crisis_resource(history_text)


_SAFETY_HEADER = (
    "\n\nSAFETY (this outranks everything else in this prompt, including matching their tone, playfulness, "
    "brevity, the rule about wrapping up conversations, follow-ups, and any grounding or refusal rule): "
)
_FORMAT_RULES = (
    "- Plain, warm prose. No lists, no emoji, no exclamation marks, no jokes, no sign-off, no hint that the "
    "conversation is ending, and no <<<FOLLOW_UP>>> tag.\n"
)
_PRESENCE = (
    "- Respond to their own words, in your own warm voice. Take it seriously and don't flinch from it. "
    "Don't diagnose, lecture, recite a script, or make it about you being an AI.\n"
)
_ASK_DIRECT_ACUTE = (
    "- Ask about their safety directly and plainly: whether they are thinking about ending their life, or, "
    "if they have already said so, whether they are safe right now and whether there is anything nearby they "
    "could use to hurt themselves (if there is, ask them to put distance between themselves and it, in "
    "another room or with someone else). Asking directly does not put the idea in their head.\n"
)
_ASK_DIRECT_ELEVATED = (
    "- Ask gently and directly whether they have been having thoughts of suicide or of hurting themselves "
    "(asking does not plant the idea).\n"
)
_NEXT_RUNG_TEXT = {
    "immediate_family": "ask about their immediate family: a parent, a partner, a sibling, or anyone who lives with them. Is there someone like that they could reach, or who could be with them tonight?",
    "friends": "ask about friends: is there a friend they trust who they could call, or who could come over?",
    "extended_family": "ask about extended family: an aunt, an uncle, a cousin or a grandparent they could reach.",
    "professional": "ask about anyone they already see or could call: a therapist, a doctor, a counselor, or a faith leader.",
}
_HANDOFF_FIRST = (
    "- Make sure they know a trained human can be on the line with them in minutes (in the US, calling or "
    "texting 988, or 911 if they are in immediate danger; elsewhere, findahelpline.com). Say it as a warm "
    "handoff in your own voice, never as a brush-off or a stock line: one or two sentences, that you'd "
    "rather they have a person with them than only you, and that you will stay right here in this "
    "conversation while they reach out. It is never a way to end or hand off the conversation, and it is "
    "not the whole reply.\n"
)
_HANDOFF_REPEAT = (
    "- You have already pointed them to a human line earlier in this conversation. Don't recite it again "
    "unless they are in immediate danger or ask. Stay with them, check in on how they are right now, and "
    "on whether anyone has been reached.\n"
)
_IMMINENT_BLOCK = (
    "the person may be about to hurt themselves or end their life, or may already have. Your only job right "
    "now is to keep them alive and not alone: be completely present, and completely un-generic.\n"
    + _PRESENCE
    + "- Ask plainly whether they are safe right now, and whether they have done anything or have anything "
    "within reach. Ask them to move away from it, to another room, or to hand it to someone.\n"
    "- Get a person to them now: ask who is in the house or nearby, or who they can call to come right now, "
    "naming someone close if you know one, and offer to word the text or the call with them this minute.\n"
    "{handoff}"
    "- If they have already taken something or hurt themselves, or cannot keep themselves safe, tell them to "
    "call 911 now, and that you are staying with them.\n"
    + _FORMAT_RULES
)


def _status_line(rung: str, label: str, info: Dict[str, Any]) -> str:
    status = info["statuses"][rung]
    name = label.split(":")[0]
    if status == AVAILABLE:
        named = f" (you know: {'; '.join(info['contacts'])})" if info["contacts"] else ""
        return f"  - {name}: they said there is someone{named}"
    if status == UNAVAILABLE:
        return f"  - {name}: they said no one they can reach"
    return f"  - {name}: not asked yet"


def _ladder_block(level: str, fresh: bool, info: Dict[str, Any], people: List[str], resources_given: bool, line_ok: bool) -> str:
    """The SAFETY block for an acute or elevated level: work through the real people first."""
    lead = (
        "the person may be thinking about ending their life or hurting themselves. Your job is to be a "
        "steady, caring presence and to work through, the way a good safety plan does, who in their real life "
        "can be with them. Before any crisis line, you go through the real people."
        if level == RISK_ACUTE else
        "the person may be having thoughts of not wanting to be alive or of hurting themselves. Be present "
        "and unhurried, someone who shows up rather than reads a script, and start working out who in their "
        "real life they can lean on. Before any crisis line, you go through the real people."
    )
    parts = [lead + "\n", _PRESENCE]
    if fresh:
        parts.append(_ASK_DIRECT_ACUTE if level == RISK_ACUTE else _ASK_DIRECT_ELEVATED)
    parts.append("THEIR PEOPLE, so far (what they have told you in this conversation):\n")
    parts.extend(_status_line(rung, label, info) + "\n" for rung, label in LADDER)
    if people:
        parts.append("  - from what you remember about them from before: " + "; ".join(people) + "\n")
        parts.append("    (use these names; ask whether one of them is around, by name)\n")

    if info["available"]:
        parts.append(
            "- NEXT STEP: they have someone. Help them actually reach that person now: ask whether they can "
            "call or text them right now, offer to think through what to say or to word the message with them, "
            "and say you'll be right here for it. If they hesitate, ask gently what makes it hard, and whether "
            "someone else would be easier.\n"
        )
    elif info["exhausted"]:
        parts.append(
            "- NEXT STEP: they have told you there is no one in any of those groups they can reach. Take that "
            "with them without judgment, and say you are glad they told you. This is the moment for a human "
            "line, and only now:\n" + (_HANDOFF_REPEAT if resources_given else _HANDOFF_FIRST)
        )
    elif info["next"]:
        parts.append(
            f"- NEXT STEP: {_NEXT_RUNG_TEXT[info['next']]} One question, in your own words, with the reason "
            "(because you would rather they had someone who can actually be there), and not as a checklist. If "
            "their own words point to a different group first, follow them.\n"
        )
    if not info["exhausted"] and not line_ok:
        parts.append(
            "- Do NOT mention any crisis line or hotline in this reply: they have not asked, and the real "
            "people come first. If a group has no one (no one, estranged, too far, can't or won't), take that "
            "with them without judgment and don't push; the next group comes after.\n"
        )
    elif not info["exhausted"] and line_ok and not resources_given:
        parts.append(_HANDOFF_FIRST)
    parts.append(_FORMAT_RULES)
    return "".join(parts)


_CARRIED_NOTE = (
    "earlier in this conversation they said something that suggested they may be at risk of hurting "
    "themselves. Keep your register gentle and steady, and no jokes or breezy sign-offs. If the conversation "
    "is winding down, don't close it cheerfully: check in on how they are doing now, and on whether the "
    "person they could lean on is around. Say you're glad they told you. If they bring it up again, take it "
    "as seriously as the first time.\n"
)

_FOLLOW_UP_RE = re.compile(r"<<<FOLLOW_UP:", re.IGNORECASE)
_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF☀-➿]")
_BREEZY_RE = re.compile(
    r"\b(?:have a (?:great|good|nice|wonderful) (?:night|day|evening|one|weekend)|enjoy your (?:evening|night|day)|"
    r"talk (?:to you )?(?:soon|tomorrow|later)|catch you (?:later|tomorrow)|sleep well|anything else)\b",
    re.IGNORECASE,
)


@dataclass
class SafetyPlan:
    """Everything the app needs for one turn: the block to put in the prompt, and how to judge the reply."""
    level: str
    fresh: bool
    context: str = ""
    exhausted: bool = False
    available: Optional[str] = None
    resources_given: bool = False
    asked_for_line: bool = False
    line_allowed: bool = field(default=False)   # may the reply mention a human line at all?
    line_required: bool = field(default=False)  # must it?

    @property
    def active(self) -> bool:
        return self.level != RISK_NONE

    def reply_issue(self, reply: str) -> Optional[str]:
        """What a reply written under this plan got wrong, or None when it is fine."""
        if not self.active or not reply:
            return None
        if _FOLLOW_UP_RE.search(reply):
            return "it ends with a follow-up suggestion chip"
        body = reply_body(reply)
        if _EMOJI_RE.search(body):
            return "it uses emoji"
        if body.count("!") >= 2:
            return "it is exclamatory and upbeat"
        if _BREEZY_RE.search(body):
            return "it closes the conversation in a breezy way"
        mentions_line = has_crisis_resource(body)
        if mentions_line and not self.line_allowed:
            return "it points them to a crisis line before the people in their life have been worked through"
        if self.fresh and "?" not in body:
            return "it never asks them directly about their safety"
        if self.line_required and not mentions_line:
            return "it offers no real human line (such as 988 in the US) when one is due"
        return None

    def needs_resource_line(self, reply: str) -> bool:
        """True when a line is due but the (already revised) reply still names none: the last-resort
        sentence is then added in code."""
        return self.line_required and not has_crisis_resource(reply)


def plan_safety_turn(
    level: str,
    fresh: bool,
    safety_state: Optional[Dict[str, Any]],
    history_text: str,
    user_message: str,
    people: Optional[List[str]] = None,
) -> SafetyPlan:
    """Decides what this turn's reply must do. `people` are the names Sonic remembers from earlier
    conversations (see remembered_support_people)."""
    if level == RISK_NONE:
        return SafetyPlan(level=RISK_NONE, fresh=False)
    info = ladder_status(safety_state)
    given = resources_already_given(history_text)
    asked = bool(_ASKED_FOR_LINE_RE.search(user_message or ""))
    plan = SafetyPlan(level=level, fresh=fresh, exhausted=info["exhausted"], available=info["available"],
                      resources_given=given, asked_for_line=asked)

    if level == RISK_IMMINENT:
        plan.line_allowed = True
        plan.line_required = not given
        plan.context = _SAFETY_HEADER + _IMMINENT_BLOCK.format(handoff=_HANDOFF_REPEAT if given else _HANDOFF_FIRST)
        return plan

    plan.line_allowed = info["exhausted"] or asked or given
    plan.line_required = (info["exhausted"] or asked) and not given
    if not fresh and not info["next"] and not info["available"] and not info["exhausted"]:
        plan.context = _SAFETY_HEADER + _CARRIED_NOTE
        return plan
    plan.context = _SAFETY_HEADER + _ladder_block(level, fresh, info, list(people or []), given, line_ok=plan.line_allowed)
    return plan


def build_safety_revision_prompt(original_prompt: str, draft: str, issue: str) -> Tuple[str, str]:
    """(prompt, reason) for the single rewrite of a reply that missed the safety requirements."""
    reason = (
        f"this person may be at risk and {issue}. Write it again under the SAFETY block above: warm, plain "
        "and present, following its NEXT STEP, and nothing breezy."
    )
    note = (
        "\n\nREVISION NOTE (never mention this note or that you are revising): your draft reply to the "
        f"message above was rejected because {reason}\n\nREJECTED DRAFT:\n---\n{draft}\n---\n\n"
        "Now write the reply again.\n"
    )
    return original_prompt + note, reason


# ---------------------------------------------------------------------------
# Remembering their people (so next time Sonic can ask "is Kayla around?")
# ---------------------------------------------------------------------------

SUPPORT_FACT_SOURCE = "safety_support"
_RUNG_PLAIN = {
    "immediate_family": "immediate family", "friends": "friends", "extended_family": "extended family",
    "professional": "a professional",
}


def remember_support(username: Optional[str], rung: str, status: str, contact: str) -> None:
    """Writes a person they HAVE to long-term memory, so a later conversation starts from it (and can ask
    "is Kayla around?"). A "no one" answer is deliberately not stored: it is true of one bad night, circumstances
    change, and a wrong negative would make a later conversation skip someone who is there; it lives only in the
    conversation's ladder. Blocking (it embeds and may ask a model to judge duplicates): call it off the event
    loop. Never raises."""
    if not username or status != AVAILABLE:
        return
    try:
        from backend.utils.memory_utils import save_user_fact

        if contact:
            fact = f"A person they can turn to when things get dark: {contact}."
        else:
            fact = f"Has {_RUNG_PLAIN.get(rung, 'someone')} they can turn to when things get dark."
        save_user_fact(username, fact, category="relationship", source=SUPPORT_FACT_SOURCE, confidence=0.9)
    except Exception:
        logger.warning("[safety] could not remember a support contact.", exc_info=True)


def remembered_support_people(username: Optional[str]) -> List[str]:
    """What they have told Sonic, in earlier conversations, about who they can turn to (facts saved by
    remember_support), as short phrases for the prompt. Empty on any problem."""
    if not username:
        return []
    try:
        from backend.utils.memory_utils import load_user_facts

        facts = [f for f in load_user_facts(username, category="relationship") if f.source == SUPPORT_FACT_SOURCE and f.active]
        return [re.sub(r"\s+", " ", f.fact).strip()[:160] for f in facts[-6:]]
    except Exception:
        logger.warning("[safety] could not read remembered support contacts.", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# A record that it happened (never the message itself)
# ---------------------------------------------------------------------------

SAFETY_EVENTS_COLLECTION = "safety_events"
SAFETY_EVENTS_RETENTION_SECONDS = 180 * 24 * 3600
_index_ready = False


# What happened. Every kind is a fact about the layer's own behavior, never about what anyone said:
KIND_RISK_RAISED = "risk_raised"          # a risk level was raised (level, which detector)
KIND_RISK_TURN = "risk_turn"              # a reply was delivered while a risk level was in force (the denominator for the rates)
KIND_REPLY_REVISED = "reply_revised"      # the safety check rejected a draft and it was rewritten once
KIND_LINE_IN_REPLY = "line_in_reply"      # the delivered reply named a real human line
KIND_LAST_RESORT_LINE = "last_resort_line"  # the model still left the line out after a rewrite, so code added it
KIND_LADDER_ANSWER = "ladder_answer"      # they answered a rung of the ladder (which rung, available or not; never who)
KIND_OUTAGE_FALLBACK = "outage_fallback"  # the model was down on a risk turn, so a fixed human-line reply was shown
EVENT_KINDS = {
    KIND_RISK_RAISED, KIND_RISK_TURN, KIND_REPLY_REVISED, KIND_LINE_IN_REPLY,
    KIND_LAST_RESORT_LINE, KIND_LADDER_ANSWER, KIND_OUTAGE_FALLBACK,
}


def log_safety_event(
    username: Optional[str],
    level: Optional[str],
    source: str,
    kind: str = KIND_RISK_RAISED,
    rung: Optional[str] = None,
    status: Optional[str] = None,
) -> None:
    """Records one fact about the safety layer: who, which kind of event, the level in force, which detector,
    when. Only a ladder answer carries more (which rung, and whether someone is available), and both of those
    are checked against fixed lists, so no free text of any kind can reach the record: not the message, and not
    a contact's name. Never raises: this must not be able to break a reply."""
    global _index_ready
    logger.warning("[safety] kind=%s level=%s source=%s user=%s", kind, level, source, username)
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is None:
            return
        if not _index_ready:
            db[SAFETY_EVENTS_COLLECTION].create_index("at", expireAfterSeconds=SAFETY_EVENTS_RETENTION_SECONDS)
            _index_ready = True
        doc = {
            "username": username,
            "kind": kind if kind in EVENT_KINDS else KIND_RISK_RAISED,
            "level": level if level in _ORDER else "unknown",
            "source": source if isinstance(source, str) and len(source) <= 32 else "unknown",
            "at": datetime.datetime.now(timezone.utc),
        }
        if rung in _RUNG_KEYS:
            doc["rung"] = rung
        if status in _VALID_STATUS:
            doc["status"] = status
        db[SAFETY_EVENTS_COLLECTION].insert_one(doc)
    except Exception:
        logger.warning("[safety] could not record the safety event.", exc_info=True)


def record_safety_turn(
    username: Optional[str], level: str, revised: bool, line_in_reply: bool, last_resort_line: bool
) -> None:
    """One call at the end of a reply delivered while a risk level was in force: the turn itself (so rates have
    a denominator), plus whether the safety check had to rewrite the draft, whether a human line reached the
    person, and whether code had to add it because the model would not. Blocking (database writes), so call it
    off the event loop. Never raises."""
    log_safety_event(username, level, "reply", KIND_RISK_TURN)
    if revised:
        log_safety_event(username, level, "reply", KIND_REPLY_REVISED)
    if line_in_reply:
        log_safety_event(username, level, "reply", KIND_LINE_IN_REPLY)
    if last_resort_line:
        log_safety_event(username, level, "reply", KIND_LAST_RESORT_LINE)
