"""Words on a photo: the drawing half of "put Merry Christmas across the bottom of this picture".

Sonic never draws anything itself. A small model call turns what the person asked for into a SPEC, a fixed set of choices (which words, which of a
handful of bundled fonts, which colour, where, how big), and this module checks the spec and does the drawing with Pillow. So a model slip can only
produce a wrong choice from the list, never a broken file, and nothing here costs anything to run beyond the CPU.

- NOTHING IS EVER ADDED THAT THE PERSON DID NOT ASK FOR. The text is copied into the picture as written; it is only cleaned (control characters and
  emoji that none of the bundled fonts can draw are dropped, length is capped).
- THE ORIGINAL IS NEVER CHANGED. A card is drawn onto a copy, so "try gold instead" starts again from the untouched photo (see backend/services/image_cards.py).
- IT STAYS READABLE. The outline is picked to contrast with the text colour, and when the text colour is close to the photo behind it a soft scrim is
  added (reported back, so Sonic can say so).
- THE FILE STAYS SMALL. The long edge is capped and the result saved as JPEG, because every card is stored for the person (see MAX_OUT_EDGE).

The fonts are in backend/assets/fonts with their licences (SIL Open Font License, and Apache 2.0 for Mountains of Christmas); both allow bundling.
"""
import io
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")

# id -> label shown to the person, the file, and a size correction (script faces look small next to block capitals at the same point size).
FONTS: Dict[str, Dict[str, Any]] = {
    "elegant_script": {"label": "elegant script", "file": "GreatVibes-Regular.ttf", "scale": 1.45},
    "friendly_script": {"label": "friendly script", "file": "Pacifico-Regular.ttf", "scale": 1.0},
    "festive": {"label": "festive", "file": "MountainsofChristmas-Bold.ttf", "scale": 1.25},
    "bold_caps": {"label": "bold capitals", "file": "BebasNeue-Regular.ttf", "scale": 1.15},
    "retro": {"label": "retro script", "file": "Lobster-Regular.ttf", "scale": 1.05},
    "classic_serif": {"label": "classic serif", "file": "CinzelDecorative-Bold.ttf", "scale": 0.85},
    "heavy": {"label": "heavy block", "file": "Anton-Regular.ttf", "scale": 1.0},
}
DEFAULT_FONT = "elegant_script"

COLORS: Dict[str, Tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "cream": (255, 248, 225),
    "black": (20, 20, 20),
    "gold": (212, 175, 55),
    "hawkeye_gold": (255, 205, 0),
    "red": (200, 16, 46),
    "green": (30, 107, 58),
    "blue": (30, 80, 160),
    "silver": (200, 204, 210),
    "orange": (240, 120, 30),
    "pink": (240, 130, 170),
}
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

SIZES = {"small": 0.05, "medium": 0.08, "large": 0.12, "xlarge": 0.17}     # font height as a share of the image width
POSITIONS = ("top", "middle", "bottom")
ALIGNS = ("left", "center", "right")
OUTLINES = ("auto", "none", "dark", "light")
SCRIMS = ("auto", "on", "off")

MAX_TEXT_CHARS = 160
MAX_LINES = 6
MAX_NUDGE = 0.25                 # "a little higher" moves the text by a share of the image height, never off the picture
MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_PIXELS = 50_000_000   # refuses a decompression bomb before decoding it
# A card is kept for the person, so its size is bounded: 2400 px on the long edge is 8 inches at 300 dpi, and a JPEG at that size is about 1 MB.
MAX_OUT_EDGE = 2400
JPEG_QUALITY = 88
SMALL_FOR_PRINT_EDGE = 1500      # shorter than this and a 5x7 print will look soft: Sonic says so

NEEDS_TEXT_MESSAGE = "I need the words to put on the picture."

# What a model may call a choice instead of its exact id ("Great Vibes", "extra large", "centre"). Anything not here or in the lists is refused, never guessed.
_ALIASES: Dict[str, Dict[str, str]] = {
    "font": {
        "script": "elegant_script", "cursive": "elegant_script", "great_vibes": "elegant_script", "pacifico": "friendly_script", "christmas": "festive",
        "block": "bold_caps", "bebas": "bold_caps", "bebas_neue": "bold_caps", "serif": "classic_serif", "cinzel": "classic_serif", "anton": "heavy", "lobster": "retro",
        **{info["label"].replace(" ", "_"): font_id for font_id, info in FONTS.items()},
    },
    "size": {"extra_large": "xlarge", "x_large": "xlarge", "xl": "xlarge", "huge": "xlarge", "big": "large", "tiny": "small", "normal": "medium", "regular": "medium"},
    "position": {"center": "middle", "centre": "middle", "centered": "middle", "upper": "top", "lower": "bottom"},
    "align": {"centre": "center", "centered": "center", "middle": "center"},
}

DEFAULT_SPEC: Dict[str, Any] = {
    "text": "", "font": DEFAULT_FONT, "color": "white", "size": "large", "position": "bottom", "align": "center",
    "dy": 0.0, "outline": "auto", "scrim": "auto",
}

# Emoji and pictographs: no bundled font has them, and a missing glyph draws as an empty box.
_UNDRAWABLE_RE = re.compile("[\U00010000-\U0010ffff←-⯿☀-➿️‍]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class CardError(Exception):
    """A reason the card could not be made, already worded for the person. Never carries their text."""


@dataclass
class RenderedCard:
    data: bytes
    content_type: str
    width: int
    height: int
    notes: List[str]            # plain facts for Sonic to mention: "too small to print large", "added a soft shade behind the words"


def clean_text(value: Any) -> str:
    """The words as they will be drawn: no control characters, no emoji, tidy spacing, a bounded length and number of lines."""
    text = _CONTROL_RE.sub("", str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = _UNDRAWABLE_RE.sub("", text)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    lines = [line for line in lines if line][:MAX_LINES]
    return "\n".join(lines)[:MAX_TEXT_CHARS].strip()


def color_key(value: Any) -> Optional[str]:
    """A palette name ("hawkeye gold" -> "hawkeye_gold") or a hex colour ("#FFCD00" -> "#ffcd00"); None for anything else."""
    name = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if name in COLORS:
        return name
    match = _HEX_RE.match(str(value or "").strip())
    return "#" + match.group(1).lower() if match else None


def resolve_color(value: Any) -> Optional[Tuple[int, int, int]]:
    key = color_key(value)
    if key is None:
        return None
    if key in COLORS:
        return COLORS[key]
    return (int(key[1:3], 16), int(key[3:5], 16), int(key[5:7], 16))


def _choice(key: str, value: Any) -> str:
    name = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    return _ALIASES.get(key, {}).get(name, name)


def validate_spec(raw: Any, prior: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(a clean spec, None) or (None, why not). `raw` may hold only what changed: it is laid over `prior` (an earlier card's spec), which is how
    "make it bigger" or "try gold" keeps everything else. Unknown keys are ignored; a known key with a value outside its list is a problem, never a guess."""
    if not isinstance(raw, dict):
        return None, "I couldn't tell what to put on the picture."
    spec = dict(DEFAULT_SPEC)
    if isinstance(prior, dict):
        spec.update({key: prior[key] for key in DEFAULT_SPEC if key in prior})
    for key in DEFAULT_SPEC:
        if key in raw and raw[key] is not None:
            spec[key] = raw[key]

    spec["text"] = clean_text(spec["text"])
    if not spec["text"]:
        return None, NEEDS_TEXT_MESSAGE
    spec["font"] = _choice("font", spec["font"])
    if spec["font"] not in FONTS:
        return None, "That font isn't one I have."
    spec["color"] = color_key(spec["color"])
    if spec["color"] is None:
        return None, "I don't know that colour."
    for key, allowed in (("size", SIZES), ("position", POSITIONS), ("align", ALIGNS), ("outline", OUTLINES), ("scrim", SCRIMS)):
        spec[key] = _choice(key, spec[key])
        if spec[key] not in allowed:
            return None, f"I can't use that {key}."
    try:
        spec["dy"] = max(-MAX_NUDGE, min(MAX_NUDGE, float(spec["dy"])))
    except (TypeError, ValueError):
        return None, "I can't use that position."
    return spec, None


def describe_spec(spec: Dict[str, Any]) -> str:
    """What was drawn, in plain words for Sonic to report. Includes the text, so it is for the reply only and is never logged."""
    font = FONTS[spec["font"]]["label"]
    where = {"top": "at the top", "middle": "across the middle", "bottom": "along the bottom"}[spec["position"]]
    return f"\"{spec['text']}\" in {spec['color'].replace('_', ' ')} {font} lettering {where}, size {spec['size']}"


def _luminance(rgb: Tuple[int, int, int]) -> float:
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def _load_font(font_id: str, pixels: float) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, FONTS[font_id]["file"])
    try:
        return ImageFont.truetype(path, size=max(8, int(pixels)))
    except OSError:
        raise CardError("My lettering files aren't available right now.")


def _wrap(font: ImageFont.FreeTypeFont, text: str, max_width: float) -> List[str]:
    """Greedy word wrap that respects the person's own line breaks. A single word wider than the line stays whole (the caller shrinks the type)."""
    lines: List[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split(" "):
            attempt = f"{current} {word}".strip()
            if current and font.getlength(attempt) > max_width:
                lines.append(current)
                current = word
            else:
                current = attempt
        lines.append(current)
    return lines


def _layout(spec: Dict[str, Any], width: int, height: int) -> Tuple[ImageFont.FreeTypeFont, List[str], int, int, float]:
    """(font, lines, line height, stroke width, font pixels): the type is shrunk until every word fits the width and the block takes no more than
    45% of the height, so long text becomes smaller and never runs off the picture."""
    base = SIZES[spec["size"]] * width * FONTS[spec["font"]]["scale"]
    max_width = width * 0.88
    pixels = base
    while True:
        font = _load_font(spec["font"], pixels)
        lines = _wrap(font, spec["text"], max_width)
        ascent, descent = font.getmetrics()
        line_height = int((ascent + descent) * 1.02)
        stroke = max(2, int(pixels * 0.06))
        too_wide = any(font.getlength(line) + 2 * stroke > max_width for line in lines)
        too_tall = line_height * len(lines) > height * 0.45
        if (not too_wide and not too_tall) or pixels <= 14:
            return font, lines, line_height, stroke, pixels
        pixels = max(14, pixels * 0.92)


def render_card(source: bytes, spec: Dict[str, Any]) -> RenderedCard:
    """The picture with the words drawn on it. `spec` must have passed validate_spec. Raises CardError (with a message fit to show) for anything
    that is not a usable picture."""
    if not source or len(source) > MAX_SOURCE_BYTES:
        raise CardError("That picture is too large for me to work with.")
    try:
        image = Image.open(io.BytesIO(source))
        if image.size[0] * image.size[1] > MAX_SOURCE_PIXELS:
            raise CardError("That picture is too large for me to work with.")
        image = ImageOps.exif_transpose(image)          # a phone photo's rotation is in its metadata, not its pixels
        has_alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
        image = image.convert("RGBA")
    except CardError:
        raise
    except Exception:
        raise CardError("I couldn't open that picture.")

    notes: List[str] = []
    width, height = image.size
    long_edge = max(width, height)
    if long_edge > MAX_OUT_EDGE:
        scale = MAX_OUT_EDGE / long_edge
        image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
        width, height = image.size
    if has_alpha:
        flat = Image.new("RGBA", image.size, (255, 255, 255, 255))
        flat.alpha_composite(image)
        image = flat
    if max(width, height) < SMALL_FOR_PRINT_EDGE:
        notes.append("the photo is small, so it may look soft if printed larger than a few inches")

    color = resolve_color(spec["color"])
    font, lines, line_height, stroke, pixels = _layout(spec, width, height)
    block_height = line_height * len(lines)
    widths = [font.getlength(line) for line in lines]
    margin_x, margin_y = int(width * 0.05), int(height * 0.06)
    if spec["position"] == "top":
        top = margin_y
    elif spec["position"] == "middle":
        top = (height - block_height) // 2
    else:
        top = height - margin_y - block_height
    top = int(max(0, min(height - block_height, top + spec["dy"] * height)))

    def line_x(line_width: float) -> int:
        if spec["align"] == "left":
            return margin_x
        if spec["align"] == "right":
            return int(width - margin_x - line_width)
        return int((width - line_width) / 2)

    block_left = int(min(line_x(w) for w in widths))
    block_right = int(max(line_x(w) + w for w in widths))

    text_lum = _luminance(color)
    outline = spec["outline"]
    if outline == "auto":
        outline = "dark" if text_lum > 0.5 else "light"
    outline_rgb = {"dark": (10, 10, 10), "light": (250, 250, 250), "none": None}[outline]

    # The photo's tone behind the words. A shade is only added when the words would genuinely be lost: nearly the same tone as the photo, or close
    # to it with no outline to separate them.
    behind = image.crop((block_left, top, max(block_left + 1, block_right), max(top + 1, top + block_height))).convert("L").resize((1, 1), Image.BOX).getpixel((0, 0)) / 255.0
    gap = abs(behind - text_lum)
    scrim = spec["scrim"] == "on" or (spec["scrim"] == "auto" and (gap < 0.15 or (outline_rgb is None and gap < 0.30)))

    # The patch is cut wide enough that the shade's blur has faded out before the edge of it, so no seam shows.
    pad = int(pixels * (1.8 if scrim else 0.6)) + stroke
    region = (max(0, block_left - pad), max(0, top - pad), min(width, block_right + pad), min(height, top + block_height + pad))
    patch = image.crop(region)
    if scrim:
        notes.append("the words are close in tone to the photo behind them, so I added a soft shade for legibility")
        mask = Image.new("L", patch.size, 0)
        inflate = int(pixels * 0.3)
        ImageDraw.Draw(mask).rectangle(
            (block_left - region[0] - inflate, top - region[1] - inflate, block_right - region[0] + inflate, top + block_height - region[1] + inflate), fill=105,
        )
        mask = mask.filter(ImageFilter.GaussianBlur(max(2, pixels * 0.3)))
        shade = Image.new("RGBA", patch.size, (0, 0, 0, 255) if text_lum > 0.5 else (255, 255, 255, 255))
        patch = Image.composite(shade, patch, mask)

    shadow = Image.new("RGBA", patch.size, (0, 0, 0, 0))
    letters = Image.new("RGBA", patch.size, (0, 0, 0, 0))
    shadow_draw, letter_draw = ImageDraw.Draw(shadow), ImageDraw.Draw(letters)
    offset = max(2, int(pixels * 0.05))
    for index, line in enumerate(lines):
        x = line_x(widths[index]) - region[0]
        y = top + index * line_height - region[1]
        if text_lum > 0.35 or outline == "dark":
            shadow_draw.text((x + offset, y + offset), line, font=font, fill=(0, 0, 0, 170), stroke_width=stroke)
        letter_draw.text((x, y), line, font=font, fill=color + (255,), stroke_width=stroke if outline_rgb else 0, stroke_fill=(outline_rgb + (255,)) if outline_rgb else None)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1.5, pixels * 0.04)))
    patch.alpha_composite(shadow)
    patch.alpha_composite(letters)
    image.paste(patch, region[:2])

    out = io.BytesIO()
    image.convert("RGB").save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return RenderedCard(out.getvalue(), "image/jpeg", width, height, notes)
