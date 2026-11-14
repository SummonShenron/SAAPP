"""A private note written BEFORE the reply to someone who is hurting: what is new in their message, one fact about their situation that is surely true, and what earlier replies already said.

WHY (2026-11-13, Jack put the same messages to Haiku and to Sonnet). Sonnet added something true about his situation and stayed with what he said; Haiku reflected, coached, and once invented a cause. Sonnet works out what is worth
saying first and writes second. Haiku does both in one breath, so it reaches for a safe reflection or a guess. This splits them: the reasoner model (Claude Haiku with thinking on, models.reasoner_llm_for) writes a short note, and the
reply is written with the note in front of it.

WHAT THE FIRST TRY TAUGHT (same day, 12 made-up turns): asked for "one sure fact about their situation", Haiku wrote guesses and the reply then said them with confidence ("her posting it means she did not hide the event"; "no one has read
your work and turned you down"), which is worse than none. Replies that ended on a question fell from 11 of 12 to 5 of 12 with the note, so its structure helps. So the note carries NO fact about the world: only what is new, what was already said,
and something THEY said earlier that points the other way, which the code checks against the conversation. A fact about how things work comes only from the facts a person has approved (backend/utils/situation_facts.py).

WHEN. Only where the fresh low-mood block is used (a negative feeling read from THIS message, a need for company), never under a safety plan, only on a conversational reply, and only for a person whose base model is Claude Haiku (the
model it was measured on: the reasoner follows the person's setting, so a person on another model never has this note written by a provider they did not pick). A failure or an empty note changes nothing: the reply is written as before.

WHAT IT MUST NOT DO. Invent. The fact must be one the model is sure of, never a guessed cause, never "people often"; the code drops a fact that talks that way (GENERAL_RE / CAUSE_GENERAL_RE are the checks the reply itself is held to).
NOTHING PERSONAL IS LOGGED: no message, no note; counters only.
"""
import json
import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional

from backend.utils.emotion_checks import CAUSE_GENERAL_RE, GENERAL_RE, _content, _stems
from backend.utils.emotion_utils import EMOTION_INJECTION_THRESHOLD, NEGATIVE_VALENCES, PRESENCE_NEEDS, effective_intensity

logger = logging.getLogger("SASS Logger")

MAX_FIELD_CHARS = 240
MAX_HISTORY_CHARS = 3500
FIELDS = ("new", "earlier", "avoid")
MIN_GROUNDED_WORDS = 2
_STOCK_GENERAL_RE = re.compile(r"\bpeople (?:often|usually|commonly|tend)\b|\bmost people\b|\bmany people\b|\bit'?s (?:common|normal|natural) (?:to|for)\b", re.IGNORECASE)

NOTE_PROMPT = """You are helping Sonic, a warm assistant, think BEFORE it replies to someone who is hurting. Read the conversation and the person's latest message, then write a short private note for Sonic. The person never sees it.

Return ONLY a JSON object with these three keys:
- "new": one short sentence: what this latest message tells Sonic compared with the earlier ones (a new fact, a new feeling, or a harsher conclusion they have drawn); if it is the first message, what has just happened to them.
- "earlier": something THEY told you in an EARLIER message of this conversation (never in the latest one) that points the other way from a harsh conclusion the latest message draws about themselves, in their own terms. Write "" unless the latest message draws such a conclusion AND an earlier message really says something that points the other way. Never write anything you infer, assume, or know about the world or about other people.
- "avoid": one point, example or piece of evidence that Sonic's earlier replies in this conversation already used and should not use again. "" if none.

Each value is at most 30 words, in plain words, with no names.

CONVERSATION SO FAR:
{history}

THEIR LATEST MESSAGE:
{question}
"""

BLOCK_HEAD = "\nYOUR PRIVATE NOTES ON THIS MESSAGE (your own thinking before you reply: use only what fits, in your own words, and never mention them):\n"
_BLOCK_LINES = (("new", "New in this message"), ("earlier", "Something they told you earlier that points the other way"), ("avoid", "You have already said, so do not say it again"))


def enabled() -> bool:
    """OFF unless SUBSTANCE_NOTE=on. Measured 2026-11-13 on 12 made-up turns, a note with no facts did little (replies ending on a question 11 of 12 with and without it; stamp lines and check failures 7 to 4, within noise) for an extra model call and a
    wait before the first word, so it stays off until it is tested together with the approved situation facts. Pure."""
    return os.getenv("SUBSTANCE_NOTE", "").strip().lower() in ("1", "true", "on", "yes")


def applies(state: Optional[Dict[str, Any]], now: datetime, *, safety_active: bool, source_type: str) -> bool:
    """True when this turn gets the fresh low-mood block (the one place the note is used): a negative feeling read from THIS message, not a lift, a need for company, strong enough to inject, no safety plan. Pure."""
    if safety_active or source_type != "conversational" or not state:
        return False
    valence = str(state.get("valence", "neutral"))
    if valence not in NEGATIVE_VALENCES or valence == "frustrated":
        return False
    if not state.get("read_this_turn") or state.get("lift_this_turn"):
        return False
    need = state.get("need") if state.get("need") else "none"
    return need in PRESENCE_NEEDS and effective_intensity(state, now) >= EMOTION_INJECTION_THRESHOLD


def build_prompt(history: str, question: str) -> str:
    """The prompt for the note: the last MAX_HISTORY_CHARS of the conversation so far, and their message. Pure."""
    return NOTE_PROMPT.format(history=(history or "")[-MAX_HISTORY_CHARS:] or "(this is the first message)", question=(question or "").strip())


def _grounded(text: str, history: str) -> bool:
    """True when at least MIN_GROUNDED_WORDS of the text's content words (by stem) appear in the history. Pure."""
    words = {w[:4] for w in _content(_stems(text))}          # the first four letters: "applied" and "applying" are one word here
    seen = {w[:4] for w in _stems(history or "")}
    return len(words & seen) >= MIN_GROUNDED_WORDS


def parse(raw: Any, history: str = "") -> Optional[Dict[str, str]]:
    """The note from the model's text, or None when it is not usable (not JSON, not an object, or every field empty). A field is trimmed to MAX_FIELD_CHARS; `earlier` is kept only when at least MIN_GROUNDED_WORDS of its content words
    really appear in the conversation so far (`history`), and never when it talks about people in general or how such things usually go: the reply is held to the same rule. Pure."""
    text = raw if isinstance(raw, str) else ""
    text = text.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(text)
    except Exception:
        match = re.search(r"\{.*\}", text, re.S)
        try:
            data = json.loads(match.group(0)) if match else None
        except Exception:
            data = None
    if not isinstance(data, dict):
        return None
    note = {}
    for field in FIELDS:
        value = data.get(field)
        value = re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else ""
        note[field] = value[:MAX_FIELD_CHARS]
    earlier = note["earlier"]
    if earlier and (GENERAL_RE.search(earlier) or CAUSE_GENERAL_RE.search(earlier) or _STOCK_GENERAL_RE.search(earlier) or not _grounded(earlier, history)):
        note["earlier"] = ""
    return note if any(note.values()) else None


def build_block(note: Optional[Dict[str, str]]) -> str:
    """The prompt block for a note, or "" when there is none. Pure."""
    if not note:
        return ""
    lines = [f"- {label}: {note[field]}\n" for field, label in _BLOCK_LINES if note.get(field)]
    return (BLOCK_HEAD + "".join(lines)) if lines else ""


def tally_turn(counts: Dict[str, int], note: Optional[Dict[str, str]], tally) -> None:
    """This turn's counters with the counters' own `tally`: a note was asked for, and whether it came back empty, was put in the prompt, and carried something they said earlier. Counts only."""
    tally(counts, "substance_note_asked")
    if not note:
        tally(counts, "substance_note_empty")
        return
    tally(counts, "substance_note_used")
    if note.get("earlier"):
        tally(counts, "substance_note_earlier")


async def write_note(llm: Any, history: str, question: str) -> Optional[Dict[str, str]]:
    """Asks the model for the note. None on any failure or an unusable answer (the reply is then written as before). Never raises; logs no text."""
    try:
        response = await llm.ainvoke(build_prompt(history, question))
        content = response.content if hasattr(response, "content") else response
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        return parse(content if isinstance(content, str) else str(content), history)
    except Exception:
        logger.warning("[substance_note] could not write a note for this turn.", exc_info=True)
        return None
