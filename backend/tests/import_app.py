"""Importing app.py in a test WITHOUT a database.

WHY. app.py boots the whole app when it is imported (`services = startup_services()`, backend/services/orchestrator.py): it connects to MongoDB, reads the user directory, builds the vector stores and the
checkpointer. Three test files import it at COLLECTION time (they need the app's request classes and endpoint functions), and the autouse fixtures in conftest.py that block real databases only run inside a test,
so that import was the one place a test suite could reach a real cluster. On a machine with a .env it connected to the real Atlas cluster every run; in CI, with no database, collection failed with
"HTTPException 500: Database connection unavailable" and no test ran (2026-11-19).

WHAT IT DOES. Imports app with `startup_services` replaced by one that connects to nothing and returns the same keys, all empty. The tests that need a service patch it themselves (`monkeypatch.setattr(app_module, "_admin_db", ...)`,
`app_module.services`, ...), as they always did. The real startup is covered by its own tests (test_orchestrator*.py) and by the server booting.
"""
import sys
from types import ModuleType
from unittest.mock import patch


def no_services() -> dict:
    """What startup_services() returns, with nothing behind it."""
    return {"user_directory": {}, "vector_store": None, "user_memory_vector_store": None, "compiled_workflow": None, "checkpointer": None}


def import_app() -> ModuleType:
    """The app module, imported once for the whole test run with no database. Importing it again (from another test file) returns the same module."""
    if "app" in sys.modules:
        return sys.modules["app"]
    with patch("backend.services.orchestrator.startup_services", no_services):
        import app
    return app
