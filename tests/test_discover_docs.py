import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

def test_discover_documents_endpoint():
    # Mock discover_workspace_documents to be an async function returning a list of files
    mock_discover = AsyncMock(return_value=["doc1.pdf", "doc2.pdf"])
    
    # Mock get_current_user dependency
    mock_user = {"sub": "test_user", "email": "test@example.com"}
    
    # Patch discover_workspace_documents in app.py
    with patch("app.discover_workspace_documents", mock_discover):
        from app import app, get_current_user
        
        # Override get_current_user dependency
        app.dependency_overrides[get_current_user] = lambda: mock_user
        
        try:
            client = TestClient(app)
            response = client.get("/api/discover-docs?affiliate=Affiliate_A")
            
            assert response.status_code == 200
            data = response.json()
            assert "accessible_documents" in data
            assert data["accessible_documents"] == ["doc1.pdf", "doc2.pdf"]
            
            # Verify that discover_workspace_documents was called with the correct affiliate
            mock_discover.assert_called_once_with("Affiliate_A")
        finally:
            # Clean up dependency overrides
            app.dependency_overrides.clear()
