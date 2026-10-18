"""Words on a photo, from the chat: "put 'Merry Christmas' across the bottom of this in gold".

Three small jobs around the drawing code in backend/utils/image_card.py:

1. NOTICING THE REQUEST, in code (the same way an admin's question about incidents is routed): a message that asks for words on a picture, with a picture
   attached or named, or a short change to the card just made ("bigger", "try red"). No model call, and no extra tokens on any other turn.
2. PLANNING IT, with one small model call: the request becomes a spec of fixed choices (image_card.validate_spec), never drawing code. The model can say the
   message was not about a picture after all, and then it is answered like any other.
3. KEEPING IT: the finished card is stored for the person like an attached photo (same GridFS kind, so the same 30-day clean-up and the same delete with the
   conversation), and only the newest few per person are kept, so what one person can store is bounded (MAX_KEPT_PER_PERSON x about 1 MB).

The original photo is never changed: every change starts again from it. Nothing the person wrote or the photo shows is ever put in a log line.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from backend.utils import daily_allowance, image_card

logger = logging.getLogger("SASS Logger")

ALLOWANCE = "photo_cards"
DAILY_CARDS = 30                 # CPU and storage writes only (no model cost beyond one small call), so this is generous: it stops a runaway loop, not a family
MAX_KEPT_PER_PERSON = 8          # cards and generated pictures together; the oldest go first
MAX_REQUEST_CHARS = 600
GENERATED_CARD = "card"
GENERATED_IMAGE = "image"        # backend/services/image_gen.py stores its pictures the same way
ATTACHMENT_KIND = "chat_attachment"

# ---------------------------------------------------------------------------------------------------------------
# Noticing the request
# ---------------------------------------------------------------------------------------------------------------

_VERB = r"(?:add|put|write|place|overlay|stamp|print|type|caption|insert|superimpose|slap)"
_WORDS = r"(?:text|words?|wording|message|caption|greeting|saying|says?|name|quote|lettering|title|tagline|slogan)"
_PICTURE = r"(?:image|photo|photograph|picture|pic|snapshot|card|postcard)"
_VERB_RE = re.compile(rf"\b{_VERB}\b", re.IGNORECASE)
_CARD_VERB_RE = re.compile(
    rf"\b{_VERB}\b.{{0,60}}\b{_WORDS}\b|\b{_WORDS}\b.{{0,40}}\b(?:on|onto|over|across)\b"
    rf"|\b{_VERB}\b.{{0,80}}\b(?:on|onto|over|across)\s+(?:(?:the|this|that|my|our)\s+)?(?:{_PICTURE}|it|this|that)\b",
    re.IGNORECASE,
)
_QUOTED_RE = re.compile(r"[\"“”][^\"“”]{2,160}[\"“”]|(?<!\w)'[^']{2,160}'(?!\w)")     # single quotes only when they stand apart from a word, so "Season's" is not one
_ONTO_RE = re.compile(rf"\b(?:on|onto|over|across|to|in|into)\s+(?:the\s+|this\s+|that\s+|my\s+|our\s+|a\s+)?(?:{_PICTURE}|it|this|that)\b|\b(?:top|bottom|middle|center|centre)\s+of\b", re.IGNORECASE)
_PICTURE_WORD_RE = re.compile(rf"\b{_PICTURE}\b", re.IGNORECASE)
_THIS_RE = re.compile(r"\b(?:this|that|it|these|the (?:photo|picture|image|pic))\b", re.IGNORECASE)
_MAKE_CARD_RE = re.compile(r"\b(?:make|turn|create|design|build|convert|use)\b.{0,50}\b(?:greeting card|card|postcard|invitation|invite)\b", re.IGNORECASE)
_NAMED_CARD_RE = re.compile(
    r"\b(?:christmas|holiday|birthday|greeting|thank[- ]you|new year'?s?|valentine'?s?|easter|halloween|anniversary|get well|wedding|baby|announcement)\s+(?:card|postcard|invitation|invite)\b",
    re.IGNORECASE,
)
# Said about a picture that is being asked about, not decorated.
_NOT_A_CARD_RE = re.compile(r"\b(?:describe|what(?:'s| is| are)|who(?:'s| is)|identify|analy[sz]e|explain|summari[sz]e|transcribe|read (?:the|this|that)|ocr|caption this for me)\b", re.IGNORECASE)

_ADJUST_RE = re.compile(
    r"\b(?:bigger|larger|smaller|tiny|huge|higher|lower|move|shift|nudge|center|centre|left|right|top|bottom|middle|font|lettering|color|colour|bold|bolder|brighter|darker|"
    r"gold|golden|yellow|red|green|blue|white|black|silver|cream|orange|pink|purple|script|fancier|fancy|simpler|plain|outline|shadow|readable|legible|different|another|instead|"
    r"again|redo|retry|change|swap|replace|remove|try)\b",
    re.IGNORECASE,
)
MAX_ADJUST_WORDS = 30
RECENT_MESSAGES = 4


def asks_for_card(message: str, attached_image: bool) -> bool:
    """Whether a message asks for words on a picture. With a picture attached to THIS message the request can be short; otherwise the message has to name
    the picture (or a card), so "write a message to my boss" is never taken for one just because an old photo is in the conversation."""
    text = message or ""
    if not text.strip() or _NOT_A_CARD_RE.search(text):
        return False
    quoted = bool(_QUOTED_RE.search(text))
    wants = bool(_CARD_VERB_RE.search(text) or _MAKE_CARD_RE.search(text) or _NAMED_CARD_RE.search(text) or (quoted and (attached_image or _VERB_RE.search(text))))
    if not wants:
        return False
    if attached_image:
        return bool(_ONTO_RE.search(text) or quoted or _MAKE_CARD_RE.search(text) or _NAMED_CARD_RE.search(text) or _PICTURE_WORD_RE.search(text))
    # No picture on this message: it has to be about one that is already here, said plainly ("on this photo", "make this a Christmas card").
    return bool(_ONTO_RE.search(text) and _PICTURE_WORD_RE.search(text)) or bool(_MAKE_CARD_RE.search(text) and _THIS_RE.search(text))


def asks_to_adjust(message: str) -> bool:
    """A short message that could be a change to the card just made. Only consulted when a card was made within the last few messages, and the planner
    can still say it was about something else."""
    text = (message or "").strip()
    return bool(text) and len(text.split()) <= MAX_ADJUST_WORDS and bool(_ADJUST_RE.search(text))


def recent_generated_kind(messages: List[Any]) -> Optional[str]:
    """What Sonic made most recently, within the last few messages: "card" (words on a photo), "image" (a generated picture) or None. The newest one wins, so
    "make it bigger" after a card changes the card, and "make it snowier" after a picture changes the picture."""
    for message in reversed(list(messages or [])[-RECENT_MESSAGES:]):
        if getattr(message, "type", None) != "ai":
            continue
        for image in (getattr(message, "additional_kwargs", None) or {}).get("kb_images") or []:
            if isinstance(image, dict) and image.get("generated") in (GENERATED_CARD, GENERATED_IMAGE):
                return image["generated"]
    return None


def made_card_recently(messages: List[Any]) -> bool:
    """Whether the newest thing Sonic made, in the last few messages, was a card."""
    return recent_generated_kind(messages) == GENERATED_CARD


def attached_image_ref(refs: Any) -> Optional[Dict[str, Any]]:
    """The newest picture attached to this message ({filename, gridfs_id, content_type}), or None."""
    for ref in reversed(list(refs or [])):
        if isinstance(ref, dict) and ref.get("gridfs_id") and str(ref.get("content_type", "")).startswith("image/"):
            return ref
    return None


def wants_card(message: str, refs: Any, recent_messages: List[Any]) -> bool:
    attached = attached_image_ref(refs) is not None
    kind = recent_generated_kind(recent_messages)
    # Right after Sonic made a picture, "it" is that picture: "put 'Merry Christmas' on it" needs no attachment.
    if asks_for_card(message, attached or kind == GENERATED_IMAGE):
        return True
    return not attached and kind == GENERATED_CARD and asks_to_adjust(message)


# ---------------------------------------------------------------------------------------------------------------
# Where the pictures live
# ---------------------------------------------------------------------------------------------------------------

class MongoStore:
    """The person's pictures in GridFS. Blocking: callers run it in a worker thread. Every lookup is limited to this person's own files."""

    _index_ready = False

    def __init__(self, db: Any):
        self.db = db

    def _ensure_index(self) -> None:
        if MongoStore._index_ready:
            return
        try:
            self.db["fs.files"].create_index([("metadata.username", 1), ("metadata.session_id", 1), ("uploadDate", -1)])
            MongoStore._index_ready = True
        except Exception:
            logger.warning("[photo cards] could not create the lookup index.", exc_info=True)

    def session_image(self, username: str, session_id: str) -> Optional[Dict[str, Any]]:
        """The newest picture in this conversation that a card can be made from: a photo the person attached, or a picture Sonic generated for them (not a
        card, which already has words on it). None when there is none."""
        self._ensure_index()
        row = next(iter(self.db["fs.files"].find({
            "metadata.username": username, "metadata.session_id": session_id, "metadata.kind": ATTACHMENT_KIND,
            "$or": [{"metadata.generated": {"$exists": False}}, {"metadata.generated": GENERATED_IMAGE}],
            "metadata.content_type": {"$regex": "^image/"},
        }, {"_id": 1}).sort("uploadDate", -1).limit(1)), None)
        return {"id": str(row["_id"])} if row else None

    def _last(self, username: str, session_id: str, generated: str) -> Optional[Dict[str, Any]]:
        self._ensure_index()
        row = next(iter(self.db["fs.files"].find({
            "metadata.username": username, "metadata.session_id": session_id, "metadata.generated": generated,
        }, {"_id": 1, "metadata": 1}).sort("uploadDate", -1).limit(1)), None)
        if not row:
            return None
        meta = row.get("metadata") or {}
        return {"id": str(row["_id"]), "spec": meta.get("spec") or {}, "source_id": meta.get("source_id")}

    def last_card(self, username: str, session_id: str) -> Optional[Dict[str, Any]]:
        """The newest card made in this conversation: {id, spec, source_id}."""
        return self._last(username, session_id, GENERATED_CARD)

    def last_image(self, username: str, session_id: str) -> Optional[Dict[str, Any]]:
        """The newest generated picture in this conversation: {id, spec: {prompt, aspect}}."""
        return self._last(username, session_id, GENERATED_IMAGE)

    def read(self, username: str, file_id: str) -> Optional[bytes]:
        """The bytes of one of this person's own pictures; None if it is gone or is not theirs."""
        from bson import ObjectId
        from gridfs import GridFS

        try:
            oid = ObjectId(file_id)
        except Exception:
            return None
        row = self.db["fs.files"].find_one({"_id": oid}, {"metadata": 1})
        if not row or (row.get("metadata") or {}).get("username") != username:
            return None
        try:
            return GridFS(self.db).get(oid).read()
        except Exception:
            return None

    def save(self, username: str, session_id: str, data: bytes, content_type: str, filename: str, generated: str, extra: Optional[Dict[str, Any]] = None) -> Optional[str]:
        from gridfs import GridFS

        try:
            file_id = GridFS(self.db).put(data, filename=filename, metadata={
                "username": username, "session_id": session_id, "content_type": content_type,
                "kind": ATTACHMENT_KIND, "generated": generated, **(extra or {}),
            })
            return str(file_id)
        except Exception:
            logger.warning("[photo cards] could not store a picture.", exc_info=True)
            return None

    def prune(self, username: str) -> int:
        """Keeps only this person's newest MAX_KEPT_PER_PERSON generated pictures."""
        from gridfs import GridFS

        rows = list(self.db["fs.files"].find(
            {"metadata.username": username, "metadata.generated": {"$in": [GENERATED_CARD, GENERATED_IMAGE]}}, {"_id": 1},
        ).sort("uploadDate", -1))
        gone = rows[MAX_KEPT_PER_PERSON:]
        files = GridFS(self.db)
        for row in gone:
            files.delete(row["_id"])
        return len(gone)


# ---------------------------------------------------------------------------------------------------------------
# Making the card
# ---------------------------------------------------------------------------------------------------------------

MADE, NO_IMAGE, NEEDS_TEXT, BAD_IMAGE, LIMIT, NOT_FOR_ME, UNAVAILABLE = "made", "no_image", "needs_text", "bad_image", "limit", "not_for_me", "unavailable"


@dataclass
class CardOutcome:
    kind: str
    facts: str = ""                                           # what the reply may say, as plain facts (it may include the person's own words: for the reply only)
    images: List[Dict[str, Any]] = field(default_factory=list)


def local_day(tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """The person's own calendar day, so a daily allowance does not start over in their evening."""
    moment = now or datetime.now(timezone.utc)
    try:
        return moment.astimezone(ZoneInfo(tz_name or "UTC")).date().isoformat()
    except Exception:
        return moment.date().isoformat()


def _is_newer(candidate: Optional[str], than: Optional[str]) -> bool:
    """Whether stored file `candidate` was saved after `than`. Mongo ids start with their creation time, so they sort in time order; anything that is not an id is never newer."""
    from bson import ObjectId

    if not candidate or not than or not (ObjectId.is_valid(candidate) and ObjectId.is_valid(than)):
        return False
    return str(candidate) > str(than)


def _parse_json(raw: Any) -> Dict[str, Any]:
    content = getattr(raw, "content", raw)
    if isinstance(content, list):
        content = "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    cleaned = str(content or "").replace("```json", "").replace("```", "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(cleaned[start:end + 1])
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _file_name(spec: Dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", spec["text"].lower()).strip("-")[:40].strip("-")
    return f"{slug or 'card'}.jpg"


def _facts_made(spec: Dict[str, Any], card: image_card.RenderedCard) -> str:
    notes = ("Worth mentioning, briefly and only because it is true: " + "; ".join(card.notes) + ". ") if card.notes else ""
    return (
        "A card was just made from the user's own photo and is shown to them right below your reply, with a Download button under it. "
        f"What was drawn on it: {image_card.describe_spec(spec)}. It is {card.width} by {card.height} pixels. {notes}"
        "Tell them it is ready, in a sentence or two. Do not describe the picture itself (you have not been shown what is in it) and do not repeat their words back at length. "
        "Offer ONE specific change they could ask for (a different font or colour, moving the words, making them bigger), not a list of everything possible."
    )


async def make_card(
    *, store: Any, username: str, session_id: str, message: str, attached: Optional[Dict[str, Any]],
    invoke: Callable[[str], Awaitable[Any]], db: Any = None, day: Optional[str] = None,
) -> CardOutcome:
    """Plans, draws and stores one card. Never raises: every problem is an outcome the reply can explain. `store` does the blocking storage work."""
    from backend.components.constraints import CARD_SPEC_PROMPT

    # Which picture "it" is: a photo attached to this message; otherwise the picture the last card was made from, unless a newer picture has arrived since
    # (a photo attached or a picture generated after that card), which then starts a new card.
    prior, source_id = None, (attached or {}).get("gridfs_id")
    if not source_id:
        prior = await asyncio.to_thread(store.last_card, username, session_id)
        newest = ((await asyncio.to_thread(store.session_image, username, session_id)) or {}).get("id")
        if prior and prior.get("source_id") and not _is_newer(newest, prior.get("id")):
            source_id = prior["source_id"]
        else:
            source_id, prior = newest, None
    if not source_id:
        return CardOutcome(NO_IMAGE, "There is no photo in this conversation to put words on. If they meant to, ask them to attach the photo to a message and say what the card should say.")

    day = day or local_day(None)
    taken, _ = daily_allowance.take(db, ALLOWANCE, username, DAILY_CARDS, day)
    if not taken:
        return CardOutcome(LIMIT, "They have made a lot of cards today, so there are no more for today. It starts again tomorrow.")

    def give_up(kind: str, facts: str) -> CardOutcome:
        daily_allowance.give_back(db, ALLOWANCE, username, day)
        return CardOutcome(kind, facts)

    prior_spec = (prior or {}).get("spec") or None
    try:
        prompt = CARD_SPEC_PROMPT.format(request=" ".join((message or "").split())[:MAX_REQUEST_CHARS], prior=json.dumps(prior_spec) if prior_spec else "none")
        planned = _parse_json(await invoke(prompt))
    except Exception:
        logger.warning("[photo cards] the planning call failed.", exc_info=True)
        return give_up(UNAVAILABLE, "Something went wrong working out what to put on the picture. Ask them to try again.")
    if planned.get("applies") is False:
        daily_allowance.give_back(db, ALLOWANCE, username, day)
        return CardOutcome(NOT_FOR_ME)
    planned.pop("applies", None)

    spec, problem = image_card.validate_spec(planned, prior_spec)
    if spec is None:
        if problem == image_card.NEEDS_TEXT_MESSAGE:
            return give_up(NEEDS_TEXT, "They want words on a picture but did not say what the words should be. Ask what the card should say (or offer to suggest a greeting).")
        return give_up(UNAVAILABLE, "Couldn't work out what to put on the picture from that. Ask them to say the words, and if they like where and what colour.")

    data = await asyncio.to_thread(store.read, username, source_id)
    if data is None:
        return give_up(BAD_IMAGE, "The photo is no longer stored (photos are kept for about a month). Ask them to attach it again.")
    try:
        card = await asyncio.to_thread(image_card.render_card, data, spec)
    except image_card.CardError as error:
        return give_up(BAD_IMAGE, f"The picture couldn't be used: {error}")
    except Exception:
        logger.warning("[photo cards] drawing failed.", exc_info=True)
        return give_up(UNAVAILABLE, "Something went wrong drawing the words on the picture. Ask them to try again.")

    name = _file_name(spec)
    file_id = await asyncio.to_thread(store.save, username, session_id, card.data, card.content_type, name, GENERATED_CARD, {"spec": spec, "source_id": str(source_id)})
    if not file_id:
        return give_up(UNAVAILABLE, "The card was made but couldn't be saved, so it can't be shown. Ask them to try again in a moment.")
    try:
        await asyncio.to_thread(store.prune, username)
    except Exception:
        logger.warning("[photo cards] could not tidy old pictures.", exc_info=True)
    logger.info("[photo cards] made a card (%d KB, %d notes).", len(card.data) // 1024, len(card.notes))
    return CardOutcome(MADE, _facts_made(spec, card), [{"filename": name, "fileId": file_id, "generated": GENERATED_CARD}])
