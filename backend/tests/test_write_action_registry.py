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
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: Mock(status_code=404, text="not found"))
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

# ---------------------------------------------------------------------------
# create_calendar_event / update_calendar_event — personal actions, no admin gate,
# and the propose_write_node (None, message) short-circuit when there's no connection yet.
# ---------------------------------------------------------------------------

def test_propose_write_node_no_calendar_connection_sends_plain_message_not_a_card(monkeypatch):
    monkeypatch.setattr(aw, "get_calendar_connection_status", lambda username: {"connected": False})

    state = {
        "username": "jack",
        "write_action": "create_calendar_event",
        "messages": [HumanMessage(content="schedule a call with Sam tomorrow at 2pm")],
    }
    result = aw.propose_write_node(state)

    assert result["pending_action"] is None
    assert result["relevance_grade"] == "conversational"
    assert "connect" in result["generation"].lower()
    assert "Approval Required" not in result["generation"]


def test_propose_write_node_drafts_calendar_event_when_connected(monkeypatch):
    monkeypatch.setattr(aw, "get_calendar_connection_status", lambda username: {"connected": True})
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: _llm_json(
            '{"summary": "Call with Sam", "start_iso": "2026-06-21T14:00:00", "duration_minutes": 30}'
        ))
    )

    state = {
        "username": "jack",
        "write_action": "create_calendar_event",
        "messages": [HumanMessage(content="schedule a call with Sam tomorrow at 2pm")],
    }
    result = aw.propose_write_node(state)

    assert result["pending_action"]["action_type"] == "create_calendar_event"
    assert result["pending_action"]["details"]["summary"] == "Call with Sam"
    assert result["relevance_grade"] == "hitl_approval_required"
    assert "Approval Required" in result["generation"]


def test_execute_write_node_allows_non_admin_to_schedule_calendar_event(monkeypatch):
    """The regression this guards: create_calendar_event/update_calendar_event's required_role
    of None must not require ANY group, while every other registered action still does."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])  # no groups at all

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")
    monkeypatch.setattr(
        aw, "create_event",
        lambda token, summary, start_iso, duration_minutes, tz: {
            "id": "evt1", "summary": summary, "html_link": "https://calendar.google.com/evt1", "start": start_iso,
        },
    )

    details = {"summary": "Call with Sam", "start_iso": "2026-06-21T14:00:00", "duration_minutes": 30}
    result = aw.execute_write_node(_pending_state("create_calendar_event", details))

    assert "denied" not in result.get("content_to_format", "").lower()
    assert "Event scheduled" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_still_blocks_non_admin_for_create_pr_after_rbac_change(monkeypatch):
    """Regression guard for the one-line RBAC change (falsy required_role bypasses the gate) —
    confirms it didn't accidentally weaken the check for actions that DO require a role."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    mock_post = Mock()
    monkeypatch.setattr(aw.requests, "post", mock_post)

    details = {"title": "t", "body": "b", "head_branch": "h", "base_branch": "m", "repo": "o/r"}
    result = aw.execute_write_node(_pending_state("create_pr", details))

    assert "denied" in result["content_to_format"].lower()
    mock_post.assert_not_called()


def test_execute_write_node_reports_failure_when_calendar_connection_missing(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            raise aw.GoogleCalendarConnectionError("No Google Calendar connection found for jack")

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    details = {"summary": "Call with Sam", "start_iso": "2026-06-21T14:00:00", "duration_minutes": 30}
    result = aw.execute_write_node(_pending_state("create_calendar_event", details))

    assert "Failed to schedule event" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_updates_calendar_event_by_finding_it_first(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")
    monkeypatch.setattr(
        aw, "find_event_by_summary_on_day",
        lambda token, search_summary, event_date_iso, tz: {"id": "evt1", "summary": "Call with Sam"},
    )
    monkeypatch.setattr(
        aw, "update_event",
        lambda token, event_id, updates, tz: {
            "id": event_id, "summary": updates.get("summary", "Call with Sam"),
            "html_link": "https://calendar.google.com/evt1", "start": updates.get("start_iso"),
        },
    )

    details = {
        "search_summary": "Call with Sam", "event_date_iso": "2026-06-21",
        "start_iso": "2026-06-21T15:00:00", "duration_minutes": 30, "timezone": "America/Chicago",
    }
    result = aw.execute_write_node(_pending_state("update_calendar_event", details))

    assert "Event updated" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_update_calendar_event_reports_not_found(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-access-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "get_user_timezone", lambda username: "America/Chicago")
    monkeypatch.setattr(aw, "find_event_by_summary_on_day", lambda token, search_summary, event_date_iso, tz: None)

    details = {"search_summary": "Nonexistent event", "event_date_iso": "2026-06-21"}
    result = aw.execute_write_node(_pending_state("update_calendar_event", details))

    assert "couldn't find an event" in result["content_to_format"]


def test_recover_create_calendar_event_from_card_text():
    card = (
        "Ready to schedule this event:\n"
        "- **Title:** Call with Sam\n"
        "- **When:** 2026-06-21T14:00:00 (America/Chicago), 30 minutes"
    )
    recovered = aw._recover_create_calendar_event([AIMessage(content=card)], "")
    assert recovered == {
        "summary": "Call with Sam", "start_iso": "2026-06-21T14:00:00",
        "timezone": "America/Chicago", "duration_minutes": 30,
    }


def test_recover_update_calendar_event_from_card_text_with_both_changes():
    card = (
        "Ready to update this calendar event:\n"
        '- **Find:** "Call with Sam" on 2026-06-21\n'
        "- **Rename to:** Call with Samantha\n"
        "- **Move to:** 2026-06-21T15:00:00 (America/Chicago)"
    )
    recovered = aw._recover_update_calendar_event([AIMessage(content=card)], "")
    assert recovered == {
        "search_summary": "Call with Sam", "event_date_iso": "2026-06-21",
        "new_summary": "Call with Samantha",
        "start_iso": "2026-06-21T15:00:00", "timezone": "America/Chicago",
    }


# ---------------------------------------------------------------------------
# append_target_doc — inline-proposed like run_mongo_write, generic (not tied to any one
# document's purpose), execute re-resolves doc_id fresh rather than trusting stored details.
# ---------------------------------------------------------------------------

def test_execute_append_target_doc_succeeds(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "get_user_target_doc_id", lambda username: "doc123")
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    monkeypatch.setattr(aw, "append_doc_text", lambda token, doc_id, content: "Appended successfully.")

    details = {"content": "Summary of the week.", "doc_id": "doc123"}
    result = aw.execute_write_node(_pending_state("append_target_doc", details))

    assert "Appended successfully" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_append_target_doc_fails_when_doc_no_longer_configured(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "get_user_target_doc_id", lambda username: None)

    details = {"content": "Summary.", "doc_id": "doc123"}
    result = aw.execute_write_node(_pending_state("append_target_doc", details))

    assert "Failed to append" in result["content_to_format"]
    assert "no target document" in result["content_to_format"]


def test_execute_append_target_doc_fails_when_docs_scope_missing(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "get_user_target_doc_id", lambda username: "doc123")
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    details = {"content": "Summary.", "doc_id": "doc123"}
    result = aw.execute_write_node(_pending_state("append_target_doc", details))

    assert "Docs access not granted" in result["content_to_format"]


def test_recover_append_target_doc_from_card_text():
    card = "Ready to append this to your target document:\n\nSome summary text.\n\n**Purpose:** Weekly update"
    recovered = aw._recover_append_target_doc([AIMessage(content=card)], "")
    assert recovered == {"content": "Some summary text.", "doc_id": None}


@run_async
async def test_propose_append_target_doc_end_to_end_via_tool_agent_node(monkeypatch):
    """Regression guard: the generalized _UnsafeActionRequested handler must still produce
    run_mongo_write's exact original card/pending_action shape (tested elsewhere), AND correctly
    branch to the new append_target_doc shape — this test exercises the new branch through the
    real tool_agent_node -> run_react_loop -> _is_unsafe -> _UnsafeActionRequested path, not just
    a hand-built pending_action."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "get_user_target_doc_id", lambda username: "doc123")

    from backend.tests.test_tool_agent_node import _setup_github_repo, _llm_response, _state

    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(
            action="query", purpose="Append summary",
            tool_action="propose_append_target_doc", args={"content": "Weekly summary text."},
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("summarize this and add it to my doc"))

    assert result["pending_action"]["action_type"] == "append_target_doc"
    assert result["pending_action"]["details"]["content"] == "Weekly summary text."
    assert result["pending_action"]["details"]["doc_id"] == "doc123"
    assert result["relevance_grade"] == "hitl_approval_required"
    assert "Ready to append this to your target document" in result["content_to_format"]


@run_async
async def test_propose_append_target_doc_no_target_doc_configured_short_circuits(monkeypatch):
    """When no target document is configured, the model's proposal must produce a plain
    informational message with no approval card — not a broken "Approve/Reject" prompt over
    nothing real, same (None, message) convention used by _draft_create_calendar_event."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "get_user_target_doc_id", lambda username: None)

    from backend.tests.test_tool_agent_node import _setup_github_repo, _llm_response, _state

    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(
            action="query", purpose="Append summary",
            tool_action="propose_append_target_doc", args={"content": "Weekly summary text."},
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("summarize this and add it to my doc"))

    assert result["pending_action"] is None
    assert result["relevance_grade"] == "conversational"
    assert "haven't set a target document" in result["content_to_format"]
    assert "Approval Required" not in result["content_to_format"]


# ---------------------------------------------------------------------------
# send_email — upfront-triggered like create_calendar_event, no htmlLink-style confirmation
# since Gmail's send response has no equivalent user-facing link.
# ---------------------------------------------------------------------------

def test_propose_write_node_no_gmail_send_scope_sends_plain_message(monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    state = {
        "username": "jack",
        "write_action": "send_email",
        "messages": [HumanMessage(content="send an email to sam@example.com saying hi")],
    }
    result = aw.propose_write_node(state)

    assert result["pending_action"] is None
    assert result["relevance_grade"] == "conversational"
    assert "connect" in result["generation"].lower() or "reconnect" in result["generation"].lower()


def test_propose_write_node_drafts_send_email_when_scope_granted(monkeypatch):
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)
    monkeypatch.setattr(
        aw, "get_chat_llm",
        lambda username: SimpleNamespace(invoke=lambda prompt: _llm_json(
            '{"to": "sam@example.com", "subject": "Hello", "body": "Just saying hi."}'
        ))
    )

    state = {
        "username": "jack",
        "write_action": "send_email",
        "messages": [HumanMessage(content="send an email to sam@example.com saying hi")],
    }
    result = aw.propose_write_node(state)

    assert result["pending_action"]["action_type"] == "send_email"
    assert result["pending_action"]["details"]["to"] == "sam@example.com"
    assert result["relevance_grade"] == "hitl_approval_required"


def test_execute_write_node_sends_email_on_approval(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)
    sent = {}
    monkeypatch.setattr(aw, "send_message", lambda token, to, subject, body: sent.update(to=to, subject=subject, body=body))

    details = {"to": "sam@example.com", "subject": "Hello", "body": "Just saying hi."}
    result = aw.execute_write_node(_pending_state("send_email", details))

    assert sent == {"to": "sam@example.com", "subject": "Hello", "body": "Just saying hi."}
    assert "Email sent" in result["content_to_format"]
    assert result["relevance_grade"] == "action_complete"


def test_execute_write_node_send_email_fails_when_scope_missing(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    class _FakeOAuth:
        def get_valid_access_token(self, username):
            return "fake-token"

    monkeypatch.setattr(aw, "GoogleCalendarOAuth", _FakeOAuth)

    details = {"to": "sam@example.com", "subject": "Hello", "body": "Hi"}
    result = aw.execute_write_node(_pending_state("send_email", details))

    assert "Gmail send access not granted" in result["content_to_format"]


def test_recover_send_email_from_card_text():
    card = (
        "Ready to send this email:\n"
        "- **To:** sam@example.com\n"
        "- **Subject:** Hello\n\n"
        "**Body:**\nJust saying hi.\n\n*Please Approve, Modify parameters, or Reject this action.*"
    )
    recovered = aw._recover_send_email([AIMessage(content=card)], "")
    assert recovered == {"to": "sam@example.com", "subject": "Hello", "body": "Just saying hi."}


# ---------------------------------------------------------------------------
# propose_send_email — inline-proposed like append_target_doc, for a send that depends on
# something gathered mid-loop (e.g. "search github for the last 3 PRs and email me a summary")
# rather than one draftable straight from the user's raw message.
# ---------------------------------------------------------------------------

@run_async
async def test_propose_send_email_end_to_end_via_tool_agent_node(monkeypatch):
    """Regression guard for the same bug propose_append_target_doc's end-to-end test guards:
    the generalized _UnsafeActionRequested handler must branch correctly to the send_email shape,
    using content the model actually composed itself via other actions first — not content
    drafted straight from the user's raw message before anything was looked up."""
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)

    from backend.tests.test_tool_agent_node import _setup_github_repo, _llm_response, _state

    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(
            action="query", purpose="Send PR summary",
            tool_action="propose_send_email",
            args={
                "to": "harper.jack@principal.com",
                "subject": "Summary of Recent GitHub Pull Requests",
                "body": "Here is a summary of the last 3 real pull requests I found.",
            },
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("search github for the last 3 PRs and email me a summary"))

    assert result["pending_action"]["action_type"] == "send_email"
    assert result["pending_action"]["details"] == {
        "to": "harper.jack@principal.com",
        "subject": "Summary of Recent GitHub Pull Requests",
        "body": "Here is a summary of the last 3 real pull requests I found.",
    }
    assert result["relevance_grade"] == "hitl_approval_required"
    assert "Ready to send this email" in result["content_to_format"]


@run_async
async def test_propose_send_email_missing_scope_short_circuits(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: False)

    from backend.tests.test_tool_agent_node import _setup_github_repo, _llm_response, _state

    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(
            action="query", purpose="Send PR summary",
            tool_action="propose_send_email",
            args={"to": "sam@example.com", "subject": "Hi", "body": "Just checking in."},
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("email sam a summary"))

    assert result["pending_action"] is None
    assert result["relevance_grade"] == "conversational"
    assert "haven't connected Gmail send access" in result["content_to_format"]
    assert "Approval Required" not in result["content_to_format"]


@run_async
async def test_propose_send_email_incomplete_args_short_circuits(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: [])
    monkeypatch.setattr(aw, "has_granted_scope", lambda username, scope: True)

    from backend.tests.test_tool_agent_node import _setup_github_repo, _llm_response, _state

    _setup_github_repo(monkeypatch)

    responses = [
        _llm_response(
            action="query", purpose="Send PR summary",
            tool_action="propose_send_email",
            args={"to": "sam@example.com", "subject": "", "body": ""},
        ),
    ]
    monkeypatch.setattr(aw.lite_llm, "ainvoke", AsyncMock(side_effect=responses))

    result = await aw.tool_agent_node(_state("email sam a summary"))

    assert result["pending_action"] is None
    assert result["relevance_grade"] == "conversational"
    assert "recipient, subject, and body" in result["content_to_format"]


@run_async
async def test_mongo_write_proposal_to_execution_end_to_end(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Global_Admins"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setattr(aw.requests, "get", lambda url, headers=None, params=None, **kwargs: _http_response(200, {"default_branch": "main"}))
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

    # Step 2: user approves, as a TRULY independent turn — no pending_action carried over
    # (it never survives between real chat turns; see app.py's initial_state, rebuilt fresh
    # from stored messages every request). Only the card text the assistant actually sent
    # persists. classify_intent must resolve write_action from that card text alone, the same
    # way a real second HTTP request would.
    followup_messages = state["messages"] + [AIMessage(content=proposal["content_to_format"]), HumanMessage(content="yes, approved")]
    followup_state = {"username": "jack", "messages": followup_messages, "documents": []}
    assert aw.classify_intent("yes, approved", state=followup_state) == "execute_write"
    assert followup_state["write_action"] == "run_mongo_write"

    result = aw.execute_write_node(followup_state)

    assert fake_db.tasks.deleted is True
    assert result["relevance_grade"] == "action_complete"
    assert "Executed Successfully" in result["content_to_format"]
    assert result["pending_action"] is None
