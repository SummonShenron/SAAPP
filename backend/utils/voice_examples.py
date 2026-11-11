"""Finding the voice examples that fit what someone just said, and putting them in the reply (the registry is backend/components/sonic_voice_examples.py).

WHY. The standard voice will be migrated away from and its feel has to live on the cheaper model; rules describe that feel badly and examples show it (docs/model-eval-plan.md, Update 14). Two examples of the voice in a similar moment go in front of the reply, for every base model.

HOW. The same machinery as the working leanings and the people notes: every approved example is embedded once from its situation plus its invented messages, the message is embedded (the turn's question embedding is already computed for memory), and the closest
few above a threshold are used. No model call is made beyond that embedding.

WHEN. Only on a conversational reply and never under a safety plan (those turns were built and measured on the standard voice and have their own rules). A closing message IS allowed: "good night" is exactly where an example of a warm short close helps. In an emotional
context (a mood was read) the "light" examples are never served, so a person in pain is not shown how to answer good news; an upbeat message gets no "hurting" example because it is not close to one. At most two, and the block tells the model to match the register and never the wording.

NOTHING PERSONAL. The message is embedded and compared, never stored; the examples are invented. What is recorded is a count per example (aggregate counters), so one that never matches can be reworded or removed.
"""
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.components import sonic_voice_examples as registry
from backend.utils import leanings
from backend.utils.emotion_checks import PROBE_RE, STATE_ASK_RE
from backend.utils.identity_checks import identity_reply_issue

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_voice_example_index"
# Calibrated 2026-11-08 on real embeddings (gemini-embedding-001) with 57 made-up messages worded fresh for the examples and 25 unrelated ones: at 0.70 the intended example is served for 48 of 57 (84%), no wrong example is returned for a
# message that has its own, and the one unrelated message that scores above it ("thanks", 0.727) is shorter than MIN_REQUEST_CHARS. Lower (0.65) serves 52 but lets 5 of 25 unrelated messages through; 0.74 serves only 28. High on purpose: an
# example that does not fit teaches the wrong register.
MIN_SCORE = float(os.getenv("VOICE_EXAMPLES_MIN_SCORE", "0.70"))
PER_TURN = max(int(os.getenv("VOICE_EXAMPLES_PER_TURN", "2")), 1)
MIN_REQUEST_CHARS = 8
MAX_REQUEST_CHARS = 700
ALLOWED_ROUTES = frozenset({"conversational"})

_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}


def enabled() -> bool:
    return os.getenv("VOICE_EXAMPLES_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def live(examples: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The examples that may be used: only the ones a person approved. A draft is never put in a prompt. Pure."""
    return [e for e in (registry.EXAMPLES if examples is None else examples) if e.get("approved") is True]


def view(example: Dict[str, Any]) -> Dict[str, Any]:
    """An example in the shape the leanings' index and ranking read: the situation is what a message resembles (`when`), the invented reply the text (`line`), the invented messages `asks`. Pure."""
    return {"id": example["id"], "when": example.get("situation", ""), "line": example.get("reply", ""), "asks": list(example.get("asks") or []), "approved": example.get("approved") is True}


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    source_type: str = "conversational",
    risk_active: bool = False,
    emotional_active: bool = False,
    vector: Optional[List[float]] = None,
    examples: Optional[List[Dict[str, Any]]] = None,
    threshold: Optional[float] = None,
    k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The examples for this message, best first ([] when the feature is off, a gate is closed, or nothing is close enough). Never raises. Blocking (at most one embedding)."""
    try:
        if not enabled() or risk_active or source_type not in ALLOWED_ROUTES:
            return []
        approved = [e for e in live(examples) if not (emotional_active and e.get("tone") == "light")]
        text = (request or "").strip()[:MAX_REQUEST_CHARS]
        if not approved or len(text) < MIN_REQUEST_CHARS:
            return []
        views = [view(e) for e in approved]
        vectors = leanings.index_vectors(db, embed, views, fill=False, collection=COLLECTION, cache=_cache)
        if len(vectors) < len(views):
            warm(db, embed, approved)
        asked = vector or embed(text)
        if not asked or not vectors:
            return []
        by_id = {e["id"]: e for e in approved}
        ranked = leanings.rank(asked, views, vectors, threshold=MIN_SCORE if threshold is None else threshold, k=PER_TURN if k is None else k)
        return [by_id[v["id"]] for _, v in ranked if v["id"] in by_id]
    except Exception:
        logger.warning("[voice_examples] could not choose examples for this message.", exc_info=True)
        return []


_warm_lock = threading.Lock()
_warming = {"on": False}


def warm(db: Any, embed: Callable[[str], Optional[List[float]]], examples: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Builds the index in a background thread (embedding what is missing), one build at a time, so no turn waits for embeddings. True when a build started. Never raises."""
    try:
        with _warm_lock:
            if _warming["on"]:
                return False
            _warming["on"] = True

        def build() -> None:
            try:
                leanings.index_vectors(db, embed, [view(e) for e in live(examples)], fill=True, collection=COLLECTION, cache=_cache)
            except Exception:
                logger.warning("[voice_examples] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="voice-examples-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


BLOCK_HEAD = (
    "HOW YOU HAVE ANSWERED THINGS LIKE THIS (your own voice in a similar moment). Match the register, the energy and the length. Never reuse their wording, details or structure: this message is its own, "
    "and a different situation gets its own words. If an example does not fit what they said, ignore it.\n"
)


def build_block(chosen: List[Dict[str, Any]]) -> str:
    """The prompt block for the chosen examples, or "": each as what was said and how you answered. Pure."""
    parts = []
    for example in chosen:
        asks = example.get("asks") or [""]
        parts.append(f"- They said: {asks[0]}\n  You said: {example.get('reply', '')}\n")
    return (BLOCK_HEAD + "".join(parts)) if parts else ""


def tally_turn(counts: Dict[str, int], chosen: List[Dict[str, Any]], tally: Callable[..., None]) -> None:
    """Adds this turn's counters to `counts` with the counters' own `tally` (which ignores any key that is not registered): one for the turn and one per example. Counts only."""
    if not chosen:
        return
    tally(counts, "voice_examples_offered")
    for example in chosen:
        tally(counts, "voice_example_used", example["id"])


# ---- what the registry may contain ----------------------------------------------------------------------------------------------------------------------------------------------------------
# The people, projects and products of Jack's real thread: an example must never carry one, because a prompt many people see must hold no one's real conversation.
BLOCKED_TERMS = ("jack", "yashu", "lahari", "casey", "pfg", "pag ui", "saapp", "sonic", "errAgent", "erragent", "render", "claude", "haiku", "flash")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_OPENER_RE = re.compile(r"^\W*(?:got it|understood|noted|fair enough|fair point|sure thing)\b", re.IGNORECASE)
_FIRST_PERSON_FEELING_RE = re.compile(r"\bI(?:'m| am) (?:dying|so curious|super curious|curious|excited|thrilled|proud|worried|sad|happy|glad)\b|\bI (?:love|loved|hate|hated|missed|feel|felt)\b|\bI've (?:updated|saved|noted|recorded|stored)\b", re.IGNORECASE)
_LIST_RE = re.compile(r"\*\*|^\s*[-*] |^\s*\d+\. |^#+ ", re.MULTILINE)


def _questions(text: str) -> int:
    return text.count("?")


def _ends_on_question(text: str) -> bool:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]
    return bool(sentences) and sentences[-1].endswith("?")


def check_registry(examples: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the examples, in words ([] when they are sound). Checks drafts too. Each: a unique snake_case id; a known tone; a situation and three or four invented messages; a reply within its limit that passes the identity check, carries
    no blocked term (a person or project from a real thread), no claimed feeling or action, no "Got it" opener, no list, no em dash and at most one question; and an `approved` flag. Pure."""
    problems: List[str] = []
    seen = set()
    for example in registry.EXAMPLES if examples is None else examples:
        example_id = str(example.get("id", ""))
        if not _ID_RE.match(example_id) or example_id in seen:
            problems.append(f"{example_id or '(no id)'}: the id must be unique snake_case")
        seen.add(example_id)
        if example.get("tone") not in registry.TONES:
            problems.append(f"{example_id}: tone must be one of {', '.join(registry.TONES)}")
        situation = str(example.get("situation", "")).strip()
        if not situation or len(situation) > registry.MAX_SITUATION_CHARS:
            problems.append(f"{example_id}: a situation of at most {registry.MAX_SITUATION_CHARS} characters is needed")
        asks = example.get("asks") or []
        if not 3 <= len(asks) <= 4 or any(not str(a).strip() or len(str(a)) > registry.MAX_ASK_CHARS for a in asks):
            problems.append(f"{example_id}: asks must be 3 or 4 invented messages of at most {registry.MAX_ASK_CHARS} characters")
        reply = str(example.get("reply", "")).strip()
        if not reply or len(reply) > registry.MAX_REPLY_CHARS:
            problems.append(f"{example_id}: the reply must be present and at most {registry.MAX_REPLY_CHARS} characters")
        everything = " ".join([situation, reply, *map(str, asks)]).lower()
        for term in BLOCKED_TERMS:
            if term.lower() in everything:
                problems.append(f"{example_id}: carries '{term.strip()}', which belongs to a real conversation or to the product")
        if reply:
            if identity_reply_issue(reply):
                problems.append(f"{example_id}: the reply fails the identity check ({identity_reply_issue(reply)})")
            if _FIRST_PERSON_FEELING_RE.search(reply):
                problems.append(f"{example_id}: the reply claims a feeling or an action")
            if _OPENER_RE.match(reply):
                problems.append(f"{example_id}: the reply opens with an acknowledgement filler ('Got it', 'Understood')")
            if _LIST_RE.search(reply):
                problems.append(f"{example_id}: the reply uses a list or bold")
            if "—" in reply:
                problems.append(f"{example_id}: the reply uses an em dash")
            if _questions(reply) > 1:
                problems.append(f"{example_id}: the reply asks more than one question")
        if not isinstance(example.get("approved"), bool):
            problems.append(f"{example_id}: approved must be true or false")
    return problems


def check_set(examples: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the examples AS A SET, because the set is what teaches the voice: at most half end on a question, and of the HURTING ones between 30% and 70% do (Jack's blind A/B, 2026-11-10: the replies he chose ended on a
    question 9 of 14 times, and a set that never did taught Haiku to end every reply on a statement); no example asks a probe (what happened, what was said, why) or how they are; "I'm right here" and "I hear you" together at most twice (they were overused); at least 6 light and 5 hurting ones; and most light
    ones carry an exclamation mark (the clearest gap to the standard voice). Pure."""
    items = list(registry.EXAMPLES if examples is None else examples)
    if not items:
        return ["there are no examples"]
    problems: List[str] = []
    replies = [str(e.get("reply", "")) for e in items]
    if sum(_ends_on_question(r) for r in replies) > 0.5 * len(items):
        problems.append("more than half of the examples end on a question")
    hurting_replies = [str(e.get("reply", "")) for e in items if e.get("tone") == "hurting"]
    if hurting_replies:
        asking = sum(_ends_on_question(r) for r in hurting_replies)
        if not 0.3 * len(hurting_replies) <= asking <= 0.7 * len(hurting_replies):
            problems.append(f"between 30% and 70% of the hurting examples should end on a question (end on a question: {asking} of {len(hurting_replies)})")
    for reply in replies:
        for sentence in (x for x in re.split(r"(?<=[.!?])\s+", reply) if x.strip().endswith("?")):
            if PROBE_RE.search(sentence) or STATE_ASK_RE.search(sentence):
                problems.append("an example asks a probe (what happened, what was said, why) or how they are")
    if sum(len(re.findall(r"right here|I hear you", r, re.IGNORECASE)) for r in replies) > 2:
        problems.append("'I'm right here' and 'I hear you' appear more than twice across the examples")
    tones = [e.get("tone") for e in items]
    if tones.count("light") < 6 or tones.count("hurting") < 5:
        problems.append("the set needs at least 6 light and 5 hurting examples")
    light = [str(e.get("reply", "")) for e in items if e.get("tone") == "light"]
    if light and sum("!" in r for r in light) < 0.5 * len(light):
        problems.append("fewer than half of the light examples carry an exclamation mark")
    return problems
