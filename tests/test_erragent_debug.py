import sys
import os
from unittest.mock import MagicMock

# Mock all backend and external dependencies to avoid side-effects during import
sys.modules['backend'] = MagicMock()
sys.modules['backend.components'] = MagicMock()
sys.modules['backend.utils'] = MagicMock()
sys.modules['backend.utils.taskboard_utils'] = MagicMock()
sys.modules['backend.models'] = MagicMock()
sys.modules['backend.models.models'] = MagicMock()
sys.modules['backend.models.attachment'] = MagicMock()
sys.modules['backend.services'] = MagicMock()
sys.modules['backend.services.github_service'] = MagicMock()
sys.modules['backend.components.time_storage'] = MagicMock()
sys.modules['backend.components.constraints'] = MagicMock()
sys.modules['backend.services.search'] = MagicMock()
sys.modules['local_function_app'] = MagicMock()
sys.modules['local_function_app.function_app'] = MagicMock()
sys.modules['backend.state'] = MagicMock()
sys.modules['backend.state.graph_state'] = MagicMock()
sys.modules['backend.services.insights_workflow'] = MagicMock()
sys.modules['backend.utils.app_utils'] = MagicMock()
sys.modules['backend.utils.attachment_utils'] = MagicMock()
sys.modules['backend.utils.fallback_utils'] = MagicMock()
sys.modules['backend.logging'] = MagicMock()
sys.modules['backend.logging.sass_logger'] = MagicMock()
sys.modules['backend.logging.erragent_handler'] = MagicMock()
sys.modules['backend.services.orchestrator'] = MagicMock()
sys.modules['backend.utils.isolation_kb_utils'] = MagicMock()
sys.modules['backend.utils.db_utils'] = MagicMock()
sys.modules['backend.auth.isolation_auth'] = MagicMock()
sys.modules['settings'] = MagicMock()

from fastapi.testclient import TestClient
from app import app, logger as app_logger

def test_erragent_debug_no_unhandled_exception():
    """
    Verify that the /api/erragent-debug endpoint handles the ZeroDivisionError
    internally and returns a JSONResponse with status 500, rather than raising
    an HTTPException or letting the exception propagate to the global exception
    handler or middleware, which would log an unhandled exception.
    """
    # Reset mock calls on app_logger
    app_logger.reset_mock()
    
    client = TestClient(app)
    response = client.get("/api/erragent-debug")
    
    # 1. Assert the response status code is 500
    assert response.status_code == 500
    
    # 2. Assert the response body contains the expected detail
    assert response.json() == {"detail": "ZeroDivisionError: division by zero"}
    
    # 3. Assert that the endpoint hit was logged
    app_logger.info.assert_any_call("--> /api/erragent-debug endpoint hit!")
    
    # 4. Assert that NO unhandled exception was logged by the global exception handler
    for call in app_logger.error.call_args_list:
        args, kwargs = call
        if args and isinstance(args[0], str):
            assert "Caught unhandled exception" not in args[0]
            
    # 5. Assert that NO unhandled request failure was logged by the middleware
    for call in app_logger.exception.call_args_list:
        args, kwargs = call
        if args and isinstance(args[0], str):
            assert "Unhandled request failure" not in args[0]
