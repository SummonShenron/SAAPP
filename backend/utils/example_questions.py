"""Example questions on the welcome screen that are about this person, not a demo.

Built from what the server already knows (which documents are in the knowledge base, which integrations are
connected, whether a repo is set), so there is no model call and no latency, and a brand-new user with an
empty knowledge base still gets something real: a way to get started. Three tiers, in order:

  A. Specific to their own state ("What's in quarterly-notes.pdf?", "What's on my calendar today?")
  B. A nudge toward something not set up yet ("How do I connect my Google Calendar?")
  C. What the assistant can do, for filling the rest

Deliberately not used as a source: conversation titles (they are the user's first message verbatim, and this
screen can be seen by anyone looking over their shoulder) and the content of what the assistant remembers
about them (it holds things like who they turn to when things get dark). Memory only decides whether the
neutral "what do you remember about me?" card appears at all.
"""
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SASS Logger")

DEFAULT_LIMIT = 6
MAX_TIER_A = 4
_MAX_NAME_CHARS = 60
_DOC_LOOKUP_LIMIT = 5

_STARTERS = [
    "What can you help me with?",
    "Search the web for the latest news on AI agents",
    "Help me think through a decision I'm weighing",
]

# The welcome cards (build_welcome_cards): a few, each a different kind, so the screen shows range instead of a wall of look-alikes.
LABEL_YOURS = "From your stuff"
LABEL_SETUP = "Get started"
LABEL_TRY = "Try this"
LABEL_PICK = "Sonic's pick"
CARD_LIMIT = 4
SHELF_QUESTION = "What have you been reading lately?"      # must stay a question the reading shelf recognizes (reading_shelf.asks_what_reading)

# The one thing that needs a file first: it opens the photo/file picker (and leaves the question in the box) instead of sending its text.
ATTACH_CARD = {
    "text": "Photograph a letter or bill and ask what it means",
    "label": LABEL_TRY,
    "action": "attach",
    "prompt": "What does this say, and is there anything I need to do about it?",
}

# Everyday things Sonic does well, each one a request a person could send as it stands. None needs a document, an account, or any setup.
_TRY_THESE = [
    "Help me think through a decision I'm weighing",
    "Draft a polite email for me to look over before I send it",
    "Look into something I'm curious about and show me your sources",
    "Help me plan my week around what matters most",
]


@dataclass
class ExampleInputs:
    doc_names: List[str] = field(default_factory=list)  # newest first, only from the knowledge base(s) in scope
    owns_scope: bool = False                              # the scope is (or includes) their own knowledge base
    calendar_available: bool = True                       # False for shared identities, which can't connect one
    calendar_connected: bool = False
    target_repo: Optional[str] = None
    has_memories: bool = False
    shelf_available: bool = False                         # Sonic has something it has read to talk about, and the person allows it to bring things up


def clean_doc_name(filename: str) -> str:
    """A file name as a person would say it: no folders, no extension, bounded length."""
    name = re.sub(r"\s+", " ", str(filename or "").replace("\\", "/").split("/")[-1]).strip()
    name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name).strip()
    return name[:_MAX_NAME_CHARS].rstrip()


def _distinct_doc_names(names: List[str]) -> List[str]:
    seen, out = set(), []
    for raw in names:
        name = clean_doc_name(raw)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _tiers(inputs: ExampleInputs, rng: random.Random) -> tuple:
    """(tier A, tier B), each shuffled: questions about their own state, and nudges toward what is not set up yet."""
    docs = _distinct_doc_names(inputs.doc_names)

    tier_a: List[str] = []
    for name in docs[:2]:
        tier_a.append(f"What's in {name}?")
    if len(docs) >= 2:
        tier_a.append("Which topics come up across my documents?")
    if inputs.calendar_connected:
        tier_a += ["What's on my calendar today?", "Am I free tomorrow afternoon?"]
    if inputs.target_repo:
        tier_a.append(f"What changed in the last pull request on {inputs.target_repo}?")
    if inputs.has_memories:
        tier_a.append("What do you remember about me?")

    tier_b: List[str] = []
    if inputs.owns_scope and not docs:
        tier_b.append("How do I add a document to my knowledge base?")
    if inputs.calendar_available and not inputs.calendar_connected:
        tier_b.append("How do I connect my Google Calendar?")
    if not inputs.target_repo:
        tier_b.append("How do I point you at one of my GitHub repos?")

    rng.shuffle(tier_a)
    rng.shuffle(tier_b)
    return tier_a, tier_b


def build_welcome_cards(
    inputs: ExampleInputs, limit: int = CARD_LIMIT, rng: Optional[random.Random] = None
) -> List[Dict[str, str]]:
    """A few labelled cards, each a different kind of thing. What is true of this person leads (their own documents, calendar or repo,
    or, when none of that exists yet, how to get started); then one thing Sonic can do with no setup at all, then what Sonic has been
    reading when there is something to say. Any room left is filled from what remains, still personal first."""
    rng = rng or random.Random()
    tier_a, tier_b = _tiers(inputs, rng)
    own = [{"text": q, "label": LABEL_YOURS} for q in tier_a]
    setup = [{"text": q, "label": LABEL_SETUP} for q in tier_b]
    # Photographing a letter is the thing free chat tools cannot do with a person's own paperwork, so it is always the first thing to try.
    tries = [dict(ATTACH_CARD)] + [{"text": q, "label": LABEL_TRY} for q in rng.sample(_TRY_THESE, len(_TRY_THESE))]
    pick = [{"text": SHELF_QUESTION, "label": LABEL_PICK}] if inputs.shelf_available else []

    lead = own[:2] if own else setup[:2]
    chosen = lead + tries[:1] + pick
    leftovers = [card for card in own[2:] + (setup[2:] if not own else setup) + tries[1:] if card not in chosen]
    for card in leftovers:
        if len(chosen) >= limit:
            break
        chosen.insert(len(chosen) - len(pick), card)        # before Sonic's pick, which stays last
    return chosen[:limit]


def build_example_questions(
    inputs: ExampleInputs, limit: int = DEFAULT_LIMIT, rng: Optional[random.Random] = None
) -> List[str]:
    """Up to `limit` questions, most personal first. Shuffled within each tier so the screen varies between
    visits without ever pushing a generic question ahead of a personal one."""
    rng = rng or random.Random()
    tier_a, tier_b = _tiers(inputs, rng)
    starters = list(_STARTERS)
    rng.shuffle(starters)

    questions: List[str] = []
    for question in tier_a[:MAX_TIER_A] + tier_b + starters:
        if question not in questions:
            questions.append(question)
        if len(questions) >= limit:
            break
    return questions


def gather_example_inputs(username: str, affiliate: str, directory: Dict[str, Any]) -> ExampleInputs:
    """What the server knows that decides which questions make sense for this person and this knowledge-base
    scope. Each source fails soft (a missing piece just means fewer personal questions), because this must
    never be the reason the welcome screen is empty. Blocking: call it off the event loop."""
    from backend.utils.isolation_kb_utils import SHARED_IDENTITIES

    inputs = ExampleInputs()
    entry = directory.get(username) or {}
    shared = username in SHARED_IDENTITIES or entry.get("username") in SHARED_IDENTITIES
    personal_id = (entry.get("personal_kb") or {}).get("id")

    # "All" means everything they can query, but the documents worth naming are the ones they put there.
    scope = [personal_id] if affiliate == "All" and personal_id else ([] if affiliate == "All" else [affiliate])
    inputs.owns_scope = bool(personal_id) and (affiliate == "All" or affiliate == personal_id)

    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is not None and scope:
            rows = db["fs.files"].find(
                {"metadata.affiliate": {"$in": scope}, "metadata.status": "pages"}, {"filename": 1}
            ).sort("uploadDate", -1).limit(_DOC_LOOKUP_LIMIT * 2)
            inputs.doc_names = [row.get("filename", "") for row in rows][: _DOC_LOOKUP_LIMIT]
    except Exception:
        logger.warning("[example questions] could not list documents.", exc_info=True)

    if shared:
        inputs.calendar_available = False
        return inputs

    try:
        from backend.utils.google_calendar_utils import get_connection_status

        inputs.calendar_connected = bool(get_connection_status(username).get("connected"))
    except Exception:
        logger.warning("[example questions] could not read the calendar connection.", exc_info=True)

    try:
        from backend.utils.user_settings_utils import get_user_settings_bundle

        inputs.target_repo = (get_user_settings_bundle(username).get("target_repo") or None)
    except Exception:
        logger.warning("[example questions] could not read the target repo.", exc_info=True)

    try:
        from backend.utils.memory_utils import load_user_facts
        from backend.utils.safety_utils import SUPPORT_FACT_SOURCE

        # Only whether there is anything; never what it says, and never counting what the safety layer wrote.
        inputs.has_memories = any(
            getattr(f, "active", True) and getattr(f, "source", "") != SUPPORT_FACT_SOURCE
            for f in load_user_facts(username)
        )
    except Exception:
        logger.warning("[example questions] could not read memory.", exc_info=True)

    try:
        # Sonic's pick only when Sonic has read something and the person lets it bring things up (the switch on their profile).
        from backend.utils import profile_utils, reading_shelf
        from backend.utils.db_utils import get_db
        from backend.utils.user_settings_utils import get_user_profile

        if reading_shelf.shelf_enabled() and profile_utils.offers_allowed(get_user_profile(username)):
            shelf_db = get_db()
            inputs.shelf_available = bool(shelf_db is not None and reading_shelf.live_items(shelf_db))
    except Exception:
        logger.warning("[example questions] could not check the reading shelf.", exc_info=True)

    return inputs
