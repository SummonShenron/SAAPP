"""What wrote a reply and what it was shown: a small stamp kept on each reply, so a thumbs-up or thumbs-down can be told apart by voice.

WHY (Jack, 2026-11-08). He is running a mix of the standard voice (Gemini 3.6 Flash) and Claude Haiku and will rate turns to keep tuning the Haiku voice. A feedback record held the prompt, the first 400 characters of the reply, the rating, a reason and a tag, and nothing
about which model wrote the reply or which voice examples and people notes it was given, so a rating could not be traced to either. The stamp fixes that.

WHAT IT HOLDS: `label` (the voice that wrote the reply: "flash" for the standard voice, "gpt", "haiku", "lite", or "other"), `tier` (the tier that actually wrote it: standard, lite or haiku), `reason` (why the reply tier was chosen: one of reply_tier.REASONS), `choice`
(the person's base-model setting), and `examples` and `notes` (the ids of the voice examples and the people notes the reply was shown, at most two of each). IDS AND LABELS ONLY: no text of the reply, the question, an example or a note.

WHERE IT GOES. On the reply message (it is saved with the conversation, so it survives a restart), and, when the person rates that reply, copied onto the feedback record. `find` locates the stamped reply by its text among the person's conversations held in memory.
The Sonic Health feedback page counts feedback by voice, by example and by note (`summarize`). A reply written before this existed has no stamp and its feedback is counted as unstamped.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

from backend.utils import reply_tier

KEY = "voice"
LABELS = ("flash", "gpt", "haiku", "lite", "other")
TIERS = (reply_tier.STANDARD, reply_tier.LITE, reply_tier.HAIKU, "other")
CHOICES = ("gemini", "gpt", "lite", "haiku", "auto")
MAX_IDS = 2
SEARCH_LAST_MESSAGES = 80
MATCH_CHARS = 160
_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,60}$")
_TAG_RE = re.compile(r"<<<.*?>>>", re.DOTALL)


def label_for(tier: str, choice: str) -> str:
    """The voice that wrote a reply, by the tier that did and the person's setting: the standard tier is Flash unless they chose GPT. Pure."""
    if tier == reply_tier.HAIKU:
        return "haiku"
    if tier == reply_tier.LITE:
        return "lite"
    if tier == reply_tier.STANDARD:
        return "gpt" if choice == "gpt" else "flash"
    return "other"


def _ids(raw: Any) -> List[str]:
    return [i for i in (raw or []) if isinstance(i, str) and _ID_RE.match(i)][:MAX_IDS]


def build(tier: Any, reason: Any, choice: Any, example_ids: Iterable[Any] = (), note_ids: Iterable[Any] = ()) -> Dict[str, Any]:
    """The stamp for a reply. Anything outside the fixed lists becomes "other", so nothing but a label and an id can be stored. Pure."""
    tier = tier if tier in TIERS else "other"
    choice = choice if choice in CHOICES else "other"
    return {
        "label": label_for(tier, choice),
        "tier": tier,
        "reason": reason if reason in reply_tier.REASONS else "other",
        "choice": choice,
        "examples": _ids(list(example_ids or [])),
        "notes": _ids(list(note_ids or [])),
    }


def clean(raw: Any) -> Optional[Dict[str, Any]]:
    """A stamp read back from storage or from a message, made safe again, or None when it is not one. Pure."""
    if not isinstance(raw, dict) or raw.get("label") not in LABELS:
        return None
    return build(raw.get("tier"), raw.get("reason"), raw.get("choice"), raw.get("examples"), raw.get("notes")) | {"label": raw["label"]}


def _head(text: Any) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", str(text or ""))).strip()[:MATCH_CHARS]


def find(sessions: Dict[str, Any], username: str, text: str) -> Optional[Dict[str, Any]]:
    """The stamp of the person's most recent reply whose text starts the way `text` does, among their conversations held in memory (`sessions` is {"username::session": [messages]}), or None. Pure."""
    wanted = _head(text)
    if not wanted or not username:
        return None
    prefix = f"{username}::"
    for key, messages in (sessions or {}).items():
        if not str(key).startswith(prefix):
            continue
        for message in reversed(list(messages or [])[-SEARCH_LAST_MESSAGES:]):
            kwargs = getattr(message, "additional_kwargs", None) or {}
            if getattr(message, "type", "") == "ai" and kwargs.get(KEY) and _head(getattr(message, "content", "")) == wanted:
                return clean(kwargs[KEY])
    return None


SEARCH_SAVED_CONVERSATIONS = 10


def find_saved(db: Any, username: str, text: str) -> Optional[Dict[str, Any]]:
    """The stamp of the person's most recent saved reply whose text starts the way `text` does, read from their SAVED conversations (their most recently updated few, the last SEARCH_LAST_MESSAGES messages of each),
    or None. The fallback for `find`: the server holds a conversation in memory only after it has been used since the last restart, and on 2026-11-09 six of Jack's nine thumbs (on replies that were stamped and saved)
    found nothing there, so the stamp was lost. Blocking (a database read); never raises."""
    wanted = _head(text)
    if db is None or not wanted or not username:
        return None
    try:
        rows = db["conversations"].find({"username": username}, {"_id": 0, "updated_at": 1, "messages": {"$slice": -SEARCH_LAST_MESSAGES}}).sort("updated_at", -1).limit(SEARCH_SAVED_CONVERSATIONS)
        for row in rows:
            for message in reversed(list(row.get("messages") or [])):
                if message.get("type") == "ai" and message.get(KEY) and _head(message.get("content")) == wanted:
                    return clean(message[KEY])
    except Exception:
        return None
    return None


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts of a person's thumbs by voice, by example and by note, from feedback rows that carry a stamp (`voice`) and a polarity ("positive" or "negative", already read by correction_kinds). Rows
    without a stamp are only counted. Counts and ids only. Pure."""
    labels: Dict[str, Dict[str, int]] = {}
    examples: Dict[str, Dict[str, int]] = {}
    notes: Dict[str, Dict[str, int]] = {}
    stamped = unstamped = 0
    for row in rows:
        kind = row.get("kind")
        if kind not in ("positive", "negative"):
            continue
        stamp = clean(row.get("voice"))
        if stamp is None:
            unstamped += 1
            continue
        stamped += 1
        labels.setdefault(stamp["label"], {"positive": 0, "negative": 0})[kind] += 1
        for example_id in stamp["examples"]:
            examples.setdefault(example_id, {"positive": 0, "negative": 0})[kind] += 1
        for note_id in stamp["notes"]:
            notes.setdefault(note_id, {"positive": 0, "negative": 0})[kind] += 1

    def listed(table: Dict[str, Dict[str, int]], key: str) -> List[Dict[str, Any]]:
        return sorted(({key: name, **counts} for name, counts in table.items()), key=lambda r: (-(r["positive"] + r["negative"]), r[key]))

    return {"stamped": stamped, "unstamped": unstamped, "labels": listed(labels, "label"), "examples": listed(examples, "id"), "notes": listed(notes, "id")}
