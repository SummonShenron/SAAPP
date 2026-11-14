"""Reviewed facts about how a situation really works, shown to the reply when someone who is hurting is facing it (the registry is backend/components/sonic_situation_facts.py).

WHY (2026-11-13). Sonnet, asked the same thing as Haiku, added something true about the person's situation ("companies that run several interview rounds weigh the whole loop"); Haiku, left to find a fact itself, guessed ("her posting it means
she did not hide the event") and said the guess with confidence. A fact about the world has to come from somewhere reliable, so it comes from here: written once, read and approved by a person, retrieved by meaning like the voice examples
(backend/utils/voice_examples.py), and never invented by the model.

WHEN. Only on a conversational reply, only in an emotional context (a mood was read: the facts are for a hurting person, not a casual question), never under a safety plan. At most PER_TURN facts, above a high threshold (a fact that
does not fit teaches a wrong claim). The block tells the model the fact is general, that it must not state it as certain for this person's case, and that it may add no other claim about their situation.

NOTHING PERSONAL. The message is embedded and compared, never stored; the facts are about processes (interviews, applications, reviews), never about a person. What is recorded is a count per fact, so one that never matches can be reworded or removed.
"""
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from backend.components import sonic_situation_facts as registry
from backend.utils import leanings, voice_review
from backend.utils.emotion_checks import GENERAL_RE
from backend.utils.identity_checks import identity_reply_issue

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_situation_fact_index"
MIN_SCORE = float(os.getenv("SITUATION_FACTS_MIN_SCORE", "0.72"))        # a little above the voice examples' 0.70: a wrong fact does more harm than a wrong register
PER_TURN = max(int(os.getenv("SITUATION_FACTS_PER_TURN", "1")), 1)
MIN_REQUEST_CHARS = 8
MAX_REQUEST_CHARS = 700
MAX_FACT_CHARS = 240
ALLOWED_ROUTES = frozenset({"conversational"})
_cache: Dict[str, Any] = {"at": 0.0, "vectors": {}}


def enabled() -> bool:
    return os.getenv("SITUATION_FACTS_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


def live(entries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """The facts that may be used: only the ones a person approved. A draft is never put in a prompt. Pure."""
    return [e for e in (registry.FACTS if entries is None else entries) if e.get("approved") is True]


def view(entry: Dict[str, Any]) -> Dict[str, Any]:
    """An entry in the shape the leanings' index and ranking read: the situation is what a message resembles (`when`), the facts the text (`line`), the invented messages `asks`. Pure."""
    return {"id": entry["id"], "when": entry.get("situation", ""), "line": " ".join(entry.get("facts") or []), "asks": list(entry.get("asks") or []), "approved": entry.get("approved") is True}


def select(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    source_type: str = "conversational",
    risk_active: bool = False,
    emotional_active: bool = False,
    vector: Optional[List[float]] = None,
    entries: Optional[List[Dict[str, Any]]] = None,
    threshold: Optional[float] = None,
    k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The facts for this message, best first ([] when the feature is off, a gate is closed, or nothing is close enough). Never raises. Blocking (at most one embedding)."""
    try:
        if not enabled() or risk_active or not emotional_active or source_type not in ALLOWED_ROUTES:
            return []
        # A person's decisions on the dashboard (backend/utils/voice_review.py) win over the registry's own approved flags.
        approved = live(voice_review.apply(registry.FACTS if entries is None else entries, "fact", voice_review.overrides(db)))
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
        logger.warning("[situation_facts] could not choose facts for this message.", exc_info=True)
        return []


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
                logger.warning("[situation_facts] the background index build failed.", exc_info=True)
            finally:
                _warming["on"] = False

        threading.Thread(target=build, name="situation-facts-index", daemon=True).start()
        return True
    except Exception:
        _warming["on"] = False
        return False


BLOCK_HEAD = (
    "FACTS ABOUT HOW THIS KIND OF SITUATION WORKS (written and approved by a person, true in general). Use at most one, and only if it truly fits what they said; say it plainly in your own words, as how the thing works and not as "
    "a promise about their case. Do not add any other claim about their situation, or about what other people think or meant, beyond what they told you.\n"
)


def build_block(chosen: List[Dict[str, Any]]) -> str:
    """The prompt block for the chosen facts, or "". Pure."""
    lines = [f"- {fact}\n" for entry in chosen for fact in (entry.get("facts") or [])]
    return ("\n" + BLOCK_HEAD + "".join(lines)) if lines else ""


def tally_turn(counts: Dict[str, int], chosen: List[Dict[str, Any]], tally: Callable[..., None]) -> None:
    """Adds this turn's counters with the counters' own `tally`: one for the turn and one per entry. Counts only."""
    if not chosen:
        return
    tally(counts, "situation_facts_offered")
    for entry in chosen:
        tally(counts, "situation_fact_used", entry["id"])


# ---- what the registry may contain -------------------------------------------------------------------------------------------------------------------------------------------------------------
BLOCKED_TERMS = ("jack", "yashu", "lahari", "casey", "pfg", "pag ui", "saapp", "sonic", "erragent", "render", "claude", "haiku", "flash")
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_STAT_RE = re.compile(r"\d+\s*(?:%|percent|per cent)|\b(?:\d+ (?:in|out of) \d+)\b", re.IGNORECASE)


def check_registry(entries: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    """Problems with the facts, in words ([] when they are sound). Checks drafts too. Each: a unique snake_case id; a situation and three or four invented messages; one to three facts of one sentence each within MAX_FACT_CHARS; no
    blocked term, no statistic (a number is the first thing to go stale or be wrong), no claim about how such things "often" or "usually" go (the check a reply is held to), no claimed feeling, no list or em dash; and an `approved` flag. Pure."""
    problems: List[str] = []
    seen = set()
    for entry in registry.FACTS if entries is None else entries:
        entry_id = str(entry.get("id", ""))
        if not _ID_RE.match(entry_id) or entry_id in seen:
            problems.append(f"{entry_id or '(no id)'}: the id must be unique snake_case")
        seen.add(entry_id)
        situation = str(entry.get("situation", "")).strip()
        if not situation or len(situation) > registry.MAX_SITUATION_CHARS:
            problems.append(f"{entry_id}: a situation of at most {registry.MAX_SITUATION_CHARS} characters is needed")
        asks = entry.get("asks") or []
        if not 3 <= len(asks) <= 4 or any(not str(a).strip() or len(str(a)) > registry.MAX_ASK_CHARS for a in asks):
            problems.append(f"{entry_id}: asks must be 3 or 4 invented messages of at most {registry.MAX_ASK_CHARS} characters")
        facts = entry.get("facts") or []
        if not 1 <= len(facts) <= 3:
            problems.append(f"{entry_id}: one to three facts are needed")
        for fact in facts:
            fact = str(fact).strip()
            if not fact or len(fact) > MAX_FACT_CHARS or len(_SENTENCE_RE.split(fact)) != 1:
                problems.append(f"{entry_id}: each fact must be one sentence of at most {MAX_FACT_CHARS} characters")
            if GENERAL_RE.search(fact):
                problems.append(f"{entry_id}: a fact talks about how such things often or usually go ({fact[:40]}...)")
            if _STAT_RE.search(fact):
                problems.append(f"{entry_id}: a fact carries a statistic ({fact[:40]}...)")
            if "—" in fact or re.search(r"^\s*(?:[-*]|\d+\.)\s", fact):
                problems.append(f"{entry_id}: a fact uses an em dash or a list")
            if identity_reply_issue(fact):
                problems.append(f"{entry_id}: a fact fails the identity check ({identity_reply_issue(fact)})")
        everything = " ".join([situation, *map(str, facts), *map(str, asks)]).lower()
        for term in BLOCKED_TERMS:
            if term.lower() in everything:
                problems.append(f"{entry_id}: carries '{term}', which belongs to a real conversation or to the product")
        if not isinstance(entry.get("approved"), bool):
            problems.append(f"{entry_id}: approved must be true or false")
    return problems
