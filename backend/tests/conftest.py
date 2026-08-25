import pytest
from unittest.mock import MagicMock

@pytest.fixture(autouse=True)
def mock_embeddings(monkeypatch):
    monkeypatch.setattr(
        "backend.services.search.GoogleGenerativeAIEmbeddings",
        MagicMock()
    )
