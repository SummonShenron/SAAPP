"""Pictures made from a description: "make me a snowy stadium at dusk for a Christmas card".

It sits next to the words-on-a-photo feature (backend/services/photo_cards.py) and shares its storage, so the two chain: make a picture, then "put 'Merry
Christmas' on it". The words are never asked of the image model (models draw lettering badly); the picture is made without any and the exact words are added
afterwards by code.

- NOTICED IN CODE, like the card requests: only an explicit "generate / create / draw / paint ... a picture of ..." (never "show me", which asks for a picture
  from the library), or a short change right after a picture ("make it snowier"). Technical uses of the word ("a docker image", "a diagram") are left alone.
- PLANNED WITH ONE SMALL MODEL CALL that turns the request into an image prompt and an aspect ratio, and also refuses the kinds of request that should never be
  made (a named real person, sexual or graphic content, forgeries). Google's own safety filters still apply to the image model.
- LIMITED: IMAGE_GEN_DAILY_LIMIT pictures a day per person (3), counted in the person's own day, given back when a picture fails. Each is about 3 to 5 cents, so this
  bounds one person at roughly 15 cents a day. The usage meter counts every call, so the budget guard sees images too (price the model in LLM_PRICES, see
  backend/utils/llm_usage.py), and low-power mode pauses picture making.
- KEPT LIKE A CARD: stored for the person with the same 30-day clean-up, the newest few only (photo_cards.MAX_KEPT_PER_PERSON), as a JPEG of modest size.

Nothing the person asked for, and no prompt, is put in a log line.
"""
import asyncio
import io
import json
import logging
import os
import re
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from backend.services import photo_cards
from backend.services.photo_cards import CardOutcome
from backend.utils import daily_allowance, image_card

logger = logging.getLogger("SASS Logger")

MODEL = os.getenv("IMAGE_GEN_MODEL", "gemini-3.1-flash-lite-image")
ALLOWANCE = "image_gen"
DAILY_IMAGES = int(os.getenv("IMAGE_GEN_DAILY_LIMIT", "3"))
TIMEOUT_SECONDS = 60
ASPECTS = ("1:1", "3:4", "4:3", "2:3", "3:2", "9:16", "16:9")
DEFAULT_ASPECT = "4:3"
MAX_REQUEST_CHARS = 2500         # a detailed description of a picture can run to a page; it must reach the model whole
MAX_PROMPT_CHARS = 2000
MAX_OUT_EDGE = 2400
JPEG_QUALITY = 86
ASSUMED_OUTPUT_TOKENS = 1300     # what a 1K picture costs when the response does not say, so the meter never counts a picture as free

NOT_FOR_ME = photo_cards.NOT_FOR_ME
MADE, LIMIT, REFUSED, BLOCKED, UNAVAILABLE = "made", "limit", "refused", "blocked", "unavailable"

# Finish reasons that mean the model (or Google's filters) declined to draw it, as opposed to a fault.
_SAFETY_FINISH = {"SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT", "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "IMAGE_RECITATION", "RECITATION"}

REFUSAL_REASONS = {
    "real_person": "pictures of a specific real person",
    "sexual": "sexual or nude pictures",
    "violence": "graphic violence",
    "hate": "hateful imagery",
    "deception": "realistic fakes of real events, documents, IDs or money",
    "minors": "that kind of picture involving a child",
    "other": "that kind of picture",
}

# ---------------------------------------------------------------------------------------------------------------
# Noticing the request
# ---------------------------------------------------------------------------------------------------------------

_VERB = r"(?:generate|create|make|draw|paint|design|illustrate|render|produce|sketch|imagine|come up with|whip up)"
_NOUN = r"(?:image|picture|illustration|drawing|painting|artwork|poster|wallpaper|portrait|cartoon|scene|graphic|photo|photograph|background|art)"
_ASK_RE = re.compile(rf"\b{_VERB}\b.{{0,40}}\b{_NOUN}s?\b(?:\s+\w+){{0,2}}", re.IGNORECASE)
_DRAW_RE = re.compile(r"\b(?:draw|paint|sketch|illustrate)\s+(?:me\s+)?(?:a|an|the|some|my|our)\s+\w+", re.IGNORECASE)
# The word means something else: a container image, a diagram, a chart, code. Checked only against the few words of the request itself (the verb up to just
# after the picture word), never the whole message: a long description of the picture is full of words like "line", "table" or "frame" that mean nothing here.
_NOT_RE = re.compile(
    r"\b(?:docker|dockerfile|container|kubernetes|k8s|disk image|iso|vm|virtual machine|base image|ami|screenshot|diagram|flowchart|chart|graph|uml|architecture|ascii|svg|"
    r"code|css|html|react|component|function|script|sql|query|spreadsheet|document|resume|email|letter|essay|story|summary|"
    r"frame|frames|album|conclusion|conclusions|attention|comparison|parallel|distinction|breath|blood)\b",
    re.IGNORECASE,
)
# "make me a Christmas card": with no photo to put words on, that is a request for the picture of one.
_CARD_RE = re.compile(
    rf"\b{_VERB}\b.{{0,40}}\b(?:christmas|holiday|birthday|greeting|thank[- ]you|valentine'?s?|easter|halloween|anniversary|wedding|baby|new year'?s?|get well)\s+(?:card|postcard|invitation|invite)\b",
    re.IGNORECASE,
)
_REFINE_RE = re.compile(
    r"\b(?:again|another|different|instead|snowier|warmer|colder|darker|brighter|lighter|cozier|simpler|busier|wider|taller|more|less|without|remove|add|change|try|redo|retry|"
    r"style|version|variation|variations|cartoon|realistic|painting|watercolor|sunset|night|winter|summer|"
    r"look like|looks like|look more like|should look|resemble|more like|like the (?:image|photo|picture|attachment|one))\b",
    re.IGNORECASE,
)
MAX_REFINE_WORDS = 30


def asks_to_generate(message: str) -> bool:
    """Whether the message asks for a picture to be made. Each place a request could be (a verb near a picture word, "draw a ...", "make me a Christmas card")
    is judged on its own few words, so a technical use of the word ("a docker image") does not stop a later, real request in the same message."""
    text = message or ""
    if not text.strip():
        return False
    return any(not _NOT_RE.search(found.group(0)) for pattern in (_ASK_RE, _DRAW_RE, _CARD_RE) for found in pattern.finditer(text))


def asks_to_refine(message: str) -> bool:
    text = (message or "").strip()
    return bool(text) and len(text.split()) <= MAX_REFINE_WORDS and bool(_REFINE_RE.search(text)) and not _NOT_RE.search(text)


def wants_image(message: str, refs: Any, recent_messages: List[Any]) -> bool:
    """A request to make a picture, or a short change to the one just made. With a photo attached, an explicit request to make a picture ("generate a picture of
    Patchy") still counts, and the photo goes to the image model as a reference; the card maker (words on the photo) is checked before this, so "put 'Merry
    Christmas' on this" never gets here. A short change right after a picture ("make it snowier", "try one more time, look like the image I attached") is a change to
    that picture, with the newly attached photo as its reference; with no picture just made, a short message about an attached photo is never taken for one."""
    if asks_to_generate(message):
        return True
    return photo_cards.recent_generated_kind(recent_messages) == photo_cards.GENERATED_IMAGE and asks_to_refine(message)


# ---------------------------------------------------------------------------------------------------------------
# The image model
# ---------------------------------------------------------------------------------------------------------------

class Blocked(Exception):
    """Google's filters (or the model) declined to draw it."""


class Failed(Exception):
    """The call failed or returned no picture."""


_client = None


def _get_client():
    global _client
    if _client is None:
        from google import genai
        from google.genai import types

        key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        _client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=TIMEOUT_SECONDS * 1000))
    return _client


def _usage_of(response: Any) -> Dict[str, int]:
    meta = getattr(response, "usage_metadata", None)
    out = int(getattr(meta, "candidates_token_count", 0) or 0)
    return {"calls": 1, "input": int(getattr(meta, "prompt_token_count", 0) or 0), "output": out or ASSUMED_OUTPUT_TOKENS, "reasoning": 0, "cached": 0}


def to_jpeg(data: bytes) -> bytes:
    """The picture as a modest JPEG (the model returns about 1 MB; this is a third of that). Raises Failed for anything that is not a picture."""
    from PIL import Image

    try:
        image = Image.open(io.BytesIO(data))
        image = image.convert("RGB")
    except Exception:
        raise Failed("the model returned something that is not a picture")
    if max(image.size) > MAX_OUT_EDGE:
        scale = MAX_OUT_EDGE / max(image.size)
        image = image.resize((max(1, int(image.size[0] * scale)), max(1, int(image.size[1] * scale))))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return out.getvalue()


REFERENCE_EDGE = 1024     # the reference photo is shrunk to this before it is sent: a bigger one costs more input and makes no better picture


def reference_part(data: Optional[bytes]) -> Optional[Tuple[bytes, str]]:
    """A photo the person attached, as (JPEG bytes, mime type) small enough to send along as a reference, or None when it cannot be read as a picture. Never raises."""
    if not data:
        return None
    try:
        from PIL import Image, ImageOps

        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        image.thumbnail((REFERENCE_EDGE, REFERENCE_EDGE))
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception:
        return None


def generate_sync(prompt: str, aspect: str, reference: Optional[Tuple[bytes, str]] = None) -> Tuple[bytes, Dict[str, int]]:
    """Blocking: one call to the image model, with the person's attached photo as a reference when there is one. Returns (JPEG bytes, token usage). Raises Blocked or Failed."""
    from backend.utils import llm_usage

    try:
        from google.genai import types

        contents: Any = prompt if reference is None else [prompt, types.Part.from_bytes(data=reference[0], mime_type=reference[1])]
        response = _get_client().models.generate_content(
            model=MODEL, contents=contents,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=aspect)),
        )
    except Exception as error:
        error_name = type(error).__name__
        logger.warning("[image gen] the image call failed (%s).", error_name)
        raise Failed(error_name)
    usage = _usage_of(response)
    llm_usage.record(MODEL, "image_gen.generate", usage)       # priced like any other call, so the budget guard sees it
    feedback = getattr(response, "prompt_feedback", None)
    if getattr(feedback, "block_reason", None):
        raise Blocked("the request was blocked")
    candidate = (getattr(response, "candidates", None) or [None])[0]
    reason = getattr(getattr(candidate, "finish_reason", None), "name", str(getattr(candidate, "finish_reason", "") or ""))
    for part in getattr(getattr(candidate, "content", None), "parts", None) or []:
        inline = getattr(part, "inline_data", None)
        if inline is not None and getattr(inline, "data", None):
            return to_jpeg(inline.data), usage
    if reason in _SAFETY_FINISH:
        raise Blocked("the picture was blocked")
    raise Failed("no picture came back")


# ---------------------------------------------------------------------------------------------------------------
# Making the picture
# ---------------------------------------------------------------------------------------------------------------

def _file_name(title: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:40].strip("-")
    return f"{slug or 'picture'}.jpg"


def clean_prompt(value: Any) -> str:
    return " ".join(re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).split())[:MAX_PROMPT_CHARS]


def _facts_made(prompt_idea: str, left: int, words: str) -> str:
    more = f" They have {left} more picture{'s' if left != 1 else ''} to make today." if left > 0 else " That was their last one for today; it starts again tomorrow."
    wording = (f" They also wanted the words \"{words}\" on it, which are not in the picture yet: tell them to say where and in what style (for example 'put it across the bottom in gold') "
               "and you will add them exactly." if words else " They can ask for words on it next, or a change to the picture itself.")
    return (
        "A picture was just made from the user's description and is shown to them right below your reply, with a Download button under it. "
        f"What was asked of the image model: {prompt_idea[:300]} "
        "You have NOT seen the result. Say only that it is ready and that it was made from their description. Do not say it matches, fits, hits the mark, captures or shows anything, "
        "do not describe how it looks, and do not name the image model or any service. Ask what they think. "
        f"{wording}{more} Offer ONE specific change or next step, not a list."
    )


REFERENCE_NOTE = (
    "A reference picture the person attached will be sent to the image model together with your prompt. Write the prompt as a NEW picture of what they asked for, "
    "based on that reference (keep the look of its subject and its colours), and do not describe the reference in detail: the model can see it."
)


async def make_image(
    *, store: Any, username: str, session_id: str, message: str,
    invoke: Callable[[str], Awaitable[Any]], db: Any = None, day: Optional[str] = None,
    generate: Callable[..., Tuple[bytes, Dict[str, int]]] = generate_sync, attached: Optional[Dict[str, Any]] = None,
) -> CardOutcome:
    """Plans, generates and stores one picture, using the photo attached to the message (`attached`: {gridfs_id, ...}) as a reference when there is one. Never
    raises: every problem is an outcome the reply can explain."""
    from backend.components.constraints import IMAGE_PROMPT_PROMPT

    day = day or photo_cards.local_day(None)
    prior = await asyncio.to_thread(store.last_image, username, session_id)
    prior_prompt = ((prior or {}).get("spec") or {}).get("prompt")
    reference = None
    if attached and attached.get("gridfs_id"):
        reference = reference_part(await asyncio.to_thread(store.read, username, attached["gridfs_id"]))
    try:
        prompt = IMAGE_PROMPT_PROMPT.format(
            request=" ".join((message or "").split())[:MAX_REQUEST_CHARS], prior=json.dumps(prior_prompt) if prior_prompt else "none",
            reference=REFERENCE_NOTE if reference else "No reference picture is attached.",
        )
        planned = photo_cards._parse_json(await invoke(prompt))
    except Exception:
        logger.warning("[image gen] the planning call failed.", exc_info=True)
        return CardOutcome(UNAVAILABLE, "Something went wrong working out what to draw. Ask them to try again.")
    if planned.get("applies") is False:
        return CardOutcome(NOT_FOR_ME)
    refusal = planned.get("refuse")
    if refusal:
        what = REFUSAL_REASONS.get(str(refusal), REFUSAL_REASONS["other"])
        return CardOutcome(REFUSED, f"They asked for a picture you won't make: {what}. Say so kindly in a sentence, with no lecture, and offer something close you can make instead.")
    image_prompt = clean_prompt(planned.get("prompt"))
    if not image_prompt:
        return CardOutcome(UNAVAILABLE, "Couldn't work out what to draw from that. Ask them to describe the picture they want.")
    aspect = str(planned.get("aspect") or DEFAULT_ASPECT)
    aspect = aspect if aspect in ASPECTS else DEFAULT_ASPECT

    taken, used = daily_allowance.take(db, ALLOWANCE, username, DAILY_IMAGES, day)
    if not taken:
        return CardOutcome(LIMIT, f"They have already made their {DAILY_IMAGES} pictures for today. It starts again tomorrow, and adding words to a picture they already have is still unlimited.")

    def give_up(kind: str, facts: str) -> CardOutcome:
        daily_allowance.give_back(db, ALLOWANCE, username, day)
        return CardOutcome(kind, facts)

    try:
        data, _usage = await asyncio.wait_for(asyncio.to_thread(generate, image_prompt, aspect, reference), timeout=TIMEOUT_SECONDS + 10)
    except Blocked:
        return give_up(BLOCKED, "The picture could not be made: the image service's safety filters declined it. Say so kindly in a sentence and offer to try a different idea. Do not guess why.")
    except asyncio.TimeoutError:
        return give_up(UNAVAILABLE, "The picture took too long and was not made. Ask them to try again in a moment.")
    except Failed:
        return give_up(UNAVAILABLE, "The picture could not be made right now. Ask them to try again in a moment.")
    except Exception:
        logger.warning("[image gen] generating failed.", exc_info=True)
        return give_up(UNAVAILABLE, "The picture could not be made right now. Ask them to try again in a moment.")

    name = _file_name(planned.get("title"))
    file_id = await asyncio.to_thread(
        store.save, username, session_id, data, "image/jpeg", name, photo_cards.GENERATED_IMAGE, {"spec": {"prompt": image_prompt, "aspect": aspect}},
    )
    if not file_id:
        return give_up(UNAVAILABLE, "The picture was made but couldn't be saved, so it can't be shown. Ask them to try again in a moment.")
    try:
        await asyncio.to_thread(store.prune, username)
    except Exception:
        logger.warning("[image gen] could not tidy old pictures.", exc_info=True)
    words = image_card.clean_text(planned.get("words")) if planned.get("words") else ""
    logger.info("[image gen] made a picture (%d KB, %d of %d today).", len(data) // 1024, used, DAILY_IMAGES)
    return CardOutcome(MADE, _facts_made(image_prompt, max(DAILY_IMAGES - used, 0), words), [{"filename": name, "fileId": file_id, "generated": photo_cards.GENERATED_IMAGE}])
