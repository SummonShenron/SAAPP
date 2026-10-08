"""Encouragement for someone who has just voiced doubt in themselves, built only from evidence.

"You've been through this before and came out the other side" helps, but it is credible only because it is true of the
person. Sonic has no past of its own to offer, so it never says it has been through anything. What it CAN truthfully
point at, and only when it actually has it:

1. Something real this person already did: a goal or project their memory records as achieved.
2. Something they got through earlier in THIS conversation ("that worked", "fixed it").
3. A plain truth that what they describe is common with this kind of work, with no promise about the outcome.

It is offered, not forced: the model may add ONE calm, specific sentence after it has acknowledged what they said, or
skip it. It is silent whenever a quiet reply matters more (a safety context, acute distress, a closing message, grounded
KB answers) and rate-limited, because repeated encouragement turns into cheerleading. The reply is then checked
(`encouragement_reply_issue`) for the ways encouragement goes wrong: hype, minimizing, promising an outcome, "I believe
in you", and inventing a record the person doesn't have.

This module only DECIDES and WORDS; the facts it reads belong to this one user and go only into this user's prompt.
"""
import re
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional

ENCOURAGEMENT_MARK = "encouragement_offered"
ENCOURAGEMENT_MIN_GAP = 12  # messages (both sides) between offers
_ALLOWED_ROUTES = {"conversational", "tool_output", "web"}
_ACUTE_INTENSITY = 0.6      # a distressed reading at or above this gets listening, not a pep point

SELF_DOUBT_RE = re.compile(
    r"\bi(?:'m| am) (?:so |really |just |completely |totally )?(?:bad|terrible|awful|useless|hopeless|dumb|stupid|an idiot) (?:at|with)\b"
    r"|\bi (?:can'?t|cannot) (?:do|figure|get|make|understand|seem to)\b[^.!?\n]{0,25}\b(?:this|it|anything|work|right)\b"
    r"|\bnothing (?:i (?:do|try) )?(?:works|is working|ever works)\b"
    r"|\bi(?:'ll| will) never (?:get|figure|finish|understand|learn|be able)\b"
    r"|\bi(?:'m| am) not (?:smart|good|cut out|talented|capable)\b"
    r"|\bi (?:don'?t|do not) know what i(?:'m| am) doing\b"
    r"|\bi suck at\b|\bwhy am i (?:so )?(?:bad|slow|dumb|stupid)\b|\bi(?:'m| am) (?:such )?a (?:failure|fraud|joke)\b"
    r"|\bi(?:'m| am) (?:in )?over my head\b",
    re.IGNORECASE,
)
_RESOLVED_RE = re.compile(
    r"\b(?:that|it|this) (?:worked|did it|fixed it|solved it|fixed (?:the|my) )|\bit works now\b|\bfixed it\b|\bgot it working\b"
    r"|\bthat'?s fixed\b|\bit'?s (?:fixed|working)\b|\bsolved it\b|\bthat did the trick\b|\bworks now\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "this", "that", "with", "have", "from", "they", "what", "when", "where", "which", "there", "their", "about", "would",
    "could", "should", "just", "like", "been", "into", "than", "then", "them", "were", "will", "your", "make", "made",
    "need", "want", "work", "working", "works", "thing", "things", "really", "still", "even", "some", "much", "very",
}


def has_self_doubt(message: str) -> bool:
    """Whether the message voices doubt in the person's own ability ("I'm so bad at this", "nothing works", "I'll never
    get this"): the moment evidence helps most. A cheap gate, checked before anything is read from a database."""
    return bool(SELF_DOUBT_RE.search(message or ""))


def _content_words(text: str) -> set:
    return {w for w in re.findall(r"[a-z][a-z0-9_+#.-]{3,}", (text or "").lower()) if w not in _STOPWORDS}


def _clip(text: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, list):
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content or "")


def _is_human(message: Any) -> bool:
    return getattr(message, "type", "") == "human" or type(message).__name__ == "HumanMessage"


def session_evidence(recent_messages: Iterable[Any]) -> Optional[Dict[str, str]]:
    """Something they got through earlier in this conversation: the newest user message that confirms a fix, paired with
    the problem they had raised just before it. Nothing from beyond this conversation."""
    messages = list(recent_messages)
    # The newest message is the one voicing doubt now, so only earlier ones count.
    for i in range(len(messages) - 2, -1, -1):
        if _is_human(messages[i]) and _RESOLVED_RE.search(_text(messages[i])):
            for j in range(i - 1, -1, -1):
                if _is_human(messages[j]):
                    problem = _text(messages[j])
                    if problem.strip():
                        return {"kind": "session", "what": _clip(problem)}
            return None
    return None


def record_evidence(message: str, facts: Iterable[Any]) -> Optional[Dict[str, str]]:
    """A goal or project this person's memory records as achieved, and that is genuinely related to what they are
    struggling with now (they share a real content word). An unrelated achievement is not evidence for this."""
    wanted = _content_words(message)
    best, best_score, best_updated = None, 0, ""
    for fact in facts or []:
        if not getattr(fact, "active", True) or getattr(fact, "goal_status", None) != "achieved":
            continue
        if getattr(fact, "category", "") not in ("goal", "project"):
            continue
        overlap = len(wanted & _content_words(getattr(fact, "fact", "")))
        updated = str(getattr(fact, "updated_at", "") or "")
        if overlap > best_score or (overlap == best_score and overlap > 0 and updated > best_updated):
            best, best_score, best_updated = fact, overlap, updated
    if best is None or best_score == 0:
        return None
    return {"kind": "record", "what": _clip(getattr(best, "fact", ""))}


def recently_offered(recent_messages: Iterable[Any], gap: int = ENCOURAGEMENT_MIN_GAP) -> bool:
    return any((getattr(m, "additional_kwargs", None) or {}).get(ENCOURAGEMENT_MARK) for m in list(recent_messages)[-gap:])


def select_encouragement(
    message: str,
    recent_messages: Iterable[Any],
    load_facts: Callable[[], Iterable[Any]],
    *,
    source_type: str,
    risk_active: bool,
    valence: str,
    intensity: float,
    closing: bool,
) -> Optional[Dict[str, Any]]:
    """What to offer this turn, or None: {"evidence": [..]} (possibly empty, meaning only the plain-truth route).
    `load_facts` is called only after every cheap gate passes, so an ordinary turn never touches the database."""
    if risk_active or closing or source_type not in _ALLOWED_ROUTES:
        return None
    if valence == "distressed" and intensity >= _ACUTE_INTENSITY:
        return None
    if not has_self_doubt(message):
        return None
    messages = list(recent_messages)
    if recently_offered(messages):
        return None
    evidence: List[Dict[str, str]] = []
    session = session_evidence(messages)
    if session:
        evidence.append(session)
    try:
        record = record_evidence(message, load_facts())
    except Exception:
        record = None
    if record:
        evidence.append(record)
    return {"evidence": evidence}


def build_encouragement_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section ("" when nothing is offered)."""
    if not entry:
        return ""
    lines = ["\nENCOURAGEMENT (optional, evidence only): they just voiced doubt in themselves. After you have acknowledged what "
             "they said, you may add ONE calm, specific sentence that points at something real:"]
    evidence = entry.get("evidence") or []
    for item in evidence:
        if item["kind"] == "session":
            lines.append(f"- Earlier in this conversation they got through: \"{item['what']}\".")
        else:
            lines.append(f"- From what you remember about them, they achieved: \"{item['what']}\".")
    if not evidence:
        lines.append(
            "- You have NO record to point to, so say nothing about their past or their track record. At most, say plainly, "
            "and only if you are sure, that what they describe is common with this kind of work."
        )
    lines.append(
        "Rules: one sentence, no exclamation marks, no cheering. Don't say it will be easy or that they will definitely "
        "succeed, don't compare it to harder things, and never say you've been through it yourself or that you believe in "
        "them. Point only at the evidence above and never invent any. If it wouldn't land, skip it.\n"
    )
    return "\n".join(lines)


# How encouragement goes wrong.
_HYPE_RE = re.compile(r"!|\byou(?:'ve| have) got this\b|\bcrush(?:ing)? it\b|\byou(?:'re| are) (?:amazing|awesome|incredible|a rockstar)\b", re.IGNORECASE)
_MINIMIZE_RE = re.compile(
    r"\bit(?:'s| is) (?:really |actually |honestly )?(?:easy|simple|no big deal|nothing)\b|\bno big deal\b|\bjust (?:a )?(?:small|tiny|quick|simple)\b"
    r"|\byou(?:'ve| have) (?:done|handled|solved|survived) (?:much |way )?(?:harder|worse|tougher)\b|\bcould be worse\b|\beveryone (?:struggles|goes through)\b|\bhappens to everyone\b|\bevery(?:one|body) (?:has|gets) (?:stuck|been there)\b",
    re.IGNORECASE,
)
_PROMISE_RE = re.compile(
    r"\byou(?:'ll| will) (?:definitely |certainly |surely |absolutely )?(?:get|figure|solve|nail|crack|master|be fine|succeed)\b"
    r"|\bI (?:know|believe|trust|am sure|'m sure) (?:you(?:'ll| will| can| are)|in you)\b|\bI believe in you\b|\bit will (?:all )?(?:work out|be (?:fine|okay|ok))\b"
    r"|\bI(?:'m| am) proud of you\b|\beverything happens for a reason\b"
    r"|\byou can (?:definitely|certainly|absolutely|totally|easily) (?:handle|do|get|solve|figure|fix|nail|crack)\b"
    r"|\byou(?:'re| are) (?:definitely|certainly|absolutely|more than) (?:capable|able|smart enough)\b",
    re.IGNORECASE,
)
_INVENTED_RECORD_RE = re.compile(
    r"\byou(?:'ve| have) (?:done|solved|handled|overcome|gotten through|got through|been through|mastered) (?:this|these|that|things like|something like)\b"
    r"|\byou(?:'ve| have) (?:solved|fixed|handled|overcome|figured out|debugged|dealt with) (?:\w+ )?(?:problems?|bugs?|issues?|things?|errors?|failures?) (?:like|similar to) (?:this|that)\b"
    r"|\byou(?:'ve| have) (?:done|solved|handled|overcome|figured out) (?:it|this) before\b|\byour track record\b|\byou always (?:figure|get|solve)\b"
    r"|\b(?:last time|before),? you (?:figured|got|solved|fixed)\b",
    re.IGNORECASE,
)


def encouragement_reply_issue(reply: str, entry: Optional[Dict[str, Any]]) -> Optional[str]:
    """What is wrong with the encouragement in a reply, or None. Applied when encouragement was offered. An invented record
    is only a problem when there was no evidence to point at; with evidence, the model is allowed to use it."""
    from backend.utils.emotion_checks import reply_body

    text = reply_body(reply or "")
    if _HYPE_RE.search(text):
        return "cheers them on instead of staying calm"
    if _MINIMIZE_RE.search(text):
        return "minimizes what they are going through"
    if _PROMISE_RE.search(text):
        return "promises an outcome or says it believes in them"
    if entry is not None and not (entry.get("evidence") or []) and _INVENTED_RECORD_RE.search(text):
        return "points at a record the person doesn't have"
    return None


def build_encouragement_revision_prompt(original_prompt: str, draft: str, issue: str) -> tuple:
    """(prompt, reason) for a single rewrite of encouragement that went wrong."""
    reason = (
        f"the draft {issue}. Encouragement here is one calm sentence that points only at real evidence they were given, "
        "with no cheering, no promises, no 'I believe in you', and no record invented. Write the reply again, keeping the "
        "substance and warmth, and either fix that sentence or drop it."
    )
    note = (
        "\n\nREVISION NOTE (never mention this note or that you are revising): your draft reply to the "
        f"message above was rejected because {reason}\n\nREJECTED DRAFT:\n---\n{draft}\n---\n\n"
        "Now write the reply again.\n"
    )
    return original_prompt + note, reason
