"""A draft kept inside the tool loop, and the halfway nudge that asks for one (backend/utils/draft_checkpoint.py; the failure it answers is in that file's docstring)."""
import asyncio
import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from backend.services import agent_workflow as aw
from backend.services import native_loop as nl
from backend.utils import draft_checkpoint as dc


# ---- the pieces --------------------------------------------------------------------------------------------------------------------------------------

def test_a_draft_is_taken_trimmed_and_capped_and_only_from_text():
    assert dc.take_draft("  def f():\n    return 1\n ") == "def f():\n    return 1"
    assert dc.take_draft("") is None and dc.take_draft("   ") is None and dc.take_draft(None) is None and dc.take_draft(["x"]) is None
    assert len(dc.take_draft("x" * 50_000)) == dc.MAX_DRAFT_CHARS
    assert dc.take_draft({"draft": "a"}) == "a" and dc.take_draft({"text": "b"}) == "b"


def test_the_nudge_waits_for_the_halfway_step_and_some_reading():
    assert [dc.halfway_step(b) for b in (2, 4, 6, 8)] == [1, 1, 2, 3]
    assert dc.halfway_notice(0, 6, 0, False, False) is None                  # the first step
    assert dc.halfway_notice(1, 6, 2, False, False) is None                  # before halfway
    assert dc.halfway_notice(2, 6, 0, False, False) is None                  # nothing read yet
    notice = dc.halfway_notice(2, 6, 2, False, False)
    assert notice and "Halfway" in notice and "2 of 5 tool steps" in notice and "ignore this" in notice
    assert dc.halfway_notice(3, 6, 3, False, False)                          # and again at later steps while there is no draft


def test_there_is_no_nudge_once_a_draft_exists_or_on_the_forced_last_step():
    assert dc.halfway_notice(3, 6, 3, True, False) is None
    assert dc.halfway_notice(5, 6, 5, False, True) is None


def test_the_draft_is_shown_back_with_what_to_do_with_it_and_an_unfinished_one_is_labelled():
    assert dc.draft_block(None) == ""
    block = dc.draft_block("code here")
    assert "YOUR DRAFT SO FAR" in block and "code here" in block and "must exist as you wrote it" in block
    assert dc.forced_final_addition(None) == "" and "Start your answer from it" in dc.forced_final_addition("x")
    assert dc.fallback_answer(None) is None
    assert dc.fallback_answer("code").startswith(dc.UNFINISHED_LABEL) and dc.fallback_answer("code").endswith("code")


# ---- the old (JSON) loop -------------------------------------------------------------------------------------------------------------------------------

def _read(path, **extra):
    return SimpleNamespace(content=json.dumps({"action": "query", "purpose": "read", "tool_action": "read_repo_file", "args": {"path": path}, **extra}))


def _final(text, **extra):
    return SimpleNamespace(content=json.dumps({"action": "final", "answer": text, **extra}))


def _react(responses, max_iterations=6):
    prompts = []

    async def fake(prompt):
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    async def act(decision):
        return "def existing():\n    return 1\n"

    async def go():
        original = aw.lite_llm.ainvoke
        aw.lite_llm.ainvoke = fake
        try:
            return await aw.run_react_loop(question="write a route", schema="s", prompt_template="{question} | {attempts}", act=act, max_iterations=max_iterations, node_name="t")
        finally:
            aw.lite_llm.ainvoke = original

    return asyncio.run(go()), prompts


def test_reading_without_a_draft_gets_the_halfway_nudge_but_not_before_halfway():
    _, prompts = _react([_read("a.py"), _read("b.py"), _read("c.py"), _read("d.py"), _read("e.py"), _final("done")])
    assert "Halfway" not in prompts[0] and "Halfway" not in prompts[1]
    assert "Halfway" in prompts[2]                                      # step 3 of 5 tool steps, with two files read
    assert "Halfway" not in prompts[-1]                                 # the forced last step carries the final notice instead


def test_a_draft_on_a_step_is_shown_back_at_every_later_step_and_stops_the_nudge():
    _, prompts = _react([_read("a.py"), _read("b.py", draft="def route():\n    return 1"), _read("c.py"), _read("d.py"), _read("e.py"), _final("done")])
    assert "YOUR DRAFT SO FAR" not in prompts[1]
    assert all("def route():" in prompt and "YOUR DRAFT SO FAR" in prompt for prompt in prompts[2:])
    assert all("Halfway" not in prompt for prompt in prompts)


def test_a_corrected_draft_replaces_the_earlier_one():
    _, prompts = _react([_read("a.py", draft="first version"), _read("b.py", draft="second version"), _read("c.py"), _final("done")], max_iterations=5)
    assert "first version" in prompts[1] and "second version" not in prompts[1]
    assert "second version" in prompts[2] and "first version" not in prompts[2]


def test_the_forced_last_step_is_told_to_start_from_the_draft():
    _, prompts = _react([_read("a.py", draft="def route(): ..."), _read("b.py"), _read("c.py"), _final("done")], max_iterations=4)
    assert "Start your answer from it" in prompts[-1] and "def route(): ..." in prompts[-1]


def test_a_final_does_not_keep_a_draft_and_the_answer_is_returned_as_given():
    result, _ = _react([_read("a.py"), _final("the answer", draft="ignored")])
    assert result["final_answer"] == "the answer"


def test_a_loop_cut_off_with_no_answer_returns_the_draft_labelled_as_unfinished():
    result, _ = _react([_read("a.py", draft="def route(): ..."), _read("b.py"), _read("c.py"), _final("")], max_iterations=4)
    assert result["final_answer"].startswith(dc.UNFINISHED_LABEL) and "def route(): ..." in result["final_answer"]


def test_the_prompt_schema_documents_the_draft():
    from backend.components.constraints import TOOL_AGENT_PROMPT
    assert '"draft"' in TOOL_AGENT_PROMPT and "DRAFTING A CHANGE" in TOOL_AGENT_PROMPT


# ---- the native loop --------------------------------------------------------------------------------------------------------------------------------------

class ToolLLM:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if not tools:
            return self.responses[-1]
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


TOOLS = [{"name": "search_code", "description": "find things"}, {"name": "read_repo_file", "description": "read a file"}]


def _call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}", "type": "tool_call"}


class Acts:
    def __init__(self):
        self.decisions = []

    async def __call__(self, decision):
        self.decisions.append(decision)
        return "result"


def _native(llm, tools=TOOLS, max_steps=6, act=None):
    return asyncio.run(nl.run(llm=llm, tools=tools, act=act or Acts(), question="add a route", max_steps=max_steps))


def test_the_save_draft_tool_is_offered_next_to_the_repository_tools_only():
    llm = ToolLLM(AIMessage(content="done"))
    _native(llm)
    assert [t["name"] for t in llm.calls[0][1]] == ["search_code", "read_repo_file", "save_draft"]
    other = ToolLLM(AIMessage(content="done"))
    _native(other, tools=[{"name": "web_search", "description": "search"}])
    assert [t["name"] for t in other.calls[0][1]] == ["web_search"]


def test_a_saved_draft_costs_no_dispatch_and_is_not_listed_as_a_step():
    llm = ToolLLM(AIMessage(content="", tool_calls=[_call("read_repo_file", {"path": "a.py"}), _call("save_draft", {"text": "def route(): ..."})]), AIMessage(content="answer"))
    act = Acts()
    result = _native(llm, act=act)
    assert [d["tool_action"] for d in act.decisions] == ["read_repo_file"]
    assert [a["action_desc"] for a in result["attempts"]] == ["read_repo_file(path=a.py)"]
    tool_message = llm.calls[1][0][-1]
    assert tool_message.name == "save_draft" and "Draft saved" in tool_message.content


def test_an_empty_draft_is_an_error_the_model_can_fix():
    llm = ToolLLM(AIMessage(content="", tool_calls=[_call("save_draft", {"text": ""})]), AIMessage(content="answer"))
    _native(llm)
    assert "ERROR" in llm.calls[1][0][-1].content


def test_the_native_halfway_nudge_comes_once_with_reading_done_and_no_draft():
    reads = [AIMessage(content="", tool_calls=[_call("read_repo_file", {"path": f"{n}.py"}, id=f"r{n}")]) for n in range(5)]
    llm = ToolLLM(*reads, AIMessage(content="answer"))
    _native(llm, max_steps=6)
    nudges = [m for m in llm.calls[-1][0] if "Halfway" in str(m.content)]
    assert len(nudges) == 1 and "save_draft" in str(nudges[0].content)
    assert not any("Halfway" in str(m.content) for m in llm.calls[0][0])


def test_a_native_loop_cut_off_by_the_step_limit_is_told_to_start_from_its_draft():
    steps = [AIMessage(content="", tool_calls=[_call("save_draft", {"text": "def route(): ..."}, id="d")])] + [
        AIMessage(content="", tool_calls=[_call("read_repo_file", {"path": f"{n}.py"}, id=f"r{n}")]) for n in range(5)
    ]
    llm = ToolLLM(*steps, AIMessage(content="final"))
    result = _native(llm, max_steps=3)
    last_messages = llm.calls[-1][0]
    assert llm.calls[-1][1] == [] and "def route(): ..." in str(last_messages[-1].content) and "Start your answer from it" in str(last_messages[-1].content)
    assert result["final_answer"] == "final"
