import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

def test_discover_docs_sync_and_async_handling():
    """
    Regression test for incident inc_1787698006016_349ff622.
    Verifies that the /api/discover-docs endpoint correctly handles both
    synchronous and asynchronous implementations of discover_workspace_documents
    without raising a TypeError.
    """
    # Mock startup services and logging to avoid side effects during app import
    mock_services = {"insight_workflow": MagicMock()}
    with patch("backend.services.orchestrator.startup_services", return_value=mock_services), \
         patch("backend.logging.sass_logger.setup_logging", return_value=MagicMock()), \
         patch("backend.logging.erragent_handler.install_erragent_logging", return_value=MagicMock()), \
         patch("app.load_chat_history", return_value={}):
         
        from app import app, get_current_user

    mock_user = {"sub": "test_user", "email": "test_user@example.com"}
    app.dependency_overrides[get_current_user] = lambda: mock_user

    try:
        client = TestClient(app)

        # 1. Test with synchronous return value from discover_workspace_documents
        sync_docs = ["sync_doc_1.pdf", "sync_doc_2.pdf"]
        sync_mock = MagicMock(return_value=sync_docs)
        with patch("app.discover_workspace_documents", sync_mock):
            response = client.get("/api/discover-docs?affiliate=Affiliate_Sync")
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == sync_docs
            sync_mock.assert_called_once_with("Affiliate_Sync")

        # 2. Test with asynchronous return value from discover_workspace_documents
        async_docs = ["async_doc_1.pdf", "async_doc_2.pdf"]
        async_mock = AsyncMock(return_value=async_docs)
        with patch("app.discover_workspace_documents", async_mock):
            response = client.get("/api/discover-docs?affiliate=Affiliate_Async")
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == async_docs
            async_mock.assert_called_once_with("Affiliate_Async")

    finally:
        app.dependency_overrides.clear()
