"""Claude as the model behind the tool agent's reasoning steps, with Gemini kept as the safety net.

WHY. The tool agent re-sends the whole conversation on every step, so a turn is mostly INPUT tokens, and the per-step price decides how many steps a turn
can afford. Claude Haiku 5.5 lists at $0.10 / $0.50 per million input / output tokens (cached input $0.01), against $0.75 / $3.75 for gemini-3.6-flash and
$0.25 / $1.50 for gemini-3.1-flash-lite. Whether it is also good enough at the work is an empirical question, answered by backend/evals/behavior_eval.py
(`--tool-model claude`), not assumed.

HOW. ClaudeLLM offers the same calls LazyLLM does for the loops (backend/models/models.py): invoke, ainvoke and ainvoke_tools, with the same metering
(backend/utils/llm_usage.py, so a Claude call shows up on the usage page and counts toward the budget and per-person limits under its own model name; price it
in LLM_PRICES). Any failure (no key, rate limit, overload, a bad request) is answered by the Gemini model it was given as `fallback`, so a Claude problem
degrades a turn to what it was, never to an error. Nothing the person wrote is put in a log line.

SWITCH. models.tool_agent_llm() picks it only when TOOL_AGENT_PROVIDER=anthropic and a key is present; with nothing set, the tool agent is exactly what it was.

NOTES learned from the live API. Haiku 5.5 rejects a `temperature` (fixed sampling), so none is sent. It thinks by default and returns "thinking" blocks mixed
into its content; the loops read only the blocks that have text, so those are ignored (they are billed as output, which is what `effort` below controls). The key is
read from ANTHROPIC_API_KEY, or CLAUDE_API_KEY.
"""
import logging
import os
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage

from backend.models.split_prompt import SplitPrompt
from backend.utils import llm_usage

logger = logging.getLogger("SASS Logger")

DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-5-5")
DEFAULT_EFFORT = os.getenv("CLAUDE_EFFORT", "").strip().lower() or None          # "low" | "medium" | "high"; unset leaves the model's own default
THINKING_OFF = os.getenv("CLAUDE_THINKING", "").strip().lower() in ("0", "off", "false", "disabled")
CACHE_DEFAULT = os.getenv("CLAUDE_CACHE", "on").strip().lower() not in ("0", "off", "false", "no")      # automatic prompt caching (see the class)
MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "8192"))
TIMEOUT_SECONDS = float(os.getenv("CLAUDE_TIMEOUT", "60"))


def api_key() -> Optional[str]:
    return os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY") or None


def _messages(prompt: Any) -> Any:
    """A plain prompt string as a one-message conversation; a list of messages as it is. A SplitPrompt (backend/models/split_prompt.py) becomes one message of several blocks, each stable part marked to
    be cached, so a loop that re-sends a growing prompt only pays full price for the part that changed."""
    if isinstance(prompt, SplitPrompt) and prompt.cuts:
        return [HumanMessage(content=prompt.blocks())]
    return [HumanMessage(content=prompt)] if isinstance(prompt, str) else prompt


class ClaudeLLM:
    def __init__(self, model_name: str = DEFAULT_MODEL, fallback: Any = None, max_tokens: int = MAX_TOKENS, timeout: float = TIMEOUT_SECONDS,
                 max_retries: int = 1, effort: Optional[str] = DEFAULT_EFFORT, thinking_off: bool = THINKING_OFF, cache: bool = CACHE_DEFAULT):
        self.model_name = model_name
        self.fallback = fallback
        self.max_tokens, self.timeout, self.max_retries = max_tokens, timeout, max_retries
        self.effort, self.thinking_off, self.cache = effort, thinking_off, cache
        self._chat: Any = None
        self._tool_runnables: Dict[tuple, Any] = {}

    @property
    def caches_prompt(self) -> bool:
        """True when this model is set up to cache the conversation prefix. A loop that rewrites earlier messages (shortening old tool results) must then stop
        doing so: an edited message invalidates the cache from that point on, and every later step would pay to write the whole tail again instead of
        reading it at a tenth of the price."""
        return bool(self.cache and self.available())

    def available(self) -> bool:
        return api_key() is not None

    def _client(self) -> Any:
        if self._chat is None:
            from langchain_anthropic import ChatAnthropic

            kwargs: Dict[str, Any] = {"model": self.model_name, "api_key": api_key(), "max_tokens": self.max_tokens, "timeout": self.timeout, "max_retries": self.max_retries}
            if self.effort:
                kwargs["effort"] = self.effort
            if self.thinking_off:
                kwargs["thinking"] = {"type": "disabled"}
            if self.cache:
                # Automatic caching: one top-level field, and the API keeps the cache breakpoint at the end of the conversation as it grows.
                kwargs["model_kwargs"] = {"cache_control": {"type": "ephemeral"}}
            self._chat = ChatAnthropic(**kwargs)        # no temperature: the model rejects it
        return self._chat

    @staticmethod
    def anthropic_tools(tools: list) -> list:
        """The loop's function declarations ({name, description, parameters}) in Claude's shape ({name, description, input_schema}). A tool that takes no
        arguments is declared without `parameters` for Gemini (which rejects an empty schema); Claude requires one, so it gets an empty object schema."""
        converted = []
        for tool in tools:
            schema = tool.get("parameters") or tool.get("input_schema") or {"type": "object", "properties": {}}
            converted.append({"name": tool["name"], "description": tool.get("description") or tool["name"], "input_schema": schema})
        return converted

    def _runnable(self, tools: list) -> Any:
        if not tools:
            return self._client()
        key = tuple(sorted(str(tool.get("name")) for tool in tools))
        if key not in self._tool_runnables:
            self._tool_runnables[key] = self._client().bind_tools(self.anthropic_tools(tools))
        return self._tool_runnables[key]

    def _metered(self, response: Any, label: str) -> Any:
        try:
            llm_usage.record(llm_usage.served_by(response, self.model_name), label, llm_usage.usage_of(response))
        except Exception:
            pass
        return response

    def _gave_up(self, error: Exception) -> None:
        # Only the kind of failure is logged, never its message: an API error can quote what was sent.
        logger.warning("[claude] %s failed (%s); using the fallback model.", self.model_name, type(error).__name__)

    def _no_fallback(self) -> Exception:
        return RuntimeError("Claude is unavailable and no fallback model is configured.")

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

    async def ainvoke_tools(self, messages: Any, tools: list) -> Any:
        """One step of a tool-calling conversation, the same contract as LazyLLM.ainvoke_tools: `tools` are function declarations (name, description,
        parameters), and the returned message's `.tool_calls` are what the model wants run (none means it is answering)."""
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
        # Anything else (astream, bind_tools, ...) is the fallback's business: this class only takes over the calls the tool loops make.
        if name.startswith("_") or self.fallback is None:
            raise AttributeError(name)
        return getattr(self.fallback, name)
