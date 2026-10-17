"""The server must start even where the console cannot draw the startup banner (backend/services/orchestrator.py)."""
import inspect

from backend.services import orchestrator


def test_a_console_that_cannot_draw_the_banner_does_not_stop_startup():
    source = inspect.getsource(orchestrator.startup_services)
    body = source.split("# 1. CONNECT TO DATABASE")[0]
    assert "try:" in body and "print_logo()" in body and "except UnicodeEncodeError" in body


def test_the_banner_really_cannot_be_encoded_in_a_legacy_code_page_which_is_why_the_guard_exists():
    import io

    from backend.utils.logo import print_logo

    legacy = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    try:
        import contextlib

        with contextlib.redirect_stdout(legacy):
            print_logo()
            legacy.flush()
        raised = False
    except UnicodeEncodeError:
        raised = True
    assert raised
