"""How many model calls does one chat turn make, and how often does deep mode fire? Counts only, never content, never a user.

llm_usage.py says what the calls cost by model and by the code that made them. It cannot say how many calls ONE MESSAGE causes, and
that is the number that explains a bill: one message can run the reasoner, several tool-agent steps (each re-sending a prompt of
thousands of tokens), the checks after them and the streamed reply. This module groups the calls by turn.

- A TURN starts when the chat endpoint begins one (`begin()`) and ends when it finishes (`finish()`). In between, every call
  llm_usage records is also added to the turn, through a context variable, so no call site had to change. Calls made on other threads
  by `asyncio.to_thread` carry the context with them.
- WHAT IS KEPT, per day: turns and their calls and tokens; a histogram of calls per turn (1, 2, 3-4, 5-8, 9 or more) and the most any
  one turn made; turns that used the tool agent against turns that did not, with the same sums for each; and deep mode: how many turns it
  ran on, split by what switched it on (the user's own setting, the audit-style wording, or both), how many it would have run on but the
  budget guard's low-power mode held back, and the calls and tokens those deep turns used. That split is the point: the setting is opt-in
  and off by default, the audit wording is not, and it was not known how often the wording fires on its own.
- IT NEVER GETS IN THE WAY. Everything is in memory and written in batches with the usage counts (llm_usage.flush calls flush() here),
  and every failure is swallowed.
"""
import logging
import threading
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from backend.utils import billing_time

logger = logging.getLogger("SASS Logger")

COLLECTION = "llm_turns_daily"
BUCKETS = ("c1", "c2", "c3_4", "c5_8", "c9_plus")
SUMS = ("turns", "calls", "input", "output", "reasoning")
KINDS = ("plain", "tool", "deep")  # a deep turn is also a tool turn; it is kept separately so its cost can be read on its own
DEEP_SOURCES = ("setting", "audit", "both")
FIELDS = (
    *SUMS, *BUCKETS, "blocked_turns", "error_turns", "native_turns", "deep_suppressed_turns",
    *(f"{kind}_{name}" for kind in KINDS for name in SUMS),
    *(f"deep_{source}_turns" for source in DEEP_SOURCES),
)

_lock = threading.Lock()
_pending: Dict[str, Dict[str, int]] = {}
_pending_max: Dict[str, int] = {}
_current: ContextVar[Optional["Turn"]] = ContextVar("llm_turn", default=None)


class Turn:
    """What one chat turn has used so far."""

    def __init__(self) -> None:
        self.calls = 0
        self.input = 0
        self.output = 0
        self.reasoning = 0
        self.tool_agent = False
        self.engine: Optional[str] = None
        self.deep_source: Optional[str] = None  # "setting", "audit" or "both" when deep mode ran
        self.deep_suppressed = False  # deep mode was asked for (by either) but low-power mode held it back
        self.done = False
        self._lock = threading.Lock()


def _count(value: Any) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _today() -> str:
    return billing_time.day()


def begin() -> Turn:
    """Starts counting a turn in the current context and returns it. Never raises."""
    turn = Turn()
    try:
        _current.set(turn)
    except Exception:
        pass
    return turn


def current() -> Optional[Turn]:
    try:
        return _current.get()
    except Exception:
        return None


def note_call(usage: Dict[str, int]) -> None:
    """Adds one model call (as llm_usage records it) to the turn running in this context, if any. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        with turn._lock:
            turn.calls += 1
            turn.input += _count(usage.get("input"))
            turn.output += _count(usage.get("output"))
            turn.reasoning += _count(usage.get("reasoning"))
    except Exception:
        pass


def note_tool_agent(engine: str, setting: bool, audit: bool, suppressed: bool) -> None:
    """The tool agent ran this turn, with this engine. `setting` is the user's deep-thinking setting, `audit` is whether the wording of the
    message is audit-style, and `suppressed` is whether low-power mode held deep mode back. (The chat endpoint already switches the setting
    off in low-power mode, so "held back" only ever counts turns the audit wording alone would have made deep.) Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.tool_agent = True
        turn.engine = engine
        wants = bool(setting) or bool(audit)
        if wants and suppressed:
            turn.deep_suppressed = True
        elif wants:
            turn.deep_source = "both" if (setting and audit) else ("setting" if setting else "audit")
    except Exception:
        pass


def bucket_for(calls: int) -> str:
    if calls <= 1:
        return "c1"
    if calls == 2:
        return "c2"
    if calls <= 4:
        return "c3_4"
    if calls <= 8:
        return "c5_8"
    return "c9_plus"


def finish(turn: Optional[Turn], outcome: str = "ok", day: Optional[str] = None) -> None:
    """Ends a turn and adds it to the day's counts. Safe to call more than once (only the first counts) and safe to call with None.
    `outcome` is "ok", "error" or "blocked" (the budget guard paused the chat, so no model call was made). Never raises."""
    try:
        if turn is None:
            return
        with turn._lock:
            if turn.done:
                return
            turn.done = True
        key = day or _today()
        with _lock:
            slot = _pending.setdefault(key, {field: 0 for field in FIELDS})
            if outcome == "blocked":
                slot["blocked_turns"] += 1
                return
            if outcome == "error":
                slot["error_turns"] += 1
            kinds = ["deep", "tool"] if turn.deep_source else (["tool"] if turn.tool_agent else ["plain"])
            for target in ("", *(f"{kind}_" for kind in kinds)):
                slot[f"{target}turns"] += 1
                slot[f"{target}calls"] += turn.calls
                slot[f"{target}input"] += turn.input
                slot[f"{target}output"] += turn.output
                slot[f"{target}reasoning"] += turn.reasoning
            slot[bucket_for(turn.calls)] += 1
            if turn.engine == "native":
                slot["native_turns"] += 1
            if turn.deep_source:
                slot[f"deep_{turn.deep_source}_turns"] += 1
            if turn.deep_suppressed:
                slot["deep_suppressed_turns"] += 1
            _pending_max[key] = max(_pending_max.get(key, 0), turn.calls)
    except Exception:
        pass


def reset() -> None:
    with _lock:
        _pending.clear()
        _pending_max.clear()
    try:
        _current.set(None)
    except Exception:
        pass


def snapshot() -> Dict[str, Dict[str, int]]:
    with _lock:
        return {day: {**slot, "max_calls": _pending_max.get(day, 0)} for day, slot in _pending.items()}


def flush(db: Any = None) -> int:
    """Writes the day counts to the database (one `$inc`, plus `$max` for the biggest turn, per day) and returns how many days. If the
    write fails, the counts go back to be tried again. Blocking: call it off the event loop. Never raises."""
    with _lock:
        pending = {day: dict(slot) for day, slot in _pending.items()}
        pending_max = dict(_pending_max)
        _pending.clear()
        _pending_max.clear()
    if not pending:
        return 0
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        for day, slot in pending.items():
            db[COLLECTION].update_one(
                {"day": day},
                {"$inc": {field: value for field, value in slot.items() if value}, "$max": {"max_calls": pending_max.get(day, 0)}},
                upsert=True,
            )
        return len(pending)
    except Exception:
        logger.warning("[llm_turns] could not save the per-turn counts; keeping them for the next flush.")
        with _lock:
            for day, slot in pending.items():
                back = _pending.setdefault(day, {field: 0 for field in FIELDS})
                for field, value in slot.items():
                    back[field] = back.get(field, 0) + value
                _pending_max[day] = max(_pending_max.get(day, 0), pending_max.get(day, 0))
        return 0


# ---------------------------------------------------------------------------------------------------------------
# Reading it back
# ---------------------------------------------------------------------------------------------------------------

def fetch_turn_docs(db: Any, days: int, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """The saved day rows for the last `days` days, today included. Blocking; never raises."""
    moment = now or datetime.now(timezone.utc)
    first = billing_time.days_back(moment, days)
    try:
        return list(db[COLLECTION].find({"day": {"$gte": first}}, {"_id": 0}))
    except Exception:
        logger.warning("[llm_turns] could not read the per-turn counts.")
        return []


def _average(total: int, turns: int) -> Optional[float]:
    return round(total / turns, 1) if turns else None


def summarize_turns(docs: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """What the usage page shows about turns: overall and per kind (plain, tool, deep) the number of turns, average calls and tokens per
    turn; the calls-per-turn histogram; the biggest turn; and how deep mode was switched on. Pure."""
    total = {field: 0 for field in FIELDS}
    biggest = 0
    for doc in docs:
        for field in FIELDS:
            total[field] += _count(doc.get(field))
        biggest = max(biggest, _count(doc.get("max_calls")))

    def kind_row(prefix: str) -> Dict[str, Any]:
        turns = total[f"{prefix}turns"]
        return {
            "turns": turns,
            "avg_calls": _average(total[f"{prefix}calls"], turns),
            "avg_input": _average(total[f"{prefix}input"], turns),
            "avg_output": _average(total[f"{prefix}output"], turns),
            "avg_reasoning": _average(total[f"{prefix}reasoning"], turns),
        }

    turns = total["turns"]
    deep_turns = total["deep_turns"]
    return {
        "turns": turns,
        "overall": kind_row(""),
        "plain": kind_row("plain_"),
        "tool": kind_row("tool_"),
        "deep": kind_row("deep_"),
        "histogram": [{"bucket": bucket, "turns": total[bucket]} for bucket in BUCKETS],
        "max_calls": biggest,
        "deep_sources": {source: total[f"deep_{source}_turns"] for source in DEEP_SOURCES},
        "deep_share": round(deep_turns / turns, 4) if turns else None,
        # Deep turns nobody asked for by setting: the audit wording alone. This is the number that says whether narrowing it would help.
        "deep_unasked": total["deep_audit_turns"],
        "deep_suppressed": total["deep_suppressed_turns"],
        "native_turns": total["native_turns"],
        "blocked_turns": total["blocked_turns"],
        "error_turns": total["error_turns"],
    }
