import asyncio
import io
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from PIL import Image

from backend.services import photo_cards as pc
from backend.utils import image_card

REF = {"filename": "family.jpg", "gridfs_id": "photo1", "content_type": "image/jpeg"}


def _photo(width=1800, height=1200) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (30, 40, 70)).save(out, format="JPEG")
    return out.getvalue()


# ---------------------------------------------------------------------------------------------------------------
# Noticing the request
# ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    'put "Merry Christmas" across the bottom in gold',
    "Add the text Merry Christmas to this photo",
    "write Happy Holidays from the Harpers on this",
    "make this a Christmas card that says Merry Christmas",
    "can you make a christmas card out of this?",
    "overlay 'Go Hawks' in black and gold at the top of the picture",
    "caption it \"Our first Christmas\"",
])
def test_a_request_for_words_on_an_attached_photo_is_noticed(message):
    assert pc.asks_for_card(message, attached_image=True) is True


@pytest.mark.parametrize("message", [
    'put "Merry Christmas" on the photo',
    "add the words Happy Birthday to the picture",
    "write Merry Christmas across the image in a fancy font",
    "make this a christmas card",
    "put a message on that photo",
])
def test_without_an_attachment_the_message_must_name_the_picture(message):
    assert pc.asks_for_card(message, attached_image=False) is True


@pytest.mark.parametrize("message", [
    "write a message to my boss about the deadline",
    "add a name to the guest list",
    "describe this photo",
    "what does this card say",
    "who is in this picture",
    "make a birthday card for my mom",
    "put the milk in the fridge",
    "can you transcribe the text in this image",
    "",
])
def test_ordinary_messages_are_not_taken_for_a_card_even_with_a_photo_in_the_conversation(message):
    assert pc.asks_for_card(message, attached_image=False) is False


@pytest.mark.parametrize("message", [
    "describe this photo",
    "write a message to my boss",
    "add text to the document",
    "add a name to the guest list",
    "what is in this image",
])
def test_an_attached_photo_does_not_turn_every_message_into_a_card(message):
    assert pc.asks_for_card(message, attached_image=True) is False


def test_short_changes_are_adjustments_and_everything_else_is_not():
    for message in ("make it bigger", "try gold instead", "move it up a bit", "a different font please", "can you make it red?", "put it at the top"):
        assert pc.asks_to_adjust(message) is True, message
    for message in ("thanks, that looks great", "ok", "who won the game last night", ""):
        assert pc.asks_to_adjust(message) is False, message
    assert pc.asks_to_adjust("make it bigger " + "and also " * 30) is False


def _card_reply():
    return AIMessage(content="here you go", additional_kwargs={"kb_images": [{"filename": "x.jpg", "fileId": "c1", "generated": "card"}]})


def test_a_change_is_only_considered_right_after_a_card():
    history = [HumanMessage(content="hi"), _card_reply()]
    assert pc.made_card_recently(history) is True
    assert pc.wants_card("make it bigger", None, history) is True
    assert pc.wants_card("make it bigger", None, [HumanMessage(content="hi"), AIMessage(content="hello")]) is False


def test_a_card_made_long_ago_is_not_the_last_card():
    history = [_card_reply()] + [HumanMessage(content="x"), AIMessage(content="y")] * 3
    assert pc.made_card_recently(history) is False


def test_a_library_image_is_not_a_card():
    reply = AIMessage(content="here", additional_kwargs={"kb_images": [{"filename": "logo.png", "fileId": "k1"}]})
    assert pc.made_card_recently([reply]) is False


def test_the_newest_attached_picture_is_used_and_other_files_are_ignored():
    refs = [REF, {"filename": "x.pdf", "gridfs_id": "p", "content_type": "application/pdf"}, {**REF, "gridfs_id": "photo2"}]
    assert pc.attached_image_ref(refs)["gridfs_id"] == "photo2"
    assert pc.attached_image_ref([]) is None
    assert pc.attached_image_ref(None) is None
    assert pc.attached_image_ref([{"filename": "a.jpg", "content_type": "image/jpeg"}]) is None


def test_a_photo_on_this_message_makes_a_short_request_enough():
    assert pc.wants_card('"Merry Christmas" in gold', [REF], []) is True
    assert pc.wants_card("what a nice day", [REF], []) is False


# ---------------------------------------------------------------------------------------------------------------
# Making the card
# ---------------------------------------------------------------------------------------------------------------

class FakeStore:
    def __init__(self, files=None, session=None, card=None, save_ok=True):
        self.files, self.session, self.card, self.save_ok = files if files is not None else {"photo1": _photo()}, session, card, save_ok
        self.saved, self.pruned = [], 0

    def last_card(self, username, session_id):
        return self.card

    def session_image(self, username, session_id):
        return self.session

    def read(self, username, file_id):
        return self.files.get(file_id)

    def save(self, username, session_id, data, content_type, filename, generated, extra=None):
        self.saved.append({"data": data, "content_type": content_type, "filename": filename, "generated": generated, "extra": extra, "user": username, "session": session_id})
        return "card1" if self.save_ok else None

    def prune(self, username):
        self.pruned += 1
        return 0


class Planner:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply if isinstance(self.reply, str) else json.dumps(self.reply))


@pytest.fixture
def allowance(monkeypatch):
    calls = {"take": 0, "back": 0, "allow": True}

    def take(db, name, username, limit, day, now=None):
        calls["take"] += 1
        return calls["allow"], calls["take"]

    monkeypatch.setattr(pc.daily_allowance, "take", take)
    monkeypatch.setattr(pc.daily_allowance, "give_back", lambda *a, **k: calls.__setitem__("back", calls["back"] + 1))
    return calls


def _run(store, planner, message="put Merry Christmas on it", attached=REF):
    return asyncio.run(pc.make_card(store=store, username="jack", session_id="s1", message=message, attached=attached, invoke=planner, db=object(), day="2026-10-18"))


def test_a_card_is_planned_drawn_stored_and_offered(allowance):
    store, planner = FakeStore(), Planner({"applies": True, "text": "Merry Christmas", "font": "festive", "color": "hawkeye_gold"})
    outcome = _run(store, planner)
    assert outcome.kind == pc.MADE
    assert outcome.images == [{"filename": "merry-christmas.jpg", "fileId": "card1", "generated": "card"}]
    saved = store.saved[0]
    assert saved["data"][:2] == b"\xff\xd8" and saved["content_type"] == "image/jpeg" and saved["generated"] == "card"
    assert saved["extra"]["source_id"] == "photo1" and saved["extra"]["spec"]["font"] == "festive"
    assert (saved["user"], saved["session"]) == ("jack", "s1")
    assert store.pruned == 1 and allowance["take"] == 1 and allowance["back"] == 0
    assert "Download" in outcome.facts and "Merry Christmas" in outcome.facts


def test_the_planner_sees_the_request_and_no_prior_when_there_is_none(allowance):
    planner = Planner({"applies": True, "text": "Hi"})
    _run(FakeStore(), planner, message="put Hi on it")
    assert "put Hi on it" in planner.prompts[0] and "none" in planner.prompts[0]


def test_a_very_long_request_is_clipped_before_it_reaches_the_model(allowance):
    planner = Planner({"applies": True, "text": "Hi"})
    _run(FakeStore(), planner, message="word " * 1000)
    assert len(planner.prompts[0]) < 5000


def test_without_an_attachment_the_newest_photo_in_the_conversation_is_used(allowance):
    store = FakeStore(files={"older": _photo()}, session={"id": "older"})
    outcome = _run(store, Planner({"applies": True, "text": "Hi"}), attached=None)
    assert outcome.kind == pc.MADE and store.saved[0]["extra"]["source_id"] == "older"


def test_a_change_starts_again_from_the_original_photo_with_the_earlier_choices(allowance):
    earlier, problem = image_card.validate_spec({"text": "Merry Christmas", "font": "festive", "color": "red", "size": "medium"})
    assert problem is None
    store = FakeStore(card={"id": "c0", "spec": earlier, "source_id": "photo1"})
    planner = Planner({"applies": True, "size": "xlarge"})
    outcome = _run(store, planner, message="make it bigger", attached=None)
    assert outcome.kind == pc.MADE
    spec = store.saved[0]["extra"]["spec"]
    assert (spec["text"], spec["font"], spec["color"], spec["size"]) == ("Merry Christmas", "festive", "red", "xlarge")
    assert store.saved[0]["extra"]["source_id"] == "photo1"
    assert '"font": "festive"' in planner.prompts[0], "the model was not shown the last card"


def test_a_newly_attached_photo_does_not_inherit_the_last_cards_choices(allowance):
    earlier, _ = image_card.validate_spec({"text": "Old words", "font": "festive", "color": "red"})
    store = FakeStore(files={"photo2": _photo()}, card={"id": "c0", "spec": earlier, "source_id": "photo1"})
    planner = Planner({"applies": True, "text": "New words"})
    outcome = _run(store, planner, attached={**REF, "gridfs_id": "photo2"})
    assert outcome.kind == pc.MADE and store.saved[0]["extra"]["spec"]["font"] == "elegant_script"
    assert "Old words" not in planner.prompts[0]


def test_with_no_photo_anywhere_nothing_is_spent(allowance):
    planner = Planner({"applies": True, "text": "Hi"})
    outcome = _run(FakeStore(), planner, attached=None)
    assert outcome.kind == pc.NO_IMAGE and "attach" in outcome.facts
    assert planner.prompts == [] and allowance["take"] == 0


def test_the_planner_can_say_the_message_was_not_about_a_picture(allowance):
    outcome = _run(FakeStore(), Planner({"applies": False}))
    assert outcome.kind == pc.NOT_FOR_ME and outcome.images == []
    assert allowance["back"] == 1


def test_missing_words_ask_for_the_words(allowance):
    outcome = _run(FakeStore(), Planner({"applies": True, "font": "festive"}))
    assert outcome.kind == pc.NEEDS_TEXT and "what the card should say" in outcome.facts.lower()
    assert allowance["back"] == 1


def test_an_unusable_choice_is_explained_not_guessed(allowance):
    outcome = _run(FakeStore(), Planner({"applies": True, "text": "Hi", "font": "comic sans"}))
    assert outcome.kind == pc.UNAVAILABLE and allowance["back"] == 1


def test_a_planner_that_fails_or_rambles_costs_the_person_nothing(allowance):
    assert _run(FakeStore(), Planner(RuntimeError("model down"))).kind == pc.UNAVAILABLE
    assert allowance["back"] == 1
    assert _run(FakeStore(), Planner("I think you should use a nice font!")).kind in (pc.NEEDS_TEXT, pc.UNAVAILABLE)
    assert allowance["back"] == 2


def test_a_photo_that_has_expired_is_explained(allowance):
    outcome = _run(FakeStore(files={}), Planner({"applies": True, "text": "Hi"}))
    assert outcome.kind == pc.BAD_IMAGE and "attach it again" in outcome.facts and allowance["back"] == 1


def test_a_file_that_is_not_a_picture_is_explained_without_echoing_it(allowance):
    outcome = _run(FakeStore(files={"photo1": b"my private notes, not an image"}), Planner({"applies": True, "text": "Hi"}))
    assert outcome.kind == pc.BAD_IMAGE and "private notes" not in outcome.facts and allowance["back"] == 1


def test_a_full_day_is_refused_before_any_model_call(allowance):
    allowance["allow"] = False
    planner = Planner({"applies": True, "text": "Hi"})
    outcome = _run(FakeStore(), planner)
    assert outcome.kind == pc.LIMIT and planner.prompts == []


def test_a_card_that_cannot_be_saved_is_not_shown_and_costs_nothing(allowance):
    store = FakeStore(save_ok=False)
    outcome = _run(store, Planner({"applies": True, "text": "Hi"}))
    assert outcome.kind == pc.UNAVAILABLE and outcome.images == [] and allowance["back"] == 1 and store.pruned == 0


def test_a_failure_while_tidying_old_cards_does_not_lose_the_new_one(allowance):
    store = FakeStore()
    store.prune = Mock(side_effect=RuntimeError("db"))
    assert _run(store, Planner({"applies": True, "text": "Hi"})).kind == pc.MADE


def test_a_note_about_a_small_photo_reaches_the_reply(allowance):
    store = FakeStore(files={"photo1": _photo(500, 400)})
    outcome = _run(store, Planner({"applies": True, "text": "Hi"}))
    assert "small" in outcome.facts


def test_the_file_name_comes_from_the_words_and_is_safe(allowance):
    store = FakeStore()
    _run(store, Planner({"applies": True, "text": "Merry Xmas! / ../../etc\\passwd"}))
    name = store.saved[0]["filename"]
    assert name.endswith(".jpg") and "/" not in name and "\\" not in name and ".." not in name


def test_nothing_the_person_wrote_is_logged(allowance, caplog):
    import logging

    caplog.set_level(logging.DEBUG, logger="SASS Logger")
    _run(FakeStore(), Planner({"applies": True, "text": "SECRETWORDS for grandma"}), message="put SECRETWORDS for grandma on it")
    assert "SECRETWORDS" not in caplog.text and "grandma" not in caplog.text


def test_the_persons_own_day_decides_the_allowance_day():
    midnight_utc = datetime(2026, 10, 19, 3, 0, tzinfo=timezone.utc)
    assert pc.local_day("America/Chicago", midnight_utc) == "2026-10-18"
    assert pc.local_day("UTC", midnight_utc) == "2026-10-19"
    assert pc.local_day("Not/AZone", midnight_utc) == "2026-10-19"
    assert pc.local_day(None, midnight_utc) == "2026-10-19"


# ---------------------------------------------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------------------------------------------

class FakeCursor(list):
    def sort(self, *args, **kwargs):
        return self

    def limit(self, n):
        return FakeCursor(self[:n])


class FakeFiles:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def find(self, query, projection=None):
        self.queries.append(query)
        return FakeCursor(self.rows)

    def find_one(self, query, projection=None):
        return next((row for row in self.rows if row["_id"] == query["_id"]), None)

    def create_index(self, *args, **kwargs):
        pass


class FakeMongo(dict):
    pass


def test_a_person_can_only_read_their_own_pictures(monkeypatch):
    from bson import ObjectId

    mine, theirs = ObjectId(), ObjectId()
    db = FakeMongo({"fs.files": FakeFiles([{"_id": mine, "metadata": {"username": "jack"}}, {"_id": theirs, "metadata": {"username": "alice"}}])})
    monkeypatch.setattr("gridfs.GridFS", lambda database: SimpleNamespace(get=lambda oid: io.BytesIO(b"bytes-" + str(oid).encode())))
    store = pc.MongoStore(db)
    assert store.read("jack", str(mine)).startswith(b"bytes-")
    assert store.read("jack", str(theirs)) is None
    assert store.read("jack", "not-an-id") is None


def test_only_the_newest_pictures_are_kept_per_person(monkeypatch):
    from bson import ObjectId

    rows = [{"_id": ObjectId()} for _ in range(pc.MAX_KEPT_PER_PERSON + 3)]
    db = FakeMongo({"fs.files": FakeFiles(rows)})
    deleted = []
    monkeypatch.setattr("gridfs.GridFS", lambda database: SimpleNamespace(delete=deleted.append))
    assert pc.MongoStore(db).prune("jack") == 3
    assert deleted == [row["_id"] for row in rows[pc.MAX_KEPT_PER_PERSON:]]
    assert db["fs.files"].queries[0]["metadata.username"] == "jack"


def test_the_lookups_are_limited_to_the_person_and_the_conversation(monkeypatch):
    from bson import ObjectId

    pc.MongoStore._index_ready = True
    db = FakeMongo({"fs.files": FakeFiles([{"_id": ObjectId(), "metadata": {"spec": {"text": "x"}, "source_id": "p"}}])})
    store = pc.MongoStore(db)
    assert store.session_image("jack", "s1")["id"]
    assert store.last_card("jack", "s1")["source_id"] == "p"
    for query in db["fs.files"].queries:
        assert query["metadata.username"] == "jack" and query["metadata.session_id"] == "s1"


def test_stored_cards_are_chat_attachments_so_they_expire_and_are_deleted_with_the_conversation(monkeypatch):
    put = Mock(return_value="abc")
    monkeypatch.setattr("gridfs.GridFS", lambda database: SimpleNamespace(put=put))
    assert pc.MongoStore(FakeMongo()).save("jack", "s1", b"x", "image/jpeg", "a.jpg", pc.GENERATED_CARD, {"spec": {}}) == "abc"
    meta = put.call_args.kwargs["metadata"]
    assert meta["kind"] == "chat_attachment" and meta["username"] == "jack" and meta["session_id"] == "s1" and meta["generated"] == "card"


# ---------------------------------------------------------------------------------------------------------------
# Which picture "it" is
# ---------------------------------------------------------------------------------------------------------------

def _oid(seconds):
    from bson import ObjectId

    return str(ObjectId.from_datetime(datetime.fromtimestamp(1_700_000_000 + seconds, tz=timezone.utc)))


def test_newer_is_decided_by_when_a_file_was_saved():
    assert pc._is_newer(_oid(10), _oid(5)) is True
    assert pc._is_newer(_oid(5), _oid(10)) is False
    assert pc._is_newer(_oid(5), _oid(5)) is False
    assert pc._is_newer(None, _oid(5)) is False
    assert pc._is_newer("photo1", "card0") is False


def test_a_picture_made_after_the_last_card_starts_a_new_card_from_that_picture(allowance):
    earlier, _ = image_card.validate_spec({"text": "Old words", "font": "festive", "color": "red"})
    old_source, old_card, newer_picture = _oid(1), _oid(2), _oid(3)
    store = FakeStore(files={old_source: _photo(), newer_picture: _photo()}, session={"id": newer_picture}, card={"id": old_card, "spec": earlier, "source_id": old_source})
    planner = Planner({"applies": True, "text": "New words"})
    outcome = _run(store, planner, message='put "New words" on it', attached=None)
    assert outcome.kind == pc.MADE
    assert store.saved[0]["extra"]["source_id"] == newer_picture
    assert store.saved[0]["extra"]["spec"]["font"] == "elegant_script", "the earlier card's choices must not carry over to a different picture"
    assert "Old words" not in planner.prompts[0]


def test_a_change_after_the_last_card_keeps_working_on_that_cards_picture(allowance):
    earlier, _ = image_card.validate_spec({"text": "Merry Christmas", "font": "festive", "color": "red", "size": "medium"})
    source, card = _oid(1), _oid(3)
    store = FakeStore(files={source: _photo()}, session={"id": source}, card={"id": card, "spec": earlier, "source_id": source})
    outcome = _run(store, Planner({"applies": True, "size": "large"}), message="bigger", attached=None)
    assert outcome.kind == pc.MADE and store.saved[0]["extra"]["spec"]["font"] == "festive"


def test_a_generated_picture_can_be_the_source_of_a_card_but_a_card_cannot():
    from bson import ObjectId

    db = FakeMongo({"fs.files": FakeFiles([{"_id": ObjectId()}])})
    pc.MongoStore._index_ready = True
    pc.MongoStore(db).session_image("jack", "s1")
    query = db["fs.files"].queries[0]
    assert {"metadata.generated": {"$exists": False}} in query["$or"] and {"metadata.generated": "image"} in query["$or"]
    assert {"metadata.generated": "card"} not in query["$or"]


def test_the_newest_thing_made_decides_what_a_short_change_applies_to():
    card = AIMessage(content="c", additional_kwargs={"kb_images": [{"filename": "a.jpg", "fileId": "1", "generated": "card"}]})
    image = AIMessage(content="i", additional_kwargs={"kb_images": [{"filename": "b.jpg", "fileId": "2", "generated": "image"}]})
    assert pc.recent_generated_kind([image, HumanMessage(content="x"), card]) == "card"
    assert pc.recent_generated_kind([card, HumanMessage(content="x"), image]) == "image"
    assert pc.recent_generated_kind([HumanMessage(content="x")]) is None
