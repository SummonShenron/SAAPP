"""Sonic's working character for one turn: the leanings that fit the request, and a real case from its own record when one genuinely fits.

Both are found the same way (by meaning, one embedding that the turn usually already has), put in the prompt after everything stable, and held to the same rules, so a tool turn and a plain
conversation share one entry point:
- leanings (backend/utils/leanings.py): how it tends to do this kind of work, under the philosophy each follows from, with the reason to give;
- an experience (backend/utils/experiences.py): one dated, abstract, real event from its own recorded work, cited as a record and never as a recollection.

The caller decides whether an experience may be offered this turn (`allow_experience`: not under a safety context, a heavy mood or a closing message, not twice in ten messages, and not when another
proactive item already has the turn's one slot). Nothing here raises, and a turn never waits for the indexes (they are built in the background).
"""
import logging
from typing import Any, Callable, Dict, List, Optional

from backend.utils import experience_reflections, experiences, leanings

logger = logging.getLogger("SASS Logger")


def for_turn(
    request: str,
    db: Any,
    embed: Callable[[str], Optional[List[float]]],
    *,
    vector: Optional[List[float]] = None,
    allow_experience: bool = False,
) -> Dict[str, Any]:
    """{"text": the prompt block ("" when nothing fits), "leanings": [ids], "experience": the offered entry or None}. Blocking (at most one embedding, shared by both). Never raises."""
    empty: Dict[str, Any] = {"text": "", "leanings": [], "experience": None}
    try:
        text_in = (request or "").strip()
        if len(text_in) < leanings.MIN_REQUEST_CHARS:
            return empty
        asked = vector or embed(text_in[: leanings.MAX_REQUEST_CHARS])
        chosen = leanings.select(text_in, db, embed, vector=asked)
        experience = experiences.select(text_in, db, embed, vector=asked) if allow_experience else None
        reflection = experience_reflections.approved_for(db, experience["id"]) if experience else ""      # what it takes from that case, if one was approved
        text = leanings.build_block(chosen) + (experiences.build_block(experience, reflection) if experience else "")
        return {"text": text, "leanings": [entry["id"] for entry in chosen], "experience": experience}
    except Exception:
        logger.warning("[character] could not choose leanings or a case for this turn.", exc_info=True)
        return empty
