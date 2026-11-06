"""The models the evals can score, what each costs, how to build one, and a spend ceiling (docs/model-eval-plan.md, phase 0: nothing here makes a model call by itself).

The evals call the models directly, so the budget guard never sees their spend (backend/utils/llm_usage.py mark_as_eval). A comparison across models, one of which can cost 20 times another, therefore needs
its own brake: SpendTracker adds up the dollars from the usage every call reports, and the harnesses stop at a ceiling.

- CANDIDATES is the whole list a run may name, with USD per million tokens (as of the plan's price table, 2026-11-01; LLM_PRICES, when set, wins). A name that is not here is refused, because a model with no price
  could be called without the ceiling ever seeing it.
- make_llm builds a model for a reply or a structured job; make_tool_llm builds one the way the app wires the tool loop (backend/models/models.py tool_agent_llm), so a result is the real wiring's result.
- THE CEILING IS CHECKED BETWEEN RUNS, NEVER INSIDE A CALL. Raising from inside the usage hook could be caught by a model wrapper's own error handling and turned into a quiet second call to the fallback model, which is
  the opposite of a brake. So the run in flight finishes, then check() raises: the overshoot is at most one run.
"""
import os
from typing import Any, Callable, Dict, List, Optional

# USD per million tokens: input, output, and input read from the prompt cache (assumed 10% of input where the price page names no figure).
CANDIDATES: Dict[str, Dict[str, Any]] = {
    "claude-haiku-5-5": {"provider": "claude", "price": {"input": 0.10, "output": 0.50, "cached_input": 0.01}},
    "claude-sonnet-5-5": {"provider": "claude", "price": {"input": 2.00, "output": 10.00, "cached_input": 0.20}},
    "gemini-3.1-flash-lite": {"provider": "gemini", "price": {"input": 0.25, "output": 1.50, "cached_input": 0.025}},
    "gemini-3.5-flash-lite": {"provider": "gemini", "price": {"input": 0.30, "output": 2.50, "cached_input": 0.03}},
    "gemini-3.6-flash": {"provider": "gemini", "price": {"input": 0.75, "output": 3.75, "cached_input": 0.075}},
    "gemini-3.8-flash": {"provider": "gemini", "price": {"input": 0.75, "output": 3.75, "cached_input": 0.075}},
    # OpenAI's model page, read 2026-11-02: $0.75 input, $0.075 cached input, $4.50 output; Chat Completions, tool calling, streaming, image input.
    "gpt-5.4-mini": {"provider": "openai", "price": {"input": 0.75, "output": 4.50, "cached_input": 0.075}},
    # Prices from OpenAI's pricing page (2026-11-02). Eval-only for now: not offered in the app, so not in models.py.
    "gpt-5.6-terra": {"provider": "openai", "price": {"input": 1.00, "output": 5.00, "cached_input": 0.05}},
    "gpt-5.6-luna": {"provider": "openai", "price": {"input": 0.20, "output": 1.20, "cached_input": 0.02}},
}
ALIASES = {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "mini": "gpt-5.4-mini", "gpt": "gpt-5.4-mini", "terra": "gpt-5.6-terra", "luna": "gpt-5.6-luna"}
# The reasoning effort GPT-5.4 mini runs the tool loop at (its default is none): "improved coding and reasoning" is the case for a middle tier, so the loop gets some. EVAL_OPENAI_EFFORT tries another
# (none, low, medium, high, xhigh); a reply is written at the model's own default.
TOOL_LOOP_OPENAI_EFFORT = os.getenv("EVAL_OPENAI_EFFORT", "low").strip().lower() or None
OPENAI_EVAL_RETRIES = 4  # the OpenAI client retries a 429 with backoff this many times before the call counts as failed: a new account's rate limits, or credit that is still propagating, are brief
DEFAULT_CEILING_USD = 2.0


def resolve(name: str) -> str:
    """The canonical candidate name for `name` (an alias such as "haiku" is accepted). Raises ValueError naming what is accepted."""
    wanted = ALIASES.get((name or "").strip().lower(), (name or "").strip())
    if wanted not in CANDIDATES:
        raise ValueError(f"{name!r} is not a candidate model; choose one of {sorted(CANDIDATES) + sorted(ALIASES)}")
    return wanted


def is_candidate(name: str) -> bool:
    try:
        resolve(name)
        return True
    except ValueError:
        return False


def prices() -> Dict[str, Dict[str, float]]:
    """Prices for costing a run: the candidates' own, overridden by what LLM_PRICES lists (the real prices when they are configured)."""
    from backend.utils import llm_usage

    return {**{name: dict(spec["price"]) for name, spec in CANDIDATES.items()}, **llm_usage.load_prices()}


def make_llm(name: str, *, thinking: Optional[str] = "minimal", temperature: float = 0.7, shape: str = ""):
    """`name` as the model for a reply or a structured job: no fallback (a failure is the candidate's failure, never another model's success). Claude runs at low effort with thinking off, the way the app runs
    its other Claude jobs; Gemini takes `thinking` ('minimal' is what the reply model uses)."""
    name = resolve(name)
    if CANDIDATES[name]["provider"] == "claude":
        from backend.models.claude_llm import ClaudeLLM

        return ClaudeLLM(model_name=name, fallback=None, effort="low", thinking_off=True, max_tokens=2000, voice_shape=(shape in ("system", "tail", "checkin", "fuller")), voice_tail={"tail": "all", "checkin": "checkin", "fuller": "fuller"}.get(shape, ""))
    if CANDIDATES[name]["provider"] == "openai":
        from backend.models.openai_llm import OpenAILLM

        return OpenAILLM(model_name=name, fallback=None, effort=None, max_tokens=2000, max_retries=OPENAI_EVAL_RETRIES)      # the model's own default effort (none), as a reply is written
    from backend.models.models import LazyLLM

    llm = LazyLLM(model_name=name, fallback_model=None, temperature=temperature, thinking_level=thinking)
    tail = {"tail": "all", "checkin": "checkin", "fuller": "fuller"}.get(shape, "")
    return TailedLLM(llm, tail) if tail else llm


class TailedLLM:
    """An eval-only wrapper: the same model, with closing reminders added to the end of each reply prompt (backend/utils/claude_voice.with_tail). Used to ask whether a cheaper Gemini model also follows a rule better when it is the last thing it reads."""

    def __init__(self, inner: Any, tail: str):
        self.inner, self.tail = inner, tail

    def invoke(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        from backend.utils import claude_voice

        return self.inner.invoke(claude_voice.with_tail(prompt, self.tail) if isinstance(prompt, str) else prompt, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in ("inner", "tail"):
            raise AttributeError(name)
        return getattr(self.inner, name)


class AutoLLM:
    """An eval-only stand-in for the hybrid reply voice (backend/utils/reply_tier.py): each prompt goes to the lite model unless the same gate the app uses keeps the turn on the standard one. `stats` counts
    where turns went and why, so a run can say how much of the traffic the hybrid moved."""

    def __init__(self, lite: Any, standard: Any):
        self.lite, self.standard = lite, standard
        self.stats: Dict[str, int] = {}

    def invoke(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        from backend.utils import reply_tier

        reason = reply_tier.full_reason(prompt) if isinstance(prompt, str) else reply_tier.R_COMPANION
        key = "lite" if reason is None else f"standard:{reason}"
        self.stats[key] = self.stats.get(key, 0) + 1
        return (self.standard if reason else self.lite).invoke(prompt, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_") or name in ("lite", "standard", "stats"):
            raise AttributeError(name)
        return getattr(self.standard, name)


def make_tool_llm(name: str, fallback: Any = None):
    """`name` wired the way the app wires the tool loop's reasoning (models.tool_agent_llm): Haiku with its own defaults, Sonnet at the configured effort, Gemini as the lite model is (low temperature, one try).
    `fallback` is None by default so a failed Claude call is a failed run in the comparison; pass the app's Gemini model to measure the real wiring including its rescue."""
    name = resolve(name)
    if CANDIDATES[name]["provider"] == "claude":
        from backend.models import claude_llm

        if name == claude_llm.SONNET_MODEL or "sonnet" in name:
            return claude_llm.ClaudeLLM(model_name=name, fallback=fallback, effort=claude_llm.SONNET_EFFORT)
        return claude_llm.ClaudeLLM(model_name=name, fallback=fallback)
    if CANDIDATES[name]["provider"] == "openai":
        from backend.models.openai_llm import OpenAILLM

        return OpenAILLM(model_name=name, fallback=fallback, effort=TOOL_LOOP_OPENAI_EFFORT, max_retries=OPENAI_EVAL_RETRIES)
    from backend.models.models import LazyLLM

    return LazyLLM(model_name=name, fallback_model=None, temperature=0.2, max_retries=0)


def missing_key(name: str) -> Optional[str]:
    """A sentence saying which key a candidate needs when it is not set, else None. Checked before a run starts, so a missing key is one clear message and not a hundred failed runs (each run
    with no fallback would simply fail). Presence only: a key is never read out."""
    provider = CANDIDATES[resolve(name)]["provider"]
    if provider == "claude":
        from backend.models.claude_llm import api_key
        return None if api_key() else "no Claude key: set ANTHROPIC_API_KEY in .env"
    if provider == "openai":
        from backend.models.openai_llm import api_key
        return None if api_key() else "no OpenAI key: set OPENAI_API_KEY in .env"
    return None if os.getenv("GOOGLE_API_KEY", "").strip() else "no Google key: set GOOGLE_API_KEY in .env"


class CostCeilingExceeded(RuntimeError):
    """The run's estimated spend passed its ceiling; what had finished is reported, nothing further is started."""


class SpendTracker:
    """Adds up the dollars of a run from the usage each model call reports. Counts only: no prompt or reply text ever passes through it."""

    def __init__(self, ceiling_usd: float = DEFAULT_CEILING_USD, price_table: Optional[Dict[str, Dict[str, float]]] = None) -> None:
        self.ceiling = float(ceiling_usd)
        self.prices = price_table if price_table is not None else prices()
        self.by_model: Dict[str, Dict[str, int]] = {}
        self.unpriced_calls = 0
        self.stopped = False  # set when check() raised, so a harness can say its results are partial

    def record(self, model: str, label: str, counts: Optional[Dict[str, int]], day: Any = None) -> None:
        """Same signature as llm_usage.record, so it can sit in front of it. Never raises."""
        try:
            slot = self.by_model.setdefault(str(model), {"calls": 0, "input": 0, "output": 0, "cached": 0})
            slot["calls"] += 1
            for key in ("input", "output", "cached"):
                slot[key] += int((counts or {}).get(key, 0) or 0)
        except Exception:
            self.unpriced_calls += 1

    @property
    def total(self) -> float:
        from backend.utils import llm_usage

        dollars = 0.0
        self.unpriced_calls = 0
        for model, counts in self.by_model.items():
            cost = llm_usage.estimate_cost(model, counts, self.prices)
            if cost is None:
                self.unpriced_calls += counts.get("calls", 0)
            else:
                dollars += cost
        return dollars

    def check(self) -> None:
        """Between runs: raises CostCeilingExceeded once the estimate has passed the ceiling (a ceiling of 0 or less means no limit)."""
        if self.ceiling > 0 and self.total > self.ceiling:
            self.stopped = True
            raise CostCeilingExceeded(f"estimated spend ${self.total:.2f} passed the ceiling of ${self.ceiling:.2f}")

    def line(self) -> str:
        total = self.total
        note = f"; {self.unpriced_calls} call(s) had no price, so the ceiling does not see them" if self.unpriced_calls else ""
        return f"running cost about ${total:.3f} of ${self.ceiling:.2f}{note}" if self.ceiling > 0 else f"running cost about ${total:.3f} (no ceiling){note}"


def tracking(tracker: SpendTracker):
    """A context manager that puts `tracker` in front of llm_usage.record for the length of a run, so every priced call is counted (the original still runs, so the usage page still sees the eval spend)."""
    from contextlib import contextmanager
    from unittest.mock import patch

    from backend.utils import llm_usage

    @contextmanager
    def _ctx():
        real = llm_usage.record

        def both(model, label, counts, day=None):
            tracker.record(model, label, counts, day)
            return real(model, label, counts, day)

        with patch.object(llm_usage, "record", both):
            yield tracker

    return _ctx()


def only_failed_in(record: Dict[str, Any]) -> List[str]:
    """The scenario ids that did NOT pass every run in a saved result (behavior_eval --save), for re-running an expensive model only where a cheaper one fell short."""
    return [sid for sid, row in (record.get("scenarios") or {}).items() if int(row.get("passes", 0)) < int(row.get("runs", 0))]
