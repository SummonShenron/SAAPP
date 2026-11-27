"""Things Sonic can do for someone who is low besides reflecting them back (2026-11-23 mirroring diagnosis).

WHY. On Jack's real chats, replies to a low message that only handed his words back went from about 6% a week in October to 11-16% in November, and "I hear you" opened 28% of them.
Part of the cause was the prompt itself (reflect their words back, open with "I hear you", take more room), and part was what is LEFT when someone is low: Sonic's own record, habits
and leanings are switched off (emotion_utils.own_material_tier), advice is off for venting, "people often" is off, referral is off. A long reply with almost nothing allowed in it fills up
with restating. These are moves that ARE right for a low moment, each resting on something true that the code finds first, so the model is never asked to invent one:

- push_back: they said something harsh about themselves ("I'm a failure", "I can't do anything right"). Disagree, once, with one specific true thing from what they have told you
  (a dated or finished step, never a vague one).
- pattern: one of the patterns memory has drawn from many of their own facts, sharing words with this message, said tentatively as something noticed. Never a clinical-sounding one.
- company: something they like (their own interests), offered as a thing to do together for a bit, instead of a check-in. Only when they want to be heard or distracted.

At most one move a turn, chosen by code in that order, and a kind is not used again within MOVE_GAP replies (the mark on Sonic's reply). Never in a safety context or a closing message,
and never when they are only frustrated. Pure: the caller loads the facts and interests.
"""
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

MOVE_MARK = "low_move"
MOVE_GAP = 4
KINDS = ("push_back", "pattern", "read", "company")
READ_MIN_WORDS = 12            # a read needs a situation to be a read OF: a few words give it nothing to stand on
LOW_VALENCES = ("low", "distressed")
MAX_EVIDENCE = 3
MAX_INTERESTS = 3
MIN_SHARED_WORDS = 2

HARSH_SELF_RE = re.compile(
    r"\bi(?:'|’)?m (?:such )?(?:a |an )?(?:failure|loser|idiot|mess|disappointment|joke|burden|fraud|waste|pathetic|worthless|useless|stupid|hopeless|unlovable|broken|not good enough|never going to)\b"
    r"|\bi (?:always|keep) (?:mess|screw|fuck|ruin)\w*\b|\bi (?:can(?:'|’)?t|cannot|never) do anything right\b|\bi(?:'|’)?ll never (?:be|get|find|amount)\b"
    r"|\bnobody (?:likes|wants|cares about|would want) me\b|\bwhat(?:'|’)?s wrong with me\b|\bi (?:hate|can(?:'|’)?t stand) myself\b|\bi ruin everything\b|\bi(?:'|’)?m not (?:smart|good|enough)\b"
    # Widened 2026-11-27 (push_back fired once in five days): the same judgment said as being the problem, too much, or deserving it.
    r"|\bi(?:'|’)?m (?:just )?(?:the problem|too much|hard to love|so dumb|dumb|an embarrassment|embarrassing|bad at (?:this|everything|people|life))\b"
    r"|\b(?:no one|nobody) (?:likes|wants|cares about|would want|could love) me\b|\beveryone (?:leaves|hates me|gets tired of me)\b|\bi deserve (?:this|it|to be alone)\b"
    r"|\bi (?:always|never) get (?:it|this|anything|things) (?:wrong|right)\b",
    re.IGNORECASE,
)
# A pattern note that reads like a diagnosis is not something to say to someone who is hurting.
_CLINICAL_RE = re.compile(r"\b(?:anxiety|anxious|criticism|critical|depress\w*|cycle of|trauma\w*|disorder|attachment|avoidan\w*|insecur\w*|self-worth|validation|codependen\w*|addict\w*|alcohol|drink\w*)\b", re.IGNORECASE)
_SENSITIVE_RE = re.compile(r"\b(?:alcohol|drink\w*|crav\w*|relapse|sober|therap\w*|medicat\w*|diagnos\w*|suicid\w*|self-harm|breakup|broke up|divorce|died|death|grief|debt)\b", re.IGNORECASE)
# Not a hobby to share: a plan or an appointment ("plans to build a portfolio website", "a planned visit with Kelsey on November 20"). Replayed 2026-11-23, the model rightly ignored both.
_PLAN_RE = re.compile(r"\d|\b(?:plan|plans|planned|planning|scheduled|appointment|visit|meeting|interview|deadline|intends?|aims?)\b"
                      r"|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", re.IGNORECASE)
_SPECIFIC_RE = re.compile(r"\d|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b")
_EVIDENCE_CATEGORIES = ("career", "project", "goal", "identity")
_STOP = frozenset("that this with have been just like what your they them there their about from would could should really still very much when then than into also some more were will jack user feel feels feeling things thing because being doing going want".split())


def harsh_about_self(message: str) -> bool:
    """True when the message says something harsh about the person themselves. Pure."""
    return bool(HARSH_SELF_RE.search(message or ""))


def _text(fact: Any) -> str:
    value = fact.get("fact") if isinstance(fact, dict) else getattr(fact, "fact", None)
    return " ".join(str(value or "").split())


def _get(fact: Any, key: str, default: Any = None) -> Any:
    return fact.get(key, default) if isinstance(fact, dict) else getattr(fact, key, default)


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{4,}", (text or "").lower()) if w not in _STOP}


def evidence_facts(facts: Sequence[Any]) -> List[str]:
    """Up to MAX_EVIDENCE true, specific things they have done: a finished goal, or a career/project/goal/identity fact with a date or number. Most recently told first, nothing sensitive. Pure."""
    found = []
    for fact in facts or []:
        text = _text(fact)
        if not text or not _get(fact, "active", True) or _get(fact, "category") not in _EVIDENCE_CATEGORIES or _SENSITIVE_RE.search(text):
            continue
        if _get(fact, "goal_status") == "achieved" or _SPECIFIC_RE.search(text):
            found.append((str(_get(fact, "created_at") or ""), text))       # when they told it, not when a cleanup last touched it
    found.sort(reverse=True)
    return [text for _at, text in found[:MAX_EVIDENCE]]


def matching_pattern(message: str, facts: Sequence[Any]) -> Optional[str]:
    """The pattern note (source "pattern") sharing the most words with the message, at least MIN_SHARED_WORDS, never a clinical-sounding one. Pure."""
    said = _words(message)
    best, best_shared = None, 0
    for fact in facts or []:
        text = _text(fact)
        if _get(fact, "source") != "pattern" or not text or not _get(fact, "active", True) or _CLINICAL_RE.search(text):
            continue
        shared = len(said & _words(text))
        if shared >= MIN_SHARED_WORDS and shared > best_shared:
            best, best_shared = text, shared
    return best


def recent_kinds(recent_messages: Iterable[Any], gap: int = MOVE_GAP) -> set:
    """The move kinds Sonic's last `gap` replies used (their MOVE_MARK). Pure."""
    replies = [m for m in recent_messages or [] if getattr(m, "type", "") == "ai"][-gap:]
    return {str((getattr(m, "additional_kwargs", None) or {}).get(MOVE_MARK)) for m in replies if (getattr(m, "additional_kwargs", None) or {}).get(MOVE_MARK)}


def available(message: str, state: Optional[Dict[str, Any]], *, facts: Sequence[Any], interests: Sequence[str], recent_messages: Iterable[Any], risk_active: bool, closing: bool,
              support_mode: Optional[str] = None, reach: str = "default") -> Dict[str, Dict[str, Any]]:
    """Every move this turn could make, by kind, in the default order (push_back, pattern, read, company). {} when none applies. Pure.

    2026-11-27: "company" (their interests, offered as "want to...?") was 16 of 18 moves in five days and the model ignored it; it is now only for someone who asked for a distraction, and
    never while they have asked to be just listened to. "read" is new: one sentence of Sonic's own view of the situation, which is neither advice nor their words handed back. It is left out
    in "just listen" mode and with the reach dial at "none"."""
    state = state or {}
    if risk_active or closing or not state.get("read_this_turn") or state.get("lift_this_turn") or str(state.get("valence")) not in LOW_VALENCES:
        return {}
    used = recent_kinds(recent_messages)
    found: Dict[str, Dict[str, Any]] = {}
    if "push_back" not in used and harsh_about_self(message):
        evidence = evidence_facts(facts)
        if evidence:
            found["push_back"] = {"kind": "push_back", "items": evidence}
    if "pattern" not in used:
        pattern = matching_pattern(message, facts)
        if pattern:
            found["pattern"] = {"kind": "pattern", "items": [pattern]}
    if ("read" not in used and support_mode != "listen" and reach != "none" and state.get("need") in ("venting", None, "none")
            and len(str(message or "").split()) >= READ_MIN_WORDS and not harsh_about_self(message)):
        found["read"] = {"kind": "read", "items": []}
    if "company" not in used and support_mode != "listen" and state.get("need") == "distraction":
        liked = [i for i in interests or [] if i and not _SENSITIVE_RE.search(i) and not _PLAN_RE.search(i)][-MAX_INTERESTS:]
        if liked:
            found["company"] = {"kind": "company", "items": liked}
    return found


def choose(message: str, state: Optional[Dict[str, Any]], *, facts: Sequence[Any], interests: Sequence[str], recent_messages: Iterable[Any], risk_active: bool, closing: bool,
           support_mode: Optional[str] = None, reach: str = "default", prefer: Optional[Sequence[str]] = None) -> Optional[Dict[str, Any]]:
    """{"kind", "items"} for this turn's move, or None. `prefer` is the kinds in the order this person has responded to best (backend/utils/what_lands.py order); the first of them that is
    available wins, and without it the default order stands. Pure."""
    found = available(message, state, facts=facts, interests=interests, recent_messages=recent_messages, risk_active=risk_active, closing=closing, support_mode=support_mode, reach=reach)
    for kind in list(prefer or []) + list(KINDS):
        if kind in found:
            return found[kind]
    return None


_BLOCKS = {
    "push_back": (
        "\nSOMETHING TO PUSH BACK WITH: they just said something harsh about themselves. Do not agree with it, and do not argue at length either. Once, gently and plainly, say it isn't the whole "
        "truth, using ONE of these true things they have told you (pick the one that answers what they said best; never list them, never quote their harsh words back):\n{items}\n"
    ),
    "pattern": (
        "\nSOMETHING YOU HAVE NOTICED OVER TIME (drawn from many things they have told you): {items}\nIf it bears on what they just said, say it in one sentence as your own observation, "
        "tentatively (\"I've noticed...\"), as something about them that is true and maybe useful right now, not as a diagnosis or a lesson. If it doesn't fit this moment, leave it out.\n"
    ),
    "read": (
        "\nYOUR OWN READ: when you have answered what they said, add ONE sentence that says what you make of the situation: what you think is going on, or what stands out to you, as your "
        "own view (\"I think...\", \"What stands out to me is...\"). It is not advice (nothing they should do), not a summary of what they said, and not a feeling of yours. If you see it "
        "differently from how they put it, you may say so once, kindly.\n"
    ),
    "company": (
        "\nSOMETHING TO OFFER INSTEAD OF A CHECK-IN: things they have told you they are into:\n{items}\nWhen you have answered what they said, you may offer ONE of these as something to do or "
        "talk about together for a bit (\"want to...?\"), as company, not as a fix or a distraction from their feelings. Not if they want to keep talking about what hurts.\n"
    ),
}


def build_block(move: Optional[Dict[str, Any]]) -> str:
    """The reply prompt's section for the move, or "". Pure."""
    if not move or move.get("kind") not in _BLOCKS:
        return ""
    items = move.get("items") or []
    if move["kind"] == "read":
        return _BLOCKS["read"]
    if move["kind"] == "pattern":
        return _BLOCKS["pattern"].format(items=items[0] if items else "")
    return _BLOCKS[move["kind"]].format(items="\n".join(f"- {item}" for item in items))
