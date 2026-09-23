from datetime import datetime, timezone
from unittest.mock import Mock

from backend.services import ci_test_runner as ctr


def _http_response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_data or {})
    resp.text = text
    return resp


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


HEADERS = {"Authorization": "Bearer fake"}
API_BASE = "https://api.github.com"
REPO = "SummonShenron/SAAPP"
BRANCH = "main"


def _patch_no_sleep(monkeypatch):
    monkeypatch.setattr(ctr.time, "sleep", lambda seconds: None)


# ---------------------------------------------------------------------------
# _validate_test_commands
# ---------------------------------------------------------------------------

def test_validate_accepts_plain_pytest_command():
    assert ctr._validate_test_commands("pytest backend/tests/test_memory_utils.py") is None


def test_validate_accepts_module_form_and_multiple_lines():
    assert ctr._validate_test_commands(
        "python -m pytest backend/tests/test_a.py\npytest backend/tests/test_b.py::TestX::test_y"
    ) is None


def test_validate_rejects_empty():
    assert ctr._validate_test_commands("") is not None
    assert ctr._validate_test_commands("   \n  ") is not None


def test_validate_rejects_wrong_shape():
    assert ctr._validate_test_commands("rm -rf /") is not None


def test_validate_rejects_shell_metacharacters():
    for bad in ("pytest tests/test_a.py; rm -rf /", "pytest tests/test_a.py | cat", "pytest ../secrets.py"):
        assert ctr._validate_test_commands(bad) is not None


# ---------------------------------------------------------------------------
# run_repo_tests
# ---------------------------------------------------------------------------

def test_run_repo_tests_rejects_invalid_command_without_dispatching(monkeypatch):
    fake_post = Mock()
    monkeypatch.setattr(ctr.requests, "post", fake_post)

    result = ctr.run_repo_tests(REPO, BRANCH, "not a real command", HEADERS, API_BASE)

    assert result.startswith("ERROR")
    fake_post.assert_not_called()


def test_run_repo_tests_dispatch_failure_returns_error(monkeypatch):
    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(422, text="bad ref"))

    result = ctr.run_repo_tests(REPO, BRANCH, "pytest backend/tests/test_a.py", HEADERS, API_BASE)

    assert result.startswith("ERROR")
    assert "422" in result


def test_run_repo_tests_success_returns_conclusion_and_url(monkeypatch):
    _patch_no_sleep(monkeypatch)
    run = {"id": 555, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/555", "created_at": _now_iso()}

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/555"):
            return _http_response(200, {"status": "completed", "conclusion": "success"})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    result = ctr.run_repo_tests(REPO, BRANCH, "pytest backend/tests/test_a.py", HEADERS, API_BASE)

    assert "SUCCESS" in result
    assert run["html_url"] in result


def test_run_repo_tests_failure_includes_log_excerpt(monkeypatch):
    _patch_no_sleep(monkeypatch)
    run = {"id": 777, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/777", "created_at": _now_iso()}

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/777"):
            return _http_response(200, {"status": "completed", "conclusion": "failure"})
        if url.endswith("/actions/runs/777/jobs"):
            return _http_response(200, {"jobs": [{"id": 999}]})
        if url.endswith("/actions/jobs/999/logs"):
            return _http_response(200, text="FAILED backend/tests/test_a.py::test_thing - AssertionError")
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    result = ctr.run_repo_tests(REPO, BRANCH, "pytest backend/tests/test_a.py", HEADERS, API_BASE)

    assert "FAILURE" in result
    assert "AssertionError" in result


def test_run_repo_tests_run_never_located_returns_error(monkeypatch):
    _patch_no_sleep(monkeypatch)
    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", lambda *a, **k: _http_response(200, {"workflow_runs": []}))
    monkeypatch.setattr(ctr, "DISPATCH_LOOKUP_RETRIES", 2)

    result = ctr.run_repo_tests(REPO, BRANCH, "pytest backend/tests/test_a.py", HEADERS, API_BASE)

    assert result.startswith("ERROR")
    assert "could not locate" in result


def test_run_repo_tests_times_out_if_never_completes(monkeypatch):
    _patch_no_sleep(monkeypatch)
    run = {"id": 111, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/111", "created_at": _now_iso()}

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/111"):
            return _http_response(200, {"status": "in_progress", "conclusion": None})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    result = ctr.run_repo_tests(
        REPO, BRANCH, "pytest backend/tests/test_a.py", HEADERS, API_BASE, max_wait_seconds=5
    )

    assert result.startswith("ERROR")
    assert "did not finish" in result


# ---------------------------------------------------------------------------
# run_python_snippet — Tier 1 real-execution path (docs/coding-agent-roadmap.md)
# ---------------------------------------------------------------------------

def test_run_python_snippet_rejects_empty_without_dispatching(monkeypatch):
    fake_post = Mock()
    monkeypatch.setattr(ctr.requests, "post", fake_post)

    result = ctr.run_python_snippet(REPO, BRANCH, "   ", HEADERS, API_BASE)

    assert result.startswith("ERROR")
    fake_post.assert_not_called()


def test_run_python_snippet_rejects_oversized_snippet_without_dispatching(monkeypatch):
    fake_post = Mock()
    monkeypatch.setattr(ctr.requests, "post", fake_post)

    result = ctr.run_python_snippet(REPO, BRANCH, "x" * (ctr._SNIPPET_MAX_CHARS + 1), HEADERS, API_BASE)

    assert result.startswith("ERROR")
    assert "limit" in result
    fake_post.assert_not_called()


def test_run_python_snippet_dispatch_failure_returns_error(monkeypatch):
    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(422, text="bad ref"))

    result = ctr.run_python_snippet(REPO, BRANCH, "print('hi')", HEADERS, API_BASE)

    assert result.startswith("ERROR")
    assert "422" in result


def test_run_python_snippet_dispatches_with_python_snippet_input_not_test_commands(monkeypatch):
    """The dispatch payload must carry the snippet under its own input name — reusing
    test_commands would route it through the pytest-only validation step in the workflow."""
    _patch_no_sleep(monkeypatch)
    run = {"id": 42, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/42", "created_at": _now_iso()}
    captured_payload = {}

    def fake_post(url, headers=None, json=None):
        captured_payload.update(json or {})
        return _http_response(204)

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/42"):
            return _http_response(200, {"status": "completed", "conclusion": "success"})
        if url.endswith("/actions/runs/42/jobs"):
            return _http_response(200, {"jobs": [{"id": 1}]})
        if url.endswith("/actions/jobs/1/logs"):
            return _http_response(200, text="hi\n")
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", fake_post)
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    ctr.run_python_snippet(REPO, BRANCH, "print('hi')", HEADERS, API_BASE)

    assert captured_payload["inputs"] == {"python_snippet": "print('hi')"}


def test_run_python_snippet_success_always_includes_printed_output(monkeypatch):
    """Unlike run_repo_tests (log only on failure), a snippet's printed output is the point of
    running it at all — the log excerpt must be included even on a SUCCESS conclusion."""
    _patch_no_sleep(monkeypatch)
    run = {"id": 88, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/88", "created_at": _now_iso()}

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/88"):
            return _http_response(200, {"status": "completed", "conclusion": "success"})
        if url.endswith("/actions/runs/88/jobs"):
            return _http_response(200, {"jobs": [{"id": 5}]})
        if url.endswith("/actions/jobs/5/logs"):
            return _http_response(200, text="the function returned 42")
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    result = ctr.run_python_snippet(REPO, BRANCH, "print(add(40, 2))", HEADERS, API_BASE)

    assert "SUCCESS" in result
    assert "the function returned 42" in result


def test_run_python_snippet_failure_includes_traceback(monkeypatch):
    _patch_no_sleep(monkeypatch)
    run = {"id": 99, "html_url": "https://github.com/SummonShenron/SAAPP/actions/runs/99", "created_at": _now_iso()}

    def fake_get(url, headers=None, params=None):
        if url.endswith("/runs") and params and params.get("event") == "workflow_dispatch":
            return _http_response(200, {"workflow_runs": [run]})
        if url.endswith("/actions/runs/99"):
            return _http_response(200, {"status": "completed", "conclusion": "failure"})
        if url.endswith("/actions/runs/99/jobs"):
            return _http_response(200, {"jobs": [{"id": 6}]})
        if url.endswith("/actions/jobs/6/logs"):
            return _http_response(200, text="Traceback (most recent call last):\nNameError: name 'add' is not defined")
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(ctr.requests, "post", lambda *a, **k: _http_response(204))
    monkeypatch.setattr(ctr.requests, "get", fake_get)

    result = ctr.run_python_snippet(REPO, BRANCH, "print(add(40, 2))", HEADERS, API_BASE)

    assert "FAILURE" in result
    assert "NameError" in result
