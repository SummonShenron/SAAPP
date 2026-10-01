import asyncio
import functools
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, AIMessage

from backend.services import agent_workflow as aw
from backend.services import repo_checkout as rc


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _state(question="where is the login flow implemented?", documents=None, username="jack"):
    return {
        "username": username,
        "messages": [HumanMessage(content=question)],
        "documents": documents or [],
    }


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


# A substring unique to GROUNDING_CHECK_PROMPT (constraints.py) — see the matching constant/
# comment in test_react_loop_retry_enforcement.py. Only needed here where a test's fake_ainvoke
# indexes/pops a fixed `responses` list AND asserts an exact captured_prompts count — most of this
# file's fake_ainvoke definitions always return the same canned "final" regardless of call count,
# which the grounding check already treats as ungrounded-claims-free without any special handling.
GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"


def _http_response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_data or {})
    resp.text = text
    return resp


def _b64(text: str) -> str:
    import base64
    return base64.b64encode(text.encode("utf-8")).decode("utf-8")


class _FakeCollection:
    def __init__(self, docs=None):
        self.docs = docs or []

    def find(self, query=None):
        query = query or {}
        return [d for d in self.docs if all(d.get(k) == v for k, v in query.items())]

    def find_one(self, query=None):
        results = self.find(query)
        return results[0] if results else None

    def count_documents(self, query=None):
        return len(self.find(query))


class _FakeDB:
    def __init__(self, collections=None):
        self._collections = collections or {}

    def list_collection_names(self):
        return list(self._collections.keys())

    def __getitem__(self, name):
        return self._collections.setdefault(name, _FakeCollection())


def _setup_github_repo(monkeypatch, tree_items=None):
    # tree_items, when given, serves /git/trees/ requests too — needed by any test whose prompt
    # triggers _is_audit_style_task, since that now pre-fetches the tree for the architecture map
    # before the ReAct loop even starts. Left as None (tree endpoint stays "unexpected") for every
    # other test, so a real regression that unexpectedly hits the tree endpoint still fails loudly.
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": tree_items or []})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if tree_items is not None and "/git/trees/" in url:
            return tree_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    # Every tool_agent_node call resolves the user's directory groups near the start regardless
    # of which action is ultimately tested — without this, a test that doesn't separately mock
    # it falls through to a real, now-blocked MongoDB connection (see conftest.py's
    # block_real_db_calls) rather than the harmless default every test here actually wants.
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    return fake_get


# ---------------------------------------------------------------------------
# Repo-resolution self-correction — a real production trace showed
# extract_github_repo can still false-positive on an ordinary phrase with a
# bare slash (e.g. "the updated functions/diffs for whatever needs to
# change" read as owner/repo "functions/diffs") even past its existing
# generic-path-segment denylist — no denylist can cover every English word
# pair. The old behavior silently kept the wrong repo and defaulted only the
# BRANCH to "main", so every GitHub action that turn 404'd with the model
# never learning why (it just kept retrying list_repo_tree). Now a failed
# repo-metadata fetch retries once against the pinned/default repo instead.
# ---------------------------------------------------------------------------

@run_async
async def test_bad_extracted_repo_falls_back_to_default_and_notifies_model(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/functions/diffs"):
            return _http_response(404, {"message": "Not Found"})
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return _http_response(200, {"default_branch": "main"})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    # Simulates the real false-positive: extract_github_repo reading an ordinary phrase's
    # bare slash as an owner/repo mention.
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback=None: "functions/diffs" if fallback is None else fallback)

    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("show me the updated functions/diffs for the refactor"))

    assert "repo=SummonShenron/SAAPP" in captured_kwargs["schema"]
    assert "functions/diffs" in captured_kwargs["schema"]
    assert "false-positive" in captured_kwargs["schema"]


@run_async
async def test_genuinely_inaccessible_repo_warns_instead_of_looping_silently(monkeypatch):
    # Both the extracted repo AND the fallback are the same, real-but-inaccessible repo — no
    # different repo to retry against, so this must surface a plain warning instead of silently
    # guessing branch "main" for a repo that was never confirmed to exist.
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return _http_response(404, {"message": "Not Found"})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("where is the login flow implemented?"))

    assert "WARNING" in captured_kwargs["schema"]
    assert "default_branch=main" in captured_kwargs["schema"]


@run_async
async def test_successful_repo_resolution_adds_no_note(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("where is the login flow implemented?"))

    assert "NOTE" not in captured_kwargs["schema"]
    assert "WARNING" not in captured_kwargs["schema"]


# ---------------------------------------------------------------------------
# Admin access to Mongo action
# ---------------------------------------------------------------------------

@run_async
async def test_run_python_available_to_everyone(monkeypatch):
    """Unlike run_mongo_query, run_python is fully sandboxed and side-effect-free — it should
    be offered to non-admins too, not gated behind is_admin."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what's 12 squared?"))

    assert any("run_python" in p for p in captured_prompts)


@run_async
async def test_run_python_dispatches_to_sandbox_and_normalizes_errors(monkeypatch):
    _setup_github_repo(monkeypatch)

    async def fake_sandbox(code, timeout_seconds=5.0, fuel=400_000_000):
        if "fail" in code:
            return {"output": "", "error": "boom"}
        return {"output": "42\n", "error": ""}

    monkeypatch.setattr(aw, "run_python_sandboxed", fake_sandbox)

    responses = [
        _llm_response(action="query", purpose="Compute it", tool_action="run_python", args={"code": "print(6*7)"}),
        _llm_response(action="final", answer="It's 42.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("what's 6 times 7?"))

    assert "42" in result["content_to_format"]
    # The sandbox's structured {"output","error"} result is normalized into this loop's own
    # "ERROR: ..." string convention for a failure so retry-nudge tracking picks it up.
    assert "ERROR" not in result["content_to_format"].split("**Result:**")[1][:50]


@run_async
async def test_run_python_failure_uses_error_prefix_convention(monkeypatch):
    _setup_github_repo(monkeypatch)

    async def failing_sandbox(code, timeout_seconds=5.0, fuel=400_000_000):
        return {"output": "", "error": "Rejected before running: import of 'os' is not on the safe list."}

    monkeypatch.setattr(aw, "run_python_sandboxed", failing_sandbox)

    responses = [
        _llm_response(action="query", purpose="Try os", tool_action="run_python", args={"code": "import os"}),
        _llm_response(action="final", answer="Couldn't use os for that.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("check the os module for me"))

    assert "ERROR: Rejected before running" in result["content_to_format"]


@run_async
async def test_list_google_calendar_events_returns_formatted_agenda(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(
        aw, "list_events_for_day",
        lambda token, date, tz: [{"id": "e1", "summary": "Standup", "start": "2026-06-21T09:00:00", "end": "2026-06-21T09:15:00"}],
    )

    responses = [
        _llm_response(action="query", purpose="Check calendar", tool_action="list_google_calendar_events", args={"date": "2026-06-21"}),
        _llm_response(action="final", answer="You have a Standup at 9am.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("what's on my google calendar tomorrow?"))

    assert "Standup" in result["content_to_format"]


@run_async
async def test_list_google_calendar_events_reports_no_connection(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("No Google Calendar connection found for jack")

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Check calendar", tool_action="list_google_calendar_events", args={"date": "2026-06-21"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what's on my google calendar tomorrow?"))

    # The real ERROR observation from the tool call must have been fed back to the model as
    # context for its next step — not just whatever the mocked "final" answer happens to say.
    assert any("Connect it under Integrations" in p for p in captured_prompts)


@run_async
async def test_list_google_calendar_events_blocks_locked_guest_identity(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "CALENDAR_LOCKED_USERS", {"guest_bty"})

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Check calendar", tool_action="list_google_calendar_events", args={"date": "2026-06-21"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what's on my google calendar tomorrow?", username="guest_bty"))

    assert any("ERROR: Google Calendar is not available for this account" in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# list_pull_requests — added so "the last N pull requests" is answerable with real GitHub data;
# previously nothing in the menu could list PRs (only list_commits, which covers commit history).
# ---------------------------------------------------------------------------

@run_async
async def test_list_pull_requests_returns_formatted_results(monkeypatch):
    _setup_github_repo(monkeypatch)

    repo_resp = _http_response(200, {"default_branch": "main"})
    prs_resp = _http_response(200, [
        {"number": 126, "title": "Fix mobile navigation layout issues", "state": "closed",
         "merged_at": "2026-01-01T00:00:00Z", "user": {"login": "jack"}, "updated_at": "2026-01-01T00:00:00Z"},
        {"number": 125, "title": "Optimize dashboard loading times", "state": "closed",
         "merged_at": "2025-12-30T00:00:00Z", "user": {"login": "jack"}, "updated_at": "2025-12-30T00:00:00Z"},
    ])

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/repos/SummonShenron/SAAPP/pulls"):
            return prs_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="List recent PRs", tool_action="list_pull_requests", args={"limit": 3}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("search github for the last 3 pull requests"))

    assert any("#126" in p and "Fix mobile navigation layout issues" in p and "merged" in p for p in captured_prompts)
    assert any("#125" in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# Gmail read actions
# ---------------------------------------------------------------------------

@run_async
async def test_search_gmail_returns_formatted_results(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(
        aw, "search_messages",
        lambda token, query, max_results: [{"id": "m1", "subject": "Weekly Report", "from": "app@x.com", "date": "Mon", "snippet": "..."}],
    )

    responses = [
        _llm_response(action="query", purpose="Find report email", tool_action="search_gmail", args={"query": "subject:report"}),
        _llm_response(action="final", answer="Found it.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("check my gmail for the weekly report email"))
    assert "Found it" in result["content_to_format"]


@run_async
async def test_read_gmail_message_returns_formatted_detail(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(
        aw, "get_message_detail",
        lambda token, message_id: {
            "subject": "Weekly Report", "from": "app@x.com", "date": "Mon", "body_text": "See attached.",
            "body_is_html": False, "attachments": [{"filename": "export.json", "mime_type": "application/json", "attachment_id": "att1", "size": 42}],
        },
    )

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read email", tool_action="read_gmail_message", args={"message_id": "m1"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("check my gmail for the weekly report email"))
    assert any("export.json" in p and "att1" in p for p in captured_prompts)


@run_async
async def test_get_gmail_attachment_returns_content(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(aw, "get_attachment_text", lambda token, message_id, attachment_id: '{"hours": 5}')

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read attachment", tool_action="get_gmail_attachment", args={"message_id": "m1", "attachment_id": "att1"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("read that attachment"))
    assert any('"hours": 5' in p for p in captured_prompts)


@run_async
async def test_gmail_actions_report_no_connection(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("No Google Calendar connection found for jack")

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search gmail", tool_action="search_gmail", args={"query": "report"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("search my gmail for the report"))
    assert any("Connect it under Integrations" in p for p in captured_prompts)


@run_async
async def test_gmail_actions_report_missing_scope(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search gmail", tool_action="search_gmail", args={"query": "report"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("search my gmail for the report"))
    assert any("Gmail access not granted" in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# Drive read actions
# ---------------------------------------------------------------------------

@run_async
async def test_search_drive_files_returns_formatted_results(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(
        aw, "search_drive_files_fn",
        lambda token, query, max_results: [{"id": "f1", "name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet", "modifiedTime": "2026-01-01"}],
    )

    responses = [
        _llm_response(action="query", purpose="Find budget file", tool_action="search_drive_files", args={"query": "name contains 'Budget'"}),
        _llm_response(action="final", answer="Found it.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("find my budget spreadsheet in drive"))
    assert "Found it" in result["content_to_format"]


@run_async
async def test_read_drive_file_returns_content(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(aw, "read_drive_file_fn", lambda token, file_id: "Q3 revenue: $500k")

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Read file", tool_action="read_drive_file", args={"file_id": "f1"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what's in my budget file"))
    assert any("Q3 revenue" in p for p in captured_prompts)


@run_async
async def test_drive_actions_report_no_connection(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("No Google Calendar connection found for jack")

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search drive", tool_action="search_drive_files", args={"query": "name contains 'Budget'"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("find my budget file in drive"))
    assert any("Connect it under Integrations" in p for p in captured_prompts)


@run_async
async def test_drive_actions_report_missing_scope(monkeypatch):
    _setup_github_repo(monkeypatch)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search drive", tool_action="search_drive_files", args={"query": "name contains 'Budget'"}),
        _llm_response(action="final", answer="Done.", show_work=True),
    ]

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("find my budget file in drive"))
    assert any("Google Drive access not granted" in p for p in captured_prompts)


@run_async
async def test_non_admin_action_menu_never_includes_mongo(monkeypatch):
    """The actual security property: non-admins shouldn't even see run_mongo_query as an
    option, not just be rejected if they somehow ask for it."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what does the login code do?"))

    assert all("run_mongo_query" not in p for p in captured_prompts)


@run_async
async def test_admin_action_menu_includes_mongo(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("how many facts are stored?"))

    assert any("run_mongo_query" in p for p in captured_prompts)


@run_async
async def test_non_admin_cannot_execute_mongo_action_even_if_returned(monkeypatch):
    """Defense in depth: even if the model somehow proposes run_mongo_query (e.g. a stale
    conversation), execution is still blocked for non-admins."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Try mongo anyway", tool_action="run_mongo_query", args={"code": "result = 1"}),
        _llm_response(action="final", answer="Could not run that."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state())

    assert "not authorized" in result["content_to_format"].lower()


@run_async
async def test_non_admin_action_menu_never_includes_run_repo_tests(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("does the fix for X actually work?"))

    # The general guidance paragraph mentions run_repo_tests by name (worded conditionally —
    # "if available to you (admin only)"), so check for the actual actionable menu entry
    # (its args shape) rather than the bare word, which is what actually determines whether the
    # model can call it.
    assert all("run_repo_tests — args:" not in p for p in captured_prompts)


@run_async
async def test_admin_action_menu_includes_run_repo_tests(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("does the fix for X actually work?"))

    assert any("run_repo_tests — args:" in p for p in captured_prompts)


@run_async
async def test_non_admin_cannot_execute_run_repo_tests_even_if_returned(monkeypatch):
    """Defense in depth, mirroring the equivalent run_mongo_query test."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="Try running tests anyway", tool_action="run_repo_tests", args={"test_commands": "pytest backend/tests/test_a.py"}),
        _llm_response(action="final", answer="Could not run that."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state())

    assert "not authorized" in result["content_to_format"].lower()


@run_async
async def test_run_repo_tests_dispatches_with_repo_and_default_branch(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    fake_run_tests = Mock(return_value="Test run SUCCESS: https://github.com/SummonShenron/SAAPP/actions/runs/1")
    monkeypatch.setattr(aw, "run_repo_tests", fake_run_tests)

    responses = [
        _llm_response(
            action="query", purpose="Verify the fix", tool_action="run_repo_tests",
            args={"test_commands": "pytest backend/tests/test_a.py"},
        ),
        _llm_response(action="final", answer="Confirmed — the tests pass."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("does the fix actually work?"))

    fake_run_tests.assert_called_once()
    call_args = fake_run_tests.call_args.args
    assert call_args[0] == "SummonShenron/SAAPP"  # repo
    assert call_args[1] == "main"  # falls back to default_branch when branch arg omitted
    assert call_args[2] == "pytest backend/tests/test_a.py"
    assert "Confirmed" in result["content_to_format"]


# ---------------------------------------------------------------------------
# run_snippet — Tier 1 real-execution path (docs/coding-agent-roadmap.md)
# ---------------------------------------------------------------------------

@run_async
async def test_non_admin_action_menu_never_includes_run_snippet(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("does this function actually work?"))

    assert all("run_snippet — args:" not in p for p in captured_prompts)


@run_async
async def test_admin_action_menu_includes_run_snippet(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("does this function actually work?"))

    assert any("run_snippet — args:" in p for p in captured_prompts)


@run_async
async def test_run_snippet_menu_text_forbids_hand_rolled_stand_ins(monkeypatch):
    # "Verification theater" (docs/coding-agent-roadmap.md, Section 4j/7): run_snippet was used
    # to "verify" a change by testing an isolated, invented proof-of-concept instead of the real
    # modified code — looks like verification in the trace, proves nothing. Reviewed and rejected
    # a self-drive attempt at this fix that fabricated a nonexistent CONSTRAINTS dict in
    # constraints.py; the real menu text lives inline here in agent_workflow.py.
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("does this function actually work?"))

    assert any(
        "MUST import and call the real function/module" in p and "hand-rolled stand-in" in p
        for p in captured_prompts
    )


@run_async
async def test_non_admin_cannot_execute_run_snippet_even_if_returned(monkeypatch):
    """Defense in depth, mirroring the equivalent run_repo_tests/run_mongo_query tests."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="Try running a snippet anyway", tool_action="run_snippet", args={"code": "print(1)"}),
        _llm_response(action="final", answer="Could not run that."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state())

    assert "not authorized" in result["content_to_format"].lower()


@run_async
async def test_run_snippet_dispatches_with_repo_default_branch_and_code(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection()}))

    fake_run_snippet = Mock(return_value="Snippet run SUCCESS: https://github.com/SummonShenron/SAAPP/actions/runs/1\nthe function returned 42")
    monkeypatch.setattr(aw, "run_python_snippet", fake_run_snippet)

    responses = [
        _llm_response(
            action="query", purpose="Verify the function actually works", tool_action="run_snippet",
            args={"code": "print(add(40, 2))"},
        ),
        _llm_response(action="final", answer="Confirmed — it returns 42."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("does add(40, 2) actually return 42?"))

    fake_run_snippet.assert_called_once()
    call_args = fake_run_snippet.call_args.args
    assert call_args[0] == "SummonShenron/SAAPP"  # repo
    assert call_args[1] == "main"  # falls back to default_branch when branch arg omitted
    assert call_args[2] == "print(add(40, 2))"
    assert "Confirmed" in result["content_to_format"]


# ---------------------------------------------------------------------------
# Mongo action via the unified agent
# ---------------------------------------------------------------------------

@run_async
async def test_mongo_action_correct_collection_first_try(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"user_memory_facts": _FakeCollection([{"fact": "a"}, {"fact": "b"}])}))

    responses = [
        _llm_response(action="query", purpose="Count facts", tool_action="run_mongo_query", args={"code": "result = db['user_memory_facts'].count_documents({})"}),
        _llm_response(action="final", answer="You have 2 saved facts."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("how many facts are stored?"))

    assert "2 saved facts" in result["content_to_format"]
    assert result["relevance_grade"] == "tool_agent"


@run_async
async def test_show_work_false_omits_the_steps_footer(monkeypatch):
    """A casual question shouldn't come back looking like a technical report just because a
    tool ran — the live trace panel already shows the steps in real time, so the model can
    choose not to repeat them in the chat message itself."""
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="Peek at the repo structure", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="Yeah, it's a LangGraph-based backend with a few core services.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(
        aw.requests, "get",
        lambda url, headers=None, params=None, **kwargs: (
            _http_response(200, {"default_branch": "main"}) if url.endswith("/repos/SummonShenron/SAAPP")
            else _http_response(200, {"tree": [{"path": "app.py", "type": "blob"}]})
        ),
    )

    result = await aw.tool_agent_node(_state("what kind of backend is this anyway?"))

    assert result["content_to_format"] == "Yeah, it's a LangGraph-based backend with a few core services."
    assert "Step 1" not in result["content_to_format"]


@run_async
async def test_show_work_true_includes_the_steps_footer(monkeypatch):
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="Check the tests directory", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="Here's what I verified.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(
        aw.requests, "get",
        lambda url, headers=None, params=None, **kwargs: (
            _http_response(200, {"default_branch": "main"}) if url.endswith("/repos/SummonShenron/SAAPP")
            else _http_response(200, {"tree": [{"path": "app.py", "type": "blob"}]})
        ),
    )

    result = await aw.tool_agent_node(_state("can you verify the test suite covers this?"))

    assert "Here's what I verified." in result["content_to_format"]
    assert "Step 1" in result["content_to_format"]
    assert "Check the tests directory" in result["content_to_format"]


# ---------------------------------------------------------------------------
# Deep thinking (per-user setting) raises the step cap + retry-nudge budget
# ---------------------------------------------------------------------------

@run_async
async def test_deep_thinking_off_uses_standard_loop_limits(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    state = _state("what does this repo do?")
    state["deep_thinking"] = False
    await aw.tool_agent_node(state)

    assert captured_kwargs["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS
    assert captured_kwargs["max_retry_nudges"] == aw.TOOL_AGENT_MAX_RETRY_NUDGES
    assert captured_kwargs["llm"] is aw.lite_llm


@run_async
async def test_stuck_action_redirects_passed_to_react_loop(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("what does this repo do?"))

    assert captured_kwargs["stuck_action_redirects"] is aw.TOOL_AGENT_STUCK_ACTION_REDIRECTS
    assert "search_code" in captured_kwargs["stuck_action_redirects"]


@run_async
async def test_deep_thinking_missing_from_state_defaults_to_standard_limits(monkeypatch):
    """The field is absent entirely on any state built before this feature existed (or any
    test/state dict that doesn't set it) — must fall back to the standard limits, not error."""
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("what does this repo do?"))

    assert captured_kwargs["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS
    assert captured_kwargs["max_retry_nudges"] == aw.TOOL_AGENT_MAX_RETRY_NUDGES


@run_async
async def test_deep_thinking_on_raises_step_cap_and_retry_nudge_budget(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    state = _state("what does this repo do?")
    state["deep_thinking"] = True
    await aw.tool_agent_node(state)

    assert captured_kwargs["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS_DEEP
    assert captured_kwargs["max_retry_nudges"] == aw.TOOL_AGENT_MAX_RETRY_NUDGES_DEEP
    assert captured_kwargs["llm"] is aw.lite_llm_deep
    assert aw.TOOL_AGENT_MAX_ITERATIONS_DEEP > aw.TOOL_AGENT_MAX_ITERATIONS
    assert aw.TOOL_AGENT_MAX_RETRY_NUDGES_DEEP > aw.TOOL_AGENT_MAX_RETRY_NUDGES


# ---------------------------------------------------------------------------
# Repo resolution priority: current-message mention > pinned setting > history scan > default
# ---------------------------------------------------------------------------

@run_async
async def test_explicit_repo_in_current_message_overrides_pinned_repo(monkeypatch):
    """A pinned target repo (state["repo"], set via the settings banner) is easy to forget
    about — it must not silently swallow an explicit, unambiguous repo mention in the message
    the user just sent."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    requested_repos = []

    def fake_get(url, headers=None, params=None, **kwargs):
        requested_repos.append(url)
        return _http_response(200, {"default_branch": "main"})

    monkeypatch.setattr(aw.requests, "get", fake_get)
    responses = [_llm_response(action="final", answer="done", show_work=False)]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    state = _state("check facebook/react for how they handle this")
    state["repo"] = "old-owner/pinned-repo"
    await aw.tool_agent_node(state)

    assert any("facebook/react" in url for url in requested_repos)
    assert not any("pinned-repo" in url for url in requested_repos)


@run_async
async def test_pinned_repo_used_when_current_message_names_none(monkeypatch):
    """With no explicit repo mention in the current message, the pin still applies — this is
    the whole point of pinning one."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    requested_repos = []

    def fake_get(url, headers=None, params=None, **kwargs):
        requested_repos.append(url)
        return _http_response(200, {"default_branch": "main"})

    monkeypatch.setattr(aw.requests, "get", fake_get)
    responses = [_llm_response(action="final", answer="done", show_work=False)]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    state = _state("what does this file do?")
    state["repo"] = "old-owner/pinned-repo"
    await aw.tool_agent_node(state)

    assert any("pinned-repo" in url for url in requested_repos)


@run_async
async def test_mongo_unsafe_write_triggers_approval(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setattr(aw, "get_db", lambda: _FakeDB({"tasks": _FakeCollection()}))

    responses = [
        _llm_response(action="query", purpose="Delete stale tasks", tool_action="run_mongo_query", args={"code": "result = db['tasks'].delete_many({})"}),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("delete all stale tasks"))

    # Phase 2: the unsafe-write proposal now hands off via the same pending_action shape
    # every registered write action uses, dispatched later by execute_write_node.
    assert result["relevance_grade"] == "hitl_approval_required"
    assert result["pending_action"]["action_type"] == "run_mongo_write"
    assert result["pending_action"]["details"]["code"] == "result = db['tasks'].delete_many({})"
    assert "Approval" in result["content_to_format"]


# ---------------------------------------------------------------------------
# GitHub actions via the unified agent (open to non-admins)
# ---------------------------------------------------------------------------

@run_async
async def test_github_action_self_corrects_after_wrong_file(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    wrong_file_resp = _http_response(200, {"content": _b64("not it")})
    right_file_resp = _http_response(200, {"content": _b64("def get_current_user(): ...")})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/app.py"):
            return wrong_file_resp
        if url.endswith("/contents/backend/auth/isolation_auth.py"):
            return right_file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Check app.py first", tool_action="read_repo_file", args={"path": "app.py"}),
        _llm_response(action="query", purpose="Check auth module instead", tool_action="read_repo_file", args={"path": "backend/auth/isolation_auth.py"}),
        _llm_response(action="final", answer="Login is handled in backend/auth/isolation_auth.py via get_current_user."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("where is the login flow implemented?"))

    assert "get_current_user" in result["content_to_format"]


# ---------------------------------------------------------------------------
# read_repo_file paging — the fix for a real fabrication bug: a flat char-count
# truncation could silently cut off before ever reaching a function defined
# further down a large file, with nothing telling the model to keep reading.
# ---------------------------------------------------------------------------

def _build_large_file_fixture():
    """A synthetic file comfortably over _READ_FILE_CHAR_CAP, with two known top-level
    functions separated by enough filler that the second one lands well past the truncation
    point. Returns (content, line_number_of_reasoner_node)."""
    padding = "\n".join(f"# filler line {i}" for i in range(250))
    content = (
        f"import os\n\n{padding}\n\n"
        "async def reasoner_node(state):\n    pass\n\n"
        f"{padding}\n\n"
        "async def memory_save_node(state):\n    pass\n"
    )
    assert len(content) > aw._READ_FILE_CHAR_CAP, "fixture must actually exercise truncation"
    reasoner_line = next(
        i for i, line in enumerate(content.splitlines(), start=1)
        if line.startswith("async def reasoner_node")
    )
    return content, reasoner_line


@run_async
async def test_read_repo_file_truncation_includes_definition_index(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, _ = _build_large_file_fixture()
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/services/agent_workflow.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            return _llm_response(
                action="query", purpose="Read the file",
                tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py"},
            )
        return _llm_response(action="final", answer="Done.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what does reasoner_node do?"))

    # The second prompt's ATTEMPTS SO FAR section carries the first read's observation —
    # confirming the truncated response names BOTH functions and their line numbers, not just
    # whatever happened to fit in the first _READ_FILE_CHAR_CAP characters.
    followup_prompt = captured_prompts[1]
    assert "truncated" in followup_prompt
    assert "reasoner_node" in followup_prompt
    assert "memory_save_node" in followup_prompt
    assert "start_line" in followup_prompt


def _build_file_with_many_top_level_defs(def_count=100, target_index=90, padding_per_def=6):
    """A file with enough top-level defs that the definition index itself is sizeable, and
    enough padding (comment lines, which _TOP_LEVEL_DEF_RE doesn't match) to push the total file
    size well past truncation — independently of index size, so this reproduces the real
    production gap (docs/coding-agent-roadmap.md, Section 12) without the index alone exceeding
    _MAX_OBSERVATION_CHARS. Under the OLD behavior (fixed 3500-char snippet + full index, then a
    later blanket 4000-char cap slicing from the start) the index — appended after the snippet —
    got cut off before reaching a def this late in it; under the fix, the index gets first claim
    on the budget and the snippet shrinks instead. target_index is deliberately late but still
    comfortably within _build_definition_index's own 150-entry cap, so this isolates THIS bug
    from that separate, already-accepted limit."""
    lines = ["import os", ""]
    for i in range(def_count):
        if i == target_index:
            lines.append("async def target_function(state):")
        else:
            lines.append(f"def filler_{i}():")
        lines.append("    pass")
        for p in range(padding_per_def):
            lines.append(f"    # padding line {p} for filler {i}")
        lines.append("")
    content = "\n".join(lines)
    assert len(content) > aw._READ_FILE_CHAR_CAP, "fixture must actually exercise truncation"
    target_line = next(
        i for i, line in enumerate(content.splitlines(), start=1)
        if line.startswith("async def target_function")
    )
    return content, target_line


@run_async
async def test_definition_index_survives_even_with_many_top_level_defs(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, target_line = _build_file_with_many_top_level_defs()
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/services/agent_workflow.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            return _llm_response(
                action="query", purpose="Read the file",
                tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py"},
            )
        return _llm_response(action="final", answer="Done.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("where is target_function defined?"))

    followup_prompt = captured_prompts[1]
    assert f"line {target_line}: def target_function" in followup_prompt


# ---------------------------------------------------------------------------
# _mentions_unresolved_truncation — a real production trace showed the model
# treat a truncated read_repo_file result as if it were the whole file: read
# a large file once, never re-called with start_line despite the truncation
# note explicitly saying to, and confidently fabricated a full implementation
# with 3 full steps of budget still unused. This makes "don't guess past a
# truncation" mechanical instead of relying on the model to comply with the
# note's own prose.
# ---------------------------------------------------------------------------

def test_mentions_unresolved_truncation_detects_both_read_file_variants():
    # Exercises the real truncation note text directly rather than re-deriving it by hand.
    assert aw._mentions_unresolved_truncation(
        "... [truncated — this file has 500 lines total, too long to show in full. "
        "Top-level definitions found in it:\nfoo (line 1)\nCall read_repo_file again with "
        "start_line set to the one you actually need — do not assume the file's contents "
        "past this point from general knowledge of what a file like this usually contains.]"
    )
    assert aw._mentions_unresolved_truncation(
        "... [truncated — this file has 500 lines total; re-call with a start_line to read "
        "further into it instead of guessing what comes next.]"
    )


def test_mentions_unresolved_truncation_detects_windowed_read_with_more_remaining():
    # A second real trace immediately exposed a gap in the first fix: forced to retry, the
    # model correctly called read_repo_file WITH a start_line — but a windowed read that still
    # has more content below emits a THIRD, different message shape than the two "whole file
    # too long" variants above, and it wasn't covered.
    assert aw._mentions_unresolved_truncation(
        "URL: https://github.com/x/y\nLines 400-549 of 4552 total:\n...\n"
        "... [4003 more lines below — re-call with a higher start_line to keep reading]"
    )


def test_mentions_unresolved_truncation_negative_for_unrelated_text():
    assert not aw._mentions_unresolved_truncation("URL: https://github.com/x/y\nimport os\n")
    # The OTHER, unrelated truncation mechanism (_truncate_observation, for huge Mongo results
    # etc.) uses different wording and must not false-positive here — re-reading with start_line
    # makes no sense for that case since it isn't a read_repo_file pagination situation at all.
    assert not aw._mentions_unresolved_truncation("... [truncated — 5000 total characters]")
    # A windowed read that reaches the actual end of the file has no "more lines below" note at
    # all (see _read_file's more_note logic) — must not false-positive on an ordinary complete read.
    assert not aw._mentions_unresolved_truncation(
        "URL: https://github.com/x/y\nLines 4500-4552 of 4552 total:\nasync def last_function():\n    pass\n"
    )


@run_async
async def test_final_after_truncated_read_is_rejected_once_then_accepted(monkeypatch):
    """The exact real production failure this closes: a truncated read_repo_file result, then
    a confident 'final' with 3 full steps of budget still unused — no crash, no budget
    exhaustion, just a premature stop. The retry-nudge machinery (already used for ERROR/empty
    observations) must reject that first 'final' and force a real re-read with start_line."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, _ = _build_large_file_fixture()
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/services/agent_workflow.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py"}),
        _llm_response(action="final", answer="Premature — proposing changes based on the truncated snippet alone."),
        # start_line=360 reaches the true end of this 509-line fixture (360 + 150 - 1 = 509) —
        # a genuinely complete read, not just a different-but-still-incomplete one (see
        # test_final_rejected_again_if_the_retry_read_is_still_incomplete for that case).
        _llm_response(action="query", purpose="Actually read further as instructed", tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py", "start_line": 360}),
        _llm_response(action="final", answer="Now grounded in the real content."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("what does memory_save_node do?"))

    assert "Now grounded in the real content." in result["content_to_format"]


@run_async
async def test_final_rejected_again_if_the_retry_read_is_still_incomplete(monkeypatch):
    """The exact real production failure the "more lines below" widening closes: forced to
    retry after a truncated whole-file read, the model correctly re-called with a start_line —
    but that windowed read was ITSELF still incomplete (more content below), and under deep
    thinking's multi-nudge budget it must be rejected AGAIN, not accepted just because it
    technically used a different start_line once already. Only a read that actually reaches
    the end of the file may be followed by an accepted final."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, _ = _build_large_file_fixture()  # 509 lines total
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/services/agent_workflow.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Read the file", tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py"}),
        _llm_response(action="final", answer="Premature #1 — from the truncated whole-file snippet."),
        # Still incomplete: start_line=260 + the default 150-line window ends at line 409, well
        # short of this fixture's 509 total lines.
        _llm_response(action="query", purpose="Retry with start_line", tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py", "start_line": 260}),
        _llm_response(action="final", answer="Premature #2 — still hasn't reached the real content."),
        # Reaches the true end this time (360 + 150 - 1 = 509 = total lines) — genuinely complete.
        _llm_response(action="query", purpose="Retry again, further this time", tool_action="read_repo_file", args={"path": "backend/services/agent_workflow.py", "start_line": 360}),
        _llm_response(action="final", answer="Now genuinely grounded in the full content."),
    ]
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", AsyncMock(side_effect=responses))

    state = _state("what does memory_save_node do?")
    state["deep_thinking"] = True
    result = await aw.tool_agent_node(state)

    assert "Now genuinely grounded in the full content." in result["content_to_format"]
    assert "Premature #1" not in result["content_to_format"]
    assert "Premature #2" not in result["content_to_format"]


@run_async
async def test_read_repo_file_start_line_jumps_past_the_truncation_point(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, reasoner_line = _build_large_file_fixture()
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/services/agent_workflow.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            return _llm_response(
                action="query", purpose="Jump straight to reasoner_node",
                tool_action="read_repo_file",
                args={"path": "backend/services/agent_workflow.py", "start_line": reasoner_line, "line_count": 2},
            )
        return _llm_response(action="final", answer="Found it.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("show me reasoner_node's real code"))

    followup_prompt = captured_prompts[1]
    assert "async def reasoner_node" in followup_prompt
    # A tight 2-line window starting exactly at reasoner_node — none of the padding on either
    # side of it (250 lines each) should have been dragged in.
    assert "filler line" not in followup_prompt
    assert "import os" not in followup_prompt


@run_async
async def test_read_repo_file_start_line_past_end_of_file_is_error(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(
        aw.requests, "get",
        lambda url, headers=None, params=None, **kwargs: (
            _http_response(200, {"default_branch": "main"}) if url.endswith("/repos/SummonShenron/SAAPP")
            else _http_response(200, {"content": _b64("just a few lines\nof real content\n")})
        ),
    )

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        if len(captured_prompts) == 1:
            return _llm_response(
                action="query", purpose="Jump way past the end", tool_action="read_repo_file",
                args={"path": "app.py", "start_line": 9999},
            )
        return _llm_response(action="final", answer="That line doesn't exist.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("show me line 9999 of app.py"))

    # Confirms _read_file itself reports the out-of-range request as an honest ERROR observation
    # (picked up by the same retry-nudge tracking as any other failed action) rather than
    # silently returning an empty/truncated window.
    followup_prompt = captured_prompts[1]
    assert "ERROR" in followup_prompt
    assert "only has" in followup_prompt


# ---------------------------------------------------------------------------
# Per-turn caching — _fetch_repo_tree_items and _read_file/_fetch_file_content
# were each doing a fresh GitHub API round trip on every single call, even for
# the same path/tree re-requested later in the same investigation (a common
# pattern: search_code points back to a file already opened, or find_file and
# search_literal both need the full tree). Cached in a dict scoped to this one
# tool_agent_node call — never persisted across turns.
# ---------------------------------------------------------------------------

@run_async
async def test_read_repo_file_same_path_twice_only_fetches_once(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64("def get_current_user(): ...")})
    contents_fetch_count = {"n": 0}

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/app.py"):
            contents_fetch_count["n"] += 1
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Read app.py", tool_action="read_repo_file", args={"path": "app.py"}),
        _llm_response(action="query", purpose="Re-check app.py after search_code pointed back to it", tool_action="read_repo_file", args={"path": "app.py"}),
        _llm_response(action="final", answer="It's in app.py."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("where is login handled?"))

    assert contents_fetch_count["n"] == 1


@run_async
async def test_repo_tree_only_fetched_once_across_multiple_actions(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "app.py", "type": "blob", "size": 100, "sha": "sha1"},
    ]})
    tree_fetch_count = {"n": 0}

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            tree_fetch_count["n"] += 1
            return tree_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    # list_repo_tree and find_file both go through _fetch_repo_tree_items — calling both in the
    # same turn should still only hit GitHub once.
    responses = [
        _llm_response(action="query", purpose="List the tree", tool_action="list_repo_tree", args={}),
        _llm_response(action="query", purpose="Now fuzzy-find something", tool_action="find_file", args={"query": "app"}),
        _llm_response(action="final", answer="Found it."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("where's the main app file?"))

    assert tree_fetch_count["n"] == 1


@run_async
async def test_non_admin_can_freely_use_github_and_web_actions(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    class _FakeDDG:
        def results(self, query, max_results=3):
            return [{"title": "Result", "snippet": "Info", "link": "https://example.com"}]

    monkeypatch.setattr(aw, "DuckDuckGoSearchAPIWrapper", _FakeDDG)

    responses = [
        _llm_response(action="query", purpose="Search the web", tool_action="web_search", args={"query": "some topic"}),
        _llm_response(action="final", answer="Found relevant info."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("tell me about some topic"))

    assert "Found relevant info" in result["content_to_format"]


# ---------------------------------------------------------------------------
# Web search reformulation within the unified agent
# ---------------------------------------------------------------------------

@run_async
async def test_web_search_reformulates_after_poor_first_query(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    call_log = []

    class _FakeDDG:
        def results(self, query, max_results=3):
            call_log.append(query)
            if len(call_log) == 1:
                return []
            return [{"title": "Real result", "snippet": "Relevant", "link": "https://example.com/real"}]

    monkeypatch.setattr(aw, "DuckDuckGoSearchAPIWrapper", _FakeDDG)

    responses = [
        _llm_response(action="query", purpose="Narrow search", tool_action="web_search", args={"query": "too specific"}),
        _llm_response(action="query", purpose="Broaden search", tool_action="web_search", args={"query": "broader"}),
        _llm_response(action="final", answer="Found it after broadening."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("some vague question"))

    assert call_log == ["too specific", "broader"]
    assert "Found it after broadening" in result["content_to_format"]


# ---------------------------------------------------------------------------
# The motivating scenario: cross-tool escalation from repo to web
# ---------------------------------------------------------------------------

@run_async
async def test_stack_trace_escalates_from_repo_to_web_search(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64("def connect(): pass  # no obvious bug here")})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/backend/utils/db_utils.py"):
            return file_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    class _FakeDDG:
        def results(self, query, max_results=3):
            return [{
                "title": "pymongo.errors.ServerSelectionTimeoutError - known issue",
                "snippet": "This is caused by an unreachable MongoDB URI; fix by checking network access.",
                "link": "https://stackoverflow.com/questions/12345",
            }]

    monkeypatch.setattr(aw, "DuckDuckGoSearchAPIWrapper", _FakeDDG)

    responses = [
        _llm_response(
            action="query", purpose="Check db_utils.py for connection handling",
            tool_action="read_repo_file", args={"path": "backend/utils/db_utils.py"}
        ),
        _llm_response(
            action="query", purpose="Repo didn't explain the error; search the web for it",
            tool_action="web_search", args={"query": "pymongo.errors.ServerSelectionTimeoutError fix"}
        ),
        _llm_response(
            action="final",
            answer="The repo's connection code looks standard; this is a known pymongo error caused by an unreachable MongoDB URI — check network access to the DB."
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    stack_trace = (
        "pymongo.errors.ServerSelectionTimeoutError: db.example.com:27017: "
        "[Errno 111] Connection refused"
    )
    result = await aw.tool_agent_node(_state(f"I'm getting this error, can you help?\n{stack_trace}"))

    # Both steps actually happened and both are cited in the transparency footer.
    assert "read_repo_file" in result["content_to_format"]
    assert "web_search" in result["content_to_format"]
    assert "unreachable MongoDB URI" in result["content_to_format"]
    assert result["relevance_grade"] == "tool_agent"


# ---------------------------------------------------------------------------
# Attached code still reaches the loop's question, repo resolved first
# ---------------------------------------------------------------------------

@run_async
async def test_attached_code_reaches_the_prompt(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    _setup_github_repo(monkeypatch)

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="Found it.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    attachment = Document(
        page_content="def totally_unique_marker_function(): pass",
        metadata={"source": "user_attachment_summary"},
    )
    await aw.tool_agent_node(_state("does this already exist in the repo?", documents=[attachment]))

    assert any("totally_unique_marker_function" in p for p in captured_prompts)


# ---------------------------------------------------------------------------
# browser_* actions — no admin gate, one session per turn, always closed
# ---------------------------------------------------------------------------

class _FakeBrowserSession:
    """Stands in for backend.services.browser_tool.BrowserSession — these tests only care that
    tool_agent_node constructs/reuses/closes exactly one of these per call; the real session's
    own behavior is covered by test_browser_tool.py."""
    instances = []

    def __init__(self, ws_endpoint, action_timeout_ms):
        self.ws_endpoint = ws_endpoint
        self.action_timeout_ms = action_timeout_ms
        self.live_url = None  # matches BrowserSession's own default; set per-test when needed
        self.close = AsyncMock()
        _FakeBrowserSession.instances.append(self)


@run_async
async def test_browser_actions_available_to_everyone(monkeypatch):
    """Unlike run_mongo_query, there's no is_admin gate on the browser_* actions — a guest
    should see them in the menu too."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _setup_github_repo(monkeypatch)

    captured_prompts = []

    async def fake_ainvoke(prompt):
        captured_prompts.append(prompt)
        return _llm_response(action="final", answer="No conclusive answer.")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("what's on the SAAPP homepage right now?"))

    assert any("browser_navigate" in p and "browser_screenshot" in p for p in captured_prompts)


@run_async
async def test_browser_navigate_dispatches_and_returns_observation(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)

    fake_navigate = AsyncMock(return_value="Navigated to https://sonicassistant.com/ (status 200). Page title: 'Sonic Assistant'")
    monkeypatch.setattr(aw, "browser_navigate", fake_navigate)

    responses = [
        _llm_response(action="query", purpose="Load the homepage", tool_action="browser_navigate", args={"url": "https://sonicassistant.com"}),
        _llm_response(action="final", answer="The homepage loaded fine.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("is our homepage up?"))

    fake_navigate.assert_awaited_once()
    assert fake_navigate.call_args.args[1] == "https://sonicassistant.com"
    assert "Sonic Assistant" in result["content_to_format"]


@run_async
async def test_browser_navigate_emits_live_view_event_once(monkeypatch):
    """The moment a LiveURL becomes available, tool_agent_node should emit exactly one
    browser_live_view custom event (app.py streams it to the frontend as its own SSE event,
    separate from trace_detail) — and never a second time even if browser_navigate runs again
    later in the same turn (e.g. navigating to a second page)."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)

    live_url = "https://production-sfo.browserless.io/live/index.html?i=xyz"

    async def fake_navigate(session, url):
        session.live_url = live_url  # mirrors what the real BrowserSession sets on first connect
        return f"Navigated to {url}."

    monkeypatch.setattr(aw, "browser_navigate", fake_navigate)
    fake_emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", fake_emit)

    responses = [
        _llm_response(action="query", purpose="Load page one", tool_action="browser_navigate", args={"url": "https://example.com/one"}),
        _llm_response(action="query", purpose="Load page two", tool_action="browser_navigate", args={"url": "https://example.com/two"}),
        _llm_response(action="final", answer="Checked both pages.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("check those two pages"))

    live_view_calls = [c for c in fake_emit.call_args_list if c.args[0] == "browser_live_view"]
    assert len(live_view_calls) == 1
    assert live_view_calls[0].args[1] == {"url": live_url}


@run_async
async def test_browser_session_reused_across_two_steps(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value="Navigated."))
    monkeypatch.setattr(aw, "browser_click", AsyncMock(return_value="Clicked."))

    responses = [
        _llm_response(action="query", purpose="Load the page", tool_action="browser_navigate", args={"url": "https://example.com"}),
        _llm_response(action="query", purpose="Click sign in", tool_action="browser_click", args={"text": "Sign in"}),
        _llm_response(action="final", answer="Done.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("go sign in"))

    # _get_browser_session memoizes across steps within the same tool_agent_node call — only
    # one BrowserSession should ever be constructed for these two browser_* steps.
    assert len(_FakeBrowserSession.instances) == 1


@run_async
async def test_browser_session_closed_after_normal_completion(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value="Navigated."))

    responses = [
        _llm_response(action="query", purpose="Load the page", tool_action="browser_navigate", args={"url": "https://example.com"}),
        _llm_response(action="final", answer="Done.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("go check example.com"))

    assert len(_FakeBrowserSession.instances) == 1
    _FakeBrowserSession.instances[0].close.assert_awaited_once()


@run_async
async def test_browser_session_closed_after_clarification_pause(monkeypatch):
    """The finally around run_react_loop must fire on every exit path, not just the happy one —
    a live CDP session can't be checkpointed into paused_clarification, so it has to be closed
    here even though the turn is pausing rather than finishing."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value="Navigated."))

    responses = [
        _llm_response(action="query", purpose="Load the page", tool_action="browser_navigate", args={"url": "https://example.com"}),
        _llm_response(action="clarify", question="Which page on the site did you mean?"),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("check the site"))

    assert result["relevance_grade"] == "needs_clarification"
    assert len(_FakeBrowserSession.instances) == 1
    _FakeBrowserSession.instances[0].close.assert_awaited_once()


@run_async
async def test_browser_navigate_missing_ws_endpoint_returns_error_observation(monkeypatch):
    """A missing BROWSERLESS_WS_ENDPOINT must surface as an honest ERROR observation the loop
    can react to, not a raised exception that crashes the turn."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.delenv("BROWSERLESS_WS_ENDPOINT", raising=False)
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="Load the page", tool_action="browser_navigate", args={"url": "https://example.com"}),
        _llm_response(action="final", answer="Couldn't check the live site.", show_work=True),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("check the site"))

    assert "ERROR" in result["content_to_format"]
    assert "BROWSERLESS_WS_ENDPOINT" in result["content_to_format"]


@run_async
async def test_browser_screenshot_uses_lite_llm(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    _setup_github_repo(monkeypatch)

    fake_screenshot = AsyncMock(return_value="URL: https://example.com/\nA plain page.")
    monkeypatch.setattr(aw, "browser_screenshot", fake_screenshot)
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value="Navigated."))

    responses = [
        _llm_response(action="query", purpose="Load the page", tool_action="browser_navigate", args={"url": "https://example.com"}),
        _llm_response(action="query", purpose="See what it looks like", tool_action="browser_screenshot", args={}),
        _llm_response(action="final", answer="It's a plain page.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    await aw.tool_agent_node(_state("what does example.com look like?"))

    fake_screenshot.assert_awaited_once()
    assert fake_screenshot.call_args.args[1] is aw.lite_llm


# ---------------------------------------------------------------------------
# find_file — local fuzzy matching against the repo tree, closes the gap
# search_code's literal keyword index can't (a colloquial name that shares no
# exact token with the real file, e.g. "navbar" vs menu-navigator.tsx).
# ---------------------------------------------------------------------------

def test_fuzzy_path_score_finds_navbar_in_menu_navigator():
    query_tokens = aw._tokenize_for_fuzzy_match("navbar")
    score = aw._fuzzy_path_score(query_tokens, "local/src/components/menu-navigator.tsx")
    assert score >= aw._FUZZY_MATCH_CUTOFF


def test_fuzzy_path_score_finds_navbar_in_camel_case_filename():
    query_tokens = aw._tokenize_for_fuzzy_match("navbar")
    score = aw._fuzzy_path_score(query_tokens, "local/src/components/MenuNavigator.tsx")
    assert score >= aw._FUZZY_MATCH_CUTOFF


def test_fuzzy_path_score_rejects_unrelated_path():
    query_tokens = aw._tokenize_for_fuzzy_match("navbar")
    score = aw._fuzzy_path_score(query_tokens, "backend/services/ci_test_runner.py")
    assert score < aw._FUZZY_MATCH_CUTOFF


def test_fuzzy_path_score_exact_token_match_scores_perfectly():
    query_tokens = aw._tokenize_for_fuzzy_match("affiliate")
    score = aw._fuzzy_path_score(query_tokens, "backend/utils/isolation_kb_utils.py")
    assert score < aw._FUZZY_MATCH_CUTOFF  # no real "affiliate" token in that path
    score = aw._fuzzy_path_score(query_tokens, "local/src/components/Affiliate.tsx")
    assert score == 1.0


@run_async
async def test_find_file_surfaces_a_near_match_search_code_would_miss(monkeypatch):
    """The exact real-world failure this closes: the user says "navbar", the real file is
    menu-navigator.tsx — zero literal tokens in common, so search_code would find nothing no
    matter how many times it's called, but find_file's fuzzy match against the real repo tree
    surfaces it immediately."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "local/src/components/menu-navigator.tsx", "type": "blob"},
        {"path": "backend/services/ci_test_runner.py", "type": "blob"},
        {"path": "local/src/pages/Chat.tsx", "type": "blob"},
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Fuzzy-find the navbar component", tool_action="find_file", args={"query": "navbar"}),
        _llm_response(action="final", answer="Found it at local/src/components/menu-navigator.tsx."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("I need a navbar change"))

    assert "menu-navigator.tsx" in result["content_to_format"]


@run_async
async def test_find_file_no_query_is_error(monkeypatch):
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="Find it", tool_action="find_file", args={}),
        _llm_response(action="final", answer="Couldn't find it."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("find the thing"))

    assert "ERROR" in result["content_to_format"] or "Couldn't find it" in result["content_to_format"]


# ---------------------------------------------------------------------------
# Visual-inspection directive — the real production failure this closes: asked to
# "inspect the actual page" for a z-index bug, the model did 15 steps of pure repo
# reading and never opened a browser once. A keyword-triggered directive is injected
# into the turn's own question text rather than relying solely on a static system-
# prompt paragraph to out-compete many turns of code-reading momentum.
# ---------------------------------------------------------------------------

def test_mentions_visual_inspection_detects_the_real_production_phrasing():
    assert aw._mentions_visual_inspection(
        "can you inspect the actual page and fix the z-index issue between "
        "the hero-banner and the trace panel?"
    )


def test_mentions_visual_inspection_negative_for_unrelated_question():
    assert not aw._mentions_visual_inspection("how does the memory recall system work?")
    assert not aw._mentions_visual_inspection("can you add a new field to the UserFact model?")


@run_async
async def test_visual_inspection_question_gets_browser_directive_injected(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state(
        "can you inspect the actual page to find the z-index issue between the "
        "hero-banner and trace panel?"
    ))

    assert "browser_navigate" in captured_kwargs["question"]
    assert "cannot be answered from source code alone" in captured_kwargs["question"]


@run_async
async def test_non_visual_question_gets_no_browser_directive(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("how does the goal nudge check-in work?"))

    assert "browser_navigate" not in captured_kwargs["question"]


# ---------------------------------------------------------------------------
# trace_symbol — sorts search_code's hits into write/decide vs. read/pass-through
# sites, so tracing where a value actually comes from doesn't require opening
# every hit by hand (docs/coding-agent-roadmap.md, section 1).
# ---------------------------------------------------------------------------

def test_classify_symbol_line_recognizes_python_definition():
    assert aw._classify_symbol_line(
        "get_accessible_affiliates",
        "def get_accessible_affiliates(username: str, user_directory: dict) -> dict:",
    ) == "write"


def test_classify_symbol_line_recognizes_plain_assignment():
    assert aw._classify_symbol_line(
        "accessible_affiliates",
        "accessible_affiliates = [g for g in user_groups if g not in _NON_KB_ROLE_GROUPS]",
    ) == "write"


def test_classify_symbol_line_recognizes_react_state_setter():
    assert aw._classify_symbol_line(
        "tracePanelPos",
        "const [tracePanelPos, setTracePanelPos] = useState({ x: 0, y: 0 });",
    ) == "write"
    assert aw._classify_symbol_line("tracePanelPos", "setTracePanelPos({ x: 1, y: 2 });") == "write"


def test_classify_symbol_line_recognizes_a_read():
    assert aw._classify_symbol_line(
        "tracePanelPos",
        "transform: `translate3d(${tracePanelPos.x}px, ${tracePanelPos.y}px, 0)`,",
    ) == "read"
    assert aw._classify_symbol_line(
        "get_accessible_affiliates",
        'accessible = get_accessible_affiliates(clerk_id, directory)["accessible_affiliates"]',
    ) == "read"


def test_classify_symbol_line_does_not_confuse_equality_check_with_assignment():
    assert aw._classify_symbol_line("foo", "if foo == bar:") == "read"


@run_async
async def test_trace_symbol_sorts_write_and_read_sites(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    search_resp = _http_response(200, {"items": [
        {
            "path": "backend/utils/isolation_kb_utils.py",
            "text_matches": [{"fragment": "def get_accessible_affiliates(username: str, user_directory: dict) -> dict:"}],
        },
        {
            "path": "app.py",
            "text_matches": [{"fragment": 'accessible = get_accessible_affiliates(clerk_id, directory)["accessible_affiliates"]'}],
        },
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/search/code"):
            return search_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Trace the symbol", tool_action="trace_symbol", args={"symbol": "get_accessible_affiliates"}),
        _llm_response(action="final", answer="It's defined in isolation_kb_utils.py and called from app.py."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("where is get_accessible_affiliates actually decided?"))

    assert "isolation_kb_utils.py" in result["content_to_format"]


@run_async
async def test_trace_symbol_no_query_is_error(monkeypatch):
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="Trace it", tool_action="trace_symbol", args={}),
        _llm_response(action="final", answer="Couldn't trace it."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("trace the thing"))

    assert "ERROR" in result["content_to_format"] or "Couldn't trace it" in result["content_to_format"]


@run_async
async def test_trace_symbol_no_matches(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    search_resp = _http_response(200, {"items": []})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/search/code"):
            return search_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Trace the symbol", tool_action="trace_symbol", args={"symbol": "totallyNonexistentSymbol"}),
        _llm_response(action="query", purpose="Retry with a shorter form", tool_action="trace_symbol", args={"symbol": "NonexistentSymbol"}),
        _llm_response(action="final", answer="Couldn't find it anywhere in the repo."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("trace totallyNonexistentSymbol"))

    assert "Couldn't find it anywhere in the repo." in result["content_to_format"]


# ---------------------------------------------------------------------------
# search_literal — exhaustive content grep, closing the real gap search_code's
# hosted-index approach can't: a real production plan claimed a wrong call site
# for a token it was asked to trace every usage of, because search_code's index
# is capped/lossy by construction. This fetches and greps real blob content
# instead, so "find every occurrence" gets a guaranteed-complete answer.
# ---------------------------------------------------------------------------

@run_async
async def test_search_literal_finds_every_occurrence_across_files(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "backend/services/github_service.py", "type": "blob", "size": 100, "sha": "sha1"},
        {"path": "backend/services/agent_workflow.py", "type": "blob", "size": 100, "sha": "sha2"},
        {"path": "local/src/index.css", "type": "blob", "size": 100, "sha": "sha3"},
    ]})
    blob_contents = {
        "sha1": "import os\ntoken = os.getenv(\"GITHUB_TOKEN\")\n",
        "sha2": "import os\ntoken = os.getenv(\"GITHUB_TOKEN\")\nother_token = os.getenv(\"GITHUB_TOKEN\")\n",
        "sha3": "body { color: red; }\n",
    }

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/git/blobs/" in url:
            sha = url.rsplit("/", 1)[-1]
            return _http_response(200, {"encoding": "base64", "content": _b64(blob_contents[sha])})
        if "/contents/" in url:
            # The architecture map (triggered by this same "find every place ..." phrasing)
            # fetches each .py file's content independently via the Contents API — irrelevant to
            # what this test actually checks (search_literal's own behavior), so any real content
            # works here.
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Find every usage", tool_action="search_literal", args={"term": "GITHUB_TOKEN"}),
        _llm_response(action="final", answer="Found every usage."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("find every place GITHUB_TOKEN is read"))

    assert "github_service.py" in result["content_to_format"] or "Found every usage" in result["content_to_format"]


@run_async
async def test_search_literal_no_term_is_error(monkeypatch):
    _setup_github_repo(monkeypatch)
    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_literal", args={}),
        _llm_response(action="final", answer="Couldn't search."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("search for the thing"))

    assert "ERROR" in result["content_to_format"] or "Couldn't search" in result["content_to_format"]


@run_async
async def test_search_literal_no_matches_says_so_explicitly(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "app.py", "type": "blob", "size": 100, "sha": "sha1"},
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/git/blobs/" in url:
            return _http_response(200, {"encoding": "base64", "content": _b64("nothing interesting here\n")})
        if "/contents/" in url:
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_literal", args={"term": "TOTALLY_NONEXISTENT_VAR"}),
        _llm_response(action="final", answer="Not found anywhere."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("find every place TOTALLY_NONEXISTENT_VAR is read"))

    assert "Not found anywhere." in result["content_to_format"]


@run_async
async def test_search_literal_scans_a_file_that_would_have_exceeded_the_old_per_file_cap(monkeypatch):
    """The exact regression this fix closes (docs/coding-agent-roadmap.md, Sections 12 & 14): a
    file's real content must actually get searched as long as the call's TOTAL scan budget can
    afford it — a single large file (previously permanently excluded by a flat per-file cap
    regardless of the call's actual remaining budget) is no longer excluded by size alone."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    large_size = 500_000  # would have been permanently excluded under the old 200KB-per-file cap
    tree_resp = _http_response(200, {"tree": [
        {"path": "backend/services/agent_workflow.py", "type": "blob", "size": large_size, "sha": "sha1"},
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/git/blobs/" in url:
            return _http_response(200, {"encoding": "base64", "content": _b64("def _format_react_attempts():\n    pass\n")})
        if "/contents/" in url:
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_literal", args={"term": "_format_react_attempts"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("find every occurrence of _format_react_attempts"))

    assert len(captured_prompts) == 2
    assert "backend/services/agent_workflow.py:1: def _format_react_attempts" in captured_prompts[1]
    assert "exceeded the" not in captured_prompts[1]


@run_async
async def test_search_literal_reports_files_not_reached_when_budget_exhausted(monkeypatch):
    """The total scan budget replaced the flat per-file cap, but a genuinely exhausted budget
    must still be disclosed honestly — the files left out are reported as 'not reached this
    call' rather than silently dropped, and explicitly NOT framed as a permanent exclusion."""
    monkeypatch.setattr(aw, "_SEARCH_LITERAL_MAX_TOTAL_SCAN_BYTES", 150)
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "small.py", "type": "blob", "size": 100, "sha": "sha1"},
        {"path": "big.py", "type": "blob", "size": 200, "sha": "sha2"},
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/git/blobs/" in url:
            return _http_response(200, {"encoding": "base64", "content": _b64("nothing interesting here\n")})
        if "/contents/" in url:
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_prompts = []
    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_literal", args={"term": "_format_react_attempts"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("find every occurrence of _format_react_attempts"))

    assert len(captured_prompts) == 2
    assert "not reached this call" in captured_prompts[1]
    assert "big.py" in captured_prompts[1]
    assert "NOT a permanent exclusion" in captured_prompts[1]


@run_async
async def test_search_literal_blob_fetches_run_concurrently(monkeypatch):
    """Proves the parallelization itself, not just correctness: several candidate files' blob
    fetches must overlap in time via asyncio.gather, not run one request at a time — the whole
    point of pointing PR #73's batching primitive at this loop's own fetches."""
    import time

    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_count = 6  # fits in one _SEARCH_LITERAL_FETCH_CONCURRENCY (8) batch
    tree_resp = _http_response(200, {"tree": [
        {"path": f"file{i}.py", "type": "blob", "size": 100, "sha": f"sha{i}"} for i in range(file_count)
    ]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/git/blobs/" in url:
            time.sleep(0.2)
            return _http_response(200, {"encoding": "base64", "content": _b64("nothing interesting here\n")})
        if "/contents/" in url:
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Search", tool_action="search_literal", args={"term": "nonexistent"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake_ainvoke)

    start = time.monotonic()
    await aw.tool_agent_node(_state("find every occurrence of nonexistent"))
    elapsed = time.monotonic() - start

    # 6 sequential 0.2s blob fetches would take >= 1.2s; real concurrency (all 6 fit in one
    # gather batch) keeps this well under that.
    assert elapsed < 1.0


# ---------------------------------------------------------------------------
# Local repo checkout (backend/services/repo_checkout.py) — replaces the per-file GitHub API
# calls behind list_repo_tree/read_repo_file/find_file/search_literal with one tarball fetch +
# local disk reads, falling back to the existing API path on any failure. Contract-preservation
# is the load-bearing requirement here: react_loop.py's retry-nudge/truncation-detection logic
# pattern-matches on these functions' exact return shapes, so a checkout-backed call must produce
# output indistinguishable from the API-backed one.
# ---------------------------------------------------------------------------

def _make_fake_checkout(tmp_path, files: dict) -> "rc.CheckoutHandle":
    """Builds a real local directory (files: {relative_path: content}) and wraps it in a real
    CheckoutHandle, exactly like a real extracted tarball would look — root is a real directory
    one level inside tempdir, matching fetch_and_extract_checkout's own unwrapping contract."""
    tempdir = tmp_path / "saapp_checkout_fake"
    root = tempdir / "owner-repo-abc123"
    root.mkdir(parents=True)
    for rel_path, content in files.items():
        full_path = root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
    return rc.CheckoutHandle(root=str(root), tempdir=str(tempdir))


@run_async
async def test_read_repo_file_identical_whether_checkout_or_api_backed(monkeypatch, tmp_path):
    handle = _make_fake_checkout(tmp_path, {"app.py": "import os\ntoken = os.getenv('X')\n"})
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)

    repo_resp = _http_response(200, {"default_branch": "main"})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        raise AssertionError(f"Unexpected GET (should have used the local checkout instead): {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Read app.py", tool_action="read_repo_file", args={"path": "app.py"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    captured_prompts = []

    async def capturing_ainvoke(prompt):
        captured_prompts.append(prompt)
        return await fake_ainvoke(prompt)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", capturing_ainvoke)

    await aw.tool_agent_node(_state("what does app.py do?"))

    # Same "URL: ...\n<content>" shape read_repo_file always produces from the API path.
    assert any("import os" in p and "token = os.getenv" in p for p in captured_prompts)


@run_async
async def test_read_repo_file_rejects_a_path_that_escapes_the_checkout(monkeypatch, tmp_path):
    """A model-supplied path is not inherently trustworthy the way the old API-only design was
    (GitHub's Contents API just 404s on a '../' path, never touching this server's own disk) —
    a local read must validate containment explicitly."""
    handle = _make_fake_checkout(tmp_path, {"app.py": "import os\n"})
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="Read a suspicious path", tool_action="read_repo_file", args={"path": "../../../../etc/passwd"}),
        _llm_response(action="final", answer="done"),
    ]

    captured_prompts = []

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        captured_prompts.append(prompt)
        return responses[len(captured_prompts) - 1]

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("read ../../../../etc/passwd"))

    assert any("not a valid path in this repo" in p for p in captured_prompts)


@run_async
async def test_search_literal_finds_matches_via_local_checkout(monkeypatch, tmp_path):
    handle = _make_fake_checkout(tmp_path, {
        "backend/services/github_service.py": "import os\ntoken = os.getenv(\"GITHUB_TOKEN\")\n",
        "local/src/index.css": "body { color: red; }\n",
    })
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)

    repo_resp = _http_response(200, {"default_branch": "main"})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/contents/" in url:
            # The architecture map's own separate fetch, unrelated to search_literal itself.
            return _http_response(200, {"content": _b64("import os\n")})
        raise AssertionError(f"Unexpected GET (should have used the local checkout instead): {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Find every usage", tool_action="search_literal", args={"term": "GITHUB_TOKEN"}),
        _llm_response(action="final", answer="Found it."),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake_ainvoke)

    result = await aw.tool_agent_node(_state("find every place GITHUB_TOKEN is read"))

    assert "Found it" in result["content_to_format"]


@run_async
async def test_checkout_fetch_failure_falls_back_to_the_api_path(monkeypatch):
    """The graceful-degrade contract: any failure fetching/extracting the checkout must be
    invisible to the rest of the turn — the existing API path runs exactly as it does today."""
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", Mock(side_effect=rc.RepoCheckoutError("tarball download failed (500)")))
    # A non-empty tree — an empty list_repo_tree result would trip react_loop's own unrelated
    # empty-result retry-nudge, which isn't what this test is checking.
    _setup_github_repo(monkeypatch, tree_items=[{"path": "app.py", "type": "blob", "size": 10, "sha": "abc"}])

    responses = [
        _llm_response(action="query", purpose="List the repo", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="done", show_work=False),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    result = await aw.tool_agent_node(_state("list the repo"))

    assert result["content_to_format"] == "done"


@run_async
async def test_checkout_is_only_attempted_once_per_turn_even_after_failure(monkeypatch):
    """A failed checkout must not retry on every single subsequent tool call this turn — it
    fails closed once, and every other action this turn just uses the API path directly."""
    fetch_mock = Mock(side_effect=rc.RepoCheckoutError("boom"))
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", fetch_mock)
    _setup_github_repo(monkeypatch, tree_items=[])

    responses = [
        _llm_response(action="query", purpose="List the repo", tool_action="list_repo_tree", args={}),
        _llm_response(action="query", purpose="Find a file", tool_action="find_file", args={"query": "agent workflow"}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    await aw.tool_agent_node(_state("investigate the repo"))

    fetch_mock.assert_called_once()


@run_async
async def test_get_checkout_is_race_safe_under_batched_concurrent_actions(monkeypatch, tmp_path):
    """Regression: a batched 'queries' step (see
    test_tool_agent_node_executes_a_real_batch_of_independent_reads) runs several actions
    concurrently via asyncio.gather, each on its own thread via asyncio.to_thread. Without a lock
    around the check-then-fetch in _get_checkout, two threads could both see attempted=False
    before either set it, each launching its own full tarball download of the same repo at once —
    observed in production as simultaneous codeload.github.com downloads saturating the user's
    connection. The fetch must happen exactly once no matter how many concurrent actions need it."""
    handle = _make_fake_checkout(tmp_path, {"a.py": "A_CONTENT\n", "b.py": "B_CONTENT\n"})
    fetch_calls = []

    def slow_fetch(*a, **k):
        fetch_calls.append(1)
        time.sleep(0.05)
        return handle

    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", slow_fetch)
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="Read both independent files at once", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
            {"tool_action": "read_repo_file", "args": {"path": "b.py"}, "purpose": "Read b.py"},
        ]),
        _llm_response(action="final", answer="Both files read: A_CONTENT and B_CONTENT."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("compare a.py and b.py"))

    assert len(fetch_calls) == 1
    assert "A_CONTENT" in result["content_to_format"]
    assert "B_CONTENT" in result["content_to_format"]


@run_async
async def test_checkout_is_cleaned_up_after_a_successful_turn(monkeypatch, tmp_path):
    handle = _make_fake_checkout(tmp_path, {"app.py": "import os\n"})
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)
    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(action="query", purpose="List the repo", tool_action="list_repo_tree", args={}),
        _llm_response(action="final", answer="done"),
    ]

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        return responses.pop(0)

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake_ainvoke)

    assert os.path.exists(handle.tempdir)
    await aw.tool_agent_node(_state("list the repo"))
    assert not os.path.exists(handle.tempdir)


@run_async
async def test_checkout_is_cleaned_up_even_when_the_turn_raises_mid_loop(monkeypatch, tmp_path):
    """Mirrors the same guarantee browser_session_holder's cleanup already relies on
    (run_react_loop wrapped in try/finally) — but asserted directly here rather than just
    implicitly trusted, per the heavier test bar this feature needs. Runs one real tool call
    first (establishing the checkout via the real _get_checkout lazily), then simulates the
    loop crashing on its next step — the checkout must still be cleaned up."""
    handle = _make_fake_checkout(tmp_path, {"app.py": "import os\n"})
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)
    _setup_github_repo(monkeypatch, tree_items=[])

    async def fake_run_react_loop(**kwargs):
        # Exercises one real tool call through the real `act` callback (establishing the
        # checkout via the real, lazy _get_checkout), then simulates the loop itself crashing —
        # an exception that escapes run_react_loop uncaught, the exact shape tool_agent_node's
        # own outer try/finally exists to survive.
        await kwargs["act"]({"tool_action": "list_repo_tree", "args": {}, "purpose": "test"})
        raise RuntimeError("simulated mid-loop crash")

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    assert os.path.exists(handle.tempdir)
    with pytest.raises(RuntimeError):
        await aw.tool_agent_node(_state("list the repo"))
    assert not os.path.exists(handle.tempdir)


@run_async
async def test_two_sequential_turns_each_get_their_own_isolated_checkout(monkeypatch, tmp_path):
    handle_a = _make_fake_checkout(tmp_path / "a", {"app.py": "# repo a\n"})
    handle_b = _make_fake_checkout(tmp_path / "b", {"app.py": "# repo b\n"})
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    _setup_github_repo(monkeypatch)

    def _responses():
        return [
            _llm_response(action="query", purpose="List the repo", tool_action="list_repo_tree", args={}),
            _llm_response(action="final", answer="done"),
        ]

    def _make_fake_ainvoke(responses):
        async def fake_ainvoke(prompt):
            if GROUNDING_CHECK_MARKER in prompt:
                return _llm_response(grounded=True, unsupported_claims=[])
            return responses.pop(0)
        return fake_ainvoke

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle_a)
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _make_fake_ainvoke(_responses()))
    await aw.tool_agent_node(_state("list the repo"))
    assert not os.path.exists(handle_a.tempdir)  # cleaned up before the next turn even starts

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle_b)
    monkeypatch.setattr(aw.lite_llm, "ainvoke", _make_fake_ainvoke(_responses()))
    await aw.tool_agent_node(_state("list the repo"))
    assert not os.path.exists(handle_b.tempdir)

    # Neither turn's checkout ever leaked into the other's.
    assert handle_a.tempdir != handle_b.tempdir


# ---------------------------------------------------------------------------
# Audit-style task detection — a real production plan (per-user GitHub token
# refactor) fabricated a call site because the default 7-step budget didn't
# leave room to confirm every candidate with trace_symbol/search_literal
# before finalizing. This detects that task shape and grants it deep_thinking's
# larger step/nudge budget regardless of whether deep_thinking is actually on.
# ---------------------------------------------------------------------------

def test_is_audit_style_task_detects_the_real_production_phrasing():
    assert aw._is_audit_style_task(
        "scan the repo and create a plan to turn the current github api token "
        "connection to be per user instead of global. tell me every file we need to touch"
    )


def test_is_audit_style_task_detects_the_verbatim_original_failing_prompt():
    # The literal prompt (typo included) that produced the fabricated per-user-token plan —
    # verified against real phrasing rather than just phrasing I invented for the test above.
    assert aw._is_audit_style_task(
        "your job, scan the repo and create a plan to turn the gurrent github api token "
        "connection to be per user instead of global via an env var. tell me every file we "
        "need to touch"
    )


def test_is_audit_style_task_detects_how_many_files_phrasing():
    assert aw._is_audit_style_task("how many files touch the checkpoint retention logic")
    assert aw._is_audit_style_task("which files touch the checkpoint retention logic")


def test_is_audit_style_task_negative_for_ordinary_question():
    assert not aw._is_audit_style_task("where is the login flow implemented?")
    assert not aw._is_audit_style_task("can you fix this one bug in Chat.tsx?")
    # Deliberately NOT treated as audit-style: bare "refactor" with no completeness language
    # would bump the budget for small, single-file refactors too, cutting against the "zero
    # added cost for normal turns" goal — a real precision/recall tradeoff, not an oversight.
    assert not aw._is_audit_style_task("can you refactor the auth module to support multiple providers")


@run_async
async def test_audit_style_task_gets_deep_iteration_budget(monkeypatch):
    _setup_github_repo(monkeypatch, tree_items=[])
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state(
        "scan the repo and plan a refactor — tell me every file we need to touch"
    ))

    assert captured_kwargs["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS_DEEP


@run_async
async def test_ordinary_task_keeps_normal_iteration_budget(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("where is the login flow implemented?"))

    assert captured_kwargs["max_iterations"] == aw.TOOL_AGENT_MAX_ITERATIONS


# ---------------------------------------------------------------------------
# Architecture map — an auto-injected, repo-wide internal-import graph for
# audit-style tasks (docs/coding-agent-roadmap.md, Section 4c). Closes the real
# gap behind the fabricated per-user-token plan: the model shouldn't have to
# correctly guess to call an exhaustive tool for "every place X is used" — the
# answer should already be in front of it before step 1.
# ---------------------------------------------------------------------------

def test_extract_python_imports_plain_and_from_imports():
    content = "import os\nimport json as j\nfrom backend.services import github_service\n"
    imports = aw._extract_python_imports(content)
    assert "os" in imports
    assert "json" in imports
    assert "backend.services" in imports


def test_extract_python_imports_relative_import_keeps_dots():
    content = "from . import utils\nfrom ..services import agent_workflow\n"
    imports = aw._extract_python_imports(content)
    assert ".utils" in imports
    assert "..services" in imports


def test_extract_python_imports_syntax_error_returns_empty_list():
    assert aw._extract_python_imports("def broken(:\n    pass") == []


def test_extract_js_imports_covers_default_bare_and_require():
    content = (
        "import React from 'react';\n"
        "import '../styles/index.css';\n"
        "import { KnowledgeBase } from '../api';\n"
        "const fs = require('fs');\n"
    )
    imports = aw._extract_js_imports(content)
    assert "react" in imports
    assert "../styles/index.css" in imports
    assert "../api" in imports
    assert "fs" in imports


def test_is_internal_python_import_relative_always_internal():
    assert aw._is_internal_python_import(".utils", {"backend"})
    assert aw._is_internal_python_import("..services.agent_workflow", {"backend"})


def test_is_internal_python_import_matches_real_top_level_segment():
    assert aw._is_internal_python_import("backend.services.agent_workflow", {"backend", "local"})
    assert not aw._is_internal_python_import("requests", {"backend", "local"})


def test_repo_top_level_segments_includes_dirs_and_root_file_stems():
    tree_items = [
        {"path": "backend/services/agent_workflow.py"},
        {"path": "local/src/pages/Chat.tsx"},
        {"path": "app.py"},
    ]
    segments = aw._repo_top_level_segments(tree_items)
    assert segments == {"backend", "local", "app.py", "app"}


def test_import_scope_key_disambiguates_relative_imports_by_directory():
    # The actual bug this guards: the bare string '.utils' is ambiguous repo-wide — it resolves
    # relative to whichever file imports it, so the same string in two different directories
    # names two completely different real modules.
    key_a = aw._import_scope_key("backend/services/agent_workflow.py", ".utils")
    key_b = aw._import_scope_key("frontend/components/Foo.tsx", ".utils")
    assert key_a != key_b
    assert key_a == "backend/services::.utils"
    assert key_b == "frontend/components::.utils"


def test_import_scope_key_same_directory_same_key():
    # Two files in the SAME directory importing the same relative module really do refer to the
    # same real target — must still collapse into one reverse-map entry, not over-disambiguate.
    key_a = aw._import_scope_key("backend/services/agent_workflow.py", ".utils")
    key_b = aw._import_scope_key("backend/services/react_loop.py", ".utils")
    assert key_a == key_b


def test_import_scope_key_leaves_absolute_imports_unprefixed():
    # An absolute import (a real top-level package name) has no such ambiguity — it's the exact
    # same output as before this fix, so the normal (non-relative) case stays just as readable.
    assert aw._import_scope_key("app.py", "backend.services.github_service") == "backend.services.github_service"


def test_import_scope_key_handles_root_level_importing_file():
    assert aw._import_scope_key("app.py", ".utils") == "(repo root)::.utils"


def test_build_architecture_map_builds_forward_and_reverse_graph():
    tree_items = [
        {"path": "app.py", "size": 10, "sha": "s1"},
        {"path": "backend/services/github_service.py", "size": 10, "sha": "s2"},
    ]
    file_contents = {
        "app.py": "from backend.services.github_service import process_pr_summary\n",
        "backend/services/github_service.py": "import os\n",
    }

    def fake_fetch(path):
        return file_contents[path], None

    result = aw._build_architecture_map(tree_items, fake_fetch)

    assert "app.py -> backend.services.github_service" in result
    assert "backend.services.github_service <- imported by: app.py" in result
    # External/stdlib imports (os) must not pollute the reverse map with noise.
    assert " os <-" not in result


def test_build_architecture_map_does_not_conflate_same_named_relative_imports_across_directories():
    """The actual regression this guards (docs/coding-agent-roadmap.md, Section 16): two files in
    completely different directories both write `from .utils import x` — before the fix, the
    reverse map's bare-string key would have merged them into one 'imported by: both files' entry
    even though each one's `.utils` names a totally different real module. Each directory's
    `.utils` must land in its own separate reverse-map entry, naming only its own real importer."""
    tree_items = [
        {"path": "backend/services/agent_workflow.py", "size": 10, "sha": "s1"},
        {"path": "backend/services/utils.py", "size": 10, "sha": "s2"},
        {"path": "frontend/components/Foo.tsx", "size": 10, "sha": "s3"},
        {"path": "frontend/components/utils.ts", "size": 10, "sha": "s4"},
    ]
    file_contents = {
        "backend/services/agent_workflow.py": "from .utils import helper\n",
        "backend/services/utils.py": "import os\n",
        "frontend/components/Foo.tsx": "import { helper } from './utils';\n",
        "frontend/components/utils.ts": "export function helper() {}\n",
    }

    def fake_fetch(path):
        return file_contents[path], None

    result = aw._build_architecture_map(tree_items, fake_fetch)

    # Each directory's ".utils"/"./utils" gets its OWN reverse-map entry, naming only its own
    # real importer — not merged with the unrelated other directory's same-named import.
    assert "backend/services::.utils <- imported by: backend/services/agent_workflow.py" in result
    assert "frontend/components::./utils <- imported by: frontend/components/Foo.tsx" in result
    # The bug this guards: neither entry lists the OTHER directory's importer.
    assert "backend/services::.utils <- imported by: backend/services/agent_workflow.py, frontend" not in result
    assert "frontend/components::./utils <- imported by: frontend/components/Foo.tsx, backend" not in result


def test_build_architecture_map_skips_files_with_no_internal_imports():
    tree_items = [{"path": "app.py", "size": 10, "sha": "s1"}]

    def fake_fetch(path):
        return "import os\nimport requests\n", None

    assert aw._build_architecture_map(tree_items, fake_fetch) == ""


def test_build_architecture_map_skips_fetch_errors_without_raising():
    tree_items = [{"path": "app.py", "size": 10, "sha": "s1"}]

    def fake_fetch(path):
        return None, "ERROR: could not fetch app.py (404)"

    assert aw._build_architecture_map(tree_items, fake_fetch) == ""


# ---------------------------------------------------------------------------
# README-first context for audit-style tasks (Section 17) — real documented project context
# injected before the more tactical import-graph/search-tool guidance, same "inject it, don't
# rely on the model remembering to seek it out" reasoning as the architecture map above.
# ---------------------------------------------------------------------------

def test_find_readme_path_finds_root_level_readme():
    tree_items = [{"path": "backend/README.md"}, {"path": "README.md"}, {"path": "app.py"}]
    assert aw._find_readme_path(tree_items) == "README.md"


def test_find_readme_path_ignores_nested_readmes():
    # Root-level only, deliberately — a nested README (e.g. a subpackage's own) isn't the
    # whole-project context this exists to surface.
    tree_items = [{"path": "backend/services/README.md"}, {"path": "app.py"}]
    assert aw._find_readme_path(tree_items) is None


def test_find_readme_path_none_when_no_readme_present():
    tree_items = [{"path": "app.py"}, {"path": "backend/services/agent_workflow.py"}]
    assert aw._find_readme_path(tree_items) is None


def test_build_readme_context_includes_real_content():
    tree_items = [{"path": "README.md"}]

    def fake_fetch(path):
        return "# SAAPP\n\nA LangGraph-based RAG chatbot.\n", None

    result = aw._build_readme_context(tree_items, fake_fetch)
    assert "README.md" in result
    assert "A LangGraph-based RAG chatbot." in result


def test_build_readme_context_truncates_long_readmes():
    tree_items = [{"path": "README.md"}]
    long_content = "x" * (aw._README_MAX_CHARS + 500)

    def fake_fetch(path):
        return long_content, None

    result = aw._build_readme_context(tree_items, fake_fetch)
    assert "truncated" in result
    assert len(result) < len(long_content) + 500  # genuinely cut short, not the whole thing


def test_build_readme_context_empty_when_no_readme_in_tree():
    assert aw._build_readme_context([{"path": "app.py"}], lambda path: ("unused", None)) == ""


def test_build_readme_context_empty_on_fetch_error():
    tree_items = [{"path": "README.md"}]

    def fake_fetch(path):
        return None, "ERROR: could not fetch README.md (404)"

    assert aw._build_readme_context(tree_items, fake_fetch) == ""


def test_build_readme_context_empty_for_blank_readme():
    tree_items = [{"path": "README.md"}]
    assert aw._build_readme_context(tree_items, lambda path: ("   \n", None)) == ""


@run_async
async def test_audit_task_readme_is_injected_before_the_search_nudge(monkeypatch):
    """Full regression test through the real tool_agent_node path: a repo with a root README
    must have its real content injected into architecture_map, ordered before the search_literal
    nudge and the import graph — README-first, matching the real reasoning order."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "README.md", "type": "blob", "size": 10, "sha": "s0"},
        {"path": "app.py", "type": "blob", "size": 10, "sha": "s1"},
    ]})
    file_contents = {
        "README.md": "# SAAPP\n\nA LangGraph-based RAG chatbot.\n",
        "app.py": "import os\n",
    }

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/contents/" in url:
            path = url.split("/contents/", 1)[1]
            return _http_response(200, {"content": _b64(file_contents[path])})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("scan the repo — tell me every file that touches process_pr_summary"))

    architecture_map = captured_kwargs["architecture_map"]
    assert "A LangGraph-based RAG chatbot." in architecture_map
    assert architecture_map.index("A LangGraph-based RAG chatbot.") < architecture_map.index("AUDIT TASK DETECTED")


@run_async
async def test_audit_task_gets_architecture_map_injected_into_prompt(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_resp = _http_response(200, {"tree": [
        {"path": "app.py", "type": "blob", "size": 10, "sha": "s1"},
        {"path": "backend/services/github_service.py", "type": "blob", "size": 10, "sha": "s2"},
    ]})
    file_contents = {
        "app.py": "from backend.services.github_service import process_pr_summary\n",
        "backend/services/github_service.py": "import os\n",
    }

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_resp
        if "/contents/" in url:
            path = url.split("/contents/", 1)[1]
            return _http_response(200, {"content": _b64(file_contents[path])})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("scan the repo — tell me every file that touches process_pr_summary"))

    assert "backend.services.github_service <- imported by: app.py" in captured_kwargs["architecture_map"]


@run_async
async def test_ordinary_task_gets_no_architecture_map(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("where is the login flow implemented?"))

    assert captured_kwargs["architecture_map"] == ""


@run_async
async def test_audit_task_gets_search_literal_nudge_even_with_no_internal_imports(monkeypatch):
    # Real gap found by reviewing (and rejecting) a self-drive attempt at this fix: search_literal
    # is opt-in and the model reaches for the lossier search_code out of habit even on audit-style
    # tasks, despite prose guidance already saying not to (docs/coding-agent-roadmap.md, Section
    # 4c/6). The nudge must not depend on the architecture map itself finding anything.
    _setup_github_repo(monkeypatch, tree_items=[])
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("audit the repo — find every place get_db is used"))

    assert "search_literal" in captured_kwargs["architecture_map"]
    assert "search_code" in captured_kwargs["architecture_map"]


@run_async
async def test_audit_task_gets_search_literal_nudge_even_if_tree_fetch_fails(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})
    tree_error_resp = _http_response(404, text="not found")

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if "/git/trees/" in url:
            return tree_error_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])

    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("audit the repo — find every place get_db is used"))

    assert "search_literal" in captured_kwargs["architecture_map"]


# ---------------------------------------------------------------------------
# Conversation history threaded into TOOL_AGENT_PROMPT — the real failure this
# closes: a multi-turn task's actual instruction lived a few messages back, and
# by the time the model finally acted on a short confirming reply ("yes you
# do...."), that turn's prompt contained nothing but that one line — no way to
# recover what it was supposed to check once it acted. See
# docs/coding-agent-roadmap.md.
# ---------------------------------------------------------------------------

@run_async
async def test_history_is_threaded_into_tool_agent_prompt(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    state = {
        "username": "jack",
        "messages": [
            HumanMessage(content="open a browser to btyfitness.app and verify you can click the chat widget icon"),
            AIMessage(content="I don't think I have a live browser tool for that."),
            HumanMessage(content="yes you do...."),
        ],
        "documents": [],
    }
    await aw.tool_agent_node(state)

    assert "click the chat widget icon" in captured_kwargs["prompt_template"]


@run_async
async def test_coding_preferences_are_threaded_into_tool_agent_prompt(monkeypatch):
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(
        aw, "fetch_coding_preferences",
        lambda username: "\n\nKNOWN CODING PREFERENCES (...):\n- Never use --no-verify.\n",
    )
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("fix the failing test"))

    assert "KNOWN CODING PREFERENCES" in captured_kwargs["prompt_template"]
    assert "Never use --no-verify." in captured_kwargs["prompt_template"]


@run_async
async def test_no_coding_preferences_leaves_no_placeholder_artifact(monkeypatch):
    """When there's nothing to inject, the {coding_preferences} placeholder must actually be
    replaced with the empty string — not left as a literal unresolved placeholder in the prompt
    the model sees."""
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "fetch_coding_preferences", lambda username: "")
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("fix the failing test"))

    assert "{coding_preferences}" not in captured_kwargs["prompt_template"]


@run_async
async def test_history_is_capped_at_max_messages(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    old_messages = [HumanMessage(content=f"ancient message {i} — should be dropped") for i in range(20)]
    state = {
        "username": "jack",
        "messages": old_messages + [
            HumanMessage(content="recent message — should be kept"),
            HumanMessage(content="what does this repo do?"),
        ],
        "documents": [],
    }
    await aw.tool_agent_node(state)

    assert "recent message — should be kept" in captured_kwargs["prompt_template"]
    assert "ancient message 0 — should be dropped" not in captured_kwargs["prompt_template"]


@run_async
async def test_history_fallback_text_on_first_message(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("what does this repo do?"))

    assert "(no prior messages this conversation)" in captured_kwargs["prompt_template"]


# ---------------------------------------------------------------------------
# capability_denial_watchlist wiring — confirms TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST
# is actually passed through, and end-to-end catches a real denial against the REAL
# menu tool_agent_node builds (not a synthetic test template).
# ---------------------------------------------------------------------------

@run_async
async def test_capability_denial_watchlist_passed_to_react_loop(monkeypatch):
    _setup_github_repo(monkeypatch)
    captured_kwargs = {}

    async def fake_run_react_loop(**kwargs):
        captured_kwargs.update(kwargs)
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("what does this repo do?"))

    assert captured_kwargs["capability_denial_watchlist"] is aw.TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST


@run_async
async def test_browser_capability_denial_rejected_end_to_end(monkeypatch):
    """Reproduces the real production trace end-to-end, through tool_agent_node's actual menu
    construction (browser_navigate is always available, not admin-gated) — a confident denial
    of browser access gets rejected once, then a real browser_navigate call is accepted."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("BROWSERLESS_WS_ENDPOINT", "wss://fake-endpoint")
    _setup_github_repo(monkeypatch)
    _FakeBrowserSession.instances = []
    monkeypatch.setattr(aw, "BrowserSession", _FakeBrowserSession)
    monkeypatch.setattr(aw, "browser_navigate", AsyncMock(return_value="Navigated to https://btyfitness.app"))

    responses = [
        _llm_response(
            action="final",
            answer="I don't actually have a live browser tool or sandbox execution environment right now.",
        ),
        _llm_response(
            action="query", purpose="Actually check the live site", tool_action="browser_navigate",
            args={"url": "https://btyfitness.app"},
        ),
        _llm_response(action="final", answer="Confirmed — the widget is there.", show_work=False),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("open a browser to btyfitness.app and check the widget"))

    assert "Confirmed" in result["content_to_format"]


# ---------------------------------------------------------------------------
# Batching ("queries") end-to-end through the real tool_agent_node — confirms
# the prompt splicing ({batchable_actions}) and the TOOL_AGENT_BATCHABLE_ACTIONS
# wiring work together for real, not just against the generic run_react_loop
# directly (see test_react_loop_retry_enforcement.py for the deeper mechanics).
# ---------------------------------------------------------------------------

@run_async
async def test_tool_agent_node_executes_a_real_batch_of_independent_reads(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        if url.endswith("/contents/a.py"):
            return _http_response(200, {"content": _b64("A_CONTENT")})
        if url.endswith("/contents/b.py"):
            return _http_response(200, {"content": _b64("B_CONTENT")})
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Read both independent files at once", queries=[
            {"tool_action": "read_repo_file", "args": {"path": "a.py"}, "purpose": "Read a.py"},
            {"tool_action": "read_repo_file", "args": {"path": "b.py"}, "purpose": "Read b.py"},
        ]),
        _llm_response(action="final", answer="Both files read: A_CONTENT and B_CONTENT."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("compare a.py and b.py"))

    assert "A_CONTENT" in result["content_to_format"]
    assert "B_CONTENT" in result["content_to_format"]


def test_tool_agent_prompt_documents_the_real_batchable_actions():
    # The prompt's {batchable_actions} placeholder must actually get replaced with the real
    # TOOL_AGENT_BATCHABLE_ACTIONS constant, not left as a literal unresolved placeholder (which
    # would either crash prompt_template.format() or silently show the model a broken schema).
    assert "run_mongo_query" not in aw.TOOL_AGENT_BATCHABLE_ACTIONS
    assert "run_snippet" not in aw.TOOL_AGENT_BATCHABLE_ACTIONS
    batchable_text = ", ".join(sorted(aw.TOOL_AGENT_BATCHABLE_ACTIONS))
    filled = aw.TOOL_AGENT_PROMPT.replace("{batchable_actions}", batchable_text)
    assert "{batchable_actions}" not in filled
    assert "read_repo_file" in filled
    assert batchable_text in filled
