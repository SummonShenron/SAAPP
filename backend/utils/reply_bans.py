"""The repeat checks, run BEFORE the reply is written (2026-11-23).

WHY. The reply-fit check rewrites a reply to someone who is low or frustrated when it repeats one of Sonic's own recent replies: the same opening words, another presence line, another "you don't
have to...", a point made twice already. A rewrite is a second full model call. Of the 17 rewrites whose reason was recorded (2026-11-22/23), 14 were these. But each of them compares the draft
only with EARLIER replies, which are known before the model writes a word. So the same functions (backend/utils/emotion_checks.py) are run on the earlier replies first, and what they would
catch is told to the model as plain bans for this turn. The check after the reply stays as it is, as the backstop.

Pure: the caller passes the earlier replies (oldest first) and the person's message.
"""
import re
from typing import Any, Dict, List, Optional, Sequence

from backend.utils import emotion_checks as ec

MAX_POINTS = 3
MAX_POINT_CHARS = 140


def _first_words(text: str, count: int = 3) -> str:
    words = ec.reply_body(text).strip().replace("’", "'").split()
    return " ".join(words[:count]).strip(" ,.;:!?—-") if len(words) >= count else ""


def _sentences(text: str) -> List[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", ec.reply_body(text)) if s.strip()]


def repeated_points(earlier: Sequence[str], message: str = "") -> List[str]:
    """Sentences from the latest earlier reply that carry a point (an anchor or a four-word idea) already used in at least RECYCLE_MIN_EARLIER of the last RECYCLE_WINDOW replies, leaving out
    what their message brings up itself: the points the recycled-point check would catch if said again. Readable, at most MAX_POINTS. Pure."""
    recent = list(earlier or [])[-ec.RECYCLE_WINDOW:]
    if not recent:
        return []
    theirs = ec._evidence(message or "") | ec._idea_grams(message or "") | {w.lower() for w in ec._WEEKDAY_RE.findall(message or "")}
    found: List[str] = []
    for sentence in reversed(_sentences(recent[-1])):              # the latest reply's own wording, its last points first
        items = (ec._evidence(sentence) | ec._idea_grams(sentence)) - theirs
        evidence_sets = [ec._evidence(e) | ec._idea_grams(e) for e in recent]
        if any(sum(1 for seen in evidence_sets if item in seen) >= ec.RECYCLE_MIN_EARLIER for item in items):
            short = sentence if len(sentence) <= MAX_POINT_CHARS else sentence[:MAX_POINT_CHARS].rstrip() + "…"
            if short not in found:
                found.append(short)
        if len(found) >= MAX_POINTS:
            break
    return found


def bans(earlier: Sequence[str], message: str = "") -> Dict[str, Any]:
    """What the repeat checks would catch in this turn's reply, worked out from the earlier replies alone. Pure."""
    openings = []
    for reply in list(earlier or [])[-ec.OPENING_WINDOW:]:
        words = _first_words(reply)
        if words and words.lower() not in (o.lower() for o in openings):
            openings.append(words)
    return {
        "openings": openings,
        "presence": any(ec.PRESENCE_LINE_RE.search(ec.reply_body(r)) for r in list(earlier or [])[-ec.PRESENCE_WINDOW:]),
        "permission": any(ec._PERMISSION_RE.search(ec.reply_body(r)) for r in list(earlier or [])[-ec.PERMISSION_WINDOW:]),
        "points": repeated_points(earlier, message),
    }


MOODS = ("distressed", "low", "frustrated")


def applies(state: Optional[Dict[str, Any]]) -> bool:
    """True when the after-reply repeat checks would run on this turn: a negative feeling read from THIS message, with no lift (emotion_checks.emotional_reply_issue's own gate). Pure."""
    state = state or {}
    return bool(state.get("read_this_turn")) and not state.get("lift_this_turn") and str(state.get("valence")) in MOODS


def mood_label(state: Optional[Dict[str, Any]]) -> str:
    """The mood read this turn, for the rewrite counts: distressed / low / frustrated, or "other". Pure."""
    state = state or {}
    valence = str(state.get("valence"))
    return valence if state.get("read_this_turn") and valence in MOODS else "other"


def build_block(found: Optional[Dict[str, Any]]) -> str:
    """The reply prompt's section, or "" when nothing is banned. Pure."""
    if not found:
        return ""
    lines = []
    if found.get("openings"):
        lines.append("- Don't open with " + " or ".join(f"\"{o}\"" for o in found["openings"]) + " (how your last replies began). Open with something about what they just said.")
    if found.get("presence"):
        lines.append("- No presence line this time (\"I'm right here with you\", \"I'm here for you\", \"I've got you\"): one of your last two replies said one.")
    if found.get("permission"):
        lines.append("- No \"you don't have to...\" this time: one of your last two replies said it.")
    if found.get("points"):
        lines.append("- You have already made these points more than once; don't make them again, in any words:\n" + "\n".join(f"    \"{p}\"" for p in found["points"]))
    if not lines:
        return ""
    return "\nDON'T REPEAT YOURSELF THIS TURN (worked out from your last few replies):\n" + "\n".join(lines) + "\n"
