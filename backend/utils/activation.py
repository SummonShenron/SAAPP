"""Whether a new account gets to value (the first-ten-minutes work, 2026-11-07), as counts only.

Two things are counted, nothing else:
- ACTIVATION: the first time a signed-in person has connected a project folder, had a proposed change applied, saved a memory, connected a runner, or put a document in their knowledge base. Once per
  person per thing (a flag on their settings record), counted as `activation_<thing>` in backend/utils/self_counters.py. The flag is what keeps the welcome screen from offering a step again after it is done.
- WELCOME CARDS: which of the new-account cards was pressed (`welcome_card_<kind>`).

Neither records a message, a file name, a path or what was in a memory: a count and a name from a fixed list. The guests share one identity and are not counted here (their funnel is backend/utils/guest_signup.py).
"""
import datetime
import logging
from typing import Any, Dict, Iterable, List, Optional, Set

from backend.utils.self_counters import record_turn_counts, tally

logger = logging.getLogger("SASS Logger")

# The steps of getting value. The words are counter keys, so they are letters only.
EVENTS = ("folder", "applied", "memory", "runner", "document")

# The new-account welcome cards (backend/utils/example_questions.py): `demo` the demo project, `remember` a memory to save, `runner` the runner setup, `document` the knowledge-base page.
KINDS = ("demo", "remember", "runner", "document")


def _counts_user(username: Optional[str]) -> bool:
    """Signed-in people only: not the shared guests, not the embedded view, not nobody."""
    from backend.services.local_workspace import is_guest_username

    return bool(username) and not is_guest_username(username)


def activated(db: Any, username: Optional[str]) -> Set[str]:
    """The steps this person has already been seen to complete. Empty when unknown. Blocking; never raises."""
    if db is None or not _counts_user(username):
        return set()
    try:
        doc = db["user_settings"].find_one({"username": username}, {"activated": 1, "_id": 0}) or {}
        return {event for event in (doc.get("activated") or {}) if event in EVENTS}
    except Exception:
        logger.warning("[activation] could not read the activation flags.")
        return set()


def mark(db: Any, username: Optional[str], event: str) -> bool:
    """Notes that this person completed a step, once. True the first time (and counts it), False if it was already noted, unknown, or could not be saved. Blocking; never raises."""
    if db is None or event not in EVENTS or not _counts_user(username):
        return False
    try:
        if event in activated(db, username):
            return False
        db["user_settings"].update_one(
            {"username": username}, {"$set": {f"activated.{event}": datetime.datetime.now(datetime.timezone.utc)}}, upsert=True,
        )
        counts: Dict[str, int] = {}
        tally(counts, "activation", event)
        record_turn_counts(counts)
        return True
    except Exception:
        logger.warning("[activation] could not note a completed step.")
        return False


def observe(db: Any, username: Optional[str], done: Iterable[str]) -> List[str]:
    """Notes every step in `done` the person is seen to have completed (from their real state: a folder held, a runner connected, a memory or a document that exists), and returns the ones that were new."""
    return [event for event in done if mark(db, username, event)]


def count_card(username: Optional[str], kind: Any) -> Optional[str]:
    """Counts one press of a new-account welcome card and returns its kind, or None when nothing was counted (a guest, or a name that is not on the list). Blocking; never raises."""
    try:
        if not _counts_user(username) or not isinstance(kind, str) or kind not in KINDS:
            return None
        counts: Dict[str, int] = {}
        tally(counts, "welcome_card", kind)
        record_turn_counts(counts)
        return kind
    except Exception:
        logger.warning("[activation] could not count a welcome card.")
        return None
