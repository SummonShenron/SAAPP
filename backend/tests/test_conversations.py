import pytest
from langchain_core.messages import HumanMessage, AIMessage

from backend.utils import app_utils


@pytest.fixture(autouse=True)
def isolated_conversations_dir(tmp_path, monkeypatch):
    """Redirects the JSON fallback store to a temp dir and forces get_db() to None
    so every test exercises the local-file fallback path deterministically."""
    monkeypatch.setattr(app_utils, "CONVERSATIONS_DIR", str(tmp_path))
    monkeypatch.setattr(app_utils, "get_db", lambda: None)
    yield


def test_save_conversation_turn_creates_and_titles_new_conversation():
    messages = [HumanMessage(content="What is the capital of France?"), AIMessage(content="Paris.")]
    doc = app_utils.save_conversation_turn("jack", "conv-1", messages)

    assert doc["session_id"] == "conv-1"
    assert doc["title"] == "What is the capital of France?"
    assert doc["messages"] == [
        {"type": "human", "content": "What is the capital of France?"},
        {"type": "ai", "content": "Paris."},
    ]

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1
    assert conversations[0]["session_id"] == "conv-1"


def test_save_conversation_turn_preserves_title_and_created_at_on_update():
    first = app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="First question")])
    second = app_utils.save_conversation_turn(
        "jack", "conv-1",
        [HumanMessage(content="First question"), AIMessage(content="An answer"), HumanMessage(content="Follow-up")]
    )

    assert second["title"] == first["title"] == "First question"
    assert second["created_at"] == first["created_at"]
    assert len(second["messages"]) == 3

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1  # updated in place, not duplicated


def test_save_conversation_turn_with_no_human_message_defaults_title():
    doc = app_utils.save_conversation_turn("jack", "conv-1", [])
    assert doc["title"] == "New conversation"


def test_clear_chat_semantics_empties_messages_but_keeps_title():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Remember this title")])
    cleared = app_utils.save_conversation_turn("jack", "conv-1", [])

    assert cleared["title"] == "Remember this title"
    assert cleared["messages"] == []

    conversations = app_utils.load_user_conversations("jack")
    assert len(conversations) == 1
    assert conversations[0]["messages"] == []


def test_conversations_are_scoped_per_username():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Jack's message")])
    app_utils.save_conversation_turn("alice", "conv-2", [HumanMessage(content="Alice's message")])

    jack_convos = app_utils.load_user_conversations("jack")
    alice_convos = app_utils.load_user_conversations("alice")
    assert [c["session_id"] for c in jack_convos] == ["conv-1"]
    assert [c["session_id"] for c in alice_convos] == ["conv-2"]


def test_multiple_conversations_per_user_are_independent():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Thread one")])
    app_utils.save_conversation_turn("jack", "conv-2", [HumanMessage(content="Thread two")])

    conversations = app_utils.load_user_conversations("jack")
    session_ids = {c["session_id"] for c in conversations}
    assert session_ids == {"conv-1", "conv-2"}


def test_delete_user_conversation():
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Hello")])

    assert app_utils.delete_user_conversation("jack", "conv-1") is True
    assert app_utils.load_user_conversations("jack") == []
    assert app_utils.delete_user_conversation("jack", "conv-1") is False


class _FakeMongoCollection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, filt, projection=None):
        return [d for d in self.docs if d.get("username") == filt.get("username")]

    def delete_one(self, filt):
        self.docs[:] = [
            d for d in self.docs
            if not (d.get("username") == filt.get("username") and d.get("session_id") == filt.get("session_id"))
        ]


class _FakeMongoDB:
    def __init__(self, docs):
        self._collections = {"conversations": _FakeMongoCollection(docs)}

    def __getitem__(self, name):
        return self._collections[name]


def test_delete_user_conversation_reports_success_when_backed_by_mongo(monkeypatch):
    # Regression test: delete_user_conversation used to delete from Mongo FIRST, then
    # re-read Mongo to decide whether anything changed — which always looked like "nothing
    # changed" since the row was already gone, so it always reported 404 even on success.
    fake_db = _FakeMongoDB([{"username": "jack", "session_id": "conv-1", "title": "Hi", "messages": []}])
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)

    assert app_utils.delete_user_conversation("jack", "conv-1") is True
    assert app_utils.delete_user_conversation("jack", "conv-1") is False


def test_load_session_messages_reconstructs_langchain_messages():
    app_utils.save_conversation_turn(
        "jack", "conv-1",
        [HumanMessage(content="Hi"), AIMessage(content="Hello there")]
    )

    restored = app_utils.load_session_messages("jack", "conv-1")
    assert len(restored) == 2
    assert isinstance(restored[0], HumanMessage) and restored[0].content == "Hi"
    assert isinstance(restored[1], AIMessage) and restored[1].content == "Hello there"


def test_load_session_messages_is_empty_for_a_new_conversation():
    assert app_utils.load_session_messages("jack", "never-saved") == []


def test_load_session_messages_is_scoped_to_the_user():
    app_utils.save_conversation_turn("alice", "conv-1", [HumanMessage(content="Alice's secret")])
    assert app_utils.load_session_messages("jack", "conv-1") == []


def test_summaries_are_newest_first_and_carry_no_messages():
    app_utils.save_user_conversations("jack", [
        {"username": "jack", "session_id": "old", "title": "Older thread", "updated_at": "2026-01-01T00:00:00+00:00",
         "messages": _numbered(3)},
        {"username": "jack", "session_id": "new", "title": "Newer thread", "updated_at": "2026-02-01T00:00:00+00:00",
         "messages": _numbered(3)},
    ])

    summaries = app_utils.list_user_conversation_summaries("jack")
    assert [s["session_id"] for s in summaries] == ["new", "old"]
    assert all(set(s) == {"session_id", "title", "updated_at"} for s in summaries)


def _numbered(n):
    return [{"type": "human", "content": f"m{i}"} for i in range(n)]


def test_paginate_returns_the_newest_page_by_default_with_start_and_total():
    page = app_utils.paginate_messages(_numbered(250), limit=100)
    assert page["total"] == 250 and page["start"] == 150
    assert [m["content"] for m in page["messages"]][0] == "m150"
    assert [m["content"] for m in page["messages"]][-1] == "m249"


def test_paginate_before_walks_back_through_the_whole_thread_without_gaps_or_overlap():
    messages = _numbered(250)
    seen, before = [], None
    while True:
        page = app_utils.paginate_messages(messages, limit=100, before=before)
        seen = page["messages"] + seen
        if page["start"] == 0:
            break
        before = page["start"]
    assert seen == messages


def test_paginate_short_thread_returns_everything_and_clamps_inputs():
    messages = _numbered(5)
    assert app_utils.paginate_messages(messages, limit=100)["messages"] == messages
    assert app_utils.paginate_messages(messages, limit=0)["messages"] == messages  # limit floors at default
    assert app_utils.paginate_messages(messages, limit=2, before=99)["start"] == 3  # before clamps to total
    assert app_utils.paginate_messages(messages, limit=2, before=-5)["messages"] == []
    assert len(app_utils.paginate_messages(_numbered(900), limit=10_000)["messages"]) == app_utils.MAX_CONVERSATION_PAGE_SIZE


class _SingleDocMongoCollection:
    """Fake collection that records which reads/writes happen, to prove the hot path stays narrow.
    Understands just enough Mongo ($set/$setOnInsert/$push, $slice projection, the $size aggregate)
    to exercise the append-only save."""
    def __init__(self):
        self.docs = []
        self.find_calls = 0
        self.find_one_projections = []
        self.updates = []

    def _match(self, filt):
        return next((d for d in self.docs if all(d.get(k) == v for k, v in filt.items())), None)

    def find(self, filt, projection=None):
        self.find_calls += 1
        return [d for d in self.docs if d.get("username") == filt.get("username")]

    def find_one(self, filt, projection=None):
        self.find_one_projections.append(projection)
        d = self._match(filt)
        if d is None:
            return None
        out = {k: v for k, v in d.items() if k != "_id"}
        spec = (projection or {}).get("messages")
        if isinstance(spec, dict) and "$slice" in spec:
            skip, limit = spec["$slice"]
            out["messages"] = list(d.get("messages", []))[skip:skip + limit]
        return out

    def aggregate(self, pipeline):
        d = self._match(pipeline[0]["$match"])
        return [] if d is None else [{"n": len(d.get("messages", []))}]

    def update_one(self, filt, update, upsert=False):
        self.updates.append(update)
        target = self._match(filt)
        if target is None:
            if not upsert:
                return
            target = dict(filt)
            target.update(update.get("$setOnInsert", {}))
            self.docs.append(target)
        target.update(update.get("$set", {}))
        for field, spec in update.get("$push", {}).items():
            target.setdefault(field, []).extend(spec["$each"])


class _SingleDocMongoDB:
    def __init__(self):
        self.conversations = _SingleDocMongoCollection()

    def __getitem__(self, name):
        return self.conversations


def test_mongo_save_does_not_load_every_thread_or_write_the_json_mirror(monkeypatch, tmp_path):
    fake_db = _SingleDocMongoDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    monkeypatch.setattr(app_utils, "CONVERSATIONS_DIR", str(tmp_path))

    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="First question")])
    doc = app_utils.save_conversation_turn(
        "jack", "conv-1", [HumanMessage(content="First question"), AIMessage(content="Answer")]
    )

    assert fake_db.conversations.find_calls == 0  # never fetched all of the user's threads
    assert all(p == {"_id": 0, "title": 1, "created_at": 1} for p in fake_db.conversations.find_one_projections)
    assert list(tmp_path.iterdir()) == []  # no per-turn JSON mirror rewrite
    assert doc["title"] == "First question"
    assert len(fake_db.conversations.docs) == 1 and len(fake_db.conversations.docs[0]["messages"]) == 2


def test_mongo_load_single_conversation_and_summaries(monkeypatch):
    fake_db = _SingleDocMongoDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    app_utils.save_conversation_turn("jack", "conv-1", [HumanMessage(content="Hi"), AIMessage(content="Hello")])

    convo = app_utils.load_user_conversation("jack", "conv-1")
    assert [m["content"] for m in convo["messages"]] == ["Hi", "Hello"]
    assert app_utils.load_user_conversation("jack", "missing") is None
    assert app_utils.load_user_conversation("alice", "conv-1") is None
    assert [s["session_id"] for s in app_utils.list_user_conversation_summaries("jack")] == ["conv-1"]


# ---------------------------------------------------------------------------
# Append-only saves: a stale in-memory copy must never overwrite what another backend instance saved.
# Real incident: a second instance sharing the same Mongo saved from a copy that stopped at message
# 2105 and replaced the stored transcript, deleting 34 messages from earlier that morning.
# ---------------------------------------------------------------------------

def _mongo(monkeypatch):
    fake_db = _SingleDocMongoDB()
    monkeypatch.setattr(app_utils, "get_db", lambda: fake_db)
    return fake_db


def _contents(fake_db):
    return [m["content"] for m in fake_db.conversations.docs[0]["messages"]]


def test_a_stale_second_instance_cannot_delete_messages_another_instance_saved(monkeypatch):
    fake_db = _mongo(monkeypatch)
    a = app_utils.load_session_messages("jack", "c1")
    a += [HumanMessage(content="h1"), AIMessage(content="a1")]
    app_utils.save_conversation_turn("jack", "c1", a)

    b = app_utils.load_session_messages("jack", "c1")  # instance B loads now ... and goes stale
    a += [HumanMessage(content="h2"), AIMessage(content="a2")]
    app_utils.save_conversation_turn("jack", "c1", a)  # ... while A keeps going

    b += [HumanMessage(content="h3"), AIMessage(content="a3")]
    app_utils.save_conversation_turn("jack", "c1", b)  # the old replace-everything save lost h2/a2 here

    assert _contents(fake_db) == ["h1", "a1", "h2", "a2", "h3", "a3"]


def test_each_save_pushes_only_the_new_tail(monkeypatch):
    fake_db = _mongo(monkeypatch)
    t = app_utils.load_session_messages("jack", "c1")
    t.append(HumanMessage(content="h1"))
    app_utils.save_conversation_turn("jack", "c1", t)
    t.append(AIMessage(content="a1"))
    app_utils.save_conversation_turn("jack", "c1", t)

    pushes = [u["$push"]["messages"]["$each"] for u in fake_db.conversations.updates if "$push" in u]
    assert [[m["content"] for m in each] for each in pushes] == [["h1"], ["a1"]]
    assert t.persisted == 2


def test_new_conversation_is_titled_once_and_keeps_its_title(monkeypatch):
    fake_db = _mongo(monkeypatch)
    t = app_utils.load_session_messages("jack", "c1")
    t.append(HumanMessage(content="What is the capital of France?"))
    app_utils.save_conversation_turn("jack", "c1", t)
    t.append(AIMessage(content="Paris."))
    t.append(HumanMessage(content="and Spain?"))
    app_utils.save_conversation_turn("jack", "c1", t)

    doc = fake_db.conversations.docs[0]
    assert doc["title"] == "What is the capital of France?"
    assert doc["created_at"] and doc["updated_at"]


def test_sync_picks_up_messages_saved_elsewhere_before_the_next_turn(monkeypatch):
    fake_db = _mongo(monkeypatch)
    a = app_utils.load_session_messages("jack", "c1")
    a += [HumanMessage(content="h1"), AIMessage(content="a1")]
    app_utils.save_conversation_turn("jack", "c1", a)
    b = app_utils.load_session_messages("jack", "c1")

    a += [HumanMessage(content="h2"), AIMessage(content="a2")]
    app_utils.save_conversation_turn("jack", "c1", a)

    app_utils.sync_session_messages("jack", "c1", b)
    assert [m.content for m in b] == ["h1", "a1", "h2", "a2"]  # the model's context sees them too
    b.append(HumanMessage(content="h3"))
    app_utils.save_conversation_turn("jack", "c1", b)
    assert _contents(fake_db) == ["h1", "a1", "h2", "a2", "h3"]


def test_sync_keeps_unsaved_local_messages_after_the_ones_fetched(monkeypatch):
    fake_db = _mongo(monkeypatch)
    a = app_utils.load_session_messages("jack", "c1")
    a.append(HumanMessage(content="h1"))
    app_utils.save_conversation_turn("jack", "c1", a)
    b = app_utils.load_session_messages("jack", "c1")
    a.append(AIMessage(content="from-a"))
    app_utils.save_conversation_turn("jack", "c1", a)

    b.append(HumanMessage(content="local-unsaved"))
    app_utils.sync_session_messages("jack", "c1", b)

    assert [m.content for m in b] == ["h1", "from-a", "local-unsaved"]
    app_utils.save_conversation_turn("jack", "c1", b)
    assert _contents(fake_db) == ["h1", "from-a", "local-unsaved"]


def test_sync_is_a_single_cheap_count_when_nothing_changed(monkeypatch):
    fake_db = _mongo(monkeypatch)
    t = app_utils.load_session_messages("jack", "c1")
    t.append(HumanMessage(content="h1"))
    app_utils.save_conversation_turn("jack", "c1", t)
    fake_db.conversations.find_one_projections.clear()

    app_utils.sync_session_messages("jack", "c1", t)

    assert fake_db.conversations.find_one_projections == []  # no message data fetched


def test_sync_adopts_the_database_when_the_conversation_was_cleared_elsewhere(monkeypatch):
    fake_db = _mongo(monkeypatch)
    t = app_utils.load_session_messages("jack", "c1")
    t += [HumanMessage(content="h1"), AIMessage(content="a1")]
    app_utils.save_conversation_turn("jack", "c1", t)
    fake_db.conversations.docs.clear()  # deleted by another instance

    app_utils.sync_session_messages("jack", "c1", t)

    assert list(t) == [] and t.persisted == 0


def test_an_intentional_clear_still_replaces_the_stored_transcript(monkeypatch):
    fake_db = _mongo(monkeypatch)
    t = app_utils.load_session_messages("jack", "c1")
    t += [HumanMessage(content="h1"), AIMessage(content="a1")]
    app_utils.save_conversation_turn("jack", "c1", t)

    cleared = app_utils.TranscriptMessages()
    cleared.persisted = 2
    app_utils.save_conversation_turn("jack", "c1", cleared)

    assert _contents(fake_db) == []


def test_old_messages_attachments_survive_because_history_is_no_longer_rewritten(monkeypatch):
    # The replace-everything save re-serialized every old message from a reloaded copy that had lost
    # their attachment/kb_image references, erasing them from storage on any save after a restart.
    fake_db = _mongo(monkeypatch)
    fake_db.conversations.docs.append({
        "username": "jack", "session_id": "c1", "title": "t", "created_at": "x", "updated_at": "x",
        "messages": [{"type": "human", "content": "look", "attachments": [{"filename": "a.png", "gridfs_id": "123"}]},
                     {"type": "ai", "content": "nice", "kb_images": [{"filename": "k.png", "fileId": "9"}]}],
    })
    t = app_utils.load_session_messages("jack", "c1")
    t.append(HumanMessage(content="next"))
    app_utils.save_conversation_turn("jack", "c1", t)

    stored = fake_db.conversations.docs[0]["messages"]
    assert stored[0]["attachments"] == [{"filename": "a.png", "gridfs_id": "123"}]
    assert stored[1]["kb_images"] == [{"filename": "k.png", "fileId": "9"}]
    assert [m["content"] for m in stored] == ["look", "nice", "next"]


def test_a_plain_list_still_replaces_the_whole_transcript_for_untracked_callers(monkeypatch):
    fake_db = _mongo(monkeypatch)
    app_utils.save_conversation_turn("jack", "c1", [HumanMessage(content="a"), AIMessage(content="b")])
    app_utils.save_conversation_turn("jack", "c1", [HumanMessage(content="only")])
    assert _contents(fake_db) == ["only"]


def test_json_fallback_tracks_the_stored_count_too(monkeypatch):
    t = app_utils.load_session_messages("jack", "c1")
    t.append(HumanMessage(content="h1"))
    app_utils.save_conversation_turn("jack", "c1", t)
    assert t.persisted == 1
    assert [m["content"] for m in app_utils.load_user_conversation("jack", "c1")["messages"]] == ["h1"]

