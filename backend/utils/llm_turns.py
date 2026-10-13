"""How many model calls does one chat turn make, and how often does deep mode fire? Counts only, never content, never a user.

llm_usage.py says what the calls cost by model and by the code that made them. It cannot say how many calls ONE MESSAGE causes, and
that is the number that explains a bill: one message can run the reasoner, several tool-agent steps (each re-sending a prompt of
thousands of tokens), the checks after them and the streamed reply. This module groups the calls by turn.

- A TURN starts when the chat endpoint begins one (`begin()`) and ends when it finishes (`finish()`). In between, every call
  llm_usage records is also added to the turn, through a context variable, so no call site had to change. Calls made on other threads
  by `asyncio.to_thread` carry the context with them.
- WHAT IS KEPT, per day: turns and their calls and tokens; a histogram of calls per turn (1, 2, 3-4, 5-8, 9 or more) and the most any
  one turn made; turns that used the tool agent against turns that did not, with the same sums for each; and deep mode: how many turns it
  ran on (only the user's own opt-in setting runs the stronger model), how many it would have run on but the budget guard's low-power mode
  held back, and the calls and tokens those deep turns used. Audit-style wording ("scan the repo", "every file") is counted on its own: it
  gets a wider step budget on the normal model. Until 2026-10-12 that wording ran deep mode too, so older days still carry "audit" and
  "both" deep turns; the first day it was measured, one such turn cost as much as fifteen ordinary ones.
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
# WHY a turn went to the tool agent (the expensive path: about 8 calls and 59K input tokens against 2 and 9K for plain chat). A turn can have
# several reasons; each is counted, and a turn with exactly one is also counted as "alone", which is what says which rule is the driver.
ROUTE_REASONS = (
    "repo_question",           # the router read it as a question about this project's code or architecture
    "follow_up",               # a short continuation of an earlier coding task (kept in tool mode on purpose)
    "web_search", "code_or_data", "calendar", "gmail", "drive",
    "admin_ops",               # an admin asking about incidents, logs or deploys
    "approval_continuation",   # answering a pending web-search approval
    "clarification_resume",    # answering the tool agent's own question
)
FIELDS = (
    *SUMS, *BUCKETS, "blocked_turns", "error_turns", "native_turns", "deep_suppressed_turns", "audit_wording_turns",
    *(f"{kind}_{name}" for kind in KINDS for name in SUMS),
    *(f"deep_{source}_turns" for source in DEEP_SOURCES),
    *(f"route_{reason}_turns" for reason in ROUTE_REASONS),
    *(f"route_alone_{reason}_turns" for reason in ROUTE_REASONS),
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
        self.deep_suppressed = False  # the deep-thinking setting was on but low-power mode held it back
        self.route_reasons: set = set()  # why the turn went to the tool agent (see ROUTE_REASONS)
        self.audit_wording = False  # the message had audit-style wording ("scan the repo", "every file"): wider step budget, normal model
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
    """The tool agent ran this turn, with this engine. `setting` is the user's deep-thinking setting (the only thing that runs the stronger
    model), `audit` is whether the wording of the message is audit-style (a wider step budget on the normal model; it used to switch deep
    mode on by itself, which one real turn showed to be the dearest thing the app does), and `suppressed` is whether low-power mode held
    deep mode back. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.tool_agent = True
        turn.engine = engine
        turn.audit_wording = bool(audit)
        if setting and suppressed:
            turn.deep_suppressed = True
        elif setting:
            turn.deep_source = "setting"
    except Exception:
        pass


def tool_agent_reasons(flags: Optional[Dict[str, Any]], admin_ops_question: bool = False) -> List[str]:
    """The reasons the router's flags (and the admin-ops check) send a turn to the tool agent, in a fixed order. Empty when none do. Pure."""
    flags = flags or {}
    reasons: List[str] = []
    if admin_ops_question:
        reasons.append("admin_ops")
    if flags.get("needs_github_search"):
        reasons.append("follow_up" if flags.get("follow_up_intent") else "repo_question")
    if flags.get("needs_web_search"):
        reasons.append("web_search")
    if flags.get("needs_code_interpreter"):
        reasons.append("code_or_data")
    if flags.get("needs_calendar_lookup"):
        reasons.append("calendar")
    if flags.get("needs_gmail_lookup"):
        reasons.append("gmail")
    if flags.get("needs_drive_lookup"):
        reasons.append("drive")
    return reasons


def note_route(reasons: Iterable[str]) -> None:
    """Notes why this turn is going to the tool agent. Names that are not in ROUTE_REASONS are dropped. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.route_reasons.update(reason for reason in reasons if reason in ROUTE_REASONS)
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
            if turn.audit_wording:
                slot["audit_wording_turns"] += 1
            for reason in turn.route_reasons:
                slot[f"route_{reason}_turns"] += 1
                if len(turn.route_reasons) == 1:
                    slot[f"route_alone_{reason}_turns"] += 1
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


def daily_turns(docs: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """`{day: {"turns", "tool_turns", "calls"}}` from the saved day rows, for the page's chart. Pure."""
    out: Dict[str, Dict[str, int]] = {}
    for doc in docs:
        day = str(doc.get("day") or "")
        if not day:
            continue
        slot = out.setdefault(day, {"turns": 0, "tool_turns": 0, "calls": 0})
        slot["turns"] += _count(doc.get("turns"))
        slot["tool_turns"] += _count(doc.get("tool_turns"))
        slot["calls"] += _count(doc.get("calls"))
    return out


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
        # Turns whose wording was audit-style (a wider step budget on the normal model). Before 2026-10-12 these ran the deep model too.
        "audit_wording_turns": total["audit_wording_turns"],
        # Why turns went to the tool agent: per reason, how many turns had it, and how many had it as their only reason.
        "routing": [
            {"reason": reason, "turns": total[f"route_{reason}_turns"], "alone": total[f"route_alone_{reason}_turns"]}
            for reason in sorted(ROUTE_REASONS, key=lambda r: -total[f"route_{r}_turns"])
            if total[f"route_{reason}_turns"]
        ],
        "native_turns": total["native_turns"],
        "blocked_turns": total["blocked_turns"],
        "error_turns": total["error_turns"],
    }
