import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

def test_discover_docs_regression_passes_username_and_affiliate():
    mock_discover = AsyncMock(return_value=["doc_a.pdf", "doc_b.pdf"])
    mock_user = {"sub": "test_user_123", "email": "test@example.com"}
    
    with patch("app.discover_workspace_documents", mock_discover):
        from app import app, get_current_user
        
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        try:
            client = TestClient(app)
            response = client.get("/api/discover-docs?affiliate=Affiliate_B")
            
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == ["doc_a.pdf", "doc_b.pdf"]
            
            mock_discover.assert_called_once_with("test_user_123", "Affiliate_B")
        finally:
            app.dependency_overrides.clear()
