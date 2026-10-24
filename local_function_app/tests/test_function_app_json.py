import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from local_function_app import function_app as fa

WEEK = {"week_start": "2026-09-28", "standup_entries": [{"author": "jack", "today": "- wrote the parser"}, {"author": "jack", "today": "- fixed the schema"}]}


class FakeFile:
    def __init__(self, _id, filename, data, affiliate="personal_jack", status="raw"):
        self._id, self.filename, self._data = _id, filename, data
        self.metadata = {"affiliate": affiliate, "status": status}

    def read(self):
        return self._data


class FakeFS:
    """The raw uploads waiting to be indexed, and the files already indexed ("pages"); delete() removes from either."""

    def __init__(self, raw, pages=()):
        self.raw, self.pages, self.deleted = list(raw), list(pages), []

    def find(self, query):
        if query.get("metadata.status") == "raw":
            return list(self.raw)
        return [f for f in self.pages if f.filename == query.get("filename") and f.metadata.get("affiliate") == query.get("metadata.affiliate")]

    def delete(self, file_id):
        self.deleted.append(file_id)
        self.pages = [f for f in self.pages if f._id != file_id]
        self.raw = [f for f in self.raw if f._id != file_id]


class FakeCollection:
    def __init__(self):
        self.deleted_queries, self.updates = [], []

    def delete_many(self, query):
        self.deleted_queries.append(query)

    def update_one(self, flt, update):
        self.updates.append((flt, update))


class FakeDb(dict):
    def __missing__(self, name):
        self[name] = FakeCollection()
        return self[name]


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    state = SimpleNamespace(db=FakeDb(), fs=None, stored=[], graph=[], fail_embedding=False)

    def from_documents(chunks, embeddings, collection, index_name):
        if state.fail_embedding:
            raise RuntimeError("embedding service down")
        state.stored.append(list(chunks))
        return Mock()

    monkeypatch.setattr(fa, "get_db_client", lambda: state.db)
    monkeypatch.setattr(fa, "GridFS", lambda db: state.fs)
    monkeypatch.setattr(fa, "GoogleGenerativeAIEmbeddings", Mock())
    monkeypatch.setattr(fa, "DB_DIR", str(tmp_path / "no-chroma"))
    monkeypatch.setattr(fa.MongoDBAtlasVectorSearch, "from_documents", staticmethod(from_documents))
    monkeypatch.setattr(fa, "index_graph_for_source", lambda db, affiliate, source, texts: state.graph.append((affiliate, source, len(texts))) or 0)
    monkeypatch.setattr(fa.time, "sleep", lambda s: None)
    return state


def test_json_file_names_are_recognised():
    for name in ("week.json", "WEEK.JSON", "log.jsonl", "events.ndjson"):
        assert fa._is_json_filename(name)
    for name in ("report.pdf", "photo.png", "notes.txt", "jsonfile"):
        assert not fa._is_json_filename(name)


def test_a_json_upload_is_chunked_embedded_graphed_and_marked_done(pipeline):
    upload = FakeFile("new1", "week.json", json.dumps(WEEK).encode())
    pipeline.fs = FakeFS([upload])
    assert fa.run_ingestion_pipeline() is True
    chunks = pipeline.stored[0]
    assert len(chunks) == 2
    assert all(c.metadata["source"] == "week.json" and c.metadata["affiliate"] == "personal_jack" and c.metadata["doc_type"] == "json" for c in chunks)
    assert len({c.metadata["ingest_id"] for c in chunks}) == 1
    assert pipeline.graph == [("personal_jack", "week.json", 2)]
    flt, update = pipeline.db["fs.files"].updates[0]
    assert flt == {"_id": "new1"} and update["$set"] == {"metadata.status": "pages", "metadata.content_type": "application/json"}
    assert pipeline.fs.deleted == []


def test_re_uploading_a_file_replaces_the_earlier_version_only_after_the_new_one_is_stored(pipeline):
    earlier = FakeFile("old1", "week.json", b"{}", status="pages")
    other_library = FakeFile("other1", "week.json", b"{}", affiliate="Affiliate_C", status="pages")
    pipeline.fs = FakeFS([FakeFile("new1", "week.json", json.dumps(WEEK).encode())], pages=[earlier, other_library])
    fa.run_ingestion_pipeline()
    new_id = pipeline.stored[0][0].metadata["ingest_id"]
    assert pipeline.db["documents"].deleted_queries == [{"source": "week.json", "affiliate": "personal_jack", "ingest_id": {"$ne": new_id}}]
    assert pipeline.fs.deleted == ["old1"], "only this library's earlier copy goes; another library's file of the same name is untouched"


def test_a_file_that_is_not_json_is_removed_without_stopping_the_other_uploads(pipeline):
    bad = FakeFile("bad1", "broken.json", b'{"a": oops}')
    good = FakeFile("good1", "week.json", json.dumps(WEEK).encode())
    pipeline.fs = FakeFS([bad, good])
    assert fa.run_ingestion_pipeline() is True
    assert "bad1" in pipeline.fs.deleted and "good1" not in pipeline.fs.deleted
    assert len(pipeline.stored) == 1 and pipeline.stored[0][0].metadata["source"] == "week.json"


def test_a_real_fault_rolls_back_this_upload_only_and_keeps_the_earlier_version(pipeline):
    earlier = FakeFile("old1", "week.json", b"{}", status="pages")
    pipeline.fs = FakeFS([FakeFile("new1", "week.json", json.dumps(WEEK).encode())], pages=[earlier])
    pipeline.fail_embedding = True
    with pytest.raises(RuntimeError):
        fa.run_ingestion_pipeline()
    assert pipeline.fs.deleted == ["new1"], "the earlier version must survive a failed re-upload"
    queries = pipeline.db["documents"].deleted_queries
    assert len(queries) == 1 and "ingest_id" in queries[0] and queries[0]["ingest_id"] != {"$ne": queries[0]["ingest_id"]}
    assert isinstance(queries[0]["ingest_id"], str), "only the failed upload's own chunks are removed, never the earlier version's"
    assert pipeline.graph == []


def test_an_empty_json_file_is_refused_cleanly(pipeline):
    pipeline.fs = FakeFS([FakeFile("e1", "empty.json", b"{}")])
    assert fa.run_ingestion_pipeline() is True
    assert pipeline.fs.deleted == ["e1"] and pipeline.stored == []


def test_the_pdf_and_image_paths_do_not_see_json_files(pipeline, monkeypatch):
    monkeypatch.setattr(fa, "PyPDFLoader", Mock(side_effect=AssertionError("a JSON file must never reach the PDF loader")))
    pipeline.fs = FakeFS([FakeFile("j1", "week.json", json.dumps(WEEK).encode())])
    fa.run_ingestion_pipeline()
    assert pipeline.stored


def test_the_upload_route_serves_json_citations_as_json():
    from backend.utils import citation_media

    assert citation_media.media_for("week.json") == ("application/json", "inline")
    source = open(__import__("os").path.join(__import__("os").path.dirname(__file__), "..", "..", "app.py"), encoding="utf-8").read()
    assert "citation_media.media_for(clean_basename)" in source
