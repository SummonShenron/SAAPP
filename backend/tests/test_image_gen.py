import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from PIL import Image

from backend.services import image_gen as ig
from backend.services import photo_cards as pc


def _png(size=(640, 480)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (20, 90, 140)).save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------------------------------------------------------
# Noticing the request
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "generate an image of a snowy stadium at dusk",
    "make me a picture of a golden retriever in a Santa hat",
    "can you create an illustration of a lighthouse in a storm",
    "draw a cartoon hawk wearing a scarf",
    "paint me a sunset over cornfields",
    "design a poster for the neighborhood cookout",
    "come up with a wallpaper of mountains for my phone",
    "make me a Christmas card with a snowy Kinnick Stadium",
    "design a birthday card for my dad, he loves fishing",
])
def test_a_request_to_make_a_picture_is_noticed(message):
    assert ig.asks_to_generate(message) is True


@pytest.mark.parametrize("message", [
    "show me a picture of the lease",
    "what does this image show",
    "build a docker image for the api",
    "create a diagram of the architecture",
    "make a chart of my spending",
    "write a story about a dragon",
    "make me a spreadsheet",
    "draw the conclusion from this report",
    "how do I make a picture frame",
    "",
])
def test_ordinary_and_technical_messages_are_not(message):
    assert ig.asks_to_generate(message) is False


def _picture_reply(kind):
    return AIMessage(content="here", additional_kwargs={"kb_images": [{"filename": "x.jpg", "fileId": "i1", "generated": kind}]})


def test_a_short_change_is_only_a_change_right_after_a_picture():
    after_picture = [HumanMessage(content="hi"), _picture_reply("image")]
    assert ig.wants_image("make it snowier", None, after_picture) is True
    assert ig.wants_image("try a watercolor version", None, after_picture) is True
    assert ig.wants_image("make it snowier", None, [HumanMessage(content="hi"), AIMessage(content="hello")]) is False
    assert ig.wants_image("make it snowier", None, [HumanMessage(content="hi"), _picture_reply("card")]) is False
    assert ig.wants_image("thanks, that's lovely", None, after_picture) is False


def test_a_card_from_a_photo_that_is_already_here_goes_to_the_card_maker_and_a_card_from_nothing_to_the_picture_maker():
    assert pc.asks_for_card("make this a christmas card", attached_image=False) is True      # the card maker is checked first (see build_agent_plan)
    assert pc.asks_for_card("make me a christmas card with a snowy stadium", attached_image=False) is False
    assert ig.asks_to_generate("make me a christmas card with a snowy stadium") is True


def test_a_photo_on_the_message_means_the_request_is_about_the_photo_not_a_new_picture():
    ref = [{"filename": "a.jpg", "gridfs_id": "p1", "content_type": "image/jpeg"}]
    assert ig.wants_image("draw a picture of a dog", ref, []) is False


def test_words_on_a_just_made_picture_go_to_the_card_maker_not_back_to_the_picture_maker():
    history = [HumanMessage(content="hi"), _picture_reply("image")]
    assert pc.wants_card("put \"Merry Christmas\" on it in gold", None, history) is True
    assert pc.wants_card("put \"Merry Christmas\" on it in gold", None, [HumanMessage(content="hi"), AIMessage(content="hello")]) is False


# ---------------------------------------------------------------------------------------------------------------
# The image model call
# ---------------------------------------------------------------------------------------------------------------

def _response(parts=None, finish="STOP", block=None, tokens=1500):
    candidate = SimpleNamespace(finish_reason=SimpleNamespace(name=finish), content=SimpleNamespace(parts=parts or []))
    return SimpleNamespace(
        candidates=[candidate], prompt_feedback=SimpleNamespace(block_reason=block) if block else None,
        usage_metadata=SimpleNamespace(prompt_token_count=58, candidates_token_count=tokens),
    )


def _image_part(data=None):
    return SimpleNamespace(inline_data=SimpleNamespace(data=data or _png(), mime_type="image/png"), text=None)


@pytest.fixture
def fake_client(monkeypatch):
    holder = SimpleNamespace(response=None, error=None, calls=[])

    def generate_content(model, contents, config):
        holder.calls.append({"model": model, "contents": contents, "aspect": config.image_config.aspect_ratio, "modalities": config.response_modalities})
        if holder.error:
            raise holder.error
        return holder.response

    monkeypatch.setattr(ig, "_get_client", lambda: SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    recorded = []
    monkeypatch.setattr("backend.utils.llm_usage.record", lambda model, label, usage, day=None: recorded.append((model, label, usage)))
    holder.recorded = recorded
    return holder


def test_a_picture_comes_back_as_a_modest_jpeg_and_is_counted_by_the_meter(fake_client):
    fake_client.response = _response([_image_part()])
    data, usage = ig.generate_sync("a snowy stadium. No text.", "4:3")
    assert data[:2] == b"\xff\xd8"
    assert fake_client.calls[0]["model"] == ig.MODEL and fake_client.calls[0]["aspect"] == "4:3" and fake_client.calls[0]["modalities"] == ["IMAGE"]
    assert fake_client.recorded == [(ig.MODEL, "image_gen.generate", usage)]
    assert usage["output"] == 1500 and usage["input"] == 58


def test_a_response_with_no_token_count_is_still_counted_as_a_real_picture(fake_client):
    response = _response([_image_part()], tokens=0)
    fake_client.response = response
    assert ig.generate_sync("x", "1:1")[1]["output"] == ig.ASSUMED_OUTPUT_TOKENS


def test_a_safety_refusal_is_told_apart_from_a_fault(fake_client):
    fake_client.response = _response([], finish="IMAGE_SAFETY")
    with pytest.raises(ig.Blocked):
        ig.generate_sync("x", "1:1")
    fake_client.response = _response([], block="SAFETY")
    with pytest.raises(ig.Blocked):
        ig.generate_sync("x", "1:1")
    fake_client.response = _response([], finish="STOP")
    with pytest.raises(ig.Failed):
        ig.generate_sync("x", "1:1")


def test_a_call_that_errors_is_a_failure_that_does_not_echo_the_request(fake_client, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="SASS Logger")
    fake_client.error = RuntimeError("429 for prompt SECRETPROMPT")
    with pytest.raises(ig.Failed) as caught:
        ig.generate_sync("SECRETPROMPT", "1:1")
    assert "SECRETPROMPT" not in str(caught.value) and "SECRETPROMPT" not in caplog.text


def test_something_that_is_not_a_picture_is_a_failure(fake_client):
    fake_client.response = _response([_image_part(b"not a picture")])
    with pytest.raises(ig.Failed):
        ig.generate_sync("x", "1:1")


def test_a_huge_picture_is_scaled_down():
    out = ig.to_jpeg(_png((4000, 3000)))
    assert max(Image.open(io.BytesIO(out)).size) == ig.MAX_OUT_EDGE


# ---------------------------------------------------------------------------------------------------------------
# Making the picture
# ---------------------------------------------------------------------------------------------------------------

class Store:
    def __init__(self, prior=None, save_ok=True):
        self.prior, self.save_ok, self.saved, self.pruned = prior, save_ok, [], 0

    def last_image(self, username, session_id):
        return self.prior

    def save(self, username, session_id, data, content_type, filename, generated, extra=None):
        self.saved.append({"data": data, "content_type": content_type, "filename": filename, "generated": generated, "extra": extra, "user": username, "session": session_id})
        return "pic1" if self.save_ok else None

    def prune(self, username):
        self.pruned += 1


class Planner:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply if isinstance(self.reply, str) else json.dumps(self.reply))


GOOD = {"applies": True, "refuse": None, "prompt": "A snowy stadium at dusk, painterly. No text, letters or numbers.", "aspect": "3:4", "title": "Snowy Stadium!"}


@pytest.fixture
def allowance(monkeypatch):
    calls = {"take": 0, "back": 0, "allow": True, "used": 1}

    def take(db, name, username, limit, day, now=None):
        calls["take"] += 1
        calls["limit"] = limit
        return calls["allow"], calls["used"]

    monkeypatch.setattr(ig.daily_allowance, "take", take)
    monkeypatch.setattr(ig.daily_allowance, "give_back", lambda *a, **k: calls.__setitem__("back", calls["back"] + 1))
    return calls


def _make(store, planner, generate=None, message="make me a snowy stadium"):
    calls = []

    def default_generate(prompt, aspect):
        calls.append((prompt, aspect))
        return b"\xff\xd8jpegbytes", {"output": 1300}

    outcome = asyncio.run(ig.make_image(
        store=store, username="jack", session_id="s1", message=message, invoke=planner, db=object(), day="2026-10-18", generate=generate or default_generate,
    ))
    return outcome, calls


def test_a_picture_is_planned_generated_stored_and_offered(allowance):
    store, planner = Store(), Planner(GOOD)
    outcome, calls = _make(store, planner)
    assert outcome.kind == ig.MADE
    assert outcome.images == [{"filename": "snowy-stadium.jpg", "fileId": "pic1", "generated": "image"}]
    assert calls == [("A snowy stadium at dusk, painterly. No text, letters or numbers.", "3:4")]
    saved = store.saved[0]
    assert saved["generated"] == "image" and saved["content_type"] == "image/jpeg" and saved["extra"]["spec"] == {"prompt": GOOD["prompt"], "aspect": "3:4"}
    assert (saved["user"], saved["session"]) == ("jack", "s1") and store.pruned == 1
    assert allowance["take"] == 1 and allowance["limit"] == ig.DAILY_IMAGES and allowance["back"] == 0
    assert "Download" in outcome.facts and "more picture" in outcome.facts


def test_the_last_one_of_the_day_says_so(allowance):
    allowance["used"] = ig.DAILY_IMAGES
    outcome, _ = _make(Store(), Planner(GOOD))
    assert "last one for today" in outcome.facts


def test_a_change_gives_the_planner_the_prompt_that_made_the_last_picture(allowance):
    planner = Planner(GOOD)
    _make(Store(prior={"id": "p0", "spec": {"prompt": "A calm lake at sunrise."}}), planner, message="make it snowier")
    assert "A calm lake at sunrise." in planner.prompts[0]
    _make(Store(), planner)
    assert "none" in planner.prompts[1]


def test_words_asked_for_are_left_for_the_card_maker_and_the_reply_says_so(allowance):
    outcome, calls = _make(Store(), Planner({**GOOD, "words": "Merry Christmas"}))
    assert "Merry Christmas" in outcome.facts and "not in the picture yet" in outcome.facts
    assert "Merry Christmas" not in calls[0][0], "the image model must never be asked to draw the words"


def test_a_request_that_should_not_be_made_is_declined_without_spending_anything(allowance):
    outcome, calls = _make(Store(), Planner({"applies": True, "refuse": "real_person"}))
    assert outcome.kind == ig.REFUSED and "real person" in outcome.facts and calls == [] and allowance["take"] == 0
    outcome, _ = _make(Store(), Planner({"applies": True, "refuse": "something-new"}))
    assert outcome.kind == ig.REFUSED


def test_a_message_that_was_not_a_request_is_answered_normally_for_free(allowance):
    outcome, calls = _make(Store(), Planner({"applies": False}))
    assert outcome.kind == ig.NOT_FOR_ME and calls == [] and allowance["take"] == 0


def test_the_daily_limit_stops_it_before_any_picture_is_paid_for(allowance):
    allowance["allow"] = False
    outcome, calls = _make(Store(), Planner(GOOD))
    assert outcome.kind == ig.LIMIT and calls == [] and str(ig.DAILY_IMAGES) in outcome.facts


def test_the_limit_is_three_a_day_by_default():
    assert ig.DAILY_IMAGES == 3


def test_a_filtered_picture_costs_nothing_and_is_not_blamed_on_a_reason(allowance):
    def blocked(prompt, aspect):
        raise ig.Blocked("x")

    outcome, _ = _make(Store(), Planner(GOOD), generate=blocked)
    assert outcome.kind == ig.BLOCKED and "safety filters" in outcome.facts and allowance["back"] == 1


def test_any_failure_gives_the_use_back(allowance):
    def failed(prompt, aspect):
        raise ig.Failed("x")

    def broken(prompt, aspect):
        raise ValueError("boom")

    for generate in (failed, broken):
        assert _make(Store(), Planner(GOOD), generate=generate)[0].kind == ig.UNAVAILABLE
    assert allowance["back"] == 2


def test_a_picture_that_cannot_be_saved_is_not_shown_and_costs_nothing(allowance):
    outcome, _ = _make(Store(save_ok=False), Planner(GOOD))
    assert outcome.kind == ig.UNAVAILABLE and outcome.images == [] and allowance["back"] == 1


def test_a_planner_that_fails_or_gives_nothing_to_draw_costs_nothing(allowance):
    assert _make(Store(), Planner(RuntimeError("down")))[0].kind == ig.UNAVAILABLE
    assert _make(Store(), Planner({"applies": True, "refuse": None, "prompt": "  "}))[0].kind == ig.UNAVAILABLE
    assert allowance["take"] == 0


def test_a_bad_aspect_falls_back_and_an_oversized_prompt_is_cut(allowance):
    outcome, calls = _make(Store(), Planner({**GOOD, "aspect": "7:3", "prompt": "word " * 1000}))
    assert calls[0][1] == ig.DEFAULT_ASPECT and len(calls[0][0]) <= ig.MAX_PROMPT_CHARS


def test_a_failure_while_tidying_old_pictures_does_not_lose_the_new_one(allowance):
    store = Store()
    store.prune = lambda username: (_ for _ in ()).throw(RuntimeError("db"))
    assert _make(store, Planner(GOOD))[0].kind == ig.MADE


def test_nothing_the_person_asked_for_is_logged(allowance, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="SASS Logger")
    _make(Store(), Planner({**GOOD, "prompt": "SECRETSCENE for grandma. No text."}), message="draw SECRETSCENE for grandma")
    assert "SECRETSCENE" not in caplog.text and "grandma" not in caplog.text


def test_the_price_of_the_model_is_something_an_operator_can_set():
    assert ig.MODEL.startswith("gemini-") and "image" in ig.MODEL
