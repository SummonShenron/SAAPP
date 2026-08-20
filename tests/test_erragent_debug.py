from fastapi.testclient import TestClient
from app import app

def test_erragent_debug_no_zero_division():
    """
    Test that the /api/erragent-debug endpoint returns a 500 status code
    with the expected error detail, without raising an actual ZeroDivisionError.
    """
    client = TestClient(app)
    response = client.get("/api/erragent-debug")
    
    assert response.status_code == 500
    assert response.json() == {"detail": "ZeroDivisionError: division by zero"}
