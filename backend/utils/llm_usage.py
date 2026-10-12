"""What the model calls cost: tokens by model and by the code that made the call. Counts only, never content.

Until this existed nothing in SAAPP measured spend: a turn can make a dozen model calls (the reasoner, the tool agent's steps each
re-sending a prompt of several thousand tokens, the checks after them, the streamed reply) and nobody could say which of them
cost what. LazyLLM (backend/models/models.py) reports every call here; this module adds them up and keeps them per day.

- WHAT IS RECORDED. For each call: the model that actually answered (the fallback's name when the fallback served it), a label for
  the code that asked ("agent_workflow.reasoner_node"), and the token counts the API reports: input, output, thinking (a part of
  output, billed as output) and cached input (a part of input, billed at a discount). Nothing else: no prompt, no reply, no user.
- THE LABEL COMES FROM THE CALL STACK, so no call site had to change and a new one is counted automatically.
- STREAMING reports usage in pieces (the input count on the first chunk, output counts on later ones), so a stream's pieces are
  summed; a stream that is cut short is counted for what it reported.
- IT NEVER GETS IN THE WAY. Recording is in-memory and cheap, writes to the database happen in batches (at the end of a chat turn, and
  in a background thread when enough has built up), and every failure here is swallowed: a metering problem must never cost a reply.
- DOLLARS ARE OPTIONAL. Prices differ by model and change, so none are built in. Set LLM_PRICES to a JSON object of USD per million
  tokens, e.g. {"some-model": {"input": 0.30, "output": 2.50, "cached_input": 0.03}}, and the summary adds an estimate;
  a model with no price is listed as unpriced, never guessed. THE KEYS ARE THE NAMES THE API SAYS IT SERVED, which is what this
  page lists, and they can differ from the name in the code: calls configured as "gemini-3.5-flash" were found (October 2026) to be
  served as "gemini-3.6-flash", which is also how Google's billing page lists them. Price what the page shows.
"""
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.utils import billing_time, llm_turns

logger = logging.getLogger("SASS Logger")

COLLECTION = "llm_usage_daily"
ROUTES_COLLECTION = "llm_model_routes"
FIELDS = ("calls", "input", "output", "reasoning", "cached")
FLUSH_AFTER_CALLS = 40
FLUSH_AFTER_SECONDS = 120
MAX_STACK_DEPTH = 14

# Frames from these modules are plumbing, not the code that decided to call the model.
_PLUMBING_PREFIXES = (
    "backend.models", "backend.utils.llm_usage", "langchain", "langgraph", "asyncio", "threading", "concurrent",
    "contextlib", "functools", "anyio", "starlette", "fastapi", "uvicorn", "pydantic", "typing", "runpy",
)

_lock = threading.Lock()
_buffer: Dict[Tuple[str, str, str], Dict[str, int]] = {}
_buffered_calls = 0
_last_flush = time.monotonic()
_flushing = False
# Calls answered by a DIFFERENT model than the one the code asked for, by (day, asked, answered, kind). Only mismatches are kept.
_routes: Dict[Tuple[str, str, str, str], int] = {}


def empty_usage() -> Dict[str, int]:
    return {field: 0 for field in FIELDS}


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def usage_of(message: Any) -> Dict[str, int]:
    """The token counts one response (or one streamed chunk) reports, as `{input, output, reasoning, cached}`. Zeros for anything
    that reports nothing. `reasoning` and `cached` are parts of `output` and `input`, not additions to them."""
    meta = getattr(message, "usage_metadata", None)
    if not isinstance(meta, dict):
        return {"input": 0, "output": 0, "reasoning": 0, "cached": 0}
    input_details = meta.get("input_token_details") if isinstance(meta.get("input_token_details"), dict) else {}
    output_details = meta.get("output_token_details") if isinstance(meta.get("output_token_details"), dict) else {}
    return {
        "input": _count(meta.get("input_tokens")),
        "output": _count(meta.get("output_tokens")),
        "reasoning": _count(output_details.get("reasoning")),
        "cached": _count(input_details.get("cache_read")),
    }


def served_by(message: Any, default: str) -> str:
    """The model that actually answered: a fallback's name when the fallback served the call."""
    meta = getattr(message, "response_metadata", None)
    name = meta.get("model_name") if isinstance(meta, dict) else None
    return str(name) if name else default


def add_into(total: Dict[str, int], part: Dict[str, int]) -> None:
    for field in ("input", "output", "reasoning", "cached"):
        total[field] = total.get(field, 0) + _count(part.get(field))


# ---------------------------------------------------------------------------------------------------------------
# Which code made the call
# ---------------------------------------------------------------------------------------------------------------

def _script_name(frame: Any) -> str:
    """What to call code running as `__main__`: the module it was started as (`python -m backend.evals.behavior_eval`), else the
    script's file name, so a run from a terminal is recognisable in the usage page instead of every one being "__main__"."""
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if spec is not None and getattr(spec, "name", None):
        return str(spec.name)
    path = str(frame.f_globals.get("__file__", "") or "")
    return os.path.splitext(os.path.basename(path))[0] or "script"


def caller_label() -> str:
    """"module.function" of the nearest frame that is not plumbing, e.g. "agent_workflow.reasoner_node". Never raises."""
    try:
        frame = sys._getframe(1)
        for _ in range(MAX_STACK_DEPTH):
            if frame is None:
                break
            module = str(frame.f_globals.get("__name__", ""))
            if module == "__main__":
                module = _script_name(frame)  # "python -m x.y" or "python script.py" would otherwise all read "__main__"
            if module and not module.startswith(_PLUMBING_PREFIXES):
                return f"{module.rsplit('.', 1)[-1]}.{frame.f_code.co_name}"
            frame = frame.f_back
    except Exception:
        pass
    return "unknown"


# ---------------------------------------------------------------------------------------------------------------
# Adding up and saving
# ---------------------------------------------------------------------------------------------------------------

def _today() -> str:
    return billing_time.day()  # Google's day (Pacific), so "today" here is "today" on its Spend page


def record(model: str, label: str, usage: Dict[str, int], day: Optional[str] = None) -> None:
    """Counts one call. Cheap, in memory, and silent on any failure."""
    global _buffered_calls
    try:
        key = (day or _today(), str(model or "unknown")[:80], str(label or "unknown")[:120])
        with _lock:
            slot = _buffer.setdefault(key, empty_usage())
            slot["calls"] += 1
            add_into(slot, usage)
            _buffered_calls += 1
            due = _buffered_calls >= FLUSH_AFTER_CALLS or (time.monotonic() - _last_flush) >= FLUSH_AFTER_SECONDS
        if due:
            flush_in_background()
    except Exception:
        pass
    try:
        llm_turns.note_call(usage or {})  # the same call, added to the chat turn that made it (backend/utils/llm_turns.py)
    except Exception:
        pass


def note_route(requested: str, served: str, fallback: Optional[str] = None, day: Optional[str] = None) -> None:
    """Notes a call that was answered by a different model than the one asked for: "fallback" when it was the configured fallback
    that answered (the primary failed), "rerouted" for anything else (Google answering an older model name with a newer model).
    Calls answered by the model that was asked are not stored: the page only needs the exceptions. Never raises."""
    try:
        if not requested or not served or requested == served:
            return
        kind = "fallback" if fallback and served == fallback else "rerouted"
        key = (day or _today(), str(requested)[:80], str(served)[:80], kind)
        with _lock:
            _routes[key] = _routes.get(key, 0) + 1
    except Exception:
        pass


def snapshot() -> Dict[Tuple[str, str, str], Dict[str, int]]:
    with _lock:
        return {key: dict(value) for key, value in _buffer.items()}


def reset() -> None:
    global _buffered_calls, _flushing
    with _lock:
        _buffer.clear()
        _routes.clear()
        _buffered_calls = 0
        _flushing = False
    llm_turns.reset()


def flush(db: Any = None) -> int:
    """Saves the usage counts and, with them, the per-turn counts (llm_turns.py). Returns how many usage rows were written. Blocking:
    call it off the event loop. Never raises."""
    try:
        return _flush_usage(db)
    finally:
        try:
            llm_turns.flush(db)
        except Exception:
            pass


def _flush_usage(db: Any = None) -> int:
    """Writes what has been counted to the database (one `$inc` per day/model/label) and returns how many rows. If the write fails,
    the counts go back to be tried again with the next flush. Blocking: call it off the event loop. Never raises."""
    global _buffered_calls, _last_flush
    with _lock:
        pending = dict(_buffer)
        pending_routes = dict(_routes)
        _buffer.clear()
        _routes.clear()
        _buffered_calls = 0
        _last_flush = time.monotonic()
    if not pending and not pending_routes:
        return 0
    written = 0
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        for (day, model, label), counts in pending.items():
            db[COLLECTION].update_one(
                {"day": day, "model": model, "label": label},
                {"$inc": {field: counts.get(field, 0) for field in FIELDS}},
                upsert=True,
            )
            written += 1
        for (day, requested, served, kind), calls in pending_routes.items():
            db[ROUTES_COLLECTION].update_one(
                {"day": day, "requested": requested, "served": served, "kind": kind}, {"$inc": {"calls": calls}}, upsert=True
            )
        return written
    except Exception:
        logger.warning("[llm_usage] could not save the usage counts; keeping them for the next flush.")
        with _lock:
            for key, counts in pending.items():
                slot = _buffer.setdefault(key, empty_usage())
                for field in FIELDS:
                    slot[field] = slot.get(field, 0) + counts.get(field, 0)
            for key, calls in pending_routes.items():
                _routes[key] = _routes.get(key, 0) + calls
        return 0


def flush_in_background() -> None:
    """Starts a flush on its own thread, once at a time, so the call that tipped the buffer is never held up by the database."""
    global _flushing
    try:
        with _lock:
            if _flushing:
                return
            _flushing = True

        def run() -> None:
            global _flushing
            try:
                flush()
            finally:
                with _lock:
                    _flushing = False

        threading.Thread(target=run, name="llm-usage-flush", daemon=True).start()
    except Exception:
        pass


# ---------------------------------------------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------------------------------------------

def fetch_usage_docs(db: Any, days: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The saved rows for the last `days` days, today included. Blocking."""
    moment = now or datetime.now(timezone.utc)
    first = billing_time.days_back(moment, days)
    try:
        return list(db[COLLECTION].find({"day": {"$gte": first}}, {"_id": 0}))
    except Exception:
        logger.warning("[llm_usage] could not read the usage counts.")
        return []


def fetch_route_docs(db: Any, days: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Saved "answered by a different model" rows for the last `days` days. Blocking; never raises."""
    moment = now or datetime.now(timezone.utc)
    first = billing_time.days_back(moment, days)
    try:
        return list(db[ROUTES_COLLECTION].find({"day": {"$gte": first}}, {"_id": 0}))
    except Exception:
        logger.warning("[llm_usage] could not read the model routes.")
        return []


def summarize_routes(docs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """`[{requested, served, kind, calls}]`, most calls first: which model names were answered by a different model, and how often.
    An empty list means every call was answered by the model the code asked for. Pure."""
    merged: Dict[Tuple[str, str, str], int] = {}
    for doc in docs:
        key = (str(doc.get("requested") or "?"), str(doc.get("served") or "?"), str(doc.get("kind") or "rerouted"))
        merged[key] = merged.get(key, 0) + _count(doc.get("calls"))
    rows = [{"requested": r, "served": s, "kind": k, "calls": c} for (r, s, k), c in merged.items() if c]
    return sorted(rows, key=lambda row: row["calls"], reverse=True)


def load_prices(raw: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """USD per million tokens by model from LLM_PRICES (JSON). Anything malformed is ignored, so a typo never breaks the page."""
    text = os.getenv("LLM_PRICES", "") if raw is None else raw
    try:
        data = json.loads(text) if text.strip() else {}
    except ValueError:
        return {}
    prices: Dict[str, Dict[str, float]] = {}
    for model, entry in (data.items() if isinstance(data, dict) else []):
        if not isinstance(entry, dict):
            continue
        try:
            parsed = {key: float(entry[key]) for key in ("input", "output", "cached_input") if key in entry}
        except (TypeError, ValueError):
            continue
        if "input" in parsed and "output" in parsed:
            parsed.setdefault("cached_input", parsed["input"])
            prices[str(model)] = parsed
    return prices


def estimate_cost(model: str, counts: Dict[str, int], prices: Dict[str, Dict[str, float]]) -> Optional[float]:
    price = prices.get(model)
    if price is None:
        return None
    cached = min(counts.get("cached", 0), counts.get("input", 0))
    uncached = counts.get("input", 0) - cached
    return (uncached * price["input"] + cached * price["cached_input"] + counts.get("output", 0) * price["output"]) / 1_000_000


def _share(part: float, whole: float) -> Optional[float]:
    return round(part / whole, 4) if whole else None


def _with_rates(counts: Dict[str, int], total_tokens: int) -> Dict[str, Any]:
    tokens = counts["input"] + counts["output"]
    return {
        **counts,
        "tokens": tokens,
        "share": _share(tokens, total_tokens),
        "cache_rate": _share(counts["cached"], counts["input"]),
        "thinking_rate": _share(counts["reasoning"], counts["output"]),
    }


def summarize_usage(docs: Iterable[Dict[str, Any]], prices: Optional[Dict[str, Dict[str, float]]] = None) -> Dict[str, Any]:
    """The usage page's data: totals, then by model and by caller, biggest first, each with its share of all tokens, how much of its
    input was cached and how much of its output was thinking, and (only with prices) an estimated cost. Pure."""
    prices = prices or {}
    total = empty_usage()
    by_model: Dict[str, Dict[str, int]] = {}
    by_label: Dict[Tuple[str, str], Dict[str, int]] = {}
    for doc in docs:
        model, label = str(doc.get("model") or "unknown"), str(doc.get("label") or "unknown")
        for target in (total, by_model.setdefault(model, empty_usage()), by_label.setdefault((label, model), empty_usage())):
            for field in FIELDS:
                target[field] += _count(doc.get(field))
    grand = total["input"] + total["output"]

    def cost_of(items: Dict[Any, Dict[str, int]], model_of) -> Dict[Any, Optional[float]]:
        return {key: estimate_cost(model_of(key), counts, prices) for key, counts in items.items()}

    model_costs = cost_of(by_model, lambda key: key)
    label_costs = cost_of(by_label, lambda key: key[1])
    priced = [value for value in model_costs.values() if value is not None]
    models = [{"model": model, **_with_rates(counts, grand), "cost": model_costs[model]} for model, counts in by_model.items()]
    labels = [
        {"label": label, "model": model, **_with_rates(counts, grand), "cost": label_costs[(label, model)]}
        for (label, model), counts in by_label.items()
    ]
    return {
        "total": _with_rates(total, grand),
        "by_model": sorted(models, key=lambda row: row["tokens"], reverse=True),
        "by_label": sorted(labels, key=lambda row: row["tokens"], reverse=True),
        "estimated_cost": round(sum(priced), 4) if priced else None,
        "unpriced_models": sorted(model for model, cost in model_costs.items() if cost is None) if prices else [],
        "priced": bool(prices),
    }
