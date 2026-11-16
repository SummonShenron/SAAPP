"""What the server log says about a tool's result (native_loop, one INFO line per tool call).

A tool nearly always fails by RETURNING "ERROR: ...", not by raising, and nothing logged that: a page that could not be read looked like a page that was read, in the trace and in Render's logs. The line carries
the tool's own error text (system-written, what debugging needs) or only a size, and never a web address or any of what a tool read (backend/tests/test_log_hygiene.py is the rule)."""
import asyncio
import logging

from backend.services import native_loop
from backend.utils.log_hygiene import describe_tool_result

PAGE = "https://secret-site.example/private/report?token=abc123"
BLOCKED = f"ERROR: could not read {PAGE}: the site answered HTTP 403; the browser could not load it either (net::ERR_BLOCKED_BY_RESPONSE)"


def test_an_error_keeps_its_reason_and_loses_the_address_and_a_success_is_only_a_size():
    described = describe_tool_result(BLOCKED)
    assert "HTTP 403" in described and "ERR_BLOCKED_BY_RESPONSE" in described and described.startswith("ERROR: could not read <url>")
    assert "secret-site" not in described and "abc123" not in described and "private" not in described
    not_an_error = "could not resolve the host 'secret-site.example'"
    assert describe_tool_result(not_an_error) == f"{len(not_an_error)} chars"          # not an ERROR: only its size
    assert "secret-site" not in describe_tool_result("ERROR: could not read it: could not resolve the host 'secret-site.example'")
    assert describe_tool_result("URL: " + PAGE + "\nthe full text of a private report") == f"{len('URL: ' + PAGE + chr(10) + 'the full text of a private report')} chars"
    assert describe_tool_result(None) == "0 chars" and describe_tool_result(["a", "b"]).endswith("chars")
    assert len(describe_tool_result("ERROR: " + "x" * 5000)) <= 200            # an error is cut, never a whole page of one


class Reply:
    def __init__(self, calls=(), content=""):
        self.tool_calls, self.content = list(calls), content


class ScriptedLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    async def ainvoke_tools(self, messages, tools):
        return self.replies.pop(0)


def _run(result):
    async def act(decision):
        return result

    replies = [Reply([{"name": "read_url", "args": {"url": PAGE}, "id": "1"}]), Reply([], "done")]
    return asyncio.run(native_loop.run(llm=ScriptedLLM(replies), tools=[{"name": "read_url"}], act=act, question="read the report UNIQUE_QUESTION_MARKER", max_steps=6))


def test_a_returned_error_is_logged_with_its_reason_and_without_the_address(monkeypatch, caplog):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    with caplog.at_level(logging.INFO, logger="SASS Logger"):
        _run(BLOCKED)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("[native_loop] tool read_url"))
    assert "HTTP 403" in line and "ERR_BLOCKED_BY_RESPONSE" in line and "<url>" in line
    assert "secret-site" not in caplog.text and "abc123" not in caplog.text and "UNIQUE_QUESTION_MARKER" not in caplog.text
    assert next(r for r in caplog.records if r.getMessage() == line).levelno == logging.INFO


def test_a_successful_read_is_logged_as_a_size_and_nothing_it_said(monkeypatch, caplog):
    monkeypatch.setenv("TOOL_AGENT_CHECKPOINTS", "off")
    with caplog.at_level(logging.INFO, logger="SASS Logger"):
        _run("URL: " + PAGE + "\nPRIVATE_PAGE_TEXT " * 50)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("[native_loop] tool read_url"))
    assert " chars (" in line
    assert "PRIVATE_PAGE_TEXT" not in caplog.text and "secret-site" not in caplog.text
