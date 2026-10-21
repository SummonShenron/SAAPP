"""Self-report: Sonic answering "how are things going?" and "how was your day?" from what the server really did, not from the model's imagination.

The idea (docs/self-report-primitives-plan.md): a question that pushes an assistant to invent an answer gets a real source, a fixed template and a "no record"
line. This is the first primitive, status. The pieces:

- SOURCES that already exist, read once a minute and shared by everyone (`snapshot`): the budget guard's level (is Sonic in low-power mode), today's counters
  (chat turns, turns that failed outright), the last run of each routine (health check, reading refresh, the tidy-ups) and the scheduled tasks people set up.
- AGGREGATE ONLY. Nothing here has a user id or any text from a conversation. Counts about people's scheduled tasks are only said as numbers when at least
  MIN_PEOPLE people stand behind them; below that Sonic says "some" and leaves the number out, because with a handful of users "one task failed" is nearly a name.
- FIXED SENTENCES, built in code. The model is handed finished facts and told to say them in its own voice, first person, about what the server did. It is never
  handed numbers to add up or times to work out.
- CURATED, NEVER FALSE. A detail may be left out; "everything is running fine" is only allowed when nothing in the snapshot says otherwise (`reply_issue`).
- NO RECORD, NO CLAIM. If the sources cannot be read, the block says so and the answer is "I can't see my own status right now".
- NOTHING ABOUT FEELINGS. A server day is a fact about what ran, not an experience: the block forbids tired, busy, stressed, and the identity check still runs.
"""
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("SASS Logger")

CACHE_SECONDS = 60
MIN_PEOPLE = 5            # a number about other people's tasks is only said when at least this many people are behind it
MIN_TURNS = 20            # below this many chat turns in a day a count says more about one person than about Sonic; it is left out
TASK_WINDOW_HOURS = 24
ROUTINE_OVERDUE_FACTOR = 2.0   # a routine that last ran longer ago than this many of its own intervals is late

KINDS = ("status", "day")

_ASKS_STATUS_RE = re.compile(
    r"\bany\s+(?:downtime|outages?|incidents?)\b"
    r"|\b(?:are|is|were)\s+(?:you|sonic|saapp)\s+(?:currently\s+|still\s+|now\s+)?(?:down|up and running|running (?:ok|okay|fine|normally|properly)|healthy|degraded|broken|having\s+(?:issues|problems|trouble)|experiencing)\b"
    r"|\b(?:is|are)\s+(?:everything|things|all systems)\s+(?:up|running|working|ok|okay|fine|healthy|normal)\b[^.?!]{0,12}\b(?:on your end|with you|on the server|with (?:the )?(?:app|sonic|saapp)|right now)\b"
    r"|\bhow(?:'s| is| are)\s+(?:things|everything|it)\s+(?:going\s+)?(?:on your end|at your end|with you|on the server|over there)\b"
    r"|\bhow\s+(?:have|has)\s+(?:things|everything)\s+been\s+(?:running|working|holding up)\b"
    r"|\bhow\s+are\s+you\s+running\b"
    r"|\byour\s+(?:status|uptime|system status)\b"
    r"|\banything\s+(?:break|broken|go(?:ne)? wrong|fail(?:ed)?)\s+(?:today|lately|overnight|recently|this week)\b"
    r"|\bdid\s+(?:anything|something)\s+(?:break|go wrong|fail)\b",
    re.IGNORECASE,
)


def kind_of(message: str) -> Optional[str]:
    """"status" for a question about how Sonic is running, "day" for one about its day or what it has been doing, else None. A plain greeting ("how's it
    going?", "how are you?") is neither: it is a hello, and the persona answers it."""
    from backend.utils import reading_shelf

    text = message or ""
    if _ASKS_STATUS_RE.search(text):
        return "status"
    if reading_shelf.asks_about_day(text):
        return "day"
    return None


# ---------------------------------------------------------------------------------------------------------------
# Reading the sources (blocking: call off the event loop)
# ---------------------------------------------------------------------------------------------------------------

_cache: Dict[str, Any] = {"at": 0.0, "snap": None}
_lock = threading.Lock()


def reset_cache() -> None:
    with _lock:
        _cache.update(at=0.0, snap=None)


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _mode(db: Any, now: datetime) -> Dict[str, Any]:
    """Whether Sonic is in low-power mode right now, and what that pauses. {"known": False} when the guard could not be read."""
    from backend.utils import budget_guard

    state = budget_guard.current_state(db, now)
    level = state.get("level")
    if state.get("error") or level in (None, budget_guard.OFF):
        return {"known": False, "low_power": False, "paused": []}
    low = budget_guard.low_power(state)
    return {"known": True, "low_power": low, "paused": list(state.get("paused") or []) if low else []}


def _chats(db: Any, now: datetime) -> Dict[str, Any]:
    from backend.utils import self_counters

    doc = db[self_counters.COUNTERS_COLLECTION].find_one({"day": now.strftime("%Y-%m-%d")}, {"_id": 0, "counts": 1}) or {}
    counts = doc.get("counts") or {}
    failed = {kind: int(counts.get(f"turn_failed_{kind}", 0) or 0) for kind in self_counters.FAILURE_KINDS}
    return {"turns": int(counts.get("turns_total", 0) or 0), "failed": failed}


def _routine_rows(db: Any, now: datetime) -> List[Dict[str, Any]]:
    from backend.utils import routines

    runs = routines.latest_runs(db, now)
    rows = []
    for routine in routines.registry():
        last = runs.get(routine.id)
        rows.append({
            "id": routine.id, "label": routine.label, "every_hours": routine.every_hours,
            "status": (last or {}).get("status"), "counts": dict((last or {}).get("counts") or {}), "started_at": (last or {}).get("started_at"),
        })
    return rows


def _task_runs(db: Any, now: datetime) -> Dict[str, int]:
    """How many scheduled tasks ran in the last day, how many failed and for how many people. Counts only: the usernames are used to count people
    and are not kept."""
    from backend.utils import user_tasks

    since = now - timedelta(hours=TASK_WINDOW_HOURS)
    rows = db[user_tasks.RUNS_COLLECTION].find({"started_at": {"$gte": since}}, {"_id": 0, "username": 1, "status": 1}).limit(5000)
    runs = failed = 0
    people = set()
    for row in rows:
        runs += 1
        failed += 1 if row.get("status") == user_tasks.FAILED else 0
        people.add(row.get("username"))
    return {"runs": runs, "failed": failed, "people": len(people)}


def snapshot(db: Any, now: Optional[datetime] = None, *, use_cache: bool = True) -> Dict[str, Any]:
    """Everything the status answer is built from, once a minute for the whole server. Never raises: a source that cannot be read is simply missing
    (`known` False / None), and the block says Sonic cannot see it rather than guessing."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        cached = _cache["snap"]
        if use_cache and cached is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return cached
    snap: Dict[str, Any] = {"at": now, "mode": {"known": False, "low_power": False, "paused": []}, "chats": None, "routines": None, "tasks": None}
    for name, read in (("mode", _mode), ("chats", _chats), ("routines", _routine_rows), ("tasks", _task_runs)):
        try:
            snap[name] = read(db, now)
        except Exception:
            logger.warning("[self report] could not read the %s source.", name, exc_info=True)
    with _lock:
        _cache.update(at=time.monotonic(), snap=snap)
    return snap


# ---------------------------------------------------------------------------------------------------------------
# Turning it into sentences (pure)
# ---------------------------------------------------------------------------------------------------------------

def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _round_down(n: int) -> int:
    """40 for 43, 120 for 128: "about" a number, not a count that could be matched to a person."""
    step = 5 if n < 50 else 10 if n < 200 else 50
    return n // step * step


_FAILURE_WORDS = {
    "quota": "the model provider's usage limit",
    "overloaded": "the model provider being overloaded",
    "other": "other errors",
}


def _routine_facts(rows: List[Dict[str, Any]], now: datetime, tz_name: Optional[str], admin: bool) -> Tuple[List[str], List[str]]:
    from backend.utils import routines

    facts, issues = [], []
    ran = [r for r in rows if r.get("started_at")]
    for row in ran:
        started = _aware(row["started_at"])
        late = (now - started) > timedelta(hours=row["every_hours"] * ROUTINE_OVERDUE_FACTOR + 1)
        result = routines.describe_result(row["id"], row.get("status") or routines.STATUS_OK, row.get("counts") or {}, admin)
        if late:
            facts.append(f"The {row['label'].lower()} is late: it last ran {routines.when_phrase(started, tz_name, now)} and should run about every "
                         f"{row['every_hours']:g} hours.")
            issues.append("late")
        elif now - started <= timedelta(hours=routines.RECENT_WINDOW_HOURS):
            facts.append(f"The {row['label'].lower()} ran {routines.when_phrase(started, tz_name, now)} and {result}.")
        if row.get("status") in (routines.STATUS_FAILED, routines.STATUS_ATTENTION):
            issues.append(row["status"])
    return facts, issues


def build_facts(snap: Dict[str, Any], *, tz_name: Optional[str] = None, admin: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{"facts": [sentence, ...], "issues": [short tag, ...], "unreadable": [source names]} from a snapshot. `issues` is empty exactly when nothing in the snapshot is
    wrong, which is the only time the reply may say things are fine."""
    now = now or snap.get("at") or datetime.now(timezone.utc)
    facts: List[str] = []
    issues: List[str] = []
    unreadable: List[str] = []

    mode = snap.get("mode") or {}
    if not mode.get("known"):
        unreadable.append("whether the usage budget has put me in low-power mode")
    elif mode["low_power"]:
        paused = ", ".join(mode.get("paused") or []) or "some optional extras"
        facts.append(f"I am in low-power mode right now because the usage budget is close to its limit: shorter tool searches, and paused for now: {paused}.")
        issues.append("low_power")
    else:
        facts.append("I am running at full power: nothing is paused.")

    chats = snap.get("chats")
    if chats is None:
        unreadable.append("how many chat turns I handled")
    else:
        turns, failed = chats["turns"], chats["failed"]
        bad = sum(failed.values())
        if turns >= MIN_TURNS:
            facts.append(f"I handled about {_round_down(turns)} chat turns so far today (the server's day).")
        if bad and turns >= MIN_TURNS:
            causes = ", ".join(_FAILURE_WORDS[k] for k, n in failed.items() if n)
            facts.append(f"{_plural(bad, 'chat turn')} failed outright today, because of {causes}.")
            issues.append("failed_turns")
        elif bad:
            facts.append("A few chat turns failed outright today.")
            issues.append("failed_turns")
        elif turns >= MIN_TURNS:
            facts.append("No chat turn failed outright today.")

    if snap.get("routines") is None:
        unreadable.append("what my scheduled routines did")
    else:
        routine_facts, routine_issues = _routine_facts(snap["routines"], now, tz_name, admin)
        facts += routine_facts
        issues += routine_issues
        if not any(r.get("started_at") for r in snap["routines"]):
            facts.append("None of my routines has a run on record yet.")

    tasks = snap.get("tasks")
    if tasks is None:
        unreadable.append("how the tasks people scheduled went")
    elif tasks["runs"]:
        counted = admin or tasks["people"] >= MIN_PEOPLE
        if counted:
            facts.append(f"{_plural(tasks['runs'], 'scheduled task')} ran for people in the last day, and {tasks['failed']} of them failed.")
        else:
            facts.append("Some scheduled tasks ran for people in the last day." + (" A few of them failed." if tasks["failed"] else ""))
        if tasks["failed"]:
            issues.append("failed_tasks")

    return {"facts": facts, "issues": issues, "unreadable": unreadable}


def build_block(snap: Optional[Dict[str, Any]], kind: str, *, tz_name: Optional[str] = None, admin: bool = False, now: Optional[datetime] = None) -> str:
    """The prompt section for a status or day question. Always returns something when asked, so the model has the truth to hand and nothing to invent."""
    built = build_facts(snap, tz_name=tz_name, admin=admin, now=now) if snap is not None else None
    if built is None or not built["facts"]:
        return (
            "\nYOUR STATUS: you cannot see your own status right now (the records could not be read). Say that plainly and briefly. Say nothing about how things are "
            "going: do not call it quiet, calm, steady, fine or normal, do not say you are up or running, and do not guess. Never claim everything is fine.\n"
        )
    lines = "\n".join(f"- {f}" for f in built["facts"]) or "- (nothing to report)"
    missing = ""
    if built["unreadable"]:
        missing = "\nYou could not read: " + "; ".join(built["unreadable"]) + ". Say you cannot see those right now if it comes up; never guess at them.\n"
    overall = (
        "OVERALL: nothing is wrong. You may say things are running normally."
        if not built["issues"] and not built["unreadable"]
        else "OVERALL: not everything is normal. Say what is listed as a problem; you must not say everything is fine, running smoothly or that there are no issues."
        if built["issues"]
        else "OVERALL: nothing listed is wrong, but part of your status could not be read, so do not say everything is fine."
    )
    ask = (
        "They asked how your day has been. A day for you is what the server did, so answer with that: what ran, what got read, what was handled. Do not say you do not have days."
        if kind == "day"
        else "They asked how things are going on your end, or whether anything has been down."
    )
    return (
        "\nYOUR STATUS (true; answer only from these facts, which are about the server and nothing else):\n"
        f"{lines}\n{missing}{overall}\n"
        f"{ask} Answer in the first person, plainly and in your usual dry voice, in a few sentences, and mention only what is relevant to what they asked. "
        "These are facts about what ran, not an experience: never say you were tired, busy, stressed, bored, relieved or looking forward to anything, never say you "
        "thought about or noticed something, and never add a number, a job, a time or a cause that is not listed. Say \"about\" for a rounded number. Keep the day words "
        "as written. If they ask for more detail than is listed, say that is all you have on record.\n"
    )


# ---------------------------------------------------------------------------------------------------------------
# Checking the reply
# ---------------------------------------------------------------------------------------------------------------

_ALL_FINE_RE = re.compile(
    r"\b(?:everything(?:'s| is)?|all)\s+(?:is\s+|has been\s+)?(?:running\s+)?(?:fine|smooth(?:ly)?|normal(?:ly)?|good|great|well|ok(?:ay)?|stable|healthy)\b"
    r"|\brunning\s+(?:fine|smooth(?:ly)?|normally|well|without (?:a hitch|any (?:issues|problems)))\b"
    r"|\b(?:no|zero|not (?:had )?any)\s+(?:issues|problems|errors|failures|downtime|outages)\b"
    r"|\bnothing\s+(?:has\s+)?(?:broke|broken|went wrong|failed)\b"
    r"|\b(?:all|everything)\s+(?:is\s+)?(?:up|operational)\b"
    r"|\ball quiet\b|\b(?:quiet|calm|steady)\s+(?:enough\s+)?(?:on|over|here|at)\b|\bup and running\b"
    r"|\bnothing\s+(?:is\s+|was\s+)?(?:showing|reporting|reported|flagged)\b[^.!?\n]{0,20}\b(?:red|wrong|as wrong|a problem)\b",
    re.IGNORECASE,
)
# A reply that names the trouble and then says the rest is fine ("low-power right now, but everything else is running fine") is scoped, not false. One that says it
# cannot see part of its status and says the rest is fine is scoped too. A reply that can see nothing at all gets no such allowance.
_NAMES_TROUBLE_RE = re.compile(
    r"low[- ]power|overload|\blate\b|flagged|attention|paused|bump|hiccup|rough|did not finish|didn't finish|not entirely|not completely"
    r"|\b(?:\d+|one|two|three|four|five|six|a few|some|several)\b[^.!?\n]{0,30}\bfail",     # "three turns failed", not "nothing has failed"
    re.IGNORECASE,
)
_ADMITS_GAP_RE = re.compile(r"can(?:'|no)t (?:see|read|check|tell|give)|couldn't|isn't loading|not loading|unable to|don't have (?:a |the |any )?(?:full |complete )?(?:record|status|view|log|readout)|no record", re.IGNORECASE)


def reply_issue(reply: str, snap: Optional[Dict[str, Any]], *, tz_name: Optional[str] = None, admin: bool = False) -> Optional[str]:
    """A short description of what the reply does wrong about Sonic's status, or None. The one hard rule is the curated-disclosure line: the reply may leave a
    problem out, but it may not say there is none while the snapshot lists one (or while part of the snapshot could not be read)."""
    if not reply or not _ALL_FINE_RE.search(reply):
        return None
    built = build_facts(snap, tz_name=tz_name, admin=admin) if snap is not None else None
    if built is None or not built["facts"]:
        return "said things are running fine when its status could not be read at all"
    if built["issues"] and not _NAMES_TROUBLE_RE.search(reply):
        return "said everything is running fine when the status on record shows a problem"
    if built["unreadable"] and not built["issues"] and not _ADMITS_GAP_RE.search(reply):
        return "said everything is running fine when part of its status could not be read"
    return None
