"""The trace excerpt: what a read step read, shown (capped and redacted) in the requester's live trace only (backend/utils/agent_utils.py, docs/trace-panel-verbose-plan.md).

The first version of these tests only checked the helper. The helper worked and the trace panel still showed nothing, because nothing carried the excerpt from the loops to the page. So this file also proves the
whole path: both loops emit it, the stream forwards it, the page records it, and none of it is logged or saved."""
import ast
import asyncio
import inspect
import logging
import pathlib

import pytest

from backend.services import agent_workflow as aw
from backend.services import native_loop
from backend.utils import agent_utils as au
from backend.utils.agent_utils import TRACE_EXCERPT_MAX_CHARS, build_tool_excerpt, clean_trace_excerpt, emit_trace_excerpt

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---- the helper ----------------------------------------------------------------------------------------------------------------------------

def test_the_excerpt_is_capped_at_1000_characters_and_says_it_was_cut():
    excerpt = build_tool_excerpt("read_repo_file", {"path": "notes/readme.md"}, "a line of ordinary code\n" * 300)         # not one long run: a run like that looks like a key and is redacted
    assert TRACE_EXCERPT_MAX_CHARS == 1000 and len(excerpt["text"]) == 1000 and excerpt["truncated"] is True and excerpt["kind"] == "read" and excerpt["path"] == "notes/readme.md"


def test_a_short_result_is_shown_whole_and_not_marked_cut():
    excerpt = build_tool_excerpt("read_repo_file", {"path": "notes/readme.md"}, "hello")
    assert excerpt["text"] == "hello" and excerpt["truncated"] is False


def test_secret_looking_text_is_redacted_even_in_the_middle_of_ordinary_text():
    body = "config line\npassword=hunter2-very-secret\napi_key: abc123\nBearer abcdefghijklmnop\nghp_" + "a" * 30 + "\nend"
    text = build_tool_excerpt("read_repo_file", {"path": "app/config.py"}, body)["text"]
    for leaked in ("hunter2-very-secret", "abc123", "abcdefghijklmnop", "ghp_" + "a" * 30):
        assert leaked not in text, leaked
    assert "config line" in text and "end" in text and "[redacted]" in text


def test_only_a_successful_read_gets_an_excerpt():
    assert build_tool_excerpt("search_code", {"query": "x"}, "some result") is None
    assert build_tool_excerpt("run_python", {"code": "print(1)"}, "1") is None
    assert build_tool_excerpt("read_repo_file", {"path": "app/main.py"}, "ERROR: not found") is None
    assert build_tool_excerpt("read_repo_file", None, "text") is None and build_tool_excerpt("read_repo_file", {"path": "a.py"}, ["not", "a", "string"]) is None


@pytest.mark.parametrize("path", [
    ".env", "deploy/.env.production", "keys/id_rsa", "keys/id_ed25519.pub", "certs/server.pem", "config/credentials.json", "secrets.json", "config/secret.yaml", ".npmrc", "web/.npmrc", ".pypirc", ".netrc",
    "infra/terraform.tfstate", "home/.ssh/config", "x/.aws/credentials", "k/.kube/config", "vault.kdbx", "release.keystore", "C:\\proj\\.env",
])
def test_a_file_that_holds_secrets_gets_no_excerpt(path):
    assert build_tool_excerpt("read_repo_file", {"path": path}, "KEY=value") is None


@pytest.mark.parametrize("path", ["src/secrets_helper.py", "docs/environment.md", "src/key_utils.py", "README.md", "app/credentials_form.tsx".replace("credentials", "login"), "monkey.txt"])
def test_ordinary_files_with_similar_names_are_still_shown(path):
    assert build_tool_excerpt("read_repo_file", {"path": path}, "hello") is not None


# ---- the checks on the way out ------------------------------------------------------------------------------------------------------------

def test_what_leaves_the_server_is_checked_again():
    assert clean_trace_excerpt({"kind": "read", "path": "a.py", "text": "ab " * 2000, "truncated": False}) == {"kind": "read", "path": "a.py", "text": ("ab " * 2000)[:1000], "truncated": True}
    assert clean_trace_excerpt({"kind": "read", "path": "a\\b.py", "text": "hi"})["path"] == "a/b.py"
    assert len(clean_trace_excerpt({"kind": "read", "path": "p" * 900, "text": "hi"})["path"]) == 300
    for bad in (None, "text", ["x"], {"kind": "search", "text": "x"}, {"kind": "read"}, {"kind": "read", "text": ""}, {"kind": "read", "text": "   "}, {"kind": "read", "text": 5},
                {"kind": "read", "path": ".env", "text": "KEY=1"}, {"kind": "read", "path": "keys/id_rsa", "text": "KEY"}):
        assert clean_trace_excerpt(bad) is None, bad


def test_an_excerpt_is_sent_only_with_a_step_id_and_only_once_cleaned(monkeypatch):
    sent = []

    async def fake_emit(name, data):
        sent.append((name, data))

    monkeypatch.setattr(au, "safe_emit_event", fake_emit)
    good = {"kind": "read", "path": "a.py", "text": "print(1)", "truncated": False}
    asyncio.run(emit_trace_excerpt(None, good))
    asyncio.run(emit_trace_excerpt("abc", None))
    asyncio.run(emit_trace_excerpt("abc", {"kind": "read", "path": ".env", "text": "KEY=1"}))
    asyncio.run(emit_trace_excerpt("abc", good))
    assert sent == [("trace_excerpt", {"step_id": "abc", "excerpt": good})]


def test_sending_an_excerpt_logs_nothing_not_even_when_something_is_wrong(monkeypatch, caplog):
    async def boom(name, data):
        raise RuntimeError("UNIQUE_EXCERPT_MARKER_42 could not be sent")

    monkeypatch.setattr(au, "safe_emit_event", boom)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError):
            asyncio.run(emit_trace_excerpt("abc", {"kind": "read", "path": "a.py", "text": "UNIQUE_EXCERPT_MARKER_42", "truncated": False}))
        build_tool_excerpt("read_repo_file", {"path": "a.py"}, "UNIQUE_EXCERPT_MARKER_42")
    assert "UNIQUE_EXCERPT_MARKER_42" not in caplog.text.replace("UNIQUE_EXCERPT_MARKER_42 could not be sent", "")


def test_no_excerpt_helper_logs_anything():
    for fn in (au.build_tool_excerpt, au.clean_trace_excerpt, au.emit_trace_excerpt):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"logger", "log"}], fn.__name__


# ---- the whole path (this is what was missing) ---------------------------------------------------------------------------------------------

def test_the_native_loop_emits_an_excerpt_for_a_successful_read_and_never_for_an_error_or_another_tool():
    class Reply:
        def __init__(self, calls, content=""):
            self.tool_calls, self.content = calls, content

    replies = [
        Reply([{"name": "read_repo_file", "args": {"path": "a.py"}, "id": "1"}]),
        Reply([{"name": "read_repo_file", "args": {"path": ".env"}, "id": "2"}]),
        Reply([{"name": "search_literal", "args": {"term": "x"}, "id": "3"}]),
        Reply([{"name": "read_repo_file", "args": {"path": "missing.py"}, "id": "4"}]),
        Reply([], "finished"),
    ]
    results = {"a.py": "print('hello')", ".env": "KEY=value", "x": "a.py:1: x", "missing.py": "ERROR: not found"}

    class LLM:
        async def ainvoke_tools(self, messages, tools):
            return replies.pop(0)

    steps, excerpts = [], []

    async def emit_step(title, detail, step_id, tool=None, subject=None):
        steps.append(step_id)

    async def emit_excerpt(step_id, excerpt):
        excerpts.append((step_id, excerpt))

    async def act(decision):
        args = decision["args"]
        return results[args.get("path") or args.get("term")]

    result = asyncio.run(native_loop.run(llm=LLM(), tools=[{"name": "read_repo_file"}, {"name": "search_literal"}], act=act, question="q", emit_step=emit_step, emit_excerpt=emit_excerpt))
    assert result["final_answer"] == "finished"
    assert excerpts == [(steps[0], {"kind": "read", "path": "a.py", "text": "print('hello')", "truncated": False})]


def test_the_tool_agent_hands_the_native_loop_the_excerpt_emitter_and_the_old_loop_uses_it_too():
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    assert "emit_facts=emit_trace_facts, emit_excerpt=emit_trace_excerpt" in workflow
    react = (ROOT / "backend" / "services" / "react_loop.py").read_text(encoding="utf-8")
    assert "await emit_trace_excerpt(step_id, build_tool_excerpt(tool_action_name, args, observation))" in react
    assert 'safe_emit_event("trace_excerpt"' not in react                                               # only the checked emitter sends it


def test_the_stream_forwards_the_excerpt_to_the_requesters_browser_only():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app_source.split('event.get("name") == "trace_excerpt"', 1)[1][:420]
    assert "'event': 'trace_excerpt'" in block and "data['step_id']" in block and "data['excerpt']" in block
    # it is never put into the saved conversation: the stored message is built from the reply text alone
    assert "trace_excerpt" not in app_source.split("ai_message = stamp(AIMessage(content=full_response))", 1)[1][:6000]


def test_the_page_records_the_excerpt_and_shows_it_as_plain_text():
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    panel = (ROOT / "local" / "src" / "components" / "TracePanel.tsx").read_text(encoding="utf-8")
    assert "payload.event === 'trace_excerpt'" in chat and "recordExcerpt(prev, payload.step_id, payload.excerpt)" in chat and "setTraceExcerpts({})" in chat
    assert "mergeExcerpts(mergeFacts(traceSteps, traceFacts), traceExcerpts)" in chat
    assert "dangerouslySetInnerHTML" not in panel and '<pre className="trace-excerpt-text">{excerpt.text}</pre>' in panel
    assert "localStorage" not in panel and "setItem" not in chat.split("trace_excerpt", 1)[1][:400]       # not kept anywhere
