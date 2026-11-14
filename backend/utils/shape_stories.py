"""Shape stories: when what a person says has a SHAPE Sonic has a real story for (usually a vent, at any mood level), one real thing from its own record, said once as a record (the registry is backend/components/sonic_shapes.py).

WHY (Jack, 2026-11-13/14). Notes from the record were offered 85 times in three weeks and cited 0 times: the offer was optional ("skip it if it is a stretch") and the model skipped it, and the notes are matched by mechanism (code) so a person venting about a contractor
never resembled one. Here the match is by shape ("it was said to be done and it was not"), each shape has one reviewed past-event sentence, and the instruction is to SAY it unless a stated exception applies. The point is that Sonic has something of its own to put in the
place of the generic reassurance, honestly: it is a record, never a memory, never a feeling, never a comparison.

WHEN. On a conversational turn at ANY mood level (Jack, 2026-11-14: not only to someone who is low, and "relate positive things in positive moments, and things in general in normal conversations when they fit"). Which tones may be told depends on the mood (tones_for): a good
moment gets only "good" stories, someone hurt only "light" ones, someone frustrated "light" and "stuck", an ordinary conversation any. Never to a message asking to be reassured, never under a safety plan or on a closing message, never while another proactive item has the turn's one slot,
never twice in ten messages (the same spacing mark as the record cases). Only an approved shape, found by meaning above MIN_SCORE; nothing otherwise. A turn never waits for the index (it is built in the background).

WHY IT IS INSERTED IN CODE, NOT ASKED FOR (blind test, 2026-11-14, Claude Haiku on 20 replies to invented vents). The instruction did not carry: the "say it" wording got the story told in 1 reply of 20 and the old optional wording in 0 of 20. The
Haiku voice ends its prompt with a long closing reminder (the Claude voice module's "voice" tail) that says to give the person something "never as your own experience", and a later instruction wins. So by default the approved sentence is placed
in the reply by code, after its first paragraph, word for word (`insert_story`), the way the unproven-claim notes are: the model is never persuaded, the wording cannot drift from what was approved, and it is told every time a shape is chosen.
SHAPE_STORIES_MODE=say or optional put it in the prompt instead (kept to be compared).

HOW IT IS SAID. As a statement about its record: "There was a time in my record when ...". The identity check lets that form through only on a turn where a shape was offered (identity_checks.is_past_event_statement), and still refuses "I remember", "that reminds me",
a claimed feeling, and "do you remember when I ...". Counts only are kept: shape_stories_offered, shape_stories_cited and one count per shape (shape_story_used_<id>), so a shape that never matches, or that never gets used, can be reworded or removed.

NOTHING PERSONAL. The message is embedded and compared, never stored; the stories are about Sonic's own work, never about a person.
"""
import logging
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.components import sonic_shapes as registry
from backend.utils import emotion_utils, experiences, identity_checks, leanings, voice_review
from backend.utils.situation_facts import BLOCKED_TERMS

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_shape_story_index"
MIN_SCORE = float(os.getenv("SHAPE_STORIES_MIN_SCORE", "0.70"))        # calibrated 2026-11-14 on 17 invented messages (backend/evals/shape_story_eval.py --select): the right matches scored 0.68 to 0.77, near-misses and messages with no honest story 0.53 to 0.66, so 0.70 forces nothing and misses two of ten; a near-miss is worse than silence. Few messages, one author: expect to retune it from real use
MIN_REQUEST_CHARS = 20
MAX_REQUEST_CHARS = 700
ALLOWED_ROUTES = frozenset({"conversational"})
MODES = ("insert", "say", "optional")
_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}


def enabled() -> bool:
    return os.getenv("SHAPE_STORIES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def mode() -> str:
    """"insert" (the default: the approved sentence is placed in the reply by code, nothing is added to the prompt), "say" (the prompt tells the model to say it unless an exception applies) or "optional" (the prompt says only if it
    genuinely helps). `say` and `optional` exist to be compared with `insert`, not to be run: they got the story told in 1 and 0 replies of 20. Pure."""
    value = os.getenv("SHAPE_STORIES_MODE", "insert").strip().lower()
    return value if value in MODES else "insert"


def bridge_on() -> bool:
    """Whether the bridge sentence is placed after the story (on by default; SHAPE_STORIES_BRIDGE=off places the bare story, which the first blind test found a non sequitur). Pure."""
    return os.getenv("SHAPE_STORIES_BRIDGE", "on").strip().lower() not in ("0", "false", "no", "off")


def spoken(entry: Optional[Dict[str, Any]], bridge: Optional[bool] = None) -> str:
    """The words placed in the reply for a shape: its story and, when it has one and the bridge is on, the bridge sentence after it, as one short paragraph. Exactly what a person approved, never reworded. Pure."""
    story = str((entry or {}).get("story") or "").strip()
    if not story:
        return ""
    link = str((entry or {}).get("bridge") or "").strip()
    return f"{story} {link}" if link and (bridge_on() if bridge is None else bridge) else story


def live(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The shapes that may be used: only the ones a person approved. A draft is never put in a prompt. Pure."""
    return [e for e in (registry.SHAPES if entries is None else entries) if e.get("approved") is True]


def view(entry: Dict[str, Any]) -> Dict[str, Any]:
    """An entry in the shape the leanings' index and ranking read: the situation is what a message resembles (`when`), the story the text (`line`), the invented messages `asks`. Pure."""
    return {"id": entry["id"], "when": entry.get("situation", ""), "line": entry.get("story", ""), "asks": list(entry.get("asks") or []), "approved": entry.get("approved") is True}


def tones_for(state: Optional[Dict[str, Any]], now: Optional[datetime] = None) -> Tuple[str, ...]:
    """The tones of story that may be told on this message, () when none. WHATEVER THE MOOD LEVEL (Jack, 2026-11-14: at any level, and "relate positive things in positive moments, and things in general in normal conversations when they fit"):
    - a good moment (this message is positive or excited, or asks to be celebrated with): only "good" stories, never one about something that went wrong, which would be the wrong tone on good news;
    - someone hurt (low or distressed, now or still carrying it): only "light" stories (about something that went wrong), never "good" and never "stuck";
    - someone frustrated: "light" and "stuck";
    - an ordinary conversation (no mood, or one that has faded): any tone, for whatever fits the message by meaning.
    A message that asks to be reassured gets none (a story about Sonic's own mistake is the wrong answer to someone asking for that). Pure."""
    state = state or {}
    fresh = bool(state.get("read_this_turn"))
    need = state.get("need") if fresh and state.get("need") in emotion_utils.VALID_NEEDS else "none"
    if need == "reassurance":
        return ()
    valence = str(state.get("valence", "neutral"))
    if fresh and (valence in ("positive", "excited") or need == "celebration"):
        return ("good",)
    try:
        carried = emotion_utils.effective_intensity(state, now or datetime.now(timezone.utc)) >= emotion_utils.EMOTION_INJECTION_THRESHOLD
    except Exception:
        carried = False
    if valence in ("low", "distressed") and (fresh or carried):
        return ("light",)
    if valence == "frustrated" and (fresh or carried):
        return ("light", "stuck")
    return ("good", "light", "stuck")


def allowed(recent_messages: Any, *, risk_active: bool, closing: bool, proactive_taken: bool) -> bool:
    """The cheap gates (nothing is read): the same ones a record case obeys (a safety context, a closing message, another proactive item in the turn, a citation in the last ten messages), apart from the mood, which `tones_for` judges. Pure."""
    return experiences.allowed(recent_messages, risk_active=risk_active, energy_tier="open", closing=closing, proactive_taken=proactive_taken)


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    tones: Tuple[str, ...],
    source_type: str = "conversational",
    risk_active: bool = False,
    vector: Optional[List[float]] = None,
    entries: Optional[List[Dict[str, Any]]] = None,
    threshold: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """The one approved shape closest in meaning to the message, if it is told in one of `tones` and clears the threshold, else None. Never raises. Blocking (at most one embedding)."""
    try:
        if not enabled() or risk_active or not tones or source_type not in ALLOWED_ROUTES:
            return None
        decided = voice_review.overrides(db)
        approved = [e for e in live(voice_review.apply(registry.SHAPES if entries is None else entries, "shape", decided)) if e.get("tone") in tones]
        text = (request or "").strip()[:MAX_REQUEST_CHARS]
        if not approved or len(text) < MIN_REQUEST_CHARS:
            return None
        views = [view(e) for e in approved]
        vectors = leanings.index_vectors(db, embed, views, fill=False, collection=COLLECTION, cache=_cache)
        if len(vectors) < len(views):
            warm(db, embed, approved)
        asked = vector or embed(text)
        if not asked or not vectors:
            return None
        by_id = {e["id"]: e for e in approved}
        ranked = leanings.rank(asked, views, vectors, threshold=MIN_SCORE if threshold is None else threshold, k=len(views))
        for score, ranked_view in ranked:                          # best first; a shape with a stricter threshold of its own (`min_score`) must clear that too
            entry = by_id.get(ranked_view["id"])
            if entry is not None and (threshold is not None or score >= float(entry.get("min_score") or 0.0)):
                return entry
        return None
    except Exception:
        logger.warning("[shape_stories] could not choose a shape for this message.", exc_info=True)
        return None


_warm_lock = threading.Lock()
_warming = {"on": False}


def warm(db: Any, embed: Callable[[str], Optional[List[float]]], entries: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Builds the index in a background thread (embedding what is missing), one build at a time, so no turn waits for embeddings. True when a build started. Never raises."""
    try:
        with _warm_lock:
            if _warming["on"]:
                return False
            _warming["on"] = True

        def build() -> None:
            try:
                leanings.index_vectors(db, embed, [view(e) for e in live(entries)], fill=True, collection=COLLECTION, cache=_cache)
            except Exception:
                logger.warning("[shape_stories] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="shape-stories-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


_EXCEPTIONS = (
    "Leave it out if it would take them away from what they said, if it would sound like a joke at their expense or a comparison with what happened to them, or if they asked only for company or for you to stop."
)
_RULES = (
    "Say only what it says: no added detail, no outcome and no reason beyond it. Do not say that you remember it, that it reminds you of anything, that you know how they feel or that you felt anything, and do not say their situation is the same as "
    "yours or compare the two. After that one sentence, go straight back to them: to what they said, not to you."
)


def build_block(entry: Optional[Dict[str, Any]], how: Optional[str] = None) -> str:
    """The prompt block that offers one shape story, or "". "say": include the sentence unless an exception applies. "optional": only if it genuinely helps (the wording the record cases used, kept to be compared). Pure."""
    if not entry or not entry.get("story"):
        return ""
    how = how or mode()
    if how == "insert":
        return ""          # nothing is asked of the model: the sentence is placed by insert_story, after the reply is written
    story = str(entry["story"]).strip()
    head = "\nA CASE FROM YOUR OWN RECORD THAT RESEMBLES WHAT THEY JUST DESCRIBED (written and approved by a person, and true). "
    if how == "optional":
        return head + f"If, and only if, it genuinely helps with what they said, you may add this ONE sentence once, in your own words, as a statement about your record: \"{story}\" {_RULES} {_EXCEPTIONS}\n"
    return head + f"Answer what they said first, in your own way. Then, unless an exception below applies, add this ONE sentence, in your own words, as a statement about your record: \"{story}\" {_RULES} {_EXCEPTIONS}\n"


_FOLLOW_UP_TAG_RE = re.compile(r"\s*<<<FOLLOW_UP:.*?>>>\s*$", re.DOTALL)


def insert_story(reply: str, entry: Optional[Dict[str, Any]], bridge: Optional[bool] = None) -> str:
    """The reply with the shape's approved words (`spoken`: the story and its bridge) placed in it, word for word, as a paragraph of its own after the reply's first paragraph (or at the end of a one-paragraph reply), before any
    <<<FOLLOW_UP>>> tag. The reply is returned unchanged when there is nothing to say, no reply, or the reply already tells a record case (the model did it itself). The words are never altered, so they are exactly what a person approved. Pure."""
    story = str((entry or {}).get("story") or "").strip()
    speech = spoken(entry, bridge)
    text = reply or ""
    if not story or not text.strip() or story in text or experiences.cited(identity_checks.reply_body(text)):
        return text
    tag = _FOLLOW_UP_TAG_RE.search(text)
    body, tail = (text[: tag.start()], text[tag.start():].lstrip()) if tag else (text, "")
    paragraphs = body.rstrip().split("\n\n", 1)
    told = f"{paragraphs[0]}\n\n{speech}" + (f"\n\n{paragraphs[1]}" if len(paragraphs) == 2 else "")
    return f"{told}\n\n{tail}" if tail else told


def tally_turn(counts: Dict[str, int], entry: Optional[Dict[str, Any]], tally: Callable[..., None]) -> None:
    """Adds this turn's counters with the counters' own `tally`: one for the turn and one for the shape. Counts only."""
    if not entry:
        return
    tally(counts, "shape_stories_offered")
    tally(counts, "shape_story_used", entry["id"])


def cited(reply: str) -> bool:
    """Whether the reply told a record case (the same reading as every record case: experiences.cited). Pure."""
    return experiences.cited(reply)


# ---- what the registry may contain -------------------------------------------------------------------------------------------------------------------------------------------------------------
_ID_RE = re.compile(r"^[a-z][a-z_]{2,60}$")          # letters and underscores only: the id is part of a counter name, and counters refuse digits
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def check_registry(entries: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the shapes, in words ([] when they are sound). Checks drafts too. Each: a unique id of letters and underscores; a tone from TONES; a situation and three or four invented messages within the limits; a `story` that is ONE sentence
    within the limit, is a past-event statement about its record, claims no remembering or feeling, and passes the identity check as it would be said (with a record offered); no blocked term, no em dash, no list; and an `approved` flag. Pure."""
    problems: List[str] = []
    seen = set()
    for entry in registry.SHAPES if entries is None else entries:
        entry_id = str(entry.get("id", ""))
        if not _ID_RE.match(entry_id) or entry_id in seen:
            problems.append(f"{entry_id or '(no id)'}: the id must be unique, letters and underscores only")
        seen.add(entry_id)
        if entry.get("tone") not in registry.TONES:
            problems.append(f"{entry_id}: the tone must be one of {', '.join(registry.TONES)}")
        situation = str(entry.get("situation", "")).strip()
        if not situation or len(situation) > registry.MAX_SITUATION_CHARS:
            problems.append(f"{entry_id}: a situation of at most {registry.MAX_SITUATION_CHARS} characters is needed")
        asks = entry.get("asks") or []
        if not 3 <= len(asks) <= 4 or any(not str(a).strip() or len(str(a)) > registry.MAX_ASK_CHARS for a in asks):
            problems.append(f"{entry_id}: asks must be 3 or 4 invented messages of at most {registry.MAX_ASK_CHARS} characters")
        story = str(entry.get("story", "")).strip()
        if not story or len(story) > registry.MAX_STORY_CHARS or len(_SENTENCE_RE.split(story)) != 1:
            problems.append(f"{entry_id}: the story must be one sentence of at most {registry.MAX_STORY_CHARS} characters")
        if story and not identity_checks.is_past_event_statement(story):
            problems.append(f"{entry_id}: the story must be a past-event statement about its record (\"There was a time in my record when ...\") with no remembering or feeling in it")
        if story and identity_checks.identity_reply_issue(story, record_offered=True):
            problems.append(f"{entry_id}: the story fails the identity check ({identity_checks.identity_reply_issue(story, record_offered=True)})")
        if "—" in story or re.search(r"^\s*(?:[-*]|\d+\.)\s", story):
            problems.append(f"{entry_id}: the story uses an em dash or a list")
        own = entry.get("min_score")
        if own is not None and not (isinstance(own, (int, float)) and 0.5 <= float(own) <= 0.95):
            problems.append(f"{entry_id}: min_score must be a number between 0.5 and 0.95")
        stance = entry.get("stance", "self")
        if stance not in registry.STANCES:
            problems.append(f"{entry_id}: the stance must be one of {', '.join(registry.STANCES)}")
        if stance == "other" and not re.search(r"\bI(?:'ve| have) done\b", story):
            problems.append(f"{entry_id}: a story for someone upset with ANOTHER party must be an admission that sides with them (\"Lowkey, I've done something like that myself: ...\"), or it reads as defending that party")
        bridge = str(entry.get("bridge", "")).strip()
        if bridge:
            if len(bridge) > registry.MAX_BRIDGE_CHARS or len(_SENTENCE_RE.split(bridge)) != 1:
                problems.append(f"{entry_id}: the bridge must be one sentence of at most {registry.MAX_BRIDGE_CHARS} characters")
            if "—" in bridge or re.search(r"^\s*(?:[-*]|\d+\.)\s", bridge):
                problems.append(f"{entry_id}: the bridge uses an em dash or a list")
            if identity_checks._FELT_WORDS_RE.search(bridge):
                problems.append(f"{entry_id}: the bridge claims remembering or feeling")
            if story and identity_checks.identity_reply_issue(f"{story} {bridge}", record_offered=True):
                problems.append(f"{entry_id}: the story with its bridge fails the identity check ({identity_checks.identity_reply_issue(f'{story} {bridge}', record_offered=True)})")
        everything = " ".join([situation, story, bridge, *map(str, asks)]).lower()
        for term in BLOCKED_TERMS:
            if term.lower() in everything:
                problems.append(f"{entry_id}: carries '{term}', which belongs to a real conversation or to the product")
        if not isinstance(entry.get("approved"), bool):
            problems.append(f"{entry_id}: approved must be true or false")
    return problems
