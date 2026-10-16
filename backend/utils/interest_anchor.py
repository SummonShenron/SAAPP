"""A reason for Sonic to open a conversation: something the person told it they are into.

The opener in initiation.py is anchored to something concrete the person gave Sonic. Until now that was an open loop whose day had
passed, or a goal gone quiet. This adds a third, the lightest one: a topic they enjoy (a band, a game, a craft), asked about with
one open question, like "You mentioned your band was rehearsing. What are you playing?".

What makes it safe is what it refuses to be:

- NOT A WISH OF SONIC'S. The question is anchored to the person's own words and written in the present tense. The checks in
  initiation.py still reject "I was curious", "I've been wondering", and any remark about their absence: nothing ran between
  conversations, and Sonic never implies otherwise. (This is about the USER's interests. Sonic's own leanings in
  sonic_profile.INTERESTS are a different thing and are never used here.)
- ONLY A TOPIC THEY ENJOY. Not how they like answers written, not work, and above all nothing sensitive: health, grief, money,
  relationships ending, politics and the like are never eligible, by a fixed list that does not depend on any model, and the
  finished question is checked against the same list.
- RARER THAN ANYTHING ELSE. It is considered only when there is no loop or goal to ask about, only once every 14 days, each
  interest at most once every 90 days, and never while an earlier message of Sonic's is still unanswered (so it only ever speaks
  to someone who is already answering it).
- A FACT IS NOT A LICENCE. It must have been known for a few days, so Sonic never springs something on someone minutes after they
  said it.
- THE MODEL ONLY CHOOSES. A fixed filter makes the candidate list; one small model call picks AT MOST ONE from it, by id, and the
  answer is checked against the list. If the call fails or says none, nothing is sent: it never guesses.

The call happens only after every other gate has passed, which is rare, so the cost is a few small calls a month per person who has
turned Message First on.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("SASS Logger")

MIN_DAYS_BETWEEN = 14
PER_INTEREST_DAYS = 90
MIN_FACT_AGE_DAYS = 3
MIN_CONFIDENCE = 0.6
MAX_CANDIDATES = 8
MIN_FACT_CHARS = 8
MAX_ANCHOR_CHARS = 140
CATEGORY = "preference"

KIND = "interest"


def interest_openings_enabled() -> bool:
    """A server-side kill switch (INTEREST_OPENING_ENABLED=false) for this one kind of opener, whatever users have chosen."""
    return os.getenv("INTEREST_OPENING_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")


# Anything on these lists is never asked about: the topic is not Sonic's to bring up. Over-matching only costs an opener.
_SENSITIVE_RE = re.compile(
    r"\b(?:health|illness|ill|sick|sickness|disease|cancer|diagnos\w*|medic\w*|surgery|hospital\w*|therap\w*|counsel\w*|"
    r"depress\w*|anxi\w*|panic|mental|disorder|adhd|autis\w*|ptsd|trauma\w*|abus\w*|assault\w*|addict\w*|alcohol\w*|"
    r"drunk|sober|relaps\w*|drugs?|suicid\w*|self[- ]harm|grief|griev\w*|mourn\w*|died|death|dead|funeral|passed away|"
    r"miscarr\w*|pregnan\w*|fertil\w*|divorc\w*|breakup|broke up|separat\w*|affair|cheat\w*|custody|"
    r"ex[- ](?:wife|husband|girlfriend|boyfriend|partner)|money|financ\w*|debt|loans?|bankrupt\w*|mortgage|salary|income|"
    r"fired|laid off|layoffs?|unemploy\w*|lawsuit|legal|court|arrest\w*|immigra\w*|visa|diet|dieting|eating disorder|"
    r"sex\w*|porn\w*|nsfw|religio\w*|politic\w*|election|vot(?:e|ed|ing))\b",
    re.IGNORECASE,
)
# Facts about how Sonic should work with them, not about something they enjoy.
_STYLE_RE = re.compile(
    r"\b(?:answers?|responses?|repl(?:y|ies)|explain\w*|concise|brief|terse|verbose|format\w*|tone|bullets?|summar\w*|"
    r"code|coding|tests?|pull requests?|commits?|docstrings?|type hints?|prefers? (?:short|long|detailed)|"
    r"(?:wants?|likes?) (?:short|long|concise|detailed|direct))\b",
    re.IGNORECASE,
)


def is_sensitive(text: Any) -> bool:
    return bool(_SENSITIVE_RE.search(str(text or "")))


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------------------------
# Checking what the model wrote (found with the real model: it invents details the person never said)
# ---------------------------------------------------------------------------------------------------------------

MAX_OPENER_WORDS = 25
_TWO_ASKS_RE = re.compile(
    r",\s*(?:and|but|or)\s+(?:how|what|have|has|did|do|does|are|is|were|was|which|who|where|when|would|will|can|could)\b", re.IGNORECASE
)
_NAME_RE = re.compile(r"\b[A-Z][a-zA-Z]+\b")


def opening_issue(body: str, anchor_text: str) -> Optional[str]:
    """What is wrong with a written question about an interest, phrased to follow "it ", or None. These are checks the opener's
    general ones don't make: it must stay short and plain, ask one thing, stay off sensitive ground, and above all not put
    details in the person's mouth. Tried with the real model, it added things like "the Namek saga" to "loves Dragon Ball" and
    "the jazz records you were looking for" to "collects vinyl". Anything that looks like a name or title the fact doesn't
    contain is therefore refused (the template stands in), as is a second question tacked on with a comma."""
    if is_sensitive(body):
        return "touched a topic that isn't Sonic's to bring up"
    if len(body.split()) > MAX_OPENER_WORDS:
        return f"ran past {MAX_OPENER_WORDS} words for a plain question"
    if _TWO_ASKS_RE.search(body):
        return "asked two things in one"
    known = {word.lower() for word in re.findall(r"[A-Za-z']+", anchor_text or "")}
    # The first word starts the sentence, so its capital proves nothing; any later capitalised word is a name or a title.
    for word in _NAME_RE.findall(body.split(" ", 1)[1] if " " in body else ""):
        if word.lower() not in known and word != "I":
            return f"named something they never said ({word})"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Whether to look for an interest at all
# ---------------------------------------------------------------------------------------------------------------

def allowed(state: Dict[str, Any], now: datetime) -> bool:
    """The gates that belong to this kind of opener, on top of the ones every opener passes (initiation.refusal_reason)."""
    if not interest_openings_enabled():
        return False
    if int(state.get("unanswered") or 0) > 0:
        return False  # only ever speaks to someone who is already answering Sonic's earlier messages
    last = state.get("last_interest_at")
    return not (last and (now - last) < timedelta(days=MIN_DAYS_BETWEEN))


# ---------------------------------------------------------------------------------------------------------------
# Which facts could be one
# ---------------------------------------------------------------------------------------------------------------

def eligible(fact: Any, now: datetime) -> bool:
    """Whether one remembered fact may be offered to the picker. Pure: no model, no database."""
    text = str(getattr(fact, "fact", "") or "").strip()
    if getattr(fact, "category", None) != CATEGORY or not getattr(fact, "active", True):
        return False
    if len(text) < MIN_FACT_CHARS or is_sensitive(text) or _STYLE_RE.search(text):
        return False
    if float(getattr(fact, "confidence", 1.0) or 0) < MIN_CONFIDENCE:
        return False
    created = _parse(getattr(fact, "created_at", None))
    if created is None or (now - created) < timedelta(days=MIN_FACT_AGE_DAYS):
        return False
    asked = _parse(getattr(fact, "last_nudged_at", None))
    return not (asked and (now - asked) < timedelta(days=PER_INTEREST_DAYS))


def candidates(facts: List[Any], now: datetime) -> List[Any]:
    """The most recently reinforced eligible facts, at most MAX_CANDIDATES."""
    pool = [fact for fact in facts if eligible(fact, now)]
    pool.sort(key=lambda fact: str(getattr(fact, "updated_at", "") or ""), reverse=True)
    return pool[:MAX_CANDIDATES]


# ---------------------------------------------------------------------------------------------------------------
# Letting the model choose (and only choose)
# ---------------------------------------------------------------------------------------------------------------

def build_pick_prompt(pool: List[Any]) -> str:
    lines = "\n".join(f"{fact.id}: {str(fact.fact).strip()[:MAX_ANCHOR_CHARS]}" for fact in pool)
    return (
        "You are choosing whether any of these facts about a person is something they ENJOY or follow as a topic: a hobby, "
        "fandom, craft, sport, game, show, band, music or similar leisure interest.\n"
        "These are NOT interests: how they like answers or code written, their work, health, family, relationships, money, "
        "or anything hard or private. If a fact is not clearly a leisure interest, do not pick it.\n"
        "Pick at most ONE: the one that would be the most natural and welcome thing to ask them about.\n\n"
        f"FACTS (id: text):\n{lines}\n\n"
        'Reply with JSON only: {"id": "<the id>"} or {"id": null}.'
    )


def _response_text(response: Any) -> str:
    content = response.content if hasattr(response, "content") else response
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def parse_pick(text: str, pool: List[Any]) -> Optional[Any]:
    """The chosen fact, or None. Anything that is not an id from `pool` (a made-up id, null, prose, broken JSON) is None."""
    match = re.search(r"\{.*?\}", text or "", flags=re.S)
    if not match:
        return None
    try:
        chosen = json.loads(match.group(0)).get("id")
    except (ValueError, AttributeError):
        return None
    if not isinstance(chosen, str):
        return None
    return next((fact for fact in pool if fact.id == chosen), None)


async def choose_interest(
    username: str,
    invoke: Callable[[str], Awaitable[Any]],
    now: datetime,
    *,
    load_facts: Optional[Callable[[str], List[Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """`{"kind": "interest", "id", "text"}` for the one interest worth asking about, or None (the usual case). Never raises."""
    import asyncio

    try:
        if load_facts is None:
            from backend.utils import profile_utils
            from backend.utils.memory_utils import load_user_facts
            from backend.utils.user_settings_utils import get_profile_interest_asked, get_user_profile

            def load_facts(name: str) -> List[Any]:
                # What Sonic has learned, plus the interests the person listed on their profile, minus anything they asked not to be brought up.
                profile = get_user_profile(name)
                known = list(load_user_facts(name, CATEGORY)) + profile_utils.interest_facts(
                    profile, profile.get("updated_at"), get_profile_interest_asked(name)
                )
                return [fact for fact in known if not profile_utils.touches_avoided(getattr(fact, "fact", ""), profile)]
        facts = await asyncio.to_thread(load_facts, username)
        pool = candidates(facts, now)
        if not pool:
            return None
        picked = parse_pick(_response_text(await invoke(build_pick_prompt(pool))), pool)
        if picked is None:
            return None
        return {"kind": KIND, "id": picked.id, "text": str(picked.fact).strip()[:MAX_ANCHOR_CHARS]}
    except Exception:
        logger.warning("[interest_anchor] could not choose an interest.", exc_info=True)
        return None
