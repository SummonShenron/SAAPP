"""A long thread is loaded as its tail only (backend/utils/app_utils.py): 3,400 messages took ~25 s to read on the slow database link, and a turn uses the end of it."""
import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils


class Collection:
    """Just enough of a conversations collection: the tail aggregate, the size aggregate, a $slice read, and the append-only write."""

    def __init__(self):
        self.doc = None
        self.full_reads = 0
        self.aggregates = 0

    def aggregate(self, pipeline):
        self.aggregates += 1
        if self.doc is None or pipeline[0]["$match"]["session_id"] != self.doc["session_id"]:
            return []
        messages = list(self.doc["messages"])
        project = pipeline[1]["$project"]
        if "tail" in project:
            keep = -project["tail"]["$slice"][1]
            return [{"total": len(messages), "tail": messages[-keep:]}]
        return [{"n": len(messages)}]

    def find_one(self, filt, projection=None):
        if self.doc is None:
            return None
        spec = (projection or {}).get("messages")
        if isinstance(spec, dict) and "$slice" in spec:
            skip, count = spec["$slice"]
            return {"messages": self.doc["messages"][skip:skip + count]}
        if spec is None or spec == 1:
            self.full_reads += 1
        return {k: v for k, v in self.doc.items() if k != "_id"}

    def update_one(self, filt, update, upsert=False):
        if self.doc is None:
            self.doc = {"username": filt["username"], "session_id": filt["session_id"], "messages": [], **update.get("$setOnInsert", {})}
        self.doc.update(update.get("$set", {}))
        for field, spec in update.get("$push", {}).items():
            self.doc.setdefault(field, []).extend(spec["$each"])


class Db:
    def __init__(self):
        self.conversations = Collection()

    def __getitem__(self, name):
        return self.conversations


def thread(n):
    return [{"type": "human" if i % 2 == 0 else "ai", "content": f"m{i}"} for i in range(n)]


@pytest.fixture
def db(monkeypatch):
    database = Db()
    monkeypatch.setattr(app_utils, "get_db", lambda: database)
    monkeypatch.setattr(app_utils, "TAIL_MESSAGES", 50)
    return database


def stored(db, n):
    db.conversations.doc = {"username": "jack", "session_id": "s", "title": "T", "created_at": "x", "messages": thread(n)}


def test_a_long_thread_loads_as_its_last_messages_with_the_rest_counted_not_loaded(db):
    stored(db, 500)
    transcript = app_utils.load_session_messages("jack", "s")
    assert len(transcript) == 50 and transcript[0].content == "m450" and transcript[-1].content == "m499"
    assert transcript.persisted == 500 and transcript.base == 450
    assert db.conversations.full_reads == 0 and db.conversations.aggregates == 1                  # one server-side cut, never the whole array


def test_a_short_thread_loads_whole_and_a_new_one_is_empty(db):
    stored(db, 20)
    transcript = app_utils.load_session_messages("jack", "s")
    assert len(transcript) == 20 and transcript.base == 0 and transcript.persisted == 20
    empty = app_utils.load_session_messages("jack", "never-saved")
    assert len(empty) == 0 and empty.persisted == 0 and empty.base == 0


def test_a_turn_on_a_tail_appends_only_the_new_messages_and_never_touches_the_old_ones(db):
    stored(db, 500)
    transcript = app_utils.load_session_messages("jack", "s")
    transcript.append(HumanMessage(content="new question"))
    transcript.append(AIMessage(content="new answer"))
    app_utils.save_conversation_turn("jack", "s", transcript)
    messages = db.conversations.doc["messages"]
    assert len(messages) == 502 and messages[:500] == thread(500)
    assert [m["content"] for m in messages[-2:]] == ["new question", "new answer"] and transcript.persisted == 502
    transcript.append(HumanMessage(content="and again"))
    app_utils.save_conversation_turn("jack", "s", transcript)
    assert len(db.conversations.doc["messages"]) == 503


def test_sync_picks_up_exactly_what_another_instance_saved_and_keeps_unsaved_local_messages(db):
    stored(db, 500)
    transcript = app_utils.load_session_messages("jack", "s")
    transcript.append(HumanMessage(content="local, not saved yet"))
    db.conversations.doc["messages"] += [{"type": "human", "content": "elsewhere-1"}, {"type": "ai", "content": "elsewhere-2"}]   # another instance
    app_utils.sync_session_messages("jack", "s", transcript)
    contents = [m.content for m in transcript]
    assert contents[-3:] == ["elsewhere-1", "elsewhere-2", "local, not saved yet"] and transcript.persisted == 502 and transcript.base == 450
    app_utils.save_conversation_turn("jack", "s", transcript)
    assert [m["content"] for m in db.conversations.doc["messages"][-3:]] == ["elsewhere-1", "elsewhere-2", "local, not saved yet"]
    assert len(db.conversations.doc["messages"]) == 503


def test_a_thread_cleared_elsewhere_is_reloaded_and_keeps_what_is_unsaved(db):
    stored(db, 500)
    transcript = app_utils.load_session_messages("jack", "s")
    transcript.append(HumanMessage(content="local"))
    db.conversations.doc["messages"] = thread(10)
    app_utils.sync_session_messages("jack", "s", transcript)
    assert len(transcript) == 11 and transcript.base == 0 and transcript.persisted == 10 and transcript[-1].content == "local"


def test_a_tail_that_somehow_shrinks_is_never_written_back_as_the_whole_thread(db):
    stored(db, 500)
    transcript = app_utils.load_session_messages("jack", "s")
    del transcript[:]                                                                        # nothing in the app does this; if it ever happened it must not erase history
    app_utils.save_conversation_turn("jack", "s", transcript)
    assert db.conversations.doc["messages"] == thread(500)


def test_a_plain_list_still_replaces_the_whole_transcript_as_before(db):
    stored(db, 30)
    app_utils.save_conversation_turn("jack", "s", [HumanMessage(content="only")])
    assert [m["content"] for m in db.conversations.doc["messages"]] == ["only"]


def test_the_local_file_fallback_still_loads_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(app_utils, "get_db", lambda: None)
    monkeypatch.setattr(app_utils, "CONVERSATIONS_DIR", str(tmp_path))
    monkeypatch.setattr(app_utils, "TAIL_MESSAGES", 5)
    app_utils.save_conversation_turn("jack", "s", [HumanMessage(content=f"m{i}") for i in range(20)])
    assert len(app_utils.load_session_messages("jack", "s")) == 20
