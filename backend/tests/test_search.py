from dotenv import load_dotenv
load_dotenv()

from unittest.mock import Mock

from backend.services import search


def test_mongo_vector_search_preserves_gridfs_and_embedded_image_fields(monkeypatch):
    """Regression guard: MongoDBAtlasVectorSearch flattens Document.metadata into top-level
    Mongo fields, so the $project stage must explicitly whitelist any new metadata field or it
    silently never reaches Document.metadata — this is exactly the bug that would make the KB
    image-rendering feature no-op at retrieval time even though ingestion worked fine."""
    fake_collection = Mock()
    fake_collection.aggregate = Mock(return_value=[
        {
            "page_content": "A red square on a white background.",
            "source": "logo.png",
            "page": 0,
            "affiliate": "Affiliate_D",
            "priority": True,
            "gridfs_id": "abc123",
            "content_type": "image/png",
            "doc_type": "standalone_image",
            "embedded_images": [],
            "score": 0.92,
        },
        {
            "page_content": "Page text with a chart on it.",
            "source": "report.pdf",
            "page": 3,
            "affiliate": "Affiliate_D",
            "priority": False,
            "embedded_images": [{"gridfs_id": "img1", "filename": "chart.png"}],
            "score": 0.81,
        },
    ])

    monkeypatch.setattr(search, "_get_mongo_collection", Mock(return_value=fake_collection))
    monkeypatch.setattr(search, "_embeddings", Mock(embed_query=Mock(return_value=[0.1, 0.2, 0.3])))

    docs = search._mongo_vector_search("what does the logo look like?", affiliate_scope=["Affiliate_D"])

    assert len(docs) == 2
    assert docs[0].metadata["doc_type"] == "standalone_image"
    assert docs[0].metadata["gridfs_id"] == "abc123"
    assert docs[0].metadata["content_type"] == "image/png"
    assert docs[1].metadata["embedded_images"] == [{"gridfs_id": "img1", "filename": "chart.png"}]


def test_mongo_vector_search_defaults_embedded_images_to_empty_list(monkeypatch):
    fake_collection = Mock()
    fake_collection.aggregate = Mock(return_value=[
        {"page_content": "plain text", "source": "doc.pdf", "page": 0, "score": 0.5}
    ])
    monkeypatch.setattr(search, "_get_mongo_collection", Mock(return_value=fake_collection))
    monkeypatch.setattr(search, "_embeddings", Mock(embed_query=Mock(return_value=[0.1, 0.2, 0.3])))

    docs = search._mongo_vector_search("a question", affiliate_scope=[])

    assert docs[0].metadata["embedded_images"] == []
    assert docs[0].metadata.get("gridfs_id") is None
