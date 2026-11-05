import asyncio

import pytest
from langchain_core.messages import AIMessage

from backend.services import native_loop as nl
from backend.services import native_tools as nt
from backend.utils import self_counters as sc

SOLO_TOOLS = [{"name": "search_code"}, {"name": "run_repo_tests"}, {"name": "run_snippet"}]


def run(coro):
    return asyncio.run(coro)


def call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}", "type": "tool_call"}


def asks(*calls, text=""):
    return AIMessage(content=text, tool_calls=list(calls))


def answers(text):
    return AIMessage(content=text)


class ToolLLM:
    """A scripted tool-calling model: each response is an AIMessage (tool calls or text)."""

    def __init__(self, *responses):
        self.responses = list(responses)

    async def ainvoke_tools(self, messages, tools):
        if not tools:
            return self.responses[-1]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

CI_TOOLS = [{"name": "search_code"}, {"name": "run_repo_tests"}]
PLAIN_TOOLS = [{"name": "search_code"}]


@pytest.fixture(autouse=True)
def native_on(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")


def test_ci_tools_schema_and_solo():
    assert "run_repo_tests" in nt.REGISTRY
    assert "run_snippet" in nt.REGISTRY
    assert "run_repo_tests" in nt.NATIVE_DESCRIPTIONS
    assert "run_snippet" in nt.NATIVE_DESCRIPTIONS
    assert nl._SOLO_TOOLS >= {"run_mongo_query", "run_repo_tests", "run_snippet"}



def test_ci_counter_keys_are_registered():
    assert "run_repo_tests_native" in sc.CI_KEYS
    assert "run_snippet_native" in sc.CI_KEYS
    assert "run_repo_tests_native" in sc.KEYS
    assert "run_snippet_native" in sc.KEYS


def test_ci_tools_bump_native_counters_once_per_call(monkeypatch):
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))

    async def act(decision):
        return "ok"

    llm = ToolLLM(asks(call("run_repo_tests", {"path": "backend/tests"}, id="t1")), asks(call("run_snippet", {"code": "print(1)"}, id="s1")), answers("done"))
    run(nl.run(llm=llm, tools=SOLO_TOOLS, act=act, question="run the tests and a snippet"))
    counts = recorded[-1]
    assert counts["run_repo_tests_native"] == 1 and counts["run_snippet_native"] == 1


def test_ci_tools_start_only_after_other_calls_finish():
    events = []

    async def act(decision):
        name = decision["tool_action"]
        events.append(("start", name))
        await asyncio.sleep(0.05)
        events.append(("end", name))
        return "ok"

    llm = ToolLLM(
        asks(call("search_code", {"query": "a"}, id="a"), call("run_repo_tests", {"path": "x"}, id="b"), call("run_snippet", {"code": "1"}, id="c")),
        answers("done"),
    )
    run(nl.run(llm=llm, tools=SOLO_TOOLS, act=act, question="search, test and run"))
    other_end = events.index(("end", "search_code"))
    for solo in ("run_repo_tests", "run_snippet"):
        assert events.index(("start", solo)) > other_end, solo
    tests_end, snippet_end = events.index(("end", "run_repo_tests")), events.index(("end", "run_snippet"))
    tests_start, snippet_start = events.index(("start", "run_repo_tests")), events.index(("start", "run_snippet"))
    assert tests_end < snippet_start or snippet_end < tests_start, "the two solo tools overlapped"
