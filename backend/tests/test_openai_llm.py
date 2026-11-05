import asyncio
import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.evals import eval_models as em
from backend.models import openai_llm as ol
from backend.utils import llm_usage


@pytest.fixture(autouse=True)
def _a_key_but_no_network(monkeypatch):
    """Every test has a fake key and a fake client: nothing here can reach OpenAI."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-not-a-real-key-for-tests")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


def reply(text="ok", *, model="gpt-5.4-mini-2026-03-17", usage=None, tool_calls=None):
    message = AIMessage(content=text, tool_calls=tool_calls or [], response_metadata={"model_name": model},
                        usage_metadata=usage or {"input_tokens": 1000, "output_tokens": 50, "total_tokens": 1050,
                                                 "input_token_details": {"cache_read": 400}, "output_token_details": {"reasoning": 20}})
    return message


class FakeClient:
    """Stands in for ChatOpenAI: answers what it is told to, remembers what it was asked."""

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.calls, self.bound = answer or reply(), error, [], []

    def invoke(self, messages, *args, **kwargs):
        self.calls.append(messages)
        if self.error:
            raise self.error
        return self.answer

    async def ainvoke(self, messages, *args, **kwargs):
        return self.invoke(messages)

    def bind_tools(self, tools):
        self.bound.append(tools)
        return self


def llm_with(client, **kwargs):
    llm = ol.OpenAILLM(**kwargs)
    llm._chat = client
    return llm


@pytest.fixture
def recorded(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_usage, "record", lambda model, label, counts, day=None: seen.append((model, counts)))
    return seen


# ---- set-up ------------------------------------------------------------------------------------------------------------

def test_the_key_is_read_from_the_environment_and_only_its_presence_decides_availability(monkeypatch):
    assert ol.api_key() == "sk-not-a-real-key-for-tests" and ol.OpenAILLM().available() is True
    monkeypatch.setenv("OPENAI_API_KEY", "   ")
    assert ol.api_key() is None and ol.OpenAILLM().available() is False
    monkeypatch.delenv("OPENAI_API_KEY")
    assert ol.OpenAILLM().available() is False


def test_an_unknown_reasoning_effort_is_refused_and_the_documented_ones_are_accepted():
    for effort in (None, "none", "low", "medium", "high", "xhigh"):
        assert ol.OpenAILLM(effort=effort).effort == effort
    with pytest.raises(ValueError, match="reasoning effort"):
        ol.OpenAILLM(effort="minimal")       # not listed for this model on OpenAI's page


def test_the_real_client_is_built_with_the_token_limit_and_effort_and_no_temperature():
    client = ol.OpenAILLM(max_tokens=700, effort="low")._client()
    payload = client._get_request_payload([HumanMessage(content="hi")])
    assert payload["model"] == "gpt-5.4-mini" and payload["max_completion_tokens"] == 700 and payload["reasoning_effort"] == "low"
    assert "temperature" not in payload and "max_tokens" not in payload
    default = ol.OpenAILLM()._client()._get_request_payload([HumanMessage(content="hi")])
    assert "reasoning_effort" not in default             # unset leaves the model's own default (none)


def test_a_plain_prompt_and_a_message_list_each_become_messages():
    assert ol._messages("hello")[0].content == "hello"
    conversation = [HumanMessage(content="a"), AIMessage(content="b")]
    assert ol._messages(conversation) is conversation
    assert ol.OpenAILLM().caches_prompt is False


def test_tool_declarations_are_converted_to_openais_shape_and_a_tool_without_parameters_gets_an_empty_schema():
    tools = [{"name": "read_repo_file", "description": "read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
             {"name": "list_repo_tree"}]
    converted = ol.OpenAILLM.openai_tools(tools)
    assert converted[0] == {"type": "function", "function": {"name": "read_repo_file", "description": "read a file", "parameters": tools[0]["parameters"]}}
    assert converted[1]["function"] == {"name": "list_repo_tree", "description": "list_repo_tree", "parameters": {"type": "object", "properties": {}}}
    real = ol.OpenAILLM()._client().bind_tools(converted)         # the installed langchain-openai accepts exactly this shape
    assert len(real.kwargs["tools"]) == 2


def test_only_a_tool_call_with_a_reasoning_effort_uses_the_responses_endpoint():
    """Found against the live API 2026-11-02: Chat Completions refuses function tools together with a reasoning effort other than none."""
    for effort, has_tools, expected in [(None, True, False), ("none", True, False), ("low", True, True), ("medium", True, True), ("xhigh", True, True),
                                        ("low", False, False), (None, False, False), ("none", False, False)]:
        assert ol.OpenAILLM(effort=effort).uses_responses(has_tools) is expected, (effort, has_tools)


def test_the_responses_client_carries_the_effort_and_the_token_limit_the_way_that_endpoint_names_them():
    client = ol.OpenAILLM(max_tokens=700, effort="low")._client(responses=True)
    payload = client._get_request_payload([HumanMessage(content="hi")])
    assert payload["model"] == "gpt-5.4-mini" and payload["reasoning"] == {"effort": "low"} and payload["max_output_tokens"] == 700
    assert "temperature" not in payload and "reasoning_effort" not in payload
    chat = ol.OpenAILLM(max_tokens=700, effort="low")._client()
    assert chat is not client and "reasoning_effort" in chat._get_request_payload([HumanMessage(content="hi")])      # a plain call with effort stays on Chat Completions


def test_a_tool_call_at_an_effort_is_bound_on_the_responses_client_and_a_plain_call_stays_on_chat():
    chat, responses = FakeClient(reply("plain")), FakeClient(reply("tooled", tool_calls=[{"name": "read_repo_file", "args": {}, "id": "c1"}]))
    llm = ol.OpenAILLM(effort="low")
    llm._chat, llm._responses_chat = chat, responses
    tools = [{"name": "read_repo_file", "description": "read", "parameters": {"type": "object", "properties": {}}}]
    assert asyncio.run(llm.ainvoke_tools([HumanMessage(content="x")], tools)).content == "tooled"
    assert len(responses.bound) == 1 and chat.bound == []
    assert asyncio.run(llm.ainvoke_tools([HumanMessage(content="x")], [])).content == "plain"      # no tools: Chat Completions, effort allowed
    assert asyncio.run(llm.ainvoke("a reply")).content == "plain"
    # at effort none the same tools go to Chat Completions
    none = ol.OpenAILLM(effort="none")
    none._chat, none._responses_chat = FakeClient(reply("chat")), FakeClient(reply("responses"))
    assert asyncio.run(none.ainvoke_tools([HumanMessage(content="x")], tools)).content == "chat"


# ---- calls and metering ------------------------------------------------------------------------------------------------

def test_invoke_returns_the_answer_and_meters_the_call_under_the_configured_name(recorded):
    llm = llm_with(FakeClient(reply("Fine.")))
    assert llm.invoke("a prompt").content == "Fine."
    assert recorded == [("gpt-5.4-mini", {"input": 1000, "output": 50, "reasoning": 20, "cached": 400})]   # not the dated snapshot OpenAI reports


def test_a_dated_snapshot_is_metered_as_the_model_but_another_model_is_metered_as_itself(recorded):
    llm = llm_with(FakeClient())
    assert llm._served_as(reply(model="gpt-5.4-mini-2026-03-17")) == "gpt-5.4-mini"
    assert llm._served_as(reply(model="gpt-5.4-mini")) == "gpt-5.4-mini"
    assert llm._served_as(reply(model="gemini-3.1-flash-lite")) == "gemini-3.1-flash-lite"     # a fallback's own name
    assert llm._served_as(AIMessage(content="x")) == "gpt-5.4-mini"                              # nothing reported: the configured name


def test_the_metered_name_is_one_the_price_tables_know():
    for name in ("gpt-5.4-mini",):
        assert name in em.prices()
    from backend.models.openai_llm import DEFAULT_MODEL
    assert DEFAULT_MODEL in em.CANDIDATES


def test_a_failure_in_the_meter_never_breaks_a_call(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("the usage store is down")

    monkeypatch.setattr(llm_usage, "record", broken)
    assert llm_with(FakeClient(reply("still answered"))).invoke("x").content == "still answered"


def test_ainvoke_and_ainvoke_tools_work_the_same_way_and_bind_each_tool_set_once(recorded):
    client = FakeClient(reply("done", tool_calls=[{"name": "read_repo_file", "args": {"path": "a.py"}, "id": "call_1"}]))
    llm = llm_with(client)
    tools = [{"name": "read_repo_file", "description": "read", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}]
    first = asyncio.run(llm.ainvoke_tools([HumanMessage(content="read a.py")], tools))
    second = asyncio.run(llm.ainvoke_tools([HumanMessage(content="again")], tools))
    assert first.tool_calls[0]["name"] == "read_repo_file" and second.tool_calls
    assert len(client.bound) == 1 and client.bound[0][0]["function"]["name"] == "read_repo_file"      # bound once, reused
    assert asyncio.run(llm.ainvoke("plain")).content == "done" and len(recorded) == 3
    assert asyncio.run(llm.ainvoke_tools([HumanMessage(content="no tools")], [])).content == "done"    # an empty tool list is a plain call


# ---- streaming ---------------------------------------------------------------------------------------------------------

from langchain_core.messages import AIMessageChunk


def chunk(text, usage=None, model=None):
    return AIMessageChunk(content=text, usage_metadata=usage, response_metadata={"model_name": model} if model else {})


class StreamingClient:
    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after, self.sent = chunks, fail_after, []

    async def astream(self, messages, *args, **kwargs):
        self.sent.append(messages)
        for index, piece in enumerate(self.chunks):
            if self.fail_after is not None and index == self.fail_after:
                raise ValueError("stream broke")
            yield piece


async def collect(stream):
    return [piece async for piece in stream]


def test_a_streamed_reply_yields_its_chunks_and_records_the_usage_once_under_the_configured_name(recorded):
    pieces = [chunk("Fair "), chunk("question.", usage={"input_tokens": 900, "output_tokens": 12, "total_tokens": 912,
                                                         "input_token_details": {"cache_read": 300}, "output_token_details": {"reasoning": 0}},
                                    model="gpt-5.4-mini-2026-03-17")]
    llm = llm_with(StreamingClient(pieces))
    out = asyncio.run(collect(llm.astream("hello")))
    assert "".join(p.content for p in out) == "Fair question."
    assert len(recorded) == 1 and recorded[0][0] == "gpt-5.4-mini"
    assert {k: recorded[0][1][k] for k in ("input", "output", "reasoning", "cached")} == {"input": 900, "output": 12, "reasoning": 0, "cached": 300}      # the same usage dict a Gemini stream records


def test_usage_is_still_recorded_when_the_reader_stops_early(recorded):
    pieces = [chunk("one "), chunk("two ", usage={"input_tokens": 500, "output_tokens": 3, "total_tokens": 503}), chunk("three")]

    async def read_one():
        stream = llm_with(StreamingClient(pieces)).astream("hello")
        first = await stream.__anext__()
        second = await stream.__anext__()
        await stream.aclose()                                   # the Stop button
        return first.content + second.content

    assert asyncio.run(read_one()) == "one two "
    assert recorded and recorded[0][1]["input"] == 500           # what was reported was still billed


def test_a_stream_that_fails_before_any_text_is_answered_by_the_fallback_and_nothing_is_billed_to_openai(recorded):
    class StreamFallback:
        async def astream(self, prompt, *args, **kwargs):
            yield chunk("from the fallback")

    llm = llm_with(StreamingClient([chunk("x")], fail_after=0), fallback=StreamFallback())
    assert "".join(p.content for p in asyncio.run(collect(llm.astream("x")))) == "from the fallback"
    assert recorded == []


def test_a_stream_that_fails_after_text_started_raises_because_it_cannot_change_model_midway(recorded):
    llm = llm_with(StreamingClient([chunk("Hel", usage={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11}), chunk("lo")], fail_after=1), fallback=Fallback())
    seen = []

    async def go():
        async for piece in llm.astream("x"):
            seen.append(piece.content)

    with pytest.raises(ValueError, match="stream broke"):
        asyncio.run(go())
    assert seen == ["Hel"] and recorded and recorded[0][1]["input"] == 10        # the part already shown was billed


def test_streaming_with_no_key_and_no_fallback_raises_and_with_a_fallback_uses_it(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")

    class StreamFallback:
        async def astream(self, prompt, *args, **kwargs):
            yield chunk("fallback text")

    with pytest.raises(RuntimeError, match="OpenAI is unavailable"):
        asyncio.run(collect(llm_with(StreamingClient([chunk("x")])).astream("x")))
    assert asyncio.run(collect(llm_with(StreamingClient([chunk("x")]), fallback=StreamFallback()).astream("x")))[0].content == "fallback text"


# ---- failure, fallback and privacy ---------------------------------------------------------------------------------------

class Fallback:
    def __init__(self):
        self.calls = 0

    def invoke(self, prompt, *args, **kwargs):
        self.calls += 1
        return reply("from the fallback", model="gemini-3.1-flash-lite")

    async def ainvoke(self, prompt, *args, **kwargs):
        return self.invoke(prompt)

    async def ainvoke_tools(self, messages, tools):
        return self.invoke(messages)

    something_else = "a fallback attribute"


def test_a_failed_call_is_answered_by_the_fallback_and_with_no_fallback_it_raises():
    fallback = Fallback()
    llm = llm_with(FakeClient(error=RuntimeError("rate limited")), fallback=fallback)
    assert llm.invoke("x").content == "from the fallback" and asyncio.run(llm.ainvoke("x")).content == "from the fallback" and fallback.calls == 2
    assert asyncio.run(llm.ainvoke_tools([HumanMessage(content="x")], [])).content == "from the fallback"
    with pytest.raises(RuntimeError, match="no fallback model is configured"):
        llm_with(FakeClient(error=RuntimeError("rate limited"))).invoke("x")


def test_without_a_key_nothing_is_called_and_the_fallback_or_an_error_answers(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    client = FakeClient()
    with pytest.raises(RuntimeError, match="OpenAI is unavailable"):
        llm_with(client).invoke("x")
    assert client.calls == []
    assert llm_with(client, fallback=Fallback()).invoke("x").content == "from the fallback"


def test_the_log_names_the_kind_of_failure_and_never_what_was_sent_or_said(caplog):
    secret = "my private message about my doctor"
    llm = llm_with(FakeClient(error=ValueError(f"bad request: {secret}")), fallback=Fallback())
    with caplog.at_level(logging.WARNING, logger="SASS Logger"):
        llm.invoke(f"{secret} please")
    assert "ValueError" in caplog.text and secret not in caplog.text and "doctor" not in caplog.text


def test_other_attributes_belong_to_the_fallback_and_private_ones_are_never_forwarded():
    assert llm_with(FakeClient(), fallback=Fallback()).something_else == "a fallback attribute"
    with pytest.raises(AttributeError):
        llm_with(FakeClient()).something_else
    with pytest.raises(AttributeError):
        llm_with(FakeClient(), fallback=Fallback())._private


# ---- the eval harness wiring ---------------------------------------------------------------------------------------------

def test_gpt_5_4_mini_is_a_candidate_with_the_pages_prices_and_two_aliases():
    assert em.resolve("mini") == em.resolve("gpt") == em.resolve("gpt-5.4-mini") == "gpt-5.4-mini"
    assert em.CANDIDATES["gpt-5.4-mini"]["price"] == {"input": 0.75, "output": 4.50, "cached_input": 0.075}
    assert em.CANDIDATES["gpt-5.4-mini"]["provider"] == "openai"


def test_the_builders_make_an_openai_model_with_the_right_effort():
    reply_model, tool_model = em.make_llm("mini"), em.make_tool_llm("mini")
    assert isinstance(reply_model, ol.OpenAILLM) and reply_model.effort is None and reply_model.fallback is None
    assert isinstance(tool_model, ol.OpenAILLM) and tool_model.effort == em.TOOL_LOOP_OPENAI_EFFORT == "low" and tool_model.fallback is None
    fallback = object()
    assert em.make_tool_llm("mini", fallback=fallback).fallback is fallback


@pytest.mark.parametrize("name,env,message", [
    ("mini", "OPENAI_API_KEY", "no OpenAI key: set OPENAI_API_KEY in .env"),
    ("haiku", "ANTHROPIC_API_KEY", "no Claude key: set ANTHROPIC_API_KEY in .env"),
    ("gemini-3.5-flash-lite", "GOOGLE_API_KEY", "no Google key: set GOOGLE_API_KEY in .env"),
])
def test_a_missing_key_is_one_clear_sentence_and_a_present_one_is_never_read_out(monkeypatch, name, env, message):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(var, "something-present")
    assert em.missing_key(name) is None
    monkeypatch.delenv(env)
    if env == "ANTHROPIC_API_KEY":
        monkeypatch.delenv("CLAUDE_API_KEY")
    assert em.missing_key(name) == message


def test_the_harnesses_refuse_to_start_without_the_key_instead_of_failing_every_run(monkeypatch, capsys):
    from backend.evals import behavior_eval as be
    from backend.evals import character_eval as ce
    from backend.evals import routing_eval as re_

    monkeypatch.delenv("OPENAI_API_KEY")
    assert ce.main(["--model", "mini", "--only", "miss_bait"]) == 2
    assert re_.main(["--model", "mini", "--only", "greeting"]) == 2
    assert be.main(["--tool-model", "mini", "--only", be.scenario_ids()[0]]) == 2
    out = capsys.readouterr().out
    assert out.count("no OpenAI key: set OPENAI_API_KEY in .env") == 3
