import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

def test_discover_documents_sync_and_async():
    """
    Test that the /api/discover-docs endpoint correctly handles both
    synchronous and asynchronous implementations of discover_workspace_documents.
    """
    from app import app, get_current_user
    
    mock_user = {"sub": "test_user", "email": "test@example.com"}
    app.dependency_overrides[get_current_user] = lambda: mock_user
    
    try:
        client = TestClient(app)
        
        # 1. Test with synchronous return value
        sync_mock = MagicMock(return_value=["sync_doc1.pdf", "sync_doc2.pdf"])
        with patch("app.discover_workspace_documents", sync_mock):
            response = client.get("/api/discover-docs?affiliate=Affiliate_Sync")
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == ["sync_doc1.pdf", "sync_doc2.pdf"]
            sync_mock.assert_called_once_with("Affiliate_Sync")
            
        # 2. Test with asynchronous return value
        async_mock = AsyncMock(return_value=["async_doc1.pdf", "async_doc2.pdf"])
        with patch("app.discover_workspace_documents", async_mock):
            response = client.get("/api/discover-docs?affiliate=Affiliate_Async")
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == ["async_doc1.pdf", "async_doc2.pdf"]
            async_mock.assert_called_once_with("Affiliate_Async")
            
    finally:
        app.dependency_overrides.clear()
