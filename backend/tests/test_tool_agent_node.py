import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw


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
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    repo_resp = _http_response(200, {"default_branch": "main"})

    def fake_get(url, headers=None, params=None):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return repo_resp
        raise AssertionError(f"Unexpected GET: {url}")

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    return fake_get


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
async def test_non_admin_action_menu_never_includes_mongo(monkeypatch):
    """The actual security property: non-admins shouldn't even see run_mongo_query as an
    option, not just be rejected if they somehow ask for it."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: _http_response(200, {"default_branch": "main"}))
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
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: _http_response(200, {"default_branch": "main"}))
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
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    responses = [
        _llm_response(action="query", purpose="Try mongo anyway", tool_action="run_mongo_query", args={"code": "result = 1"}),
        _llm_response(action="final", answer="Could not run that."),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state())

    assert "not authorized" in result["content_to_format"].lower()


# ---------------------------------------------------------------------------
# Mongo action via the unified agent
# ---------------------------------------------------------------------------

@run_async
async def test_mongo_action_correct_collection_first_try(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    _setup_github_repo(monkeypatch)
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
        lambda url, headers=None, params=None: (
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
        lambda url, headers=None, params=None: (
            _http_response(200, {"default_branch": "main"}) if url.endswith("/repos/SummonShenron/SAAPP")
            else _http_response(200, {"tree": [{"path": "app.py", "type": "blob"}]})
        ),
    )

    result = await aw.tool_agent_node(_state("can you verify the test suite covers this?"))

    assert "Here's what I verified." in result["content_to_format"]
    assert "Step 1" in result["content_to_format"]
    assert "Check the tests directory" in result["content_to_format"]


@run_async
async def test_mongo_unsafe_write_triggers_approval(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    _setup_github_repo(monkeypatch)
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

    def fake_get(url, headers=None, params=None):
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

    def fake_get(url, headers=None, params=None):
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
