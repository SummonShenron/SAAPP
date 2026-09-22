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
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    _setup_github_repo(monkeypatch)
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
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    _setup_github_repo(monkeypatch)
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

    def fake_get(url, headers=None, params=None):
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

    def fake_get(url, headers=None, params=None):
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

    def fake_get(url, headers=None, params=None):
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


@run_async
async def test_read_repo_file_start_line_jumps_past_the_truncation_point(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    big_content, reasoner_line = _build_large_file_fixture()
    repo_resp = _http_response(200, {"default_branch": "main"})
    file_resp = _http_response(200, {"content": _b64(big_content)})

    def fake_get(url, headers=None, params=None):
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
        lambda url, headers=None, params=None: (
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
