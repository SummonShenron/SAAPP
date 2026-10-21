"""One person, two spellings: "Ashlyn" and "Ashlynn" (docs/memory-grounding-plan.md, step 5).

THE FAILURE (2026-10-20): the name was stored both ways, so the same date was two people to anything that compared names, and a calendar title spelled one way never matched a plan spelled the other.

THE RULE, deliberately narrow because a wrong merge is worse than a missed one (Mark and Marc can be two people):
- Two names are variants only when they are NEAR-IDENTICAL: the same first letter, at least MIN_LETTERS letters each, and one edit apart (a letter added, dropped, changed or two swapped). A name of
  several words must differ in one word only.
- Facts are compared only INSIDE a cluster the person's own notes already bind together (facts linked to each other), never across the whole store, and only names written in the middle of a sentence
  (a sentence's first word is "Likes" or "Lives" often enough to be one edit from each other).
- The spelling shown is the person's latest (the most recently written one), because a changed spelling is almost always a correction.
- The person can say "these are different people" (stored on their own settings, as a sorted pair), and then the two are never treated as one again: not in an event, not in the facts, not on the calendar.
- No model, no log line with a name in it.
"""
import logging
import re
import time as _time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger("SASS Logger")

MIN_LETTERS = 5
SETTINGS_COLLECTION = "user_settings"
SETTINGS_KEY = "name_alias_denied"
MAX_DENIED = 50
DENIED_CACHE_SECONDS = 60.0
MAX_CACHED_PEOPLE = 500

_NAME_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_CAP_WORD_RE = re.compile(r"\b[A-Z][a-z]{%d,}\b" % (MIN_LETTERS - 1))
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
# Capitalised words in the middle of a sentence that are not people (and could be one edit from each other).
_NOT_NAMES = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "june", "july", "august", "september", "october", "november",
    "december", "sonic", "saapp", "patchy", "python", "javascript", "typescript", "english", "spanish", "french", "german", "christmas", "easter", "google", "gmail", "github", "microsoft",
    "principal", "mongodb", "react", "flask", "django", "linux", "windows", "android",
})


def key_of(name: str) -> str:
    return " ".join(_NAME_RE.findall(str(name or "").casefold()))


def _within_one_edit(a: str, b: str) -> bool:
    """True when `a` and `b` are one letter added, dropped or changed, or two neighbouring letters swapped (and are not the same)."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:
            return True
        return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]
    short, long_ = (a, b) if la < lb else (b, a)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    return short[i:] == long_[i + 1:]


def pair_key(a: str, b: str) -> str:
    first, second = sorted((key_of(a), key_of(b)))
    return f"{first}|{second}"


def is_variant(a: str, b: str) -> bool:
    """Near-identical spellings of one name (see the rule above). Case does not matter; the same name spelled the same is not a *variant* (it is the same)."""
    ka, kb = key_of(a), key_of(b)
    if not ka or not kb or ka == kb:
        return False
    wa, wb = ka.split(), kb.split()
    if len(wa) != len(wb):
        return False
    differing = [(x, y) for x, y in zip(wa, wb) if x != y]
    if len(differing) != 1:
        return False
    x, y = differing[0]
    return len(x) >= MIN_LETTERS and len(y) >= MIN_LETTERS and x[0] == y[0] and _within_one_edit(x, y)


def same_person(a: str, b: str, denied: Iterable[str] = ()) -> bool:
    """The same name (any case) or a near-identical spelling of it, unless the person said these two are different people."""
    if key_of(a) and key_of(a) == key_of(b):
        return True
    return is_variant(a, b) and pair_key(a, b) not in set(denied)


def canonical(spellings: Sequence[Tuple[str, str]]) -> str:
    """The spelling to show: the most recently written one. `spellings` is [(name, when)], `when` any sortable stamp (an ISO time); a tie keeps the one seen last."""
    best_name, best_when = "", ""
    for name, when in spellings:
        if name and str(when or "") >= best_when:
            best_name, best_when = name, str(when or "")
    return best_name


# ---------------------------------------------------------------------------------------------------------------
# Names in the facts, inside the clusters the person's notes already bind together
# ---------------------------------------------------------------------------------------------------------------

def names_in(text: str) -> List[str]:
    """The capitalised words in the middle of a sentence that could be a person's name (never a sentence's first word, a weekday, a month or a product), as written."""
    out: List[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split((text or "").strip()):
        for match in _CAP_WORD_RE.finditer(sentence):
            if match.start() == 0:
                continue
            if match.group(0).casefold() not in _NOT_NAMES and match.group(0) not in out:
                out.append(match.group(0))
    return out


def _clusters(facts: Sequence[Any]) -> List[List[Any]]:
    """Facts grouped by the links between them (a fact and what it is `related` to, however far the chain goes). A fact bound to nothing is alone."""
    by_id = {f.id: f for f in facts}
    parent = {f.id: f.id for f in facts}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for fact in facts:
        for other in getattr(fact, "related", []) or []:
            if other in by_id:
                parent[find(fact.id)] = find(other)
    groups: Dict[str, List[Any]] = {}
    for fact in facts:
        groups.setdefault(find(fact.id), []).append(fact)
    return list(groups.values())


def variant_groups(facts: Sequence[Any], denied: Iterable[str] = ()) -> List[Dict[str, Any]]:
    """[{"names": [every spelling found], "shown_as": the latest spelling, "fact_ids": [the facts that use any of them]}]: names written two ways inside one cluster of linked facts. A pair
    the person said are different people is never grouped. Pure."""
    denied_set = set(denied)
    found: List[Dict[str, Any]] = []
    for cluster in _clusters(facts):
        if len(cluster) < 2:
            continue
        seen: Dict[str, List[Tuple[Any, str]]] = {}
        for fact in cluster:
            for name in names_in(getattr(fact, "fact", "")):
                seen.setdefault(name, []).append((fact, name))
        names = sorted(seen)
        done: Set[str] = set()
        for i, name in enumerate(names):
            if name in done:
                continue
            members = [name] + [other for other in names[i + 1:] if other not in done and is_variant(name, other) and pair_key(name, other) not in denied_set]
            if len(members) < 2:
                continue
            done.update(members)
            used = [(f, spelled) for spelled in members for f, _ in seen[spelled]]
            when = {m: max((str(getattr(f, "updated_at", "")) for f, s in used if s == m), default="") for m in members}
            found.append({
                "names": sorted(members, key=str.casefold),
                "shown_as": canonical([(m, when[m]) for m in members]),
                "fact_ids": sorted({f.id for f, _ in used}),
            })
    return found


def facts_note(facts: Sequence[Any], denied: Iterable[str] = (), limit: int = 2) -> str:
    """A line for the reply prompt when facts it is about to see spell one name two ways, or "" (the usual case). It says they are very likely one person and which spelling to use."""
    lines = []
    for group in variant_groups(facts, denied)[:limit]:
        others = [n for n in group["names"] if n != group["shown_as"]]
        lines.append(
            f"\"{group['shown_as']}\" and \"{', '.join(others)}\" in the notes above are almost certainly the same person, written two ways. Write the name as \"{group['shown_as']}\" "
            "(the spelling they used most recently) and do not treat them as two people."
        )
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


# ---------------------------------------------------------------------------------------------------------------
# "These are different people" (the person's own say-so)
# ---------------------------------------------------------------------------------------------------------------

_denied_cache: Dict[str, Tuple[float, List[str]]] = {}


def clear_cache() -> None:
    _denied_cache.clear()


def get_denied(db: Any, username: str) -> List[str]:
    """The pairs this person said are different people, as sorted "a|b" keys. Blocking."""
    stored = (db[SETTINGS_COLLECTION].find_one({"username": username}, {"_id": 0, SETTINGS_KEY: 1}) or {}).get(SETTINGS_KEY) or []
    return [str(item) for item in stored if isinstance(item, str)][:MAX_DENIED]


def denied_for(username: str, db: Any = None) -> List[str]:
    """get_denied, kept for a minute (the facts block asks on every turn). [] on any problem: a missing list only means two spellings may be treated as one, which the person can undo. Blocking."""
    if not username:
        return []
    held = _denied_cache.get(username)
    if held is not None and _time.monotonic() - held[0] < DENIED_CACHE_SECONDS:
        return held[1]
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        pairs = get_denied(db, username) if db is not None else []
    except Exception:
        logger.warning("[names] could not read the names marked as different.", exc_info=True)
        return []
    if len(_denied_cache) >= MAX_CACHED_PEOPLE:
        _denied_cache.clear()
    _denied_cache[username] = (_time.monotonic(), pairs)
    return pairs


def forget_all(username: str) -> int:
    """Part of "forget everything": the names they said were different people are names from their life."""
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        _denied_cache.pop(username, None)
        if db is None:
            return 0
        held = len(get_denied(db, username))
        db[SETTINGS_COLLECTION].update_one({"username": username}, {"$set": {SETTINGS_KEY: []}})
        return held
    except Exception:
        logger.warning("[names] could not clear the names marked as different.", exc_info=True)
        return 0


def set_different(db: Any, username: str, a: str, b: str, different: bool = True) -> List[str]:
    """Records that two names are (or, with different=False, are no longer said to be) different people. Only a pair that really are spelling variants is kept, and at most MAX_DENIED
    are. Returns the person's list. Blocking."""
    key = pair_key(a, b)
    pairs = [p for p in get_denied(db, username) if p != key]
    if different and is_variant(a, b):
        pairs.append(key)
    pairs = pairs[-MAX_DENIED:]
    db[SETTINGS_COLLECTION].update_one({"username": username}, {"$set": {SETTINGS_KEY: pairs}}, upsert=True)
    _denied_cache.pop(username, None)
    return pairs
