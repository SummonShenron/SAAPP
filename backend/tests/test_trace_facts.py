"""Trace panel facts (docs/trace-panel-verbose-plan.md, slice 1): measured metadata only, capped, masked, never logged."""
import asyncio
import functools
import json

from backend.services import native_loop
from backend.services import react_loop
from backend.utils import agent_utils as au


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


# ----- caps -----

def test_each_fact_line_is_capped_at_200_characters_and_one_line():
    cleaned = au.clean_trace_facts(["x" * 500, "two\nlines\there"])
    assert len(cleaned[0]) == au.TRACE_FACT_MAX_CHARS
    assert cleaned[1] == "two lines here"


def test_a_step_has_at_most_ten_lines_and_only_real_strings():
    cleaned = au.clean_trace_facts([f"line {i}" for i in range(40)] + [None, 7, "  "])
    assert len(cleaned) == au.TRACE_FACT_MAX_LINES == 10
    assert au.clean_trace_facts("not a list") == []
    assert au.clean_trace_facts([None, 3, ""]) == []


def test_a_huge_argument_cannot_blow_the_payload_up():
    facts = au.build_tool_facts("search_code", {f"arg{i}": "y" * 5000 for i in range(50)}, 0.4, "ok")
    assert len(facts) <= 10 and all(len(line) <= 200 for line in facts)


# ----- masking -----

def test_typed_values_bodies_and_credentials_are_masked():
    facts = " ".join(au.build_tool_facts(
        "browser_type",
        {"selector": "#email", "value": "my-real-password-123", "body": "Dear boss, I quit", "api_key": "abcdef", "to": "boss@x.com"},
        1.0, "ok",
    ))
    for secret in ("my-real-password-123", "Dear boss", "abcdef", "boss@x.com"):
        assert secret not in facts
    assert "#email" in facts and "value=•••" in facts


def test_a_secret_looking_value_is_masked_whatever_its_argument_is_called():
    facts = " ".join(au.build_tool_facts("fetch", {
        "header": "Bearer abc.def.ghi", "q": "ghp_" + "a" * 30, "id": "0123456789abcdef0123456789abcdef",
    }, 0.1, "ok"))
    assert "Bearer" not in facts and "ghp_" not in facts and "0123456789abcdef0123456789abcdef" not in facts


def test_ordinary_arguments_are_shown_but_shortened():
    facts = au.build_tool_facts("read_repo_file", {"path": "backend/app.py", "note": "z" * 300, "lines": [1, 2, 3]}, 0.1, "ok")
    args_line = next(line for line in facts if line.startswith("Args:"))
    assert "path=backend/app.py" in args_line and "lines=3 item(s)" in args_line
    assert "z" * 61 not in args_line


# ----- measured facts, never content -----

def test_the_result_body_never_appears_only_its_outcome_and_size():
    body = "SECRET-RESULT-BODY " * 10
    facts = au.build_tool_facts("read_repo_file", {"path": "a.py"}, 0.25, body)
    assert "SECRET-RESULT-BODY" not in " ".join(facts)
    assert "Result: ok, %d characters" % len(body) in facts
    assert "Took: 0.25 s" in facts


def test_error_truncated_retry_and_batch_facts():
    assert "Result: error, 15 characters" in au.build_tool_facts("t", {}, 1, "ERROR: it broke")
    assert any(line.startswith("Result: truncated") for line in au.build_tool_facts("t", {}, 1, "long", truncated=True))
    facts = au.build_tool_facts("t", {}, 1, "ok", retries=2)
    assert "Retries so far this turn: 2" in facts
    assert not any("parallel" in line for line in facts)  # the batched pill already says it
    assert not any("Retries" in line for line in au.build_tool_facts("t", {}, 1, "ok"))


def test_subject_comes_from_the_right_argument_per_tool():
    assert au.build_trace_subject("search_code", {"query": "HashRouter usage"}) == "HashRouter usage"
    assert au.build_trace_subject("search_library", {"query": "tax forms"}) == "tax forms"
    assert au.build_trace_subject("web_search", {"query": "google hash router indexing"}) == "google hash router indexing"
    assert au.build_trace_subject("read_repo_file", {"path": "local/src/main.tsx"}) == "main.tsx"
    assert au.build_trace_subject("find_file", {"query": "Integrations"}) == "Integrations"
    assert au.build_trace_subject("search_literal", {"term": "HashRouter"}) == "HashRouter"
    assert au.build_trace_subject("trace_symbol", {"symbol": "build_tool_facts"}) == "build_tool_facts"
    assert au.build_trace_subject("read_library_file", {"name": "notes.pdf"}) == "notes.pdf"
    # a wrong or missing argument, or a non-dict, gives no subject
    assert au.build_trace_subject("search_code", {"term": "x"}) is None
    assert au.build_trace_subject("search_code", {"query": "  "}) is None
    assert au.build_trace_subject("search_code", None) is None


def test_read_url_subject_is_host_and_path_only():
    subject = au.build_trace_subject("read_url", {"url": "https://user:pw@www.example.com:8443/docs/page/?token=abc&q=1#frag"})
    assert subject == "example.com/docs/page"
    assert au.build_trace_subject("read_url", {"url": "example.org"}) == "example.org"


def test_tools_without_a_subject_stay_plain():
    for tool, args in (
        ("browser_type", {"label": "Email", "value": "hunter2"}),
        ("propose_send_email", {"to": "a@b.com", "body": "hi"}),
        ("propose_append_target_doc", {"text": "private"}),
        ("run_python", {"code": "print(1)"}),
        ("run_mongo_query", {"query": "{}"}),
        ("browser_click", {"text": "Sign in"}),
    ):
        assert au.build_trace_subject(tool, args) is None, tool


def test_subject_is_masked_and_cut_to_40_characters():
    secret = "ghp_" + "a" * 30
    assert au.build_trace_subject("web_search", {"query": secret}) is None   # secret-looking: no subject at all
    assert au.build_trace_subject("read_url", {"url": f"https://example.com/{'a' * 45}"}) is None
    long = au.build_trace_subject("web_search", {"query": "word " * 30})
    assert len(long) == au.TRACE_SUBJECT_MAX_CHARS and long.endswith("…")
    exact = "ab cd " * 6 + "wxyz"   # 40 characters, with spaces so it does not look like a secret
    assert len(exact) == 40 and au.build_trace_subject("web_search", {"query": exact}) == exact


def test_subject_helper_never_logs():
    import ast
    import inspect
    for fn in (au.build_trace_subject, au._url_host_and_path):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"logger", "log"}]


def test_empty_results_are_not_called_ok():
    for empty in ("", "[]", "No matches", "No results found", "  none  "):
        facts = au.build_tool_facts("web_search", {"query": "q"}, 1, empty)
        assert f"Result: empty (no results), {len(empty)} characters" in facts, empty
    assert "Result: empty (no results), 2 characters" in au.build_tool_facts("web_search", {}, 1, [])
    assert "Result: ok, 12 characters" in au.build_tool_facts("web_search", {}, 1, "Found 3 hits")
    assert "Result: error, 9 characters" in au.build_tool_facts("web_search", {}, 1, "ERROR: no")


def test_facts_are_sent_only_with_a_step_id_and_are_cleaned_again_on_the_way_out(monkeypatch):
    sent = []

    async def fake_emit(name, data):
        sent.append((name, data))

    monkeypatch.setattr(au, "safe_emit_event", fake_emit)
    asyncio.run(au.emit_trace_facts(None, ["no id"]))
    asyncio.run(au.emit_trace_facts("abc", []))
    asyncio.run(au.emit_trace_facts("abc", ["x" * 900] * 30))
    assert len(sent) == 1 and sent[0][0] == "trace_facts" and sent[0][1]["step_id"] == "abc"
    assert len(sent[0][1]["facts"]) == 10 and all(len(f) <= 200 for f in sent[0][1]["facts"])


def test_step_ids_are_unique():
    assert len({au.new_trace_step_id() for _ in range(200)}) == 200


# ----- the loops emit a step id and matching facts -----

@run_async
async def test_react_loop_ties_each_steps_facts_to_its_step_id(monkeypatch):
    events = []

    async def fake_emit(name, data):
        events.append((name, data))

    monkeypatch.setattr(react_loop, "safe_emit_event", fake_emit)
    monkeypatch.setattr(au, "safe_emit_event", fake_emit)

    async def fake_ainvoke(prompt):
        class R:
            content = json.dumps(replies.pop(0))
        return R()

    replies = [
        {"action": "query", "purpose": "Read it", "tool_action": "read_repo_file", "args": {"path": "a.py", "value": "typed secret"}},
        {"action": "final", "answer": "done"},
    ]
    monkeypatch.setattr(react_loop.lite_llm, "ainvoke", fake_ainvoke)

    async def act(decision):
        return "file body that must not be sent"

    await react_loop.run_react_loop(
        question="q", schema="s", prompt_template="{question}{schema}{attempts}{architecture_map}", act=act,
        max_iterations=3, node_name="tool_agent_node",
    )
    steps = [data for name, data in events if name == "trace_detail" and data.get("detail") == "Read it"]
    facts = [data for name, data in events if name == "trace_facts"]
    assert len(steps) == 1 and len(facts) == 1
    assert steps[0]["step_id"] == facts[0]["step_id"]
    joined = " ".join(facts[0]["facts"])
    assert "Tool: read_repo_file" in joined and "typed secret" not in joined and "file body" not in joined


@run_async
async def test_native_loop_emits_a_step_id_then_facts_and_counts_retries():
    class Reply:
        def __init__(self, calls, content=""):
            self.tool_calls, self.content = calls, content

    replies = [
        Reply([{"name": "read_repo_file", "args": {"path": "a.py"}, "id": "1"}]),
        Reply([{"name": "read_repo_file", "args": {"path": "b.py"}, "id": "2"}]),
        Reply([], "finished"),
    ]

    class LLM:
        async def ainvoke_tools(self, messages, tools):
            return replies.pop(0)

    steps, facts = [], []

    tools_seen = []

    async def emit_step(title, detail, step_id, tool=None, subject=None):
        steps.append(step_id)
        tools_seen.append((tool, subject))

    async def emit_facts(step_id, lines):
        facts.append((step_id, lines))

    async def act(decision):
        return "ERROR: nope"

    result = await native_loop.run(
        llm=LLM(), tools=[{"name": "read_repo_file"}], act=act, question="q", emit_step=emit_step, emit_facts=emit_facts,
    )
    assert result["final_answer"] == "finished"
    assert [s for s, _ in facts] == steps and len(steps) == 2
    assert tools_seen == [("read_repo_file", "a.py"), ("read_repo_file", "b.py")]
    assert "Retries so far this turn: 1" in facts[1][1] and not any("Retries" in line for line in facts[0][1])


def test_no_trace_fact_helper_logs_anything():
    import ast
    import inspect
    for fn in (au.build_tool_facts, au.clean_trace_facts, au.mask_trace_arg, au.emit_trace_facts):
        tree = ast.parse(inspect.getsource(fn).lstrip())
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"logger", "log"}]
