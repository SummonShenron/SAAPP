"""The trace excerpt: what a read step read, shown (capped and redacted) in the requester's live trace only (backend/utils/agent_utils.py, docs/trace-panel-verbose-plan.md).

The first version of these tests only checked the helper. The helper worked and the trace panel still showed nothing, because nothing carried the excerpt from the loops to the page. So this file also proves the
whole path: both loops emit it, the stream forwards it, the page records it, and none of it is logged or saved."""
import ast
import asyncio
import inspect
import json
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
    assert [(sid, ex["kind"]) for sid, ex in excerpts] == [(steps[0], "read"), (steps[2], "search")]           # the read, and the search that found something; not the secrets file, not the failed read
    assert excerpts[0][1] == {"kind": "read", "path": "a.py", "text": "print('hello')", "truncated": False}
    assert excerpts[1][1]["files"] == [{"path": "a.py", "matches": [{"line": 1, "text": "x"}]}]


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
    components = ROOT / "local" / "src" / "components"
    # The steps are drawn by TraceSteps (shared by the trace panel and the steps kept under each reply: StepsDisclosure); an excerpt is plain text in all three.
    steps = (components / "TraceSteps.tsx").read_text(encoding="utf-8")
    assert "payload.event === 'trace_excerpt'" in chat and "recordExcerpt(prev, payload.step_id, payload.excerpt)" in chat and "setTraceExcerpts({})" in chat
    assert "mergeExcerpts(mergeFacts(traceSteps, traceFacts), traceExcerpts)" in chat
    assert "dangerouslySetInnerHTML" not in steps and '<pre className="trace-excerpt-text">{excerpt.text}</pre>' in steps
    for name in ("TraceSteps.tsx", "TracePanel.tsx", "StepsDisclosure.tsx"):
        source = (components / name).read_text(encoding="utf-8")
        assert "localStorage" not in source and "dangerouslySetInnerHTML" not in source, name
    assert "setItem" not in chat.split("trace_excerpt", 1)[1][:400]       # not kept anywhere
    # the steps a reply keeps (with their excerpts) are left out of the conversation cache in the browser
    assert "delete m.steps" in chat


# ---- searches: the files and matching lines ------------------------------------------------------------------------------------------------------

LITERAL = """Found 6 matches for 'timestamp' in 3 of 700 files:
local/src/pages/Chat.tsx:490: <span className="feedback-time">{formatMessageTime(msg.sentAt)}</span>
local/src/pages/Chat.tsx:512: const timestamp = 1
backend/utils/time_utils.py:55: def stamp(message):  # timestamp
.env:3: TIMESTAMP_KEY=abcdefghijklmnopqrstuvwxyz0123456789ABCDEF
keys/id_rsa:1: timestamp
docs/notes.md:9: password = hunter2 timestamp
"""


def test_a_literal_search_is_grouped_by_file_with_line_numbers_and_leaves_out_secrets_files():
    excerpt = build_tool_excerpt("search_literal", {"term": "timestamp"}, LITERAL)
    assert excerpt["kind"] == "search" and excerpt["term"] == "timestamp"
    assert [f["path"] for f in excerpt["files"]] == ["local/src/pages/Chat.tsx", "backend/utils/time_utils.py", "docs/notes.md"]
    assert [m["line"] for m in excerpt["files"][0]["matches"]] == [490, 512] and excerpt["files"][1]["matches"] == [{"line": 55, "text": "def stamp(message):  # timestamp"}]
    assert excerpt["hidden"] == 2 and excerpt["total_files"] == 3 and excerpt["total_matches"] == 4 and excerpt["truncated"] is False
    shown = json.dumps(excerpt)
    assert "TIMESTAMP_KEY" not in shown and "id_rsa" not in shown and ".env" not in shown and "hunter2" not in shown          # secrets files are counted, never shown; a secret-looking line is redacted


def test_a_code_search_lists_the_files_it_found_and_nothing_else():
    excerpt = build_tool_excerpt("search_code", {"query": "stamp"}, "backend/utils/time_utils.py\nlocal/src/a.tsx\nnotes with spaces.md\nERROR: nope\n.env")
    assert excerpt["files"] == [{"path": "backend/utils/time_utils.py", "matches": []}, {"path": "local/src/a.tsx", "matches": []}] and excerpt["total_files"] == 2 and excerpt["hidden"] == 1


def test_nothing_found_or_an_error_or_a_secrets_file_alone_gives_no_excerpt():
    assert build_tool_excerpt("search_literal", {"term": "x"}, "No occurrences of 'x' in 700 files scanned.") is None
    assert build_tool_excerpt("search_literal", {"term": "x"}, "ERROR: no term given") is None
    assert build_tool_excerpt("search_code", {"query": "x"}, "No matches.") is None
    assert build_tool_excerpt("search_literal", {"term": "x"}, ".env:1: x\nkeys/server.pem:2: x") is None
    assert build_tool_excerpt("search_literal", {"term": "x"}, LITERAL) is not None and build_tool_excerpt("search_literal", None, LITERAL) is None and build_tool_excerpt("search_literal", {"term": "x"}, ["a"]) is None
    assert build_tool_excerpt("find_file", {"query": "x"}, "a.py") is None                         # only these two searches have an excerpt


def test_a_long_search_is_capped_by_files_matches_per_file_matches_in_all_and_line_length():
    lines = []
    for number in range(1, 16):
        for line in range(1, 8):
            lines.append(f"src/file{number:02d}.py:{line}: " + ("a plain line of code about the term " * 8))
    excerpt = build_tool_excerpt("search_literal", {"term": "term"}, "\n".join(lines))
    assert len(excerpt["files"]) == 10 and excerpt["total_files"] == 15 and excerpt["total_matches"] == 105 and excerpt["truncated"] is True
    assert all(len(f["matches"]) <= 5 for f in excerpt["files"]) and sum(len(f["matches"]) for f in excerpt["files"]) <= 25
    assert all(len(m["text"]) <= 160 for f in excerpt["files"] for m in f["matches"])
    assert len(excerpt["term"]) <= 120 and len(build_tool_excerpt("search_literal", {"term": "t" * 500}, "a.py:1: t")["term"]) == 120


def test_matches_from_a_search_that_fit_in_the_caps_are_not_marked_cut():
    excerpt = build_tool_excerpt("search_literal", {"term": "x"}, "a.py:1: x\nb.py:2: x")
    assert excerpt["truncated"] is False and excerpt["total_matches"] == 2


def test_a_search_excerpt_is_checked_again_on_the_way_out(monkeypatch):
    good = {"kind": "search", "term": "x", "files": [{"path": "a.py", "matches": [{"line": 3, "text": "x = 1"}]}], "total_files": 1, "total_matches": 1, "hidden": 0, "truncated": False}
    assert clean_trace_excerpt(good) == good
    messy = {"kind": "search", "term": "y" * 400, "files": [{"path": ".env", "matches": [{"line": 1, "text": "KEY=1"}]}, {"path": "keys/id_rsa", "matches": []}, "junk", {"path": 5},
                                                              {"path": "ok.py", "matches": [{"line": 0, "text": "zero"}, {"line": 2, "text": "z" * 900}, {"line": "3", "text": "string line"}, {"line": 4, "text": 5}, "junk"]}], "total_files": "many", "hidden": -3}
    cleaned = clean_trace_excerpt(messy)
    assert [f["path"] for f in cleaned["files"]] == ["ok.py"] and cleaned["files"][0]["matches"] == [{"line": 2, "text": "z" * 160}]
    assert len(cleaned["term"]) == 120 and cleaned["total_files"] == 1 and cleaned["hidden"] == 0 and cleaned["total_matches"] == 1
    for bad in ({"kind": "search"}, {"kind": "search", "files": []}, {"kind": "search", "files": [{"path": ".env", "matches": []}]}, {"kind": "search", "files": "x"}):
        assert clean_trace_excerpt(bad) is None, bad
    many = {"kind": "search", "term": "t", "files": [{"path": f"f{n}.py", "matches": [{"line": k + 1, "text": "t"} for k in range(9)]} for n in range(20)]}
    capped = clean_trace_excerpt(many)
    assert len(capped["files"]) == 10 and sum(len(f["matches"]) for f in capped["files"]) == 25 and all(len(f["matches"]) <= 5 for f in capped["files"])


def test_a_search_excerpt_is_sent_like_a_read_one_and_the_old_loop_sends_it_too(monkeypatch):
    sent = []

    async def fake_emit(name, data):
        sent.append((name, data))

    monkeypatch.setattr(au, "safe_emit_event", fake_emit)
    excerpt = build_tool_excerpt("search_literal", {"term": "timestamp"}, LITERAL)
    asyncio.run(emit_trace_excerpt("abc", excerpt))
    assert sent == [("trace_excerpt", {"step_id": "abc", "excerpt": clean_trace_excerpt(excerpt)})]
    assert "SECRET" not in json.dumps(sent) and "hunter2" not in json.dumps(sent)
    react = (ROOT / "backend" / "services" / "react_loop.py").read_text(encoding="utf-8")
    assert "await emit_trace_excerpt(step_id, build_tool_excerpt(tool_action_name, args, observation))" in react             # the same call serves reads and searches


def test_the_search_excerpt_helpers_log_nothing():
    for fn in (au._search_excerpt, au._read_excerpt, au._redact_line):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"logger", "log"}], fn.__name__
