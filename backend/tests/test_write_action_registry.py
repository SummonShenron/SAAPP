import asyncio
import functools
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from langchain_core.messages import HumanMessage, AIMessage

from backend.services import agent_workflow as aw


def run_async(fn):
    """Runs an async test function synchronously, avoiding a pytest-asyncio dependency."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_json(content: str):
    return SimpleNamespace(content=content)


def _http_response(status_code, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json = Mock(return_value=json_data or {})
    resp.text = text
    return resp


# ---------------------------------------------------------------------------
# propose_write_node — drafting, both actions, repo recency preserved
# ---------------------------------------------------------------------------

def test_propose_write_node_create_pr_resolves_repo_from_an_earlier_turn(monkeypatch):
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: Mock(status_code=404, text="not found"))
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: _llm_json(
            '{"title": "feat: merge fix-branch into main", "body": "### Summary\\n- fix"}'
        ))
    )

    state = {
        "username": "jack",
        "write_action": "create_pr",
        "messages": [
            HumanMessage(content="we're working in owner/target-repo today"),
            HumanMessage(content="create a pr to merge fix-branch into main"),
        ],
    }

    result = aw.propose_write_node(state)

    assert result["pending_action"]["action_type"] == "create_pr"
    assert result["pending_action"]["details"]["repo"] == "owner/target-repo"
    assert result["relevance_grade"] == "hitl_approval_required"
    assert "Approval Required" in result["generation"]


def test_propose_write_node_create_issue_resolves_repo_from_an_earlier_turn(monkeypatch):
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: _llm_json(
            '{"title": "Bug in login flow", "body": "### Description\\n- broken"}'
        ))
    )

    state = {
        "username": "jack",
        "write_action": "create_issue",
        "messages": [
            HumanMessage(content="we're working in owner/target-repo today"),
            HumanMessage(content="file a bug about the broken login flow"),
        ],
    }

    result = aw.propose_write_node(state)

    assert result["pending_action"]["action_type"] == "create_issue"
    assert result["pending_action"]["details"]["repo"] == "owner/target-repo"
    assert result["relevance_grade"] == "hitl_approval_required"


def test_propose_write_node_falls_back_gracefully_on_malformed_llm_response(monkeypatch):
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: _llm_json("not valid json"))
    )
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    state = {
        "username": "jack",
        "write_action": "create_issue",
        "messages": [HumanMessage(content="file a bug about the broken login button")],
    }

    result = aw.propose_write_node(state)

    assert result["pending_action"]["details"]["title"]  # fallback title, non-empty
    assert result["relevance_grade"] == "hitl_approval_required"


def test_propose_write_node_unknown_action_is_a_noop():
    state = {"username": "jack", "write_action": "not_a_real_action", "messages": [HumanMessage(content="hi")]}
    result = aw.propose_write_node(state)
    assert "pending_action" not in result or result.get("pending_action") == state.get("pending_action")


# ---------------------------------------------------------------------------
# execute_write_node — shared RBAC/approval/dispatch for every registered action
# ---------------------------------------------------------------------------

def _pending_state(action_type, details, decision_message="approve", username="jack"):
    return {
        "username": username,
        "messages": [
            AIMessage(content="**Approval Required**\n\nReady to do the thing.\n\n*Please Approve, Modify parameters, or Reject this action.*"),
            HumanMessage(content=decision_message),
        ],
        "pending_action": {"action_type": action_type, "details": details},
    }


def test_execute_write_node_no_pending_action_is_handled():
    result = aw.execute_write_node({"username": "jack", "messages": [HumanMessage(content="yes")], "pending_action": None})
    assert result["pending_action"] is None
    assert "No pending write action" in result["content_to_format"]


def test_execute_write_node_blocks_non_admin_for_every_action(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    mock_post = Mock()
    monkeypatch.setattr(aw.requests, "post", mock_post)

    for action_type, details in [
        ("create_pr", {"title": "t", "body": "b", "head_branch": "h", "base_branch": "m", "repo": "o/r"}),
        ("create_issue", {"title": "t", "body": "b", "repo": "o/r"}),
        ("run_mongo_write", {"code": "result = 1"}),
    ]:
        result = aw.execute_write_node(_pending_state(action_type, details))
        assert result["pending_action"] is None
        assert "denied" in result["content_to_format"].lower(), action_type

    mock_post.assert_not_called()


def test_execute_write_node_rejection_discards_draft_without_calling_github(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    mock_post = Mock()
    monkeypatch.setattr(aw.requests, "post", mock_post)

    state = _pending_state("create_pr", {"title": "t", "body": "b", "head_branch": "h", "base_branch": "m", "repo": "o/r"}, decision_message="no, cancel that")
    result = aw.execute_write_node(state)

    mock_post.assert_not_called()
    assert result["pending_action"] is None
    assert "cancelled" in result["content_to_format"].lower() or "discarded" in result["content_to_format"].lower()


def test_execute_write_node_creates_pr_on_approval(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    created = _http_response(201, {"html_url": "https://github.com/o/r/pull/9", "number": 9})
    merged = _http_response(200)
    mock_post = Mock(return_value=created)
    monkeypatch.setattr(aw.requests, "post", mock_post)
    monkeypatch.setattr(aw.requests, "put", Mock(return_value=merged))

    details = {"title": "feat: x", "body": "b", "head_branch": "feat", "base_branch": "main", "repo": "o/r"}
    result = aw.execute_write_node(_pending_state("create_pr", details))

    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == "https://api.github.com/repos/o/r/pulls"
    assert "pull/9" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"
    assert result["pending_action"] is None


def test_execute_write_node_creates_issue_on_approval(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    created = _http_response(201, {"html_url": "https://github.com/o/r/issues/42", "number": 42})
    mock_post = Mock(return_value=created)
    monkeypatch.setattr(aw.requests, "post", mock_post)

    details = {"title": "Bug", "body": "b", "repo": "o/r"}
    result = aw.execute_write_node(_pending_state("create_issue", details))

    mock_post.assert_called_once()
    assert mock_post.call_args[0][0] == "https://api.github.com/repos/o/r/issues"
    assert "issues/42" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_reports_github_failure(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    failed = _http_response(422, text='{"message": "Validation Failed"}')
    monkeypatch.setattr(aw.requests, "post", Mock(return_value=failed))

    details = {"title": "Bug", "body": "b", "repo": "o/r"}
    result = aw.execute_write_node(_pending_state("create_issue", details))

    assert "Failed to create Issue" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_recovers_incomplete_pr_details_from_history(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    created = _http_response(201, {"html_url": "https://github.com/o/r/pull/1", "number": 1})
    monkeypatch.setattr(aw.requests, "post", Mock(return_value=created))
    monkeypatch.setattr(aw.requests, "put", Mock(return_value=_http_response(200)))

    state = {
        "username": "jack",
        "messages": [
            AIMessage(content="Ready to create a Pull Request for `o/r`:\n- **Title:** feat: x\n- **Base Branch:** `main` <- `feat`\n\n**Proposed Body:**\nbody text\n\n*Please Approve, Modify parameters, or Reject this action.*"),
            HumanMessage(content="yes"),
        ],
        # pending_action wiped/incomplete — only action_type survives.
        "pending_action": {"action_type": "create_pr", "details": {}},
    }
    result = aw.execute_write_node(state)

    assert result["relevance_grade"] == "action_complete"
    assert "pull/1" in result["content_to_format"]


def test_execute_write_node_mongo_missing_details_does_not_attempt_recovery(monkeypatch):
    """Mongo writes have no recover_from_history — a lost code string can't be honestly
    reconstructed from chat text the way a title/branch name can."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    mock_exec_db = Mock()
    monkeypatch.setattr(aw, "get_db", lambda: mock_exec_db)

    state = _pending_state("run_mongo_write", {})  # no code at all
    result = aw.execute_write_node(state)

    assert "lost between turns" in result["content_to_format"].lower()
    assert result["pending_action"] is None


# ---------------------------------------------------------------------------
# The dead-path fix: a Mongo write proposed by tool_agent_node can now
# actually be approved and executed, end to end, via execute_write_node.
# ---------------------------------------------------------------------------

@run_async
async def test_mongo_write_proposal_to_execution_end_to_end(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None: _http_response(200, {"default_branch": "main"}))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")

    class _FakeCollection:
        def __init__(self):
            self.deleted = False
        def delete_many(self, query):
            self.deleted = True
            return SimpleNamespace(deleted_count=3)
        def list_collection_names(self):
            return ["tasks"]

    class _FakeDB:
        def __init__(self):
            self.tasks = _FakeCollection()
        def list_collection_names(self):
            return ["tasks"]
        def __getitem__(self, name):
            return getattr(self, name)

    fake_db = _FakeDB()
    monkeypatch.setattr(aw, "get_db", lambda: fake_db)

    # Step 1: tool_agent_node proposes the unsafe write.
    proposal_responses = [
        SimpleNamespace(content=json.dumps({
            "action": "query", "purpose": "Delete stale tasks",
            "tool_action": "run_mongo_query", "args": {"code": "result = db['tasks'].delete_many({})"}
        })),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=proposal_responses))

    state = {"username": "jack", "messages": [HumanMessage(content="delete all stale tasks")], "documents": []}
    proposal = await aw.tool_agent_node(state)

    assert proposal["pending_action"]["action_type"] == "run_mongo_write"

    # Step 2: user approves — this exact continuation could not work before Phase 2, since
    # nothing ever transitioned the old code_approval_status field to "approved".
    followup_state = {
        **state,
        "messages": state["messages"] + [AIMessage(content=proposal["content_to_format"]), HumanMessage(content="yes, approved")],
        "pending_action": proposal["pending_action"],
    }
    result = aw.execute_write_node(followup_state)

    assert fake_db.tasks.deleted is True
    assert result["relevance_grade"] == "action_complete"
    assert "Executed Successfully" in result["content_to_format"]
    assert result["pending_action"] is None
