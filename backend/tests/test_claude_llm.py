import asyncio
import logging
import os
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.models import claude_llm
from backend.models import models as models_module
from backend.models.claude_llm import ClaudeLLM

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-not-a-real-key")
    monkeypatch.delenv("TOOL_AGENT_PROVIDER", raising=False)
    monkeypatch.setattr(models_module, "_claude_tool_llms", {})


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr("backend.utils.llm_usage.record", lambda model, label, usage, day=None: calls.append((model, label, usage)))
    return calls


class FakeChat:
    """Stands in for ChatAnthropic: records what it was asked and answers with a message that carries usage and the model that served it."""

    def __init__(self, reply=None, error=None):
        self.reply = reply or AIMessage(
            content=[{"type": "thinking", "thinking": "", "signature": "x"}, {"type": "text", "text": "done"}],
            usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150, "input_token_details": {"cache_read": 100}},
            response_metadata={"model_name": "claude-haiku-5-5"},
        )
        self.error, self.seen, self.bound = error, [], []

    async def ainvoke(self, messages, *args, **kwargs):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return self.reply

    def invoke(self, messages, *args, **kwargs):
        self.seen.append(messages)
        if self.error:
            raise self.error
        return self.reply

    def bind_tools(self, tools):
        self.bound.append(tools)
        return self


class FakeGemini:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, prompt, *args, **kwargs):
        self.calls.append(("ainvoke", prompt))
        return AIMessage(content="from gemini")

    def invoke(self, prompt, *args, **kwargs):
        self.calls.append(("invoke", prompt))
        return AIMessage(content="from gemini")

    async def ainvoke_tools(self, messages, tools):
        self.calls.append(("ainvoke_tools", messages, tools))
        return AIMessage(content="from gemini")

    def astream(self):
        return "gemini stream"


def claude(chat, fallback=None):
    llm = ClaudeLLM(fallback=fallback)
    llm._chat = chat
    return llm


TOOLS = [
    {"name": "list_repo_tree", "description": "lists files"},
    {"name": "read_repo_file", "description": "reads one", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
]


# ---------------------------------------------------------------------------------------------------------------
# What it sends
# ---------------------------------------------------------------------------------------------------------------

def test_a_tool_with_no_arguments_gets_the_empty_schema_claude_requires():
    """Regression from the first live run: list_repo_tree has no `parameters` for Gemini, and Claude's format raised KeyError, so every call fell back."""
    converted = ClaudeLLM.anthropic_tools(TOOLS)
    assert converted[0] == {"name": "list_repo_tree", "description": "lists files", "input_schema": {"type": "object", "properties": {}}}
    assert converted[1]["input_schema"]["required"] == ["path"] and "parameters" not in converted[1]


def test_a_tool_without_a_description_still_converts():
    assert ClaudeLLM.anthropic_tools([{"name": "check_deploy_status"}])[0]["description"] == "check_deploy_status"


def test_the_client_is_built_without_a_temperature_because_the_model_rejects_one(monkeypatch):
    built = {}

    class Recorder:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", Recorder)
    ClaudeLLM(effort="low", thinking_off=True)._client()
    assert "temperature" not in built and built["model"] == "claude-haiku-5-5" and built["effort"] == "low" and built["thinking"] == {"type": "disabled"}
    built.clear()
    ClaudeLLM(effort=None, thinking_off=False)._client()
    assert "effort" not in built and "thinking" not in built


def test_the_key_is_read_from_either_name_and_never_required_to_import():
    assert claude_llm.api_key() == "sk-ant-not-a-real-key"


def test_the_standard_name_wins_when_both_are_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-the-standard-one")
    assert claude_llm.api_key() == "sk-ant-the-standard-one"


def test_no_key_means_not_available(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert claude_llm.api_key() is None and ClaudeLLM().available() is False


def test_a_plain_prompt_is_sent_as_one_human_message_and_a_conversation_as_it_is(recorded):
    chat = FakeChat()
    llm = claude(chat)
    asyncio.run(llm.ainvoke("hello there"))
    llm.invoke("hello again")
    history = [HumanMessage(content="a"), AIMessage(content="b")]
    asyncio.run(llm.ainvoke(history))
    assert [type(m[0]).__name__ for m in chat.seen[:2]] == ["HumanMessage", "HumanMessage"] and chat.seen[0][0].content == "hello there"
    assert chat.seen[2] is history


def test_tools_are_bound_in_claudes_shape_once_per_tool_set(recorded):
    chat = FakeChat()
    llm = claude(chat)
    asyncio.run(llm.ainvoke_tools([HumanMessage(content="x")], TOOLS))
    asyncio.run(llm.ainvoke_tools([HumanMessage(content="y")], TOOLS))
    assert len(chat.bound) == 1 and chat.bound[0][0]["input_schema"] == {"type": "object", "properties": {}}


def test_with_no_tools_the_plain_model_answers(recorded):
    chat = FakeChat()
    reply = asyncio.run(claude(chat).ainvoke_tools([HumanMessage(content="answer now")], []))
    assert chat.bound == [] and reply is chat.reply


# ---------------------------------------------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------------------------------------------

def test_a_call_is_counted_under_the_model_that_served_it_so_it_can_be_priced(recorded):
    asyncio.run(claude(FakeChat()).ainvoke_tools([HumanMessage(content="x")], TOOLS))
    model, label, usage = recorded[0]
    assert model == "claude-haiku-5-5" and usage["input"] == 120 and usage["output"] == 30 and usage["cached"] == 100
    assert not label.startswith("claude_llm."), "the label must name the code that asked, not this plumbing"


def test_the_loops_read_only_the_text_blocks_so_thinking_blocks_are_ignored(recorded):
    from backend.services import native_loop

    reply = asyncio.run(claude(FakeChat()).ainvoke_tools([HumanMessage(content="x")], TOOLS))
    assert native_loop._text(reply.content) == "done"


# ---------------------------------------------------------------------------------------------------------------
# The fallback
# ---------------------------------------------------------------------------------------------------------------

def test_a_claude_failure_is_answered_by_gemini_with_the_same_arguments(recorded):
    gemini = FakeGemini()
    llm = claude(FakeChat(error=RuntimeError("overloaded")), fallback=gemini)
    messages = [HumanMessage(content="x")]
    assert asyncio.run(llm.ainvoke_tools(messages, TOOLS)).content == "from gemini"
    assert asyncio.run(llm.ainvoke("a prompt")).content == "from gemini"
    assert llm.invoke("another").content == "from gemini"
    assert gemini.calls == [("ainvoke_tools", messages, TOOLS), ("ainvoke", "a prompt"), ("invoke", "another")]
    assert recorded == [], "nothing is counted for Claude when Claude gave no answer"


def test_with_no_key_gemini_answers_and_claude_is_never_contacted(monkeypatch, recorded):
    monkeypatch.delenv("CLAUDE_API_KEY")
    chat, gemini = FakeChat(), FakeGemini()
    assert asyncio.run(claude(chat, fallback=gemini).ainvoke_tools([HumanMessage(content="x")], TOOLS)).content == "from gemini"
    assert chat.seen == []


def test_with_no_fallback_a_failure_is_raised_not_hidden(recorded):
    with pytest.raises(RuntimeError, match="no fallback"):
        asyncio.run(claude(FakeChat(error=ValueError("x"))).ainvoke("hi"))


def test_a_failure_is_logged_by_kind_only_never_by_message(recorded, caplog):
    caplog.set_level(logging.DEBUG, logger="SASS Logger")
    llm = claude(FakeChat(error=RuntimeError("the prompt said SECRETWORDS and the key sk-ant-xyz")), fallback=FakeGemini())
    asyncio.run(llm.ainvoke("SECRETWORDS"))
    assert "RuntimeError" in caplog.text and "SECRETWORDS" not in caplog.text and "sk-ant" not in caplog.text


def test_anything_else_is_delegated_to_the_fallback_or_missing():
    assert ClaudeLLM(fallback=FakeGemini()).astream() == "gemini stream"
    with pytest.raises(AttributeError):
        ClaudeLLM().astream
    with pytest.raises(AttributeError):
        ClaudeLLM(fallback=FakeGemini())._private


# ---------------------------------------------------------------------------------------------------------------
# Choosing the model
# ---------------------------------------------------------------------------------------------------------------

def test_nothing_set_means_the_tool_agent_is_exactly_what_it_was():
    assert models_module.tool_agent_llm(False) is models_module.lite_llm
    assert models_module.tool_agent_llm(True) is models_module.lite_llm_deep


def test_the_anthropic_provider_gives_claude_with_the_matching_gemini_model_as_its_fallback(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_PROVIDER", "anthropic")
    ordinary, deep = models_module.tool_agent_llm(False), models_module.tool_agent_llm(True)
    assert isinstance(ordinary, ClaudeLLM) and isinstance(deep, ClaudeLLM)
    assert ordinary.fallback is models_module.lite_llm and deep.fallback is models_module.lite_llm_deep
    assert models_module.tool_agent_llm(False) is ordinary, "one instance per mode, not one per turn"


def test_the_provider_switch_without_a_key_stays_on_gemini(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_PROVIDER", "anthropic")
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert models_module.tool_agent_llm(False) is models_module.lite_llm


def test_any_other_provider_value_stays_on_gemini(monkeypatch):
    monkeypatch.setenv("TOOL_AGENT_PROVIDER", "something-else")
    assert models_module.tool_agent_llm(True) is models_module.lite_llm_deep


def test_the_tool_agent_asks_for_its_model_through_the_switch():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'llm=tool_agent_llm(bool(deep_thinking), state.get("tool_model")),' in source


def test_the_packages_are_declared():
    lines = open(os.path.join(REPO, "requirements.txt"), encoding="utf-8").read().split()
    assert "anthropic" in lines and "langchain-anthropic" in lines


# ---------------------------------------------------------------------------------------------------------------
# Prompt caching
# ---------------------------------------------------------------------------------------------------------------

def test_automatic_caching_is_one_top_level_field_and_on_by_default(monkeypatch):
    built = {}

    class Recorder:
        def __init__(self, **kwargs):
            built.update(kwargs)

    monkeypatch.setattr("langchain_anthropic.ChatAnthropic", Recorder)
    ClaudeLLM()._client()
    assert built["model_kwargs"] == {"cache_control": {"type": "ephemeral"}}
    built.clear()
    ClaudeLLM(cache=False)._client()
    assert "model_kwargs" not in built


def test_a_model_that_caches_tells_the_loop_to_stop_editing_old_results():
    assert ClaudeLLM(cache=True).caches_prompt is True
    assert ClaudeLLM(cache=False).caches_prompt is False


def test_without_a_key_nothing_is_cached_so_the_gemini_fallback_keeps_its_shortening(monkeypatch):
    monkeypatch.delenv("CLAUDE_API_KEY")
    assert ClaudeLLM(cache=True).caches_prompt is False


def test_the_tool_agent_hands_the_loop_the_right_shortening_switch():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'shorten_old=not (isinstance(kwargs["llm"], ClaudeLLM) and kwargs["llm"].caches_prompt)' in source


def test_the_prompt_tells_the_model_that_writing_code_in_the_answer_is_the_job():
    from backend.services import native_loop

    assert "You CAN write code for the user" in native_loop.SYSTEM_PROMPT and "following the conventions of the files you read" in native_loop.SYSTEM_PROMPT
    assert "never say you edited, ran or applied something you did not" in native_loop.SYSTEM_PROMPT
