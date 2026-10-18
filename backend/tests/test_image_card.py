import io
import os

import pytest
from PIL import Image

from backend.utils import image_card as ic


def _photo(width=1200, height=800, color=(30, 40, 70), fmt="JPEG", **save_args) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt, **save_args)
    return out.getvalue()


def _spec(**changes):
    spec, problem = ic.validate_spec({"text": "Merry Christmas", **changes})
    assert problem is None, problem
    return spec


# ---------------------------------------------------------------------------------------------------------------
# The fonts that ship with the app
# ---------------------------------------------------------------------------------------------------------------

def test_every_bundled_font_file_exists_and_loads():
    from PIL import ImageFont

    for font_id, info in ic.FONTS.items():
        path = os.path.join(ic.FONT_DIR, info["file"])
        assert os.path.isfile(path), font_id
        assert ImageFont.truetype(path, 40).getlength("Merry Christmas") > 0


def test_every_font_family_ships_with_its_licence():
    names = set(os.listdir(ic.FONT_DIR))
    for info in ic.FONTS.values():
        family = info["file"].split("-")[0]
        assert f"{family}-OFL.txt" in names or f"{family}-LICENSE.txt" in names, family


# ---------------------------------------------------------------------------------------------------------------
# The words
# ---------------------------------------------------------------------------------------------------------------

def test_text_is_cleaned_not_rewritten():
    assert ic.clean_text("  Merry   Christmas  ") == "Merry Christmas"
    assert ic.clean_text("Happy\n\n  Holidays \n") == "Happy\nHolidays"


def test_emoji_and_control_characters_are_dropped_because_no_font_can_draw_them():
    assert ic.clean_text("Merry \U0001F384 Christmas\x07☃") == "Merry Christmas"


def test_text_is_bounded_in_length_and_lines():
    assert len(ic.clean_text("x" * 1000)) == ic.MAX_TEXT_CHARS
    assert len(ic.clean_text("\n".join(["a"] * 20)).split("\n")) == ic.MAX_LINES


# ---------------------------------------------------------------------------------------------------------------
# The spec
# ---------------------------------------------------------------------------------------------------------------

def test_a_bare_text_gets_the_defaults():
    spec = _spec()
    assert (spec["font"], spec["color"], spec["size"], spec["position"], spec["align"]) == ("elegant_script", "white", "large", "bottom", "center")


def test_missing_text_is_the_one_problem_that_asks_for_words():
    spec, problem = ic.validate_spec({"font": "festive"})
    assert spec is None and problem == ic.NEEDS_TEXT_MESSAGE


def test_what_a_model_might_call_a_choice_is_accepted_but_nothing_is_guessed():
    spec = _spec(font="Great Vibes", size="extra large", position="centre", align="centered", color="Hawkeye Gold")
    assert (spec["font"], spec["size"], spec["position"], spec["align"], spec["color"]) == ("elegant_script", "xlarge", "middle", "center", "hawkeye_gold")
    assert ic.validate_spec({"text": "hi", "font": "comic sans"})[0] is None
    assert ic.validate_spec({"text": "hi", "color": "chartreuse-ish"})[0] is None
    assert ic.validate_spec({"text": "hi", "position": "diagonal"})[0] is None


def test_a_hex_colour_is_normalised():
    assert _spec(color="#FFCD00")["color"] == "#ffcd00"
    assert ic.resolve_color("#ffcd00") == (255, 205, 0)
    assert ic.resolve_color("not a colour") is None


def test_a_change_keeps_everything_it_does_not_mention():
    first = _spec(font="festive", color="red", position="top", size="medium")
    changed, problem = ic.validate_spec({"size": "xlarge"}, prior=first)
    assert problem is None
    assert (changed["text"], changed["font"], changed["color"], changed["position"], changed["size"]) == ("Merry Christmas", "festive", "red", "top", "xlarge")


def test_a_nudge_is_clamped_so_the_words_cannot_leave_the_picture():
    assert _spec(dy=5)["dy"] == ic.MAX_NUDGE
    assert _spec(dy=-5)["dy"] == -ic.MAX_NUDGE
    assert ic.validate_spec({"text": "hi", "dy": "a lot"})[0] is None


def test_unknown_keys_in_a_spec_are_ignored():
    spec = _spec(font="festive", script="<script>alert(1)</script>", path="../../etc/passwd")
    assert "script" not in spec and "path" not in spec


def test_a_spec_that_is_not_a_dict_is_refused():
    assert ic.validate_spec("put hello on it")[0] is None
    assert ic.validate_spec(None)[0] is None


def test_describe_spec_reads_in_plain_words():
    text = ic.describe_spec(_spec(font="festive", color="hawkeye_gold", position="top"))
    assert "Merry Christmas" in text and "hawkeye gold" in text and "festive" in text and "at the top" in text


# ---------------------------------------------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------------------------------------------

def _open(card: ic.RenderedCard) -> Image.Image:
    return Image.open(io.BytesIO(card.data))


def test_a_card_is_a_jpeg_of_the_same_size_with_words_only_where_they_were_put():
    source = _photo(1200, 800)
    card = ic.render_card(source, _spec(position="bottom"))
    assert card.content_type == "image/jpeg" and (card.width, card.height) == (1200, 800)
    drawn, original = _open(card).convert("RGB"), Image.open(io.BytesIO(source)).convert("RGB")
    changed_rows = [y for y in range(0, 800, 4) if any(drawn.getpixel((x, y)) != original.getpixel((x, y)) and sum(abs(a - b) for a, b in zip(drawn.getpixel((x, y)), original.getpixel((x, y)))) > 40 for x in range(0, 1200, 8))]
    assert changed_rows, "nothing was drawn"
    assert min(changed_rows) > 800 * 0.55, "words asked for at the bottom appeared higher up"


def test_the_position_is_honoured():
    for position, expect_top in (("top", True), ("bottom", False)):
        card = ic.render_card(_photo(1200, 800), _spec(position=position, color="white"))
        image = _open(card).convert("L")
        band = lambda y0, y1: sum(1 for y in range(y0, y1, 3) for x in range(0, 1200, 6) if image.getpixel((x, y)) > 150)
        assert (band(0, 300) > band(500, 800)) is expect_top


def test_the_original_bytes_are_never_modified():
    source = _photo()
    before = bytes(source)
    ic.render_card(source, _spec())
    assert source == before


def test_a_large_photo_is_scaled_down_so_what_is_kept_stays_small():
    card = ic.render_card(_photo(4800, 3200), _spec())
    assert max(card.width, card.height) == ic.MAX_OUT_EDGE
    assert len(card.data) < 2 * 1024 * 1024


def test_a_small_photo_is_flagged_for_printing():
    card = ic.render_card(_photo(600, 400), _spec())
    assert any("small" in note for note in card.notes)
    assert not any("small" in note for note in ic.render_card(_photo(1800, 1200), _spec()).notes)


def test_words_close_in_tone_to_the_photo_get_a_soft_shade_and_say_so():
    card = ic.render_card(_photo(1200, 800, color=(250, 250, 250)), _spec(color="white"))
    assert any("shade" in note for note in card.notes)


def test_words_that_contrast_with_the_photo_need_no_shade():
    card = ic.render_card(_photo(1800, 1200, color=(20, 20, 40)), _spec(color="white"))
    assert card.notes == []


def test_shade_can_be_forced_on_or_off():
    dark = _photo(1200, 800, color=(20, 20, 40))
    assert any("shade" in n for n in ic.render_card(dark, _spec(scrim="on")).notes)
    light = _photo(1200, 800, color=(250, 250, 250))
    assert not any("shade" in n for n in ic.render_card(light, _spec(scrim="off")).notes)


def test_long_text_is_wrapped_and_shrunk_to_fit_never_run_off_the_picture():
    text = "Wishing you and your family a very merry Christmas and a happy and healthy new year filled with joy"
    card = ic.render_card(_photo(1000, 700), _spec(text=text, size="xlarge", font="classic_serif"))
    image = _open(card).convert("L")
    assert all(image.getpixel((x, y)) < 250 for x in (0, 999) for y in range(0, 700, 50)), "text touched the edge of the picture"


def test_a_single_very_long_word_still_fits_the_width():
    card = ic.render_card(_photo(800, 600), _spec(text="Supercalifragilisticexpialidocious" * 2, size="xlarge", font="heavy"))
    image = _open(card).convert("L")
    assert max(image.getpixel((0, y)) for y in range(0, 600, 20)) < 200 and max(image.getpixel((799, y)) for y in range(0, 600, 20)) < 200


def test_every_font_draws():
    for font_id in ic.FONTS:
        assert ic.render_card(_photo(800, 600), _spec(font=font_id)).data


def test_png_with_transparency_is_flattened_onto_white():
    out = io.BytesIO()
    Image.new("RGBA", (900, 600), (0, 0, 0, 0)).save(out, format="PNG")
    card = ic.render_card(out.getvalue(), _spec(color="black"))
    assert _open(card).convert("RGB").getpixel((5, 5))[0] > 200


def test_a_rotated_phone_photo_is_turned_upright():
    image = Image.new("RGB", (800, 400), (30, 40, 70))
    exif = Image.Exif()
    exif[0x0112] = 6                       # rotate 90 clockwise to view
    out = io.BytesIO()
    image.save(out, format="JPEG", exif=exif.tobytes())
    card = ic.render_card(out.getvalue(), _spec())
    assert (card.width, card.height) == (400, 800)


def test_not_a_picture_is_a_clean_error_that_does_not_echo_anything():
    with pytest.raises(ic.CardError) as caught:
        ic.render_card(b"this is not an image, it is a secret note", _spec())
    assert "secret" not in str(caught.value)


def test_an_empty_or_oversized_source_is_refused():
    with pytest.raises(ic.CardError):
        ic.render_card(b"", _spec())
    with pytest.raises(ic.CardError):
        ic.render_card(b"x" * (ic.MAX_SOURCE_BYTES + 1), _spec())


def test_a_picture_with_too_many_pixels_is_refused_before_it_is_decoded(monkeypatch):
    monkeypatch.setattr(ic, "MAX_SOURCE_PIXELS", 1000)
    with pytest.raises(ic.CardError):
        ic.render_card(_photo(200, 200), _spec())


def test_a_missing_font_file_is_a_clean_error(monkeypatch):
    monkeypatch.setattr(ic, "FONT_DIR", os.path.join(ic.FONT_DIR, "nowhere"))
    with pytest.raises(ic.CardError):
        ic.render_card(_photo(), _spec())
