"""Plain text, Markdown and Word uploads: read correctly, refused in words when unusable, and a failure leaves nothing behind (local_function_app/text_files.py, function_app.py)."""
import io
import os
import zipfile

import pytest

from local_function_app import function_app as fa
from local_function_app import text_files as tf
from local_function_app.tests.test_function_app_json import FakeFile, FakeFS, pipeline  # noqa: F401  (the fixture is used by name)

SENTENCE = "The retry delay is two seconds and the job gives up after three tries. "


def make_docx(build):
    from docx import Document as DocxDocument

    document = DocxDocument()
    build(document)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


class Recorder:
    def __init__(self):
        self.edge_deletes = []

    def __call__(self, db, affiliate, source):
        self.edge_deletes.append((affiliate, source))


@pytest.fixture
def edges(monkeypatch):
    recorder = Recorder()
    monkeypatch.setattr(fa, "delete_graph_edges", recorder)
    return recorder


# ---- reading the files ----------------------------------------------------------------------------------------------------------------------

def test_the_file_names_are_recognised():
    assert all(fa._is_text_filename(n) for n in ("a.txt", "SERVER.LOG", "x.Txt"))
    assert all(fa._is_markdown_filename(n) for n in ("README.md", "notes.MARKDOWN"))
    assert all(fa._is_docx_filename(n) for n in ("Plan.docx", "PLAN.DOCX"))
    assert not any(fa._is_text_filename(n) or fa._is_markdown_filename(n) or fa._is_docx_filename(n) for n in ("a.pdf", "week.json", "photo.png", "txt", "doc.doc"))


def test_text_is_read_as_the_encoding_it_is():
    assert tf.decode_text("héllo wörld".encode("utf-8")) == "héllo wörld"
    assert tf.decode_text(b"\xef\xbb\xbfplain with a BOM") == "plain with a BOM"
    assert tf.decode_text("café au lait".encode("utf-16")) == "café au lait"                                   # Notepad's "Unicode"
    assert tf.decode_text("café au lait".encode("cp1252")) == "café au lait"                                   # Windows ANSI is not valid UTF-8
    assert tf.decode_text(b"line one\r\nline two\rline three\x00") == "line one\nline two\nline three"


def test_binary_empty_and_oversized_files_are_refused_in_words(monkeypatch):
    for raw, fragment in ((bytes(range(256)) * 40, "binary"), (b"   \n\n  ", "no readable text"), (b"", "no readable text")):
        with pytest.raises(tf.TextIngestError, match=fragment):
            tf.build_text_document(raw, "a.txt", "personal_jack", fa.Document, "i1")
    monkeypatch.setattr(tf, "MAX_BYTES", 100)
    with pytest.raises(tf.TextIngestError, match="over the 0 MB limit|limit for text files"):
        tf.build_text_document(b"x" * 101, "a.log", "personal_jack", fa.Document, "i1")


def test_a_word_document_is_read_in_order_with_tables_where_they_sit_and_merged_cells_once():
    def build(doc):
        doc.add_paragraph("Intro paragraph")
        table = doc.add_table(rows=2, cols=3)
        for index, text in enumerate(("Name", "Role", "Team")):
            table.cell(0, index).text = text
        merged = table.cell(1, 0).merge(table.cell(1, 1))
        merged.text = "Dana (lead)"
        table.cell(1, 2).text = "Platform"
        doc.add_paragraph("After the table")

    text = tf.docx_text(make_docx(build))
    assert text == "Intro paragraph\n\nName | Role | Team\nDana (lead) | Platform\n\nAfter the table"


def test_a_word_file_that_is_not_one_empty_or_huge_is_refused_in_words(monkeypatch):
    with pytest.raises(tf.TextIngestError, match="could not be read as a Word document"):
        tf.docx_text(b"this is not a zip file")
    with pytest.raises(tf.TextIngestError, match="no readable text"):
        tf.build_docx_document(make_docx(lambda doc: None), "e.docx", "personal_jack", fa.Document, "i1")
    monkeypatch.setattr(tf, "MAX_DOCX_UNZIPPED_BYTES", 10)
    with pytest.raises(tf.TextIngestError, match="expands to far more"):
        tf.docx_text(make_docx(lambda doc: doc.add_paragraph("some text that is longer than ten bytes")))
    monkeypatch.setattr(tf, "MAX_DOCX_BYTES", 10)
    with pytest.raises(tf.TextIngestError, match="limit for Word files"):
        tf.docx_text(make_docx(lambda doc: doc.add_paragraph("x")))


# ---- the pipeline: success ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("filename,data,doc_type,content_type", [
    ("notes.txt", (SENTENCE * 30).encode(), "text", "text/plain"),
    ("server.log", (SENTENCE * 30).encode(), "text", "text/plain"),
    ("README.md", ("# Title\n\n" + SENTENCE * 30).encode(), "markdown", "text/markdown"),
])
def test_text_and_markdown_uploads_are_chunked_embedded_graphed_and_marked_done(pipeline, filename, data, doc_type, content_type):
    pipeline.fs = FakeFS([FakeFile("n1", filename, data)])
    assert fa.run_ingestion_pipeline() is True
    chunks = pipeline.stored[0]
    assert len(chunks) >= 2 and all(c.metadata["source"] == filename and c.metadata["doc_type"] == doc_type and c.metadata["affiliate"] == "personal_jack" for c in chunks)
    assert len({c.metadata["ingest_id"] for c in chunks}) == 1 and pipeline.graph == [("personal_jack", filename, len(chunks))]
    flt, update = pipeline.db["fs.files"].updates[0]
    assert flt == {"_id": "n1"} and update["$set"] == {"metadata.status": "pages", "metadata.content_type": content_type} and pipeline.fs.deleted == []


def test_a_word_upload_is_indexed_with_the_word_type(pipeline):
    data = make_docx(lambda doc: [doc.add_paragraph(SENTENCE * 5) for _ in range(6)])
    pipeline.fs = FakeFS([FakeFile("w1", "Plan.docx", data)])
    assert fa.run_ingestion_pipeline() is True
    assert all(c.metadata["doc_type"] == "docx" for c in pipeline.stored[0])
    assert pipeline.db["fs.files"].updates[0][1]["$set"]["metadata.content_type"].endswith("wordprocessingml.document")


def test_re_uploading_replaces_the_earlier_version_only_after_the_new_one_is_stored(pipeline):
    earlier = FakeFile("old1", "notes.txt", b"old", status="pages")
    other_library = FakeFile("other1", "notes.txt", b"old", affiliate="Affiliate_C", status="pages")
    pipeline.fs = FakeFS([FakeFile("new1", "notes.txt", (SENTENCE * 5).encode())], pages=[earlier, other_library])
    fa.run_ingestion_pipeline()
    new_id = pipeline.stored[0][0].metadata["ingest_id"]
    assert pipeline.db["documents"].deleted_queries == [{"source": "notes.txt", "affiliate": "personal_jack", "ingest_id": {"$ne": new_id}}]
    assert pipeline.fs.deleted == ["old1"]


def test_the_pdf_and_image_paths_never_see_these_files(pipeline, monkeypatch):
    monkeypatch.setattr(fa, "PyPDFLoader", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reach the PDF loader")))
    pipeline.fs = FakeFS([FakeFile("a", "a.txt", (SENTENCE * 5).encode()), FakeFile("b", "b.md", (SENTENCE * 5).encode()),
                          FakeFile("c", "c.docx", make_docx(lambda doc: doc.add_paragraph(SENTENCE * 5)))])
    assert fa.run_ingestion_pipeline() is True and len(pipeline.stored) == 3


# ---- the pipeline: a failure leaves nothing behind -----------------------------------------------------------------------------------------

def test_an_unusable_file_is_wiped_completely_without_stopping_the_other_uploads_or_touching_an_earlier_version(pipeline, edges):
    earlier = FakeFile("old1", "empty.txt", b"old", status="pages")
    bad = FakeFile("bad1", "empty.txt", b"   ")
    good = FakeFile("good1", "good.txt", (SENTENCE * 5).encode())
    pipeline.fs = FakeFS([bad, good], pages=[earlier])
    assert fa.run_ingestion_pipeline() is True
    assert "bad1" in pipeline.fs.deleted and "good1" not in pipeline.fs.deleted and "old1" not in pipeline.fs.deleted
    assert len(pipeline.stored) == 1 and pipeline.stored[0][0].metadata["source"] == "good.txt"
    assert edges.edge_deletes == [], "nothing was written to the graph and the earlier version is still there, so its edges stay"
    chunk_queries = [q for q in pipeline.db["documents"].deleted_queries if q.get("source") == "empty.txt"]
    assert len(chunk_queries) == 1 and isinstance(chunk_queries[0]["ingest_id"], str)


def test_a_real_fault_before_anything_is_stored_wipes_this_upload_raises_and_leaves_the_earlier_version_whole(pipeline, edges):
    earlier = FakeFile("old1", "notes.txt", b"old", status="pages")
    pipeline.fs = FakeFS([FakeFile("new1", "notes.txt", (SENTENCE * 5).encode())], pages=[earlier])
    pipeline.fail_embedding = True
    with pytest.raises(RuntimeError):
        fa.run_ingestion_pipeline()
    assert pipeline.fs.deleted == ["new1"] and edges.edge_deletes == [] and pipeline.graph == []
    queries = pipeline.db["documents"].deleted_queries
    assert len(queries) == 1 and isinstance(queries[0]["ingest_id"], str)                                    # only this upload's own chunks, never the earlier version's


def test_a_fault_after_the_graph_step_removes_the_chunks_the_edges_and_the_stored_copy(pipeline, edges):
    pipeline.fs = FakeFS([FakeFile("new1", "notes.txt", (SENTENCE * 5).encode())])

    def boom(flt, update):
        raise RuntimeError("could not mark the file as done")

    pipeline.db["fs.files"].update_one = boom
    with pytest.raises(RuntimeError):
        fa.run_ingestion_pipeline()
    assert pipeline.graph and edges.edge_deletes == [("personal_jack", "notes.txt")] and pipeline.fs.deleted == ["new1"]
    last = pipeline.db["documents"].deleted_queries[-1]                      # the first query is the replace-earlier step; the last is this upload's own rollback
    assert last["source"] == "notes.txt" and isinstance(last["ingest_id"], str)


def test_a_fault_while_replacing_the_earlier_version_still_removes_the_edges_that_version_leaves_behind(pipeline, edges, monkeypatch):
    pipeline.fs = FakeFS([FakeFile("new1", "notes.txt", (SENTENCE * 5).encode())])
    monkeypatch.setattr(fa, "_replace_earlier_version", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("replace failed halfway")))
    with pytest.raises(RuntimeError):
        fa.run_ingestion_pipeline()
    assert edges.edge_deletes == [("personal_jack", "notes.txt")] and pipeline.fs.deleted == ["new1"] and pipeline.graph == []


def test_a_corrupt_word_file_and_a_file_with_too_many_chunks_are_wiped_and_refused(pipeline, edges, monkeypatch):
    monkeypatch.setattr(tf, "MAX_CHUNKS", 2)
    pipeline.fs = FakeFS([FakeFile("c1", "broken.docx", b"not a zip"), FakeFile("c2", "huge.log", (SENTENCE * 200).encode()), FakeFile("ok", "small.txt", b"short note, one chunk only")])
    assert fa.run_ingestion_pipeline() is True
    assert pipeline.fs.deleted == ["c1", "c2"] and len(pipeline.stored) == 1 and pipeline.stored[0][0].metadata["source"] == "small.txt" and edges.edge_deletes == []


# ---- the citation link ---------------------------------------------------------------------------------------------------------------------

def test_each_kind_of_file_is_served_as_what_it_is():
    from backend.utils import citation_media as cm

    assert cm.media_for("week.JSON") == ("application/json", "inline") and cm.media_for("a.jsonl") == ("application/json", "inline")
    for name in ("notes.txt", "server.LOG", "README.md", "notes.markdown"):
        assert cm.media_for(name) == ("text/plain; charset=utf-8", "inline")
    assert cm.media_for("Plan.docx") == (cm.DOCX_TYPE, "attachment") and cm.media_for("report.pdf") == ("application/pdf", "inline") and cm.media_for("") == ("application/pdf", "inline")
    source = open(os.path.join(os.path.dirname(__file__), "..", "..", "app.py"), encoding="utf-8").read()
    assert "media_type, disposition = citation_media.media_for(clean_basename)" in source and 'f"{disposition}; filename*=' in source


def test_the_run_never_prints_the_database_user_or_password():
    assert fa._redacted_mongo_host("mongodb+srv://someuser:S3cret%21pass@cluster0.abc.mongodb.net/?appName=App") == "cluster0.abc.mongodb.net"
    assert fa._redacted_mongo_host("mongodb://localhost:27017") == "localhost:27017" and fa._redacted_mongo_host(None) == "(not set)"
    source = open(os.path.join(os.path.dirname(__file__), "..", "function_app.py"), encoding="utf-8").read()
    assert "MONGO_URI is currently" not in source
