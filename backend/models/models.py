# backend/models/models.py
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

# Shared by invoke/astream/ainvoke so a pattern added once (like 504 below, added after a real
# production trace) protects every call path automatically instead of drifting across three
# separately-maintained copies. "503"/"UNAVAILABLE" was the original coverage; "504"/
# "DEADLINE_EXCEEDED"/"Gateway Timeout" was added after a real trace showed a 504 crash
# tool_agent_node's ReAct loop mid-investigation, forcing a premature "you're out of steps"
# synthesis from barely any real findings — which produced a fully fabricated answer despite an
# explicit "never invent beyond what you found" instruction. Graceful degradation here lets the
# loop's own existing recovery path (an unparseable response just becomes one recorded failed
# step, not a crash) run instead of losing the whole turn to a transient API hiccup.
_TRANSIENT_ERROR_MARKERS = ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "Gateway Timeout")


def _is_transient_llm_error(e: Exception) -> bool:
    text = str(e)
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


class LazyLLM:
    def __init__(
        self,
        model_name="gemini-3.5-flash",
        fallback_model=None,
        temperature=0.7,
        max_retries=1,
        timeout=30,
        thinking_level=None,   # Gemini 3.5+: 'minimal', 'low', 'medium', 'high'
        thinking_budget=None,  # Gemini 2.5: token count (e.g., 0, 1024)
    ):
        self.model_name = model_name
        self.fallback_model = fallback_model
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.thinking_level = thinking_level
        self.thinking_budget = thinking_budget
        self._real_llm = None
        self.dev_mode = False

    def _ensure_initialized(self):
        if self._real_llm is None and not self.dev_mode:
            # Build primary client configuration
            primary_kwargs = {
                "model": self.model_name,
                "api_key": os.getenv("GOOGLE_API_KEY"),
                "temperature": self.temperature,
                "max_retries": self.max_retries,
                "request_timeout": self.timeout,
                "streaming": True,
            }

            # Add thinking controls natively if specified
            if self.thinking_level is not None:
                primary_kwargs["thinking_level"] = self.thinking_level
            if self.thinking_budget is not None:
                primary_kwargs["thinking_budget"] = self.thinking_budget

            primary = ChatGoogleGenerativeAI(**primary_kwargs)

            # Build fallback client configuration if specified
            if self.fallback_model:
                fallback_kwargs = {
                    "model": self.fallback_model,
                    "api_key": os.getenv("GOOGLE_API_KEY"),
                    "temperature": self.temperature,
                    "max_retries": 1,
                    "request_timeout": self.timeout,
                    "streaming": True,
                }
                fallback = ChatGoogleGenerativeAI(**fallback_kwargs)
                self._real_llm = primary.with_fallbacks([fallback])
            else:
                self._real_llm = primary

    def invoke(self, *args, **kwargs):
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response.")
        self._ensure_initialized()
        try:
            return self._real_llm.invoke(*args, **kwargs)
        except Exception as e:
            if _is_transient_llm_error(e):
                print(f"CRITICAL: {self.model_name} API overloaded/unavailable ({e}). Returning fallback note.")
                return AIMessage(content="[System Note: AI model is experiencing high traffic. Please try again.]")
            raise e

    async def ainvoke(self, *args, **kwargs):
        # Previously undefined on LazyLLM, so calls fell through __getattr__ straight to the
        # real client's own ainvoke — completely bypassing the graceful-degradation handling
        # below. tool_agent_node's entire ReAct loop calls exactly this method, so every
        # transient error on that path was an unhandled crash rather than a recoverable note.
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response.")
        self._ensure_initialized()
        try:
            return await self._real_llm.ainvoke(*args, **kwargs)
        except Exception as e:
            if _is_transient_llm_error(e):
                print(f"CRITICAL: {self.model_name} API overloaded/unavailable ({e}). Returning fallback note.")
                return AIMessage(content="[System Note: AI model is experiencing high traffic. Please try again.]")
            raise e

    async def astream(self, *args, **kwargs):
        if self.dev_mode:
            yield AIMessage(content="[DEV_MODE] Mock streaming response.")
            return

        self._ensure_initialized()
        try:
            async for chunk in self._real_llm.astream(*args, **kwargs):
                yield chunk
        except Exception as e:
            if _is_transient_llm_error(e):
                print(f"CRITICAL: {self.model_name} API overloaded/unavailable ({e}).")
                yield AIMessage(content="[System Note: AI model is experiencing high traffic. Please try again.]")
            else:
                raise e

    def __getattr__(self, name):
        if self.dev_mode:
            return lambda *args, **kwargs: AIMessage(content="Mocked method call.")
        self._ensure_initialized()
        return getattr(self._real_llm, name)


# ----------------------------------------------------------------------
# Model Definitions
# ----------------------------------------------------------------------

# 1. Primary Reasoning LLM (Full depth for backend LangGraph executions)
llm = LazyLLM(
    model_name="gemini-3.5-flash",
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="high",  # Max reasoning depth for planning & retrieval
)

# 2. Fast Utility LLM (For document grading, query rewriting, classification)
lite_llm = LazyLLM(
    model_name="gemini-3.1-flash-lite",
    temperature=0.2,
    max_retries=1,
)

# 2b. Deep-thinking variant of the fast utility LLM — same fast/cheap model, with native
# extended reasoning enabled. Used by tool_agent_node's ReAct loop when the user's deep_thinking
# setting is on: TOOL_AGENT_MAX_ITERATIONS_DEEP and the wider retry-nudge budget only pay off if
# each individual step's own decision is more carefully reasoned too, not just more numerous —
# verified live that gemini-3.1-flash-lite genuinely accepts thinking_level (the "3.5+" framing
# in LazyLLM's docstring doesn't hold here).
lite_llm_deep = LazyLLM(
    model_name="gemini-3.1-flash-lite",
    temperature=0.2,
    max_retries=1,
    thinking_level="high",
)

# 3. Dedicated Streaming LLM (Gemini 3.5 with low thinking level for fast TTFT)
stream_llm = LazyLLM(
    model_name="gemini-3.5-flash",
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="minimal",  # <--- Drops TTFT from ~31s to ~1-2s while retaining quality
)

# 4. Embedded Streaming LLM (BTY iframe traffic prioritizes minimum latency)
embedded_llm = LazyLLM(
    model_name="gemini-3.5-flash",
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="minimal",
    # retries=1,  # <--- Avoids retries to reduce latency for embedded users
)


def get_chat_llm(username: str):
    return embedded_llm if username == "guest_bty" else llm


def get_stream_llm(username: str):
    return embedded_llm if username == "guest_bty" else stream_llm