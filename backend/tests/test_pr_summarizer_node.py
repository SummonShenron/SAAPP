import asyncio
import functools
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _http_response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_data or {})
    resp.text = text
    return resp


@run_async
async def test_pr_summarizer_resolves_repo_from_an_earlier_correction_turn(monkeypatch):
    """Regression: the old behavior joined ALL history into one string and searched once,
    which returns the FIRST (earliest) repo mention, not the most recent — backwards for a
    "no wait, I meant a different repo" correction. This must now resolve to the later one."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    files_resp = _http_response(200, [{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@"}])
    pr_direct_resp = _http_response(200, {"number": 3})

    requested_urls = []

    def fake_requests_get(url, headers=None, params=None):
        requested_urls.append(url)
        if "/repos/owner/second-repo/pulls/3" in url and "files" not in url:
            return pr_direct_resp
        if url.endswith("/repos/owner/second-repo/pulls/3/files"):
            return files_resp
        return _http_response(404, {}, "not found")

    monkeypatch.setattr(aw.requests, "get", fake_requests_get)
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=SimpleNamespace(content="Looks good.")))

    state = {
        "username": "jack",
        "messages": [
            HumanMessage(content="look at owner/first-repo"),
            HumanMessage(content="actually never mind, look at owner/second-repo instead"),
            HumanMessage(content="review PR #3"),
        ],
    }

    await aw.pr_summarizer_node(state)

    assert any("owner/second-repo" in url for url in requested_urls)
    assert not any("owner/first-repo" in url for url in requested_urls)


@run_async
async def test_pr_summarizer_resolves_pr_number_from_an_earlier_turn(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    files_resp = _http_response(200, [{"filename": "app.py", "status": "modified", "patch": "@@ -1 +1 @@"}])
    pr_direct_resp = _http_response(200, {"number": 7})

    requested_urls = []

    def fake_requests_get(url, headers=None, params=None):
        requested_urls.append(url)
        if url.endswith("/repos/SummonShenron/SAAPP/pulls/7") :
            return pr_direct_resp
        if url.endswith("/repos/SummonShenron/SAAPP/pulls/7/files"):
            return files_resp
        return _http_response(404, {}, "not found")

    monkeypatch.setattr(aw.requests, "get", fake_requests_get)
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(return_value=SimpleNamespace(content="Looks good.")))

    state = {
        "username": "jack",
        "messages": [
            HumanMessage(content="review PR #7"),
            HumanMessage(content="any concerns with it?"),
        ],
    }

    result = await aw.pr_summarizer_node(state)

    assert any("/pulls/7/files" in url for url in requested_urls)
    assert "Could not locate" not in result.get("content_to_format", "")
