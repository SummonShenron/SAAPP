"""OpenAI's GPT-5.4 mini behind the same calls the loops make on Claude and Gemini (docs/model-eval-plan.md): invoke, ainvoke and ainvoke_tools, with the same metering.

WHY. A candidate for two jobs, decided by the evals and never assumed: a middle tier for coding between Haiku ($0.10 / $0.50 per million input / output tokens) and Sonnet ($2.00 / $10.00), and a reply model that
might hold Sonic's voice where the cheaper models did not (2026-11-02: Haiku scored 78% and gemini-3.5-flash-lite 90% against Flash's 97% on the voice scenarios). GPT-5.4 mini lists at $0.75 / $0.075 cached / $4.50,
the same input price as Gemini 3.6 Flash today and about half of what Flash costs from 2027-01-01 (OpenAI's model page, read 2026-11-02; Google's pricing page for Flash).

HOW. The same contract as ClaudeLLM (backend/models/claude_llm.py): any failure (no key, rate limit, an overloaded provider, a bad request) is answered by the `fallback` model it was given, or raises when there is none,
so a comparison never quietly scores another model. A call is metered under the CONFIGURED model name, not the dated snapshot OpenAI reports ("gpt-5.4-mini-2026-03-17"), so it is priced from LLM_PRICES / eval_models and
counted by the budget guard. Nothing a person wrote is put in a log line: only the kind of failure.

STREAMING (astream) serves the reply the person reads when they choose GPT as their base model (models.reply_llm); a choice for it lives in the chat settings (the Base model column of the model chip).

NOTES from OpenAI's model page, the installed langchain-openai and the live API (2026-11-02). GPT-5.4 mini accepts reasoning effort none (its default), low, medium, high and xhigh; its page does not say whether
it accepts a temperature, so none is sent. THE TWO ENDPOINTS: on Chat Completions the API REFUSES function tools together with a reasoning effort other than none ("Function tools with reasoning_effort are not
supported for gpt-5.4-mini in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'"). So a tool call with an effort set goes through the Responses endpoint
(use_responses_api, reasoning={"effort": ...}, whose answers arrive as a list of content blocks, which the loops already read), and everything else (a reply, a call with no tools, a tool call at effort none)
stays on Chat Completions, whose limit goes as max_completion_tokens. Prompt caching is automatic for a repeated prefix. The key is read from OPENAI_API_KEY.
"""
import logging
import os
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage

from backend.utils import llm_usage

logger = logging.getLogger("SASS Logger")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4-mini")
DEFAULT_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "").strip().lower() or None     # none | low | medium | high | xhigh; unset leaves the model's own default (none)
VALID_EFFORTS = ("none", "low", "medium", "high", "xhigh")
MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "8192"))
TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT", "60"))


def api_key() -> Optional[str]:
    return os.getenv("OPENAI_API_KEY", "").strip() or None


def _messages(prompt: Any) -> Any:
    """A plain prompt string as a one-message conversation (OpenAI caches a repeated prefix by itself); a list of messages as it is."""
    return [HumanMessage(content=str(prompt))] if isinstance(prompt, str) else prompt


class OpenAILLM:
    def __init__(self, model_name: str = DEFAULT_MODEL, fallback: Any = None, max_tokens: int = MAX_TOKENS, timeout: float = TIMEOUT_SECONDS,
                 max_retries: int = 1, effort: Optional[str] = DEFAULT_EFFORT):
        if effort is not None and effort not in VALID_EFFORTS:
            raise ValueError(f"reasoning effort must be one of {VALID_EFFORTS}, not {effort!r}")
        self.model_name = model_name
        self.fallback = fallback
        self.max_tokens, self.timeout, self.max_retries = max_tokens, timeout, max_retries
        self.effort = effort
        self._chat: Any = None
        self._responses_chat: Any = None
        self._last_failure: Optional[str] = None      # the kind (class name) of the last failure, for the message when there is no fallback: never what was sent
        self._tool_runnables: Dict[tuple, Any] = {}

    @property
    def caches_prompt(self) -> bool:
        """OpenAI caches a repeated prompt prefix on its own, so a loop need not stop rewriting earlier messages to keep it. False: this class adds no cache marks of its own."""
        return False

    def available(self) -> bool:
        return api_key() is not None

    def uses_responses(self, has_tools: bool) -> bool:
        """Whether a call goes through the Responses endpoint: only a tool call with a reasoning effort set (Chat Completions refuses that combination)."""
        return bool(has_tools) and self.effort not in (None, "none")

    def _client(self, responses: bool = False) -> Any:
        from langchain_openai import ChatOpenAI

        if responses:
            if self._responses_chat is None:
                kwargs: Dict[str, Any] = {"model": self.model_name, "api_key": api_key(), "use_responses_api": True, "max_tokens": self.max_tokens, "timeout": self.timeout, "max_retries": self.max_retries}
                if self.effort:
                    kwargs["reasoning"] = {"effort": self.effort}
                self._responses_chat = ChatOpenAI(**kwargs)
            return self._responses_chat
        if self._chat is None:
            kwargs = {"model": self.model_name, "api_key": api_key(), "max_completion_tokens": self.max_tokens, "timeout": self.timeout, "max_retries": self.max_retries}
            if self.effort:
                kwargs["reasoning_effort"] = self.effort
            self._chat = ChatOpenAI(**kwargs)        # no temperature: the model page does not list it
        return self._chat

    @staticmethod
    def openai_tools(tools: list) -> list:
        """The loop's function declarations ({name, description, parameters}) in OpenAI's shape ({type: "function", function: {...}}). A tool that takes no arguments is declared without `parameters`
        for Gemini (which rejects an empty schema); OpenAI wants an object schema, so it gets an empty one."""
        converted = []
        for tool in tools:
            schema = tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}}
            converted.append({"type": "function", "function": {"name": tool["name"], "description": tool.get("description") or tool["name"], "parameters": schema}})
        return converted

    def _runnable(self, tools: list) -> Any:
        if not tools:
            return self._client()
        responses = self.uses_responses(True)
        key = (tuple(sorted(str(tool.get("name")) for tool in tools)), responses)
        if key not in self._tool_runnables:
            self._tool_runnables[key] = self._client(responses).bind_tools(self.openai_tools(tools))
        return self._tool_runnables[key]

    def _served_as(self, response: Any) -> str:
        """The name to meter a call under: the configured name when OpenAI reports a dated snapshot of it ("gpt-5.4-mini-2026-03-17"), so the price lookup finds it; any other name as reported."""
        served = llm_usage.served_by(response, self.model_name)
        return self.model_name if served.startswith(self.model_name) else served

    def _metered(self, response: Any, label: str) -> Any:
        try:
            llm_usage.record(self._served_as(response), label, llm_usage.usage_of(response))
        except Exception:
            pass
        return response

    def _gave_up(self, error: Exception) -> None:
        # Only the kind of failure is logged, never its message: an API error can quote what was sent.
        self._last_failure = type(error).__name__
        logger.warning("[openai] %s failed (%s); using the fallback model.", self.model_name, self._last_failure)

    def _no_fallback(self) -> Exception:
        kind = f" ({self._last_failure})" if self._last_failure else ""
        return RuntimeError(f"OpenAI is unavailable{kind} and no fallback model is configured.")

    def invoke(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        label = llm_usage.caller_label()
        if self.available():
            try:
                return self._metered(self._client().invoke(_messages(prompt), *args, **kwargs), label)
            except Exception as error:
                self._gave_up(error)
        if self.fallback is None:
            raise self._no_fallback()
        return self.fallback.invoke(prompt, *args, **kwargs)

    async def ainvoke(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        label = llm_usage.caller_label()
        if self.available():
            try:
                return self._metered(await self._client().ainvoke(_messages(prompt), *args, **kwargs), label)
            except Exception as error:
                self._gave_up(error)
        if self.fallback is None:
            raise self._no_fallback()
        return await self.fallback.ainvoke(prompt, *args, **kwargs)

    async def astream(self, prompt: Any, *args: Any, **kwargs: Any):
        """The reply the person reads, streamed (backend/models/models.py LazyLLM.astream is the contract): chunks with `.content`. Usage arrives in pieces and is added up, then recorded once even if the
        reader stops early (the Stop button: what was reported was still billed). A failure BEFORE the first chunk is answered by the fallback; once text has started it cannot change model, so the error
        goes up (and with no fallback it goes up either way)."""
        label = llm_usage.caller_label()
        if self.available():
            streamed = llm_usage.empty_usage()
            answered_by, started = self.model_name, False
            try:
                async for chunk in self._client().astream(_messages(prompt), *args, **kwargs):
                    started = True
                    llm_usage.add_into(streamed, llm_usage.usage_of(chunk))
                    answered_by = llm_usage.served_by(chunk, answered_by)
                    yield chunk
                return
            except Exception as error:
                if started:
                    raise
                self._gave_up(error)
            finally:
                if streamed["input"] or streamed["output"]:
                    try:
                        llm_usage.record(self.model_name if answered_by.startswith(self.model_name) else answered_by, label, streamed)
                    except Exception:
                        pass
        if self.fallback is None:
            raise self._no_fallback()
        async for chunk in self.fallback.astream(prompt, *args, **kwargs):
            yield chunk

    async def ainvoke_tools(self, messages: Any, tools: list) -> Any:
        """One step of a tool-calling conversation, the same contract as LazyLLM.ainvoke_tools and ClaudeLLM.ainvoke_tools: `tools` are function declarations (name, description, parameters), and the returned
        message's `.tool_calls` are what the model wants run (none means it is answering)."""
        label = llm_usage.caller_label()
        if self.available():
            try:
                return self._metered(await self._runnable(tools).ainvoke(messages), label)
            except Exception as error:
                self._gave_up(error)
        if self.fallback is None:
            raise self._no_fallback()
        return await self.fallback.ainvoke_tools(messages, tools)

    def __getattr__(self, name: str) -> Any:
        # Anything else (astream, bind_tools, ...) is the fallback's business: this class only takes over the calls the loops make.
        if name.startswith("_") or self.fallback is None:
            raise AttributeError(name)
        return getattr(self.fallback, name)
