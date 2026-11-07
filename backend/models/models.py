# backend/models/models.py
import logging
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import AIMessage

from backend.utils import llm_usage

logger = logging.getLogger("SASS Logger")


def _job_model(env_name: str, default: str) -> str:
    """The Gemini model for one job (docs/model-eval-plan.md): `default` (today's model) unless the environment variable names another one. An override only applies when that model is PRICED in LLM_PRICES,
    the same rule as the Sonnet option: the budget guard counts dollars from those prices, so a model without one would spend money it cannot see. An unpriced name leaves today's model in place and says so
    (the log names the setting and the model, never anything a person wrote). Gemini names only: a Claude model needs its own wrapper (backend/models/claude_llm.py)."""
    wanted = os.getenv(env_name, "").strip()
    if not wanted or wanted == default:
        return default
    if wanted not in llm_usage.cached_prices():
        logger.warning("[models] %s=%s is ignored: that model has no price in LLM_PRICES, so its spend would be invisible. Using %s.", env_name, wanted, default)
        return default
    return wanted

# Shared by invoke/astream/ainvoke so a pattern added once (like 504 below, added after a real
# production trace) protects every call path automatically instead of drifting across three
# separately-maintained copies. "503"/"UNAVAILABLE" was the original coverage; "504"/
# "DEADLINE_EXCEEDED"/"Gateway Timeout" was added after a real trace showed a 504 crash
# tool_agent_node's ReAct loop mid-investigation, forcing a premature "you're out of steps"
# synthesis from barely any real findings — which produced a fully fabricated answer despite an
# explicit "never invent beyond what you found" instruction. Graceful degradation here lets the
# loop's own existing recovery path (an unparseable response just becomes one recorded failed
# step, not a crash) run instead of losing the whole turn to a transient API hiccup.
#
# A second real trace hit a DIFFERENT shape of the same underlying problem: a raw TimeoutError
# from aiohttp's own internal request timer (str(TimeoutError()) is typically empty, so no text
# marker could ever match it) — this is google-genai's HTTP client giving up on a slow request,
# not our own app-level Stop-button cancellation (that goes through Request.is_disconnected()
# polling + explicit generator .aclose(), never through an exception raised out of llm.ainvoke()
# itself), so it's safe to treat as transient rather than let it propagate. Checked by type,
# since text-matching can't catch an exception with no message. ConnectionError is included
# alongside it as the same class of "the network hiccupped, not a reasoning problem" failure.
_TRANSIENT_ERROR_MARKERS = ("503", "UNAVAILABLE", "504", "DEADLINE_EXCEEDED", "Gateway Timeout")
_TRANSIENT_ERROR_TYPES = (TimeoutError, ConnectionError)


def _is_transient_llm_error(e: Exception) -> bool:
    if isinstance(e, _TRANSIENT_ERROR_TYPES):
        return True
    text = str(e)
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


class LazyLLM:
    def __init__(
        self,
        model_name="gemini-3.6-flash",
        fallback_model=None,
        temperature=0.7,
        max_retries=1,
        timeout=30,
        thinking_level=None,   # Gemini 3.5+: 'minimal', 'low', 'medium', 'high'
        thinking_budget=None,  # Gemini 2.5: token count (e.g., 0, 1024)
        fallback_thinking_level=None,  # thinking level for the fallback client (None = its default)
    ):
        self.model_name = model_name
        self.fallback_model = fallback_model
        self.fallback_thinking_level = fallback_thinking_level
        self.temperature = temperature
        self.max_retries = max_retries
        self.timeout = timeout
        self.thinking_level = thinking_level
        self.thinking_budget = thinking_budget
        self._real_llm = None
        self._primary_client = None
        self._fallback_client = None
        self._tool_runnables = {}
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
                if self.fallback_thinking_level is not None:
                    fallback_kwargs["thinking_level"] = self.fallback_thinking_level
                fallback = ChatGoogleGenerativeAI(**fallback_kwargs)
                self._fallback_client = fallback
                self._real_llm = primary.with_fallbacks([fallback])
            else:
                self._real_llm = primary
            self._primary_client = primary

    def invoke(self, *args, **kwargs):
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response.")
        self._ensure_initialized()
        label = llm_usage.caller_label()
        try:
            return self._metered(self._real_llm.invoke(*args, **kwargs), label)
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
        label = llm_usage.caller_label()
        try:
            return self._metered(await self._real_llm.ainvoke(*args, **kwargs), label)
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
        label = llm_usage.caller_label()
        streamed = llm_usage.empty_usage()
        answered_by = self.model_name
        try:
            async for chunk in self._real_llm.astream(*args, **kwargs):
                # A stream reports its usage in pieces (input on the first chunk, output on later ones): add them up.
                llm_usage.add_into(streamed, llm_usage.usage_of(chunk))
                answered_by = llm_usage.served_by(chunk, answered_by)
                yield chunk
        except Exception as e:
            if _is_transient_llm_error(e):
                print(f"CRITICAL: {self.model_name} API overloaded/unavailable ({e}).")
                yield AIMessage(content="[System Note: AI model is experiencing high traffic. Please try again.]")
            else:
                raise e
        finally:
            # Counted even if the reader stopped early (the Stop button): what was reported so far was still billed.
            if streamed["input"] or streamed["output"]:
                llm_usage.record(answered_by, label, streamed)
                llm_usage.note_route(self.model_name, answered_by, self.fallback_model)

    def _runnable_with_tools(self, tools: list):
        """The model with `tools` (function declarations: name, description, parameters) bound to the primary AND the fallback client,
        so a failover keeps its tools. Cached by tool names. With no tools it is just the model."""
        if not tools or self._primary_client is None:
            return self._real_llm
        key = tuple(sorted(str(tool.get("name")) for tool in tools))
        cached = self._tool_runnables.get(key)
        if cached is None:
            bound = self._primary_client.bind_tools(tools)
            if self._fallback_client is not None:
                bound = bound.with_fallbacks([self._fallback_client.bind_tools(tools)])
            cached = self._tool_runnables[key] = bound
        return cached

    async def ainvoke_tools(self, messages, tools: list):
        """One step of a tool-calling conversation: `messages` is the whole message list so far (system, user, earlier assistant
        tool calls and the tool results), `tools` the function declarations. Returns the model's message: its `.tool_calls` are what it
        wants run, and none means it is answering. Metered and failure-handled like ainvoke."""
        if self.dev_mode:
            return AIMessage(content="[DEV_MODE] Mock LLM Response.")
        self._ensure_initialized()
        label = llm_usage.caller_label()
        try:
            return self._metered(await self._runnable_with_tools(tools).ainvoke(messages), label)
        except Exception as e:
            if _is_transient_llm_error(e):
                print(f"CRITICAL: {self.model_name} API overloaded/unavailable ({e}). Returning fallback note.")
                return AIMessage(content="[System Note: AI model is experiencing high traffic. Please try again.]")
            raise e

    def _metered(self, response, label: str):
        """Counts a finished call's token usage (backend/utils/llm_usage.py) and hands the response back untouched."""
        try:
            served = llm_usage.served_by(response, self.model_name)
            llm_usage.record(served, label, llm_usage.usage_of(response))
            llm_usage.note_route(self.model_name, served, self.fallback_model)
        except Exception:
            pass
        return response

    def __getattr__(self, name):
        if self.dev_mode:
            return lambda *args, **kwargs: AIMessage(content="Mocked method call.")
        self._ensure_initialized()
        return getattr(self._real_llm, name)


# ----------------------------------------------------------------------
# Model Definitions
# ----------------------------------------------------------------------
# NAMES: these were "gemini-3.5-flash" until October 2026. Google's deprecations page says requests to that name are automatically
# routed to gemini-3.6-flash (no shutdown date), so the code now names what is actually served. Google does this to older Flash
# names over time, so the usage page (backend/utils/llm_usage.py) also reports any call answered by a different model than the one
# named here. The small model, gemini-3.1-flash-lite, has an earliest shutdown of 2027-05-07 (replacement: gemini-3.5-flash-lite).


# 1. Primary Reasoning LLM (Full depth for backend LangGraph executions)
llm = LazyLLM(
    model_name=_job_model("CHAT_MODEL", "gemini-3.6-flash"),        # CHAT_MODEL: the formatter-type chat nodes (unset = today's model)
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="high",  # Max reasoning depth for planning & retrieval
)

# 2. Fast Utility LLM (For document grading, query rewriting, classification)
# The lite models drive every ReAct step. A real turn hit five 504s in a row on flash-lite (each
# costing the 30s timeout twice over via the internal retry, ~235s total) and every one became a
# "high traffic" note instead of a decision. So: no internal retry on the primary (a stuck call
# costs one timeout, not two), and fall back to the bigger model — which is on a different quota
# pool — only when the primary fails, so normal turns still cost what they did.
lite_llm = LazyLLM(
    model_name=_job_model("LITE_MODEL", "gemini-3.1-flash-lite"),   # LITE_MODEL: routing flags, memory jobs and the default tool loop (unset = today's model)
    fallback_model="gemini-3.6-flash",
    fallback_thinking_level="minimal",
    temperature=0.2,
    max_retries=0,
)

# 2b. Deep-thinking variant for tool_agent_node's ReAct loop, used when the user's deep_thinking
# setting is on (or the task is an audit): TOOL_AGENT_MAX_ITERATIONS_DEEP and the wider
# retry-nudge budget only pay off if each individual step's own decision is better, not just more
# numerous. Experiment: run these steps on the stronger gemini-3.6-flash (at a low thinking level,
# since "high" there was measured at ~31s to first token) instead of flash-lite, and fall back to
# flash-lite if it fails. Ordinary (non-deep) turns still use lite_llm above, so the extra cost is
# confined to deep/audit turns. Env-tunable so it can be compared or reverted without a deploy:
# set DEEP_REACT_MODEL=gemini-3.1-flash-lite and DEEP_REACT_THINKING=high for the old behavior.
lite_llm_deep = LazyLLM(
    model_name=os.getenv("DEEP_REACT_MODEL", "gemini-3.6-flash"),
    fallback_model=os.getenv("DEEP_REACT_FALLBACK_MODEL", "gemini-3.1-flash-lite"),
    fallback_thinking_level="low",  # a rescue should be fast
    temperature=0.2,
    max_retries=0,
    timeout=int(os.getenv("DEEP_REACT_TIMEOUT", "45")),
    thinking_level=os.getenv("DEEP_REACT_THINKING", "low"),
)

# 3. Dedicated Streaming LLM (Gemini 3.5 with low thinking level for fast TTFT)
stream_llm = LazyLLM(
    model_name=_job_model("REPLY_MODEL", "gemini-3.6-flash"),       # REPLY_MODEL: the reply the person reads, and the embedded one below (unset = today's model)
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="minimal",  # <--- Drops TTFT from ~31s to ~1-2s while retaining quality
)

# 4. Embedded Streaming LLM (BTY iframe traffic prioritizes minimum latency)
embedded_llm = LazyLLM(
    model_name=_job_model("REPLY_MODEL", "gemini-3.6-flash"),
    fallback_model="gemini-3.1-flash-lite",
    temperature=0.7,
    thinking_level="minimal",
    # retries=1,  # <--- Avoids retries to reduce latency for embedded users
)


_claude_tool_llms: dict = {}


def claude_available() -> bool:
    """Whether a Claude key is configured, so the choice of Claude can be offered at all."""
    from backend.models import claude_llm

    return claude_llm.api_key() is not None


def sonnet_available() -> bool:
    """Whether the Sonnet option can be offered: a Claude key is configured AND LLM_PRICES lists the Sonnet model. Without a price its calls would add nothing to anyone's spend, and the budget
    guard (which counts dollars from those prices) would not see them."""
    from backend.models import claude_llm
    from backend.utils import llm_usage

    return claude_llm.api_key() is not None and claude_llm.SONNET_MODEL in llm_usage.cached_prices()


def openai_available() -> bool:
    """Whether the GPT options can be offered: an OpenAI key is configured AND LLM_PRICES lists GPT-5.4 mini. The same rule as Sonnet: without a price its calls would add nothing to anyone's spend, and the budget
    guard (which counts dollars from those prices) would not see them."""
    from backend.models import openai_llm

    return openai_llm.api_key() is not None and openai_llm.DEFAULT_MODEL in llm_usage.cached_prices()


_openai_llms: dict = {}
OPENAI_TOOL_EFFORT = os.getenv("OPENAI_TOOL_EFFORT", "low").strip().lower() or None     # the reasoning effort GPT runs the tool loop at (its default is none); a tool call with an effort goes through OpenAI's Responses endpoint


_review_llm: dict = {}


def review_llm():
    """The model that reads a finished change a second time (backend/utils/diff_review.py): Claude Haiku at low effort with thinking off, so a review is about a tenth of a cent. None when no Claude key
    is configured: then there is simply no review. It has no Gemini fallback on purpose (a failed review is no review; it must never quietly cost more)."""
    from backend.models import claude_llm

    if not claude_llm.api_key():
        return None
    if "haiku" not in _review_llm:
        _review_llm["haiku"] = claude_llm.ClaudeLLM(model_name=os.getenv("DIFF_REVIEW_MODEL", claude_llm.DEFAULT_MODEL), fallback=None, effort="low", thinking_off=True, max_tokens=600)
    return _review_llm["haiku"]


_plan_review_llm: dict = {}


def plan_review_llm():
    """The model that reads the whole change of an approved plan at its end (backend/utils/diff_review.py PLAN_REVIEW_PROMPT): Claude Sonnet at low effort with thinking off, with room for five findings. None
    unless Sonnet can be offered (a Claude key AND a price in LLM_PRICES, so the spend guard sees these calls). No Gemini fallback on purpose: a failed review is no review, and it must never quietly cost
    more or send a person's code to a provider they did not choose. The caller decides whether this person may have their code read by Claude at all."""
    from backend.models import claude_llm

    if not sonnet_available():
        return None
    if "sonnet" not in _plan_review_llm:
        _plan_review_llm["sonnet"] = claude_llm.ClaudeLLM(model_name=claude_llm.SONNET_MODEL, fallback=None, effort="low", thinking_off=True, max_tokens=1500)
    return _plan_review_llm["sonnet"]


def advisor_llm_for(loop_llm):
    """The model that gives a second opinion to a loop running on `loop_llm` (backend/utils/advisor.py): the next tier up OF THE SAME PROVIDER, so nothing new is disclosed. Haiku has Sonnet (the plan-end
    reader: low effort, no fallback), the default Gemini lite model has Gemini's deeper model. None when the loop already runs on a top tier (Sonnet, the deeper Gemini, GPT) or the higher tier cannot be
    used (no Claude key or price for Sonnet)."""
    from backend.models import claude_llm

    if isinstance(loop_llm, claude_llm.ClaudeLLM):
        return None if getattr(loop_llm, "model_name", "") == claude_llm.SONNET_MODEL else plan_review_llm()
    if loop_llm is lite_llm:
        return lite_llm_deep
    return None


_summary_llm: dict = {}


def summary_llm():
    """The OPT-IN Claude model for folding the older part of a conversation into its summary (backend/utils/conversation_summary.py _fold_model uses it only when CONVERSATION_SUMMARY_MODEL is set;
    the default is the lite model): Claude at low effort with thinking off, with room for a patch of up to six items (the reviewer's 600 tokens could cut one off). CONVERSATION_SUMMARY_MODEL names the
    model (the default model when it is empty). None when no Claude key is configured, and the caller uses the lite model."""
    from backend.models import claude_llm

    if not claude_llm.api_key():
        return None
    if "haiku" not in _summary_llm:
        _summary_llm["haiku"] = claude_llm.ClaudeLLM(model_name=os.getenv("CONVERSATION_SUMMARY_MODEL", claude_llm.DEFAULT_MODEL), fallback=None, effort="low", thinking_off=True, max_tokens=1200)
    return _summary_llm["haiku"]


def tool_agent_llm(deep: bool, choice=None):
    """The model behind the tool agent's reasoning steps (the reply the person reads is written by Gemini either way). `choice` is the person's own setting,
    "claude" (Haiku), "sonnet" (Claude Sonnet, when it is offered: see sonnet_available) or "gemini"; with none, the server's default applies: Gemini unless
    TOOL_AGENT_PROVIDER=anthropic. Claude is only ever used with a key present, and carries the Gemini model it replaces as its fallback, so a Claude failure degrades to what
    the turn was before, never to an error. A "sonnet" choice that cannot be offered (no price configured) is served by Haiku, the cheaper of the two. See backend/models/claude_llm.py."""
    gemini = lite_llm_deep if deep else lite_llm
    wanted = choice if choice in ("claude", "sonnet", "gpt", "gemini") else ("claude" if os.getenv("TOOL_AGENT_PROVIDER", "").strip().lower() == "anthropic" else "gemini")
    if wanted == "gemini":
        return gemini
    if wanted == "gpt":
        # GPT-5.4 mini ("gpt"): only with a key AND a price (openai_available), else the Gemini model it would replace; carries that model as its fallback like Claude does.
        if not openai_available():
            return gemini
        key = ("gpt", deep)
        if key not in _openai_llms:
            from backend.models import openai_llm

            _openai_llms[key] = openai_llm.OpenAILLM(model_name=openai_llm.DEFAULT_MODEL, fallback=gemini, effort=OPENAI_TOOL_EFFORT)
        return _openai_llms[key]
    from backend.models import claude_llm

    if not claude_llm.api_key():
        return gemini
    if wanted == "sonnet" and not sonnet_available():
        wanted = "claude"
    key = (wanted, deep)
    if key not in _claude_tool_llms:
        if wanted == "sonnet":
            _claude_tool_llms[key] = claude_llm.ClaudeLLM(model_name=claude_llm.SONNET_MODEL, fallback=gemini, effort=claude_llm.SONNET_EFFORT)
        else:
            _claude_tool_llms[key] = claude_llm.ClaudeLLM(fallback=gemini)
    return _claude_tool_llms[key]


def get_chat_llm(username: str):
    return embedded_llm if username == "guest_bty" else llm


def get_stream_llm(username: str, choice=None):
    """The model that writes the reply the person reads. `choice` is their base-model setting: "gpt" (GPT-5.4 mini) when they chose it AND the server offers it (openai_available); anything else, a guest and the
    embedded view get Gemini, as before. GPT carries the Gemini model it replaces as its fallback (a failure before the first word is answered by Gemini), and is never used for the embedded view."""
    if username == "guest_bty":
        return embedded_llm
    if choice == "gpt" and openai_available():
        if "reply" not in _openai_llms:
            from backend.models import openai_llm

            _openai_llms["reply"] = openai_llm.OpenAILLM(model_name=openai_llm.DEFAULT_MODEL, fallback=stream_llm, effort=None)       # a reply is written at the model's own default effort (none)
        return _openai_llms["reply"]
    return stream_llm

# ---- the cheaper voices (backend/utils/reply_tier.py; docs/model-eval-plan.md) ----------------------------------------------------------------------------------------------------
# Two cheaper reply models, each its own choice in the base-model setting: Gemini flash-lite ("Lite", and what "Auto" uses for plain turns; LITE_REPLY_MODEL names another Gemini model) and Claude Haiku
# ("Haiku": the replies go to Anthropic). Like every other model option each is offered only when LLM_PRICES lists it (the budget guard counts dollars from those prices) and, for Haiku, when a Claude key is
# set. Never for the embedded view; a guest may pick either (their choice stays in their browser).
LITE_REPLY_DEFAULT = "gemini-3.5-flash-lite"
_lite_stream_llms: dict = {}


def lite_reply_model_name() -> str:
    """The Gemini lite reply model's name as the price table and the usage page know it."""
    return os.getenv("LITE_REPLY_MODEL", "").strip() or LITE_REPLY_DEFAULT


def lite_reply_available() -> bool:
    """Whether Gemini Lite (and Auto, which uses it) can be offered: the model's price is in LLM_PRICES."""
    return lite_reply_model_name() in llm_usage.cached_prices()


def haiku_reply_available() -> bool:
    """Whether Claude Haiku can be offered for replies: a Claude key is configured AND its price is in LLM_PRICES."""
    from backend.models import claude_llm

    return claude_available() and claude_llm.DEFAULT_MODEL in llm_usage.cached_prices()


def get_lite_stream_llm(username: str):
    """The Gemini model that writes a Lite or Auto reply, or None for the embedded view or a server that does not offer it. The standard reply model is its fallback, so a failure before the first word is
    answered by the standard voice."""
    if username in ("guest_bty",) or not lite_reply_available():
        return None
    name = lite_reply_model_name()
    if name not in _lite_stream_llms:
        _lite_stream_llms[name] = LazyLLM(model_name=name, fallback_model=stream_llm.model_name, temperature=0.7, thinking_level="minimal")
    return _lite_stream_llms[name]


def get_haiku_stream_llm(username: str):
    """The Claude Haiku model that writes a Haiku reply, or None for the embedded view or a server that does not offer it. Set up as the voice evals ran it: low effort, thinking off, the persona sent as a system
    prompt with the check-in reminder last (backend/utils/claude_voice.py). The standard reply model is its fallback."""
    if username in ("guest_bty",) or not haiku_reply_available():
        return None
    from backend.models import claude_llm

    name = claude_llm.DEFAULT_MODEL
    if name not in _lite_stream_llms:
        _lite_stream_llms[name] = claude_llm.ClaudeLLM(
            model_name=name, fallback=stream_llm, effort="low", thinking_off=True, max_tokens=2000, voice_shape=True, voice_tail="voice",
        )
    return _lite_stream_llms[name]


def lite_prompt(prompt: str, llm: object) -> str:
    """The prompt for a lite reply. A Claude model reshapes it itself (system prompt, closing reminder); a Gemini lite model reads the same flat prompt with the check-in reminder added last when a check-in
    is due (the eval's `checkin` shape: backend/utils/claude_voice.with_tail). Nothing changes for the standard voice."""
    from backend.utils import claude_voice

    return prompt if getattr(llm, "voice_shape", False) else claude_voice.with_tail(prompt, "checkin")
