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
    "commit_request",          # asked to commit, push or open a branch for the changes (the older pull-request drafter cannot do that)
    "place_lookup",            # a question about places or getting somewhere, with the maps tools set up (backend/utils/place_questions.py, backend/services/maps_tools.py)
)
# HOW MANY STEPS the tool agent's loop used, and whether it ran into its limit. A turn that reached the last step it was allowed (the forced final answer) may have wanted more:
# this is the number that says whether the step budget (TOOL_AGENT_MAX_ITERATIONS and the deep one) is too tight, or whether it never matters. Counts only.
STEP_BUCKETS = ("steps_1_2", "steps_3_4", "steps_5_6", "steps_7_plus")
# HOW A TOOL TURN ENDED, relative to its budget, so a checkpoint design can be judged by what turns do now (2026-10-24): answered by the halfway step, answered later, or ran into the
# last step with a draft kept or with nothing written ("read, read, read" and ran out). And whether the halfway nudge was shown, and a draft followed it. Counts only.
# Checkpoints (backend/services/native_loop.py): turns that reached one, and whether the model answered there or chose to continue.
EXIT_FIELDS = ("exit_early_turns", "exit_late_turns", "cap_draft_turns", "cap_no_draft_turns", "nudge_turns", "nudge_draft_turns",
               "checkpoint_turns", "checkpoint_answered_turns", "checkpoint_continued_turns", "checkpoint_acted_turns")
LOOP_FIELDS = ("loop_turns", "loop_steps", "cap_hit_turns", "deep_loop_turns", "deep_cap_hit_turns", *EXIT_FIELDS)
FIELDS = (
    *SUMS, *BUCKETS, *STEP_BUCKETS, *LOOP_FIELDS, "blocked_turns", "error_turns", "native_turns", "deep_suppressed_turns", "audit_wording_turns",
    *(f"{kind}_{name}" for kind in KINDS for name in SUMS),
    *(f"deep_{source}_turns" for source in DEEP_SOURCES),
    *(f"route_{reason}_turns" for reason in ROUTE_REASONS),
    *(f"route_alone_{reason}_turns" for reason in ROUTE_REASONS),
    # Of the turns with each reason (and of those with it as their only reason), how many called NO tool at all: the tool agent's prompt was paid for and it answered
    # from what it already had. A high share for a reason means that reason is sending turns down the expensive path that never needed it (2026-10-23: "a short follow-up
    # to a coding task" was the only reason on 93 of 106 turns, and nothing said how many of them used a tool).
    *(f"route_notool_{reason}_turns" for reason in ROUTE_REASONS),
    *(f"route_alone_notool_{reason}_turns" for reason in ROUTE_REASONS),
)

_lock = threading.Lock()
_pending: Dict[str, Dict[str, int]] = {}
_pending_max: Dict[str, int] = {}
# WHICH tools the tool agent called, by day and tool name: {day: {tool: {"calls", "turns", "errors"}}}. Names only: no arguments, no results.
_pending_tools: Dict[str, Dict[str, Dict[str, int]]] = {}
MAX_TOOL_NAME = 40
_current: ContextVar[Optional["Turn"]] = ContextVar("llm_turn", default=None)


class Turn:
    """What one chat turn has used so far."""

    def __init__(self, user: Optional[str] = None) -> None:
        self.user = user  # who the turn is for, only so their own usage can be added up (backend/utils/user_usage.py)
        self.calls = 0
        self.input = 0
        self.output = 0
        self.reasoning = 0
        self.cost_micro = 0  # what the turn's priced calls cost, in millionths of a dollar (a model with no price in LLM_PRICES adds nothing)
        self.tool_agent = False
        self.engine: Optional[str] = None
        self.deep_source: Optional[str] = None  # "setting", "audit" or "both" when deep mode ran
        self.deep_suppressed = False  # the deep-thinking setting was on but low-power mode held it back
        self.route_reasons: set = set()  # why the turn went to the tool agent (see ROUTE_REASONS)
        self.tools: Dict[str, Dict[str, int]] = {}  # which tools it called: {name: {"calls", "errors"}}
        self.loop_ran = False  # the tool agent's loop ran this turn (see note_tool_loop)
        self.loop_steps = 0  # how many steps (model decisions) it used
        self.cap_hit = False  # it reached the last step it was allowed, or ended without deciding to answer
        self.plan_deviated = False  # an approved plan was being carried out and the turn was stopped for going off it more times than the plan allows (backend/utils/code_plan.py PlanBudget)
        self.plan_exit = ""  # how the loop ended while carrying out an approved plan (backend/utils/code_plan.py PLAN_STOP_REASONS), as the loop saw it
        self.plan_blocked = ""  # a decision the person made on a proposal this turn that can end it: "declined" or "no_decision"
        self.plan_stop = ""  # the final reason such a turn ended, once the plan's own state is known (code_plan.stop_reason)
        self.rhythm: Optional[Dict[str, Any]] = None  # the coding loop's rhythm: reads before the first act, edits applied and checked, notices (backend/utils/loop_rhythm.py), counts only
        self.loop_exit = ""  # how the loop's last run ended: "early", "late", "cap_draft" or "cap_no_draft" (see exit_kind)
        self.nudged = False  # the halfway nudge was shown to the model this turn
        self.nudge_drafted = False  # ... and a draft existed by the end
        self.checkpoints_answered = 0  # checkpoints (a decision point at a third and two thirds of a long budget) where it answered
        self.checkpoints_continued = 0  # ... and where it chose to keep working
        self.checkpoints_acted = 0  # ... and, on a change to a folder, where it made the proposal there
        self.leanings: list = []  # the working leanings put in this turn's prompt (ids only: backend/utils/leanings.py)
        self.experience_offered = False  # a real case from its own record was offered to this turn's reply (backend/utils/experiences.py)
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


def begin(user: Optional[str] = None) -> Turn:
    """Starts counting a turn in the current context and returns it. `user` (optional) is who it is for: the turn's tokens are then also
    added to that person's own minute-by-minute count (backend/utils/user_usage.py) when it finishes. Never raises."""
    turn = Turn(user)
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


def note_call(usage: Dict[str, int], model: Optional[str] = None) -> None:
    """Adds one model call (as llm_usage records it) to the turn running in this context, if any. `model` lets its price be added to the turn's
    dollars (what a per-person dollar limit is judged against). Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        cost_micro = 0
        if model:
            from backend.utils import llm_usage

            dollars = llm_usage.estimate_cost(str(model), {k: _count(usage.get(k)) for k in ("input", "output", "cached")}, llm_usage.cached_prices())
            cost_micro = int(round((dollars or 0.0) * 1_000_000))
        with turn._lock:
            turn.cost_micro += cost_micro
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


def clean_tool_name(name: Any) -> str:
    """A tool name that is safe to use as a stored key: lowercase letters, digits and underscores, short; anything else is "other"."""
    text = str(name or "").strip()
    return text if text and len(text) <= MAX_TOOL_NAME and text.replace("_", "").isalnum() and text == text.lower() else "other"


def steps_bucket(steps: int) -> str:
    if steps <= 2:
        return "steps_1_2"
    if steps <= 4:
        return "steps_3_4"
    if steps <= 6:
        return "steps_5_6"
    return "steps_7_plus"


def exit_kind(steps_used: int, hit_cap: bool, tool_steps: Optional[int], had_draft: bool) -> str:
    """How a loop ended: "cap_draft" / "cap_no_draft" when it ran into its last step (with a draft kept, or with nothing written), else "early" when it answered by the halfway
    step and "late" after it. "" when the number of tool steps is not known. Pure."""
    if hit_cap:
        return "cap_draft" if had_draft else "cap_no_draft"
    if not tool_steps or tool_steps < 1:
        return ""
    return "early" if _count(steps_used) <= max(1, tool_steps // 2) else "late"


def note_leanings(ids: Any) -> None:
    """The working leanings (backend/utils/leanings.py) put in this turn's prompt, by id, so the reply can be marked with them and the message after it can be counted against them."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.leanings = [str(i) for i in (ids or []) if str(i)][:8]
    except Exception:
        pass


def note_experience() -> None:
    """A real case from Sonic's own record was put in this turn's prompt (backend/utils/experiences.py), so the reply can be checked for citing it."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.experience_offered = True
    except Exception:
        pass


def note_plan_deviation() -> None:
    """The plan's deviation allowance (backend/utils/code_plan.py PlanBudget) ended this turn: it went off an approved plan more times than the plan allows. Read by app.py to capture the case
    as a note in Sonic's record (the plan_deviation signal in backend/utils/experience_capture.py). Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.plan_deviated = True
    except Exception:
        pass


def note_plan_exit(reason: str) -> None:
    """The tool loop ended while carrying out an approved plan, for this reason (code_plan.PLAN_STOP_REASONS, as the loop saw it). The last one wins. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.plan_exit = str(reason or "")
    except Exception:
        pass


def note_plan_blocked(reason: str) -> None:
    """The person declined a proposed change, or did not answer it in time, while a plan was being carried out. Explains a turn that then simply stops. The last one wins. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.plan_blocked = str(reason or "")
    except Exception:
        pass


def note_plan_stop(reason: str) -> None:
    """The final reason a plan-carrying turn ended (code_plan.stop_reason): what app.py counts. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.plan_stop = str(reason or "")
    except Exception:
        pass


def note_rhythm(summary: Dict[str, Any]) -> None:
    """The coding loop ended: how it read and acted (backend/utils/loop_rhythm.LoopRhythm.summary: counts only). A turn that resumes the loop keeps the latest. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done or not isinstance(summary, dict):
            return
        turn.rhythm = {key: summary.get(key) for key in ("reads_before_first_act", "applies", "applies_checked", "notices")}
    except Exception:
        pass


def note_checkpoint(answered: bool, acted: bool = False) -> None:
    """The tool loop reached a checkpoint and the model answered there, made the proposal there (`acted`, on a change to a folder), or chose to keep working. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        if answered:
            turn.checkpoints_answered += 1
        elif acted:
            turn.checkpoints_acted += 1
        else:
            turn.checkpoints_continued += 1
    except Exception:
        pass


def note_tool_loop(steps_used: int, hit_cap: bool, *, tool_steps: Optional[int] = None, had_draft: bool = False, nudged: bool = False) -> None:
    """The tool agent's loop ended: it used `steps_used` steps, and `hit_cap` says whether it reached the last step it was allowed (the forced final answer) or ended
    without deciding to answer. `tool_steps` (the budget without the answer step), `had_draft` and `nudged` (the halfway nudge was shown) say how it ended; see EXIT_FIELDS.
    A turn that resumes the loop adds to the same totals. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        turn.loop_ran = True
        turn.loop_steps += _count(steps_used)
        turn.cap_hit = turn.cap_hit or bool(hit_cap)
        turn.loop_exit = exit_kind(steps_used, hit_cap, tool_steps, had_draft)
        if nudged:
            turn.nudged = True
            turn.nudge_drafted = turn.nudge_drafted or bool(had_draft)
    except Exception:
        pass


def note_tool_call(name: Any, ok: bool = True) -> None:
    """Notes that the tool agent called a tool this turn, and whether it came back as an error. Never raises."""
    try:
        turn = _current.get()
        if turn is None or turn.done:
            return
        with turn._lock:
            entry = turn.tools.setdefault(clean_tool_name(name), {"calls": 0, "errors": 0})
            entry["calls"] += 1
            if not ok:
                entry["errors"] += 1
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
        if turn.user and outcome != "blocked":
            from backend.utils import user_usage

            user_usage.add(turn.user, turn.input + turn.output, turn.calls, cost_micro=turn.cost_micro)
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
            if turn.loop_ran:
                slot["loop_turns"] += 1
                slot["loop_steps"] += turn.loop_steps
                slot[steps_bucket(turn.loop_steps)] += 1
                if turn.cap_hit:
                    slot["cap_hit_turns"] += 1
                if turn.loop_exit:
                    slot[f"{'exit_' if turn.loop_exit in ('early', 'late') else ''}{turn.loop_exit}_turns"] += 1
                if turn.checkpoints_answered or turn.checkpoints_continued or turn.checkpoints_acted:
                    slot["checkpoint_turns"] += 1
                    slot["checkpoint_answered_turns"] += 1 if turn.checkpoints_answered else 0
                    slot["checkpoint_continued_turns"] += 1 if turn.checkpoints_continued else 0
                    slot["checkpoint_acted_turns"] += 1 if turn.checkpoints_acted else 0
                if turn.nudged:
                    slot["nudge_turns"] += 1
                    if turn.nudge_drafted:
                        slot["nudge_draft_turns"] += 1
                if turn.deep_source:
                    slot["deep_loop_turns"] += 1
                    if turn.cap_hit:
                        slot["deep_cap_hit_turns"] += 1
            if turn.tools:
                day_tools = _pending_tools.setdefault(key, {})
                for tool, entry in turn.tools.items():
                    total = day_tools.setdefault(tool, {"calls": 0, "turns": 0, "errors": 0})
                    total["calls"] += entry["calls"]
                    total["errors"] += entry["errors"]
                    total["turns"] += 1
            used_no_tool = turn.tool_agent and not turn.tools
            for reason in turn.route_reasons:
                slot[f"route_{reason}_turns"] += 1
                if used_no_tool:
                    slot[f"route_notool_{reason}_turns"] += 1
                if len(turn.route_reasons) == 1:
                    slot[f"route_alone_{reason}_turns"] += 1
                    if used_no_tool:
                        slot[f"route_alone_notool_{reason}_turns"] += 1
            _pending_max[key] = max(_pending_max.get(key, 0), turn.calls)
    except Exception:
        pass


def reset() -> None:
    with _lock:
        _pending.clear()
        _pending_max.clear()
        _pending_tools.clear()
    try:
        _current.set(None)
    except Exception:
        pass


def snapshot() -> Dict[str, Dict[str, int]]:
    with _lock:
        return {day: {**slot, "max_calls": _pending_max.get(day, 0)} for day, slot in _pending.items()}


def tools_snapshot() -> Dict[str, Dict[str, Dict[str, int]]]:
    """What has been counted so far about which tools were called, `{day: {tool: {"calls", "turns", "errors"}}}`. For tests and debugging."""
    with _lock:
        return {day: {tool: dict(entry) for tool, entry in tools.items()} for day, tools in _pending_tools.items()}


def flush(db: Any = None) -> int:
    """Writes the day counts to the database (one `$inc`, plus `$max` for the biggest turn, per day) and returns how many days. If the
    write fails, the counts go back to be tried again. Blocking: call it off the event loop. Never raises."""
    with _lock:
        pending = {day: dict(slot) for day, slot in _pending.items()}
        pending_max = dict(_pending_max)
        pending_tools = {day: {tool: dict(entry) for tool, entry in tools.items()} for day, tools in _pending_tools.items()}
        _pending.clear()
        _pending_max.clear()
        _pending_tools.clear()
    if not pending and not pending_tools:
        return 0
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        for day in sorted(set(pending) | set(pending_tools)):
            increments = {field: value for field, value in pending.get(day, {}).items() if value}
            for tool, entry in pending_tools.get(day, {}).items():
                for name, value in entry.items():
                    if value:
                        increments[f"tools.{tool}.{name}"] = value
            update: Dict[str, Any] = {"$inc": increments}
            if day in pending:
                update["$max"] = {"max_calls": pending_max.get(day, 0)}
            db[COLLECTION].update_one({"day": day}, update, upsert=True)
        return len(set(pending) | set(pending_tools))
    except Exception:
        logger.warning("[llm_turns] could not save the per-turn counts; keeping them for the next flush.")
        with _lock:
            for day, slot in pending.items():
                back = _pending.setdefault(day, {field: 0 for field in FIELDS})
                for field, value in slot.items():
                    back[field] = back.get(field, 0) + value
                _pending_max[day] = max(_pending_max.get(day, 0), pending_max.get(day, 0))
            for day, tools in pending_tools.items():
                for tool, entry in tools.items():
                    back_entry = _pending_tools.setdefault(day, {}).setdefault(tool, {"calls": 0, "turns": 0, "errors": 0})
                    for name, value in entry.items():
                        back_entry[name] = back_entry.get(name, 0) + value
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
    docs_list = list(docs)
    for doc in docs_list:
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

    tool_totals: Dict[str, Dict[str, int]] = {}
    for doc in docs_list:
        for tool, entry in (doc.get("tools") or {}).items() if isinstance(doc.get("tools"), dict) else []:
            if not isinstance(entry, dict):
                continue
            slot = tool_totals.setdefault(str(tool), {"calls": 0, "turns": 0, "errors": 0})
            for name in ("calls", "turns", "errors"):
                slot[name] += _count(entry.get(name))
    tools = sorted(({"tool": tool, **values} for tool, values in tool_totals.items()), key=lambda row: (-row["calls"], row["tool"]))
    turns = total["turns"]
    deep_turns = total["deep_turns"]
    return {
        "tools": tools,
        "tool_calls_total": sum(row["calls"] for row in tools),
        "tool_errors_total": sum(row["errors"] for row in tools),
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
            {
                "reason": reason, "turns": total[f"route_{reason}_turns"], "alone": total[f"route_alone_{reason}_turns"],
                "no_tools": total[f"route_notool_{reason}_turns"], "alone_no_tools": total[f"route_alone_notool_{reason}_turns"],
            }
            for reason in sorted(ROUTE_REASONS, key=lambda r: -total[f"route_{r}_turns"])
            if total[f"route_{reason}_turns"]
        ],
        # The tool agent's step loop: how many ran, how many steps they used on average, how they spread, and how many ran into their limit (all of the
        # steps they were allowed), overall and for deep-thinking turns on their own. A high share means the budget is tight; a low one means it hardly matters.
        "loop": {
            "turns": total["loop_turns"],
            "avg_steps": _average(total["loop_steps"], total["loop_turns"]),
            "histogram": [{"bucket": bucket, "turns": total[bucket]} for bucket in STEP_BUCKETS],
            "cap_hits": total["cap_hit_turns"],
            "cap_hit_share": round(total["cap_hit_turns"] / total["loop_turns"], 4) if total["loop_turns"] else None,
            # How they ended: by the halfway step, later, or at the last step with a draft kept / with nothing written; and the halfway nudge, shown and followed by a draft.
            "exits": {"early": total["exit_early_turns"], "late": total["exit_late_turns"], "cap_draft": total["cap_draft_turns"], "cap_no_draft": total["cap_no_draft_turns"]},
            "checkpoints": {"turns": total["checkpoint_turns"], "answered": total["checkpoint_answered_turns"], "continued": total["checkpoint_continued_turns"], "acted": total["checkpoint_acted_turns"]},
            "nudged": total["nudge_turns"],
            "nudged_drafted": total["nudge_draft_turns"],
            "deep_turns": total["deep_loop_turns"],
            "deep_cap_hits": total["deep_cap_hit_turns"],
            "deep_cap_hit_share": round(total["deep_cap_hit_turns"] / total["deep_loop_turns"], 4) if total["deep_loop_turns"] else None,
        },
        "native_turns": total["native_turns"],
        "blocked_turns": total["blocked_turns"],
        "error_turns": total["error_turns"],
    }
