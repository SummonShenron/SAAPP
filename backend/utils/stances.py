"""Choosing one of Sonic's approved stances about people and life (backend/components/sonic_stances.py) for a turn where a view is wanted.

WHEN. Only on a turn that already calls for a view: the person asked what it thinks (backend/utils/view_check.py), they chose advice on the support card, or the turn's low move is its own
read (backend/utils/low_moves.py). Never under a safety plan, on a closing message or for a guest: the caller decides those. A stance is offered, not placed: on these turns the reply is already
told to state a view, and the stance gives it one that is its own, to be said in its own words.

HOW. A trigger phrase in the message wins; otherwise the stance closest in meaning, if it clears MIN_SCORE (the relatable lines' own matcher and threshold). `used` says whether the finished
reply really used it (backend/utils/material_use.py), so offered and used are counted apart.

Pure apart from the registry; nothing is logged.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from backend.components.sonic_stances import APPROVED_IDS, STANCES
from backend.utils import material_use

ID_PREFIX = "stance_"
MAX_LINE_CHARS = 240
MIN_USED_WORDS = 2
_ID_RE = re.compile(r"^stance_[a-z_]{3,60}$")
_VIEW_START_RE = re.compile(r"^(?:I think|I don't think|I'd rather|I'd pick|I lean toward|When [^,]{3,80}, I think)\b")
_NOT_A_VIEW_RE = re.compile(
    r"\bI (?:feel|felt|remember|recall|love|hate|miss|worry|hope|wish)\b|\bI(?:'ve| have) been (?:there|through)\b|\bin my (?:life|experience)\b|\byou (?:should|must|need to|have to)\b",
    re.IGNORECASE,
)


def approved(entries: Optional[List[Dict[str, Any]]] = None, approved_ids: Optional[Tuple[str, ...]] = None) -> List[Dict[str, Any]]:
    """The stances that may be used: those whose id is approved. Pure."""
    ids = set(APPROVED_IDS if approved_ids is None else approved_ids)
    return [dict(entry) for entry in (STANCES if entries is None else entries) if entry.get("id") in ids]


def is_stance(entry: Optional[Dict[str, Any]]) -> bool:
    return bool(entry) and str(entry.get("id") or "").startswith(ID_PREFIX)


def check_registry(entries: Optional[List[Dict[str, Any]]] = None, approved_ids: Optional[Tuple[str, ...]] = None) -> List[str]:
    """Problems with the registry: a bad or repeated id, a missing field, a line that is not a view or is too long, or an approved id with no entry. Pure."""
    entries = STANCES if entries is None else entries
    problems: List[str] = []
    seen = set()
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not _ID_RE.match(entry_id):
            problems.append(f"{entry_id or '(no id)'}: the id must be stance_ and lowercase words")
        if entry_id in seen:
            problems.append(f"{entry_id}: the id is used twice")
        seen.add(entry_id)
        line = str(entry.get("line") or "")
        if not line or len(line) > MAX_LINE_CHARS:
            problems.append(f"{entry_id}: the line must be 1 to {MAX_LINE_CHARS} characters")
        elif not _VIEW_START_RE.match(line):
            problems.append(f"{entry_id}: the line must open as a view (I think, I don't think, I'd rather, I'd pick, I lean toward)")
        if _NOT_A_VIEW_RE.search(line):
            problems.append(f"{entry_id}: the line claims a feeling or a past of its own, or tells the person what to do")
        if not entry.get("triggers"):
            problems.append(f"{entry_id}: it needs trigger phrases")
    for entry_id in (APPROVED_IDS if approved_ids is None else approved_ids):
        if entry_id not in seen:
            problems.append(f"{entry_id}: approved, but there is no such stance")
    return problems


def select(message: str, *, wanted: bool, entries: Optional[List[Dict[str, Any]]] = None, vector: Optional[List[float]] = None,
           vectors: Optional[Dict[str, List[float]]] = None) -> Optional[Dict[str, Any]]:
    """The approved stance for this message, or None. `wanted` is the caller's "this turn calls for a view"; without it nothing is chosen. A trigger phrase wins; otherwise, given the message's
    embedding and the entries' (relatable_utils.entry_vectors), the closest in meaning above the relatable threshold, returned as a copy marked {"match": "meaning"}. Pure."""
    pool = approved() if entries is None else entries
    if not wanted or not pool or not str(message or "").strip():
        return None
    from backend.utils.relatable_utils import _closest_in_meaning, _triggered

    best, best_hits = None, 0
    for entry in pool:
        hits = _triggered(message, entry.get("triggers", []))
        if hits > best_hits:
            best, best_hits = entry, hits
    if best is None and vector and vectors:
        closest = _closest_in_meaning(vector, pool, vectors)
        if closest:
            return {**closest, "match": "meaning"}
    return best


def build_block(entry: Optional[Dict[str, Any]]) -> str:
    """The prompt section for an offered stance ("" when none). Pure."""
    if not entry:
        return ""
    return (
        f"\nA VIEW YOU HOLD that bears on this: \"{entry['line']}\"\nSay it in your own words as what you think, with its reason, fitted to what they told you. It is a view, not a rule for "
        "them and not something you lived. If it does not fit what they asked, leave it out.\n"
    )


def used(reply: str, entry: Optional[Dict[str, Any]]) -> bool:
    """True when the finished reply shares at least MIN_USED_WORDS distinctive words with the stance (a looser test than material_use.used_line: the reply is asked to reword it). Pure."""
    if not entry:
        return False
    return len(material_use._words(reply) & material_use._words(entry.get("line", ""))) >= MIN_USED_WORDS
