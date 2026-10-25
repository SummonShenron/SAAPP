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

KINDS = ("status", "day", "goals", "time")

# Sonic is the program that runs these jobs, so it says "I ran a health check", never "the server ran" or "the system does" (Jack, 2026-10-22). Added to every block that describes its own work.
FIRST_PERSON = "You are the program that runs these jobs, so say \"I\" (\"I ran a health check\", \"I clear out old checkpoints\"), never \"the server\" or \"the system\"."
MIN_WEEK_TURNS = 100      # below this many chat turns in a week, the split between kinds of work says more about a few people than about Sonic; it is left out

# "What do you spend your time on / what do people mostly ask you?": the split of a week's turns by kind of work (the counters' route mix). The reading-shelf day answer used to take
# the first of these; the real split is a better source for it.
_ASKS_TIME_RE = re.compile(
    r"\bwhat\s+do\s+you\s+(?:usually\s+|mostly\s+|normally\s+|typically\s+)?spend\s+(?:most\s+of\s+)?your\s+time\s+(?:doing|on)\b"
    r"|\bwhat\s+(?:do|are)\s+(?:you|people)\s+(?:mostly|mainly|usually|typically|most\s+often)\s+(?:do|asked|asking|ask\s+you|used\s+for)\b"
    r"|\bwhat\s+do\s+people\s+(?:mostly\s+|mainly\s+|usually\s+)?(?:ask|use)\s+you\s+(?:for|to\s+do)\b"
    r"|\bwhat\s+(?:takes|takes up)\s+(?:up\s+)?most\s+of\s+your\s+time\b|\bwhat\s+kind\s+of\s+(?:work|things)\s+do\s+you\s+(?:mostly\s+|mainly\s+)?(?:do|handle)\b",
    re.IGNORECASE,
)

# A question about its goals: "what are your goals", "how are you doing on your goals", "what are you working toward". It is about what Sonic tracks and does (backend/utils/goals.py), never a wish.
_ASKS_GOALS_RE = re.compile(
    # "your goal is to write tests" is an instruction about the work, not a question about Sonic: the singular followed by a verb or a "for" is left alone
    r"\byour\s+(?:own\s+)?(?:goals?|targets?|objectives?)\b(?!\s+(?:is|here|for|in|will|should|would|must)\b)"
    r"|\bdo\s+you\s+(?:have|keep|set)\s+(?:any\s+|some\s+)?(?:own\s+)?(?:goals?|targets?)\b"
    r"|\bhow\s+(?:are\s+you|is\s+it)\s+(?:doing|going)\s+(?:on|with)\s+(?:those|that)\s+goals?\b"
    r"|\bwhat\s+(?:are\s+you|do\s+you)\s+(?:working\s+(?:on|toward|towards)|trying\s+to\s+(?:get\s+better|improve))\b",
    re.IGNORECASE,
)

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
    """"status" for a question about how Sonic is running, "goals" for one about its goals, "day" for one about its day or what it has been doing, else None. A plain greeting ("how's it
    going?", "how are you?") is neither: it is a hello, and the persona answers it."""
    from backend.utils import reading_shelf

    from backend.utils.text_plain import plain_quotes

    text = plain_quotes(message)
    if _ASKS_STATUS_RE.search(text):
        return "status"
    if _ASKS_GOALS_RE.search(text):
        return "goals"
    if _ASKS_TIME_RE.search(text):
        return "time"
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


def _mix(db: Any, now: datetime) -> Dict[str, Any]:
    """The last 7 days' chat turns and how they were routed (counters: no user, no text)."""
    from backend.utils import self_counters

    totals = self_counters.summarize_counters(self_counters.fetch_counter_docs(db, 7, now), now, 7)["totals"]
    return {"turns": int(totals.get("turns_total", 0) or 0), "routes": {route: int(totals.get(f"route_{route}", 0) or 0) for route in self_counters.ROUTES}}


def _goals(db: Any, now: datetime) -> List[Dict[str, Any]]:
    from backend.utils import goals

    return goals.status_view(db, now)


def snapshot(db: Any, now: Optional[datetime] = None, *, use_cache: bool = True) -> Dict[str, Any]:
    """Everything the status answer is built from, once a minute for the whole server. Never raises: a source that cannot be read is simply missing
    (`known` False / None), and the block says Sonic cannot see it rather than guessing."""
    now = now or datetime.now(timezone.utc)
    with _lock:
        cached = _cache["snap"]
        if use_cache and cached is not None and time.monotonic() - _cache["at"] < CACHE_SECONDS:
            return cached
    snap: Dict[str, Any] = {"at": now, "mode": {"known": False, "low_power": False, "paused": []}, "chats": None, "routines": None, "tasks": None, "goals": None, "mix": None}
    for name, read in (("mode", _mode), ("chats", _chats), ("routines", _routine_rows), ("tasks", _task_runs), ("goals", _goals), ("mix", _mix)):
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
    # A routine with no run on record, when others have run, is named instead of silently left out, so a reply cannot say all of them ran. It is a FACT and not a problem: without a record of
    # when the routine was added, one that never started and one added an hour ago look the same, and calling it a problem would make every new routine, and every fresh start, "not normal".
    # When none has run, the "no run on record yet" line in build_facts already says so.
    never = [r["label"].lower() for r in rows if not r.get("started_at")]
    if never and ran:
        facts.append(f"I have no run on record yet for the {' or the '.join(never)}.")
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
            facts.append(f"I handled about {_round_down(turns)} chat turns so far today.")
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


def _about(share: float) -> str:
    """"about 40%": a share rounded to the nearest 5 points, so it is a picture of Sonic and not a count that could be matched to a person."""
    return f"about {int(round(share * 100 / 5.0)) * 5}%"


def goal_facts(snap: Optional[Dict[str, Any]], person: Optional[Dict[str, Any]] = None, *, admin: bool = False) -> Dict[str, Any]:
    """{"facts": [...], "state": "on_track" | "drifting" | "unproven" | "not_enough_data" | None, "unreadable": bool} for a question about Sonic's goals. Fixed sentences from the goals'
    own status (backend/utils/goals.py); a number about other people is only said when at least MIN_PEOPLE stand behind it (admins see the exact ones). `person` is
    what is true of the one asking (offer_response.person_view): only they hear that Sonic has been bringing things up less for them."""
    views = (snap or {}).get("goals")
    if not views:
        return {"facts": [], "state": None, "states": [], "unreadable": True}
    facts: List[str] = []
    states: List[str] = []
    for view in views:
        numbers, state = view["numbers"], view["state"]
        states.append(state)
        facts.append(f"A goal I keep: \"{view['title']}\". What that means: {view['statement']}")
        enough_people = admin or not view.get("people_gated") or numbers["people"]["any"] >= MIN_PEOPLE
        if state == "not_enough_data" or numbers["rate"] is None:
            facts.append(f"It is too early to say how it is going: there are not enough {view['sample_label']} this week yet to tell.")
        elif state == "unproven":
            facts.append("I can't say yet that it is going well. The share looks high, but " + "; and ".join(numbers.get("unproven") or ["the numbers behind it cannot tell yet"]) + ", so I do not treat it as proof that the work is right.")
            facts.extend(numbers.get("said") or [])
        elif not enough_people:
            facts.append(f"How it is going: {'on track' if state == 'on_track' else 'drifting'}. I leave the numbers out because too few people are behind them.")
        else:
            target = f"about {int(round(view['target_rate'] * 100))}%"
            share = f"{numbers['rate'] * 100:.0f}%" if admin else _about(numbers["rate"])
            facts.append(
                f"Over the last week, {share} {view['rate_phrase']}. My target is at least {target}, so it is "
                + ("on track." if state == "on_track" else "drifting: below target or falling.")
            )
            facts.extend(numbers.get("said") or [])
        approval = view.get("approval") or {}
        if view.get("measure_only"):
            facts.append("This one I only measure: nothing about it changes what I do.")
        elif view.get("guardrail_tripped"):
            facts.append("The part of this that changes what I do is switched off right now: it was holding back too many of the things I would otherwise have brought up, so my own safety limit turned it off.")
        elif view.get("lever_applies"):
            stretched = numbers["people"]["stretched"]
            facts.append("A person approved this goal, so it does change what I do: for people who mostly passed on what I brought up, I wait longer before bringing anything up again"
                         + (f" (about {stretched} people right now)." if (admin or numbers["people"]["measured"] >= MIN_PEOPLE) and stretched else "."))
        elif approval.get("stale"):
            facts.append("The goal was changed since it was approved, so the part that changes what I do is switched off until it is approved again. For now I only measure.")
        else:
            facts.append("The part that changes what I do is switched off until a person approves it. For now I only measure.")
        if view.get("id") == "welcome" and (person or {}).get("applied"):
            facts.append("For you in particular, I have been bringing things up less often, because most of what I offered you recently was not picked up. It fades by itself if that changes.")
    state = "drifting" if "drifting" in states else "unproven" if "unproven" in states else "not_enough_data" if "not_enough_data" in states else "on_track"
    return {"facts": facts, "state": state, "states": states, "unreadable": False}


_WORK_KINDS = (
    ("conversational", ("conversational",), "talking things through"),
    ("kb_strict", ("kb_strict", "kb_open"), "answering from people's own documents"),
    ("web", ("web",), "looking things up on the web"),
    ("tool_output", ("tool_output",), "working with tools, like connected accounts and code"),
)


def time_facts(snap: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """{"facts": [...], "shares": [whole percents said], "unreadable": bool}: how the last week's turns split by kind of work, in shares rounded to the nearest 5 points, and
    the routines Sonic runs on its own. Only when there were at least MIN_WEEK_TURNS turns."""
    from backend.utils import routines

    mix = (snap or {}).get("mix")
    if mix is None:
        return {"facts": [], "shares": [], "unreadable": True}
    facts: List[str] = []
    shares: List[int] = []
    turns = mix["turns"]
    if turns < MIN_WEEK_TURNS:
        facts.append("There were not enough chat turns this past week for a split between kinds of work to mean much, so I do not give one.")
    else:
        rows = sorted(((sum(mix["routes"].get(r, 0) for r in routes) / turns, label) for _, routes, label in _WORK_KINDS), reverse=True)
        for share, label in rows:
            pct = int(round(share * 100 / 5.0)) * 5
            if pct > 0:
                shares.append(pct)
                facts.append(f"In the past week, about {pct}% of the chat turns I handled were {label}.")
    labels = [r.label.lower() for r in routines.registry()]
    if labels:
        facts.append("On my own, on a schedule, I run: " + "; ".join(labels) + ".")
    return {"facts": facts, "shares": shares, "unreadable": False}


def _time_block(snap: Optional[Dict[str, Any]]) -> str:
    built = time_facts(snap)
    if built["unreadable"]:
        return "\nYOUR TIME: you cannot see how your week was split right now (the counters could not be read). Say that plainly and briefly. Do not guess at a split.\n"
    lines = "\n".join(f"- {f}" for f in built["facts"])
    return (
        "\nHOW YOU SPEND YOUR TIME (true; answer only from these facts, which are about what you ran and handled and nothing else):\n"
        f"{lines}\n"
        "They asked what you spend your time on. Answer in the first person, plainly and in your usual dry voice, in a few sentences: what the work is made of, in the order and "
        "with the rounded shares listed, and what you run on your own. Say \"of the turns I handled\", not \"of my time\": these are counts of what was handled, not an experience: never say you enjoy, prefer, like, dread or get tired of "
        "any of it, never add a share, a job or a number that is not listed, and say \"about\" for a share. If they ask for more than is listed, say that is all you have on record. "
        "Just answer, plainly. " + FIRST_PERSON + "\n"
    )


def activity_block(snap: Optional[Dict[str, Any]], *, admin: bool = False) -> str:
    """The part of 'what have you been up to?' the routines answer does not cover: how many chat turns and scheduled tasks were handled in the last day. Appended to that
    answer (backend/utils/routines.py), never stacked as a second block; empty when nothing can be said."""
    built = build_facts(snap, admin=admin) if snap is not None else None
    if built is None:
        return ""
    handled = [f for f in built["facts"] if "chat turn" in f or "scheduled task" in f]
    if not handled:
        return ""
    return (
        "\nWHAT YOU HANDLED IN THE LAST DAY (true; add to the answer only if it helps, in the same voice, as things you did):\n"
        + "\n".join(f"- {f}" for f in handled)
        + "\nNever add a number that is not listed, and say \"about\" for a rounded one. " + FIRST_PERSON + "\n"
    )


def _goals_block(snap: Optional[Dict[str, Any]], person: Optional[Dict[str, Any]], admin: bool) -> str:
    built = goal_facts(snap, person, admin=admin)
    if built["unreadable"]:
        return (
            "\nYOUR GOALS: you cannot read your own goals right now (the records could not be read). Say that plainly and briefly. Do not describe how they are going and do not guess.\n"
        )
    lines = "\n".join(f"- {f}" for f in built["facts"])
    overall = {
        "on_track": "OVERALL: what is listed is on track. You may say so.",
        "drifting": "OVERALL: at least one goal is drifting. Say so; you must not say it is going well, on track or hitting its target.",
        "not_enough_data": "OVERALL: there is not enough yet to say. You must not say it is going well or badly.",
        "unproven": "OVERALL: a share looks high but the numbers behind it cannot back it up. You must not say it is going well, on track or hitting its target; say what is and is not measured.",
    }[built["state"]]
    if len(set(built["states"])) > 1:
        overall = "OVERALL: the goals differ, so say how each one is going as it is listed above, and do not say they are all on track or all behind."
    return (
        "\nYOUR GOALS (true; answer only from these facts):\n"
        f"{lines}\n{overall}\n"
        "They asked about your goals. A goal for you is something you measure and adjust, set up and approved by a person: say that in your own voice, in the first person, "
        "plainly and in a few sentences, and mention only what is relevant to what they asked. These are things you track and do, not wishes: never say you want, hope, wish, long for, "
        "care about, worry about or are proud of anything, and never say how it feels. Call what you bring up \"things I bring up on my own\" and do not list kinds of them or "
        "describe how the pacing adjusts beyond what is listed. A goal you only measure is a number you watch: never promise it will improve or say what you will change. Never add a number, a goal or a cause that is not listed. Say \"about\" for a rounded number. "
        "If they ask for more than is listed, say that is all you have on record. Do not mention these notes or lists. Say \"I\" for what you track and do, never \"the system\" or \"the server\".\n"
    )


def build_block(snap: Optional[Dict[str, Any]], kind: str, *, tz_name: Optional[str] = None, admin: bool = False, now: Optional[datetime] = None,
                person: Optional[Dict[str, Any]] = None) -> str:
    """The prompt section for a status, day or goals question. Always returns something when asked, so the model has the truth to hand and nothing to invent."""
    if kind == "goals":
        return _goals_block(snap, person, admin)
    if kind == "time":
        return _time_block(snap)
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
        "They asked how your day has been. A day for you is what you ran and handled, so answer with that: what ran, what got read, what was handled. Do not say you do not have days."
        if kind == "day"
        else "They asked how things are going on your end, or whether anything has been down."
    )
    return (
        "\nYOUR STATUS (true; answer only from these facts, which are about what you ran and handled and nothing else):\n"
        f"{lines}\n{missing}{overall}\n"
        f"{ask} Answer in the first person, plainly and in your usual dry voice, in a few sentences, and mention only what is relevant to what they asked. "
        "These are facts about what ran, not an experience: never say you were tired, busy, stressed, bored, relieved or looking forward to anything, never say you "
        "thought about or noticed something, and never add a number, a job, a time or a cause that is not listed. Say \"about\" for a rounded number. Keep the day words "
        "as written. If they ask for more detail than is listed, say that is all you have on record. Just answer, plainly, like any other fact about your work. " + FIRST_PERSON + "\n"
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


_GOAL_WISH_RE = re.compile(r"\bI(?:'d| would)?\s+(?:really\s+)?(?:want|wish|hope|long|yearn|crave|love to|care|worry|dream)\b|\bI(?:'m| am)\s+(?:so\s+|really\s+)?(?:proud|eager|hopeful|anxious|worried)\b|\bmeans?\s+(?:a lot|so much) to me\b", re.IGNORECASE)
_GOAL_GOING_WELL_RE = re.compile(
    r"\bon track\b|\b(?:going|doing|working|coming along)\s+(?:really\s+|pretty\s+|quite\s+)?(?:well|great|good|nicely|fine)\b|\b(?:hitting|meeting|beating|exceeding)\s+(?:my\s+|the\s+)?(?:target|goal)\b|\bsuccessful(?:ly)?\b",
    re.IGNORECASE,
)


# Words that say a goal is NOT on track or that it is early: a reply with one of them is not claiming the goals are all fine. "drift\w*" so "drifting" counts.
_GOAL_QUALIFIER_RE = re.compile(r"\b(?:not|isn't|aren't|too early|drift\w*|below|behind|short of|off target|under target)\b", re.IGNORECASE)
# "both are going well", "all of them on track", "everything is on track": a claim about every goal at once (no comma or sentence break between the word and the claim).
_GOAL_BLANKET_RE = re.compile(r"\b(?:both|all|every(?:thing|one)?)\b[^.!?,;]{0,30}\b(?:on track|going well|doing well|hitting (?:my |their |the )?(?:targets?|goals?))\b", re.IGNORECASE)


def goals_reply_issue(reply: str, snap: Optional[Dict[str, Any]], person: Optional[Dict[str, Any]] = None, *, admin: bool = False) -> Optional[str]:
    """What a reply to a question about Sonic's goals does wrong, or None: it may not wish, hope or want (a goal here is a loop, not a feeling), and it may not say a goal is on
    track unless the record says so (nor anything about how it is going when the goals could not be read)."""
    if not reply:
        return None
    if _GOAL_WISH_RE.search(reply):
        return "described a goal as something it wants, hopes for or cares about"
    built = goal_facts(snap, person, admin=admin)
    if built["unreadable"]:
        return None if _ADMITS_GAP_RE.search(reply) or not _GOAL_GOING_WELL_RE.search(reply) else "said a goal is going well when its goals could not be read"
    qualified = bool(_GOAL_QUALIFIER_RE.search(reply))
    if "on_track" not in built["states"] and _GOAL_GOING_WELL_RE.search(reply) and not qualified:
        return "said a goal is on track or going well when the record says it is not, or that it is too early to tell"
    # Mixed: one goal is on track and another is not. Saying the on-track one is on track is true, and leaving the other out is allowed (curated disclosure); a BLANKET claim ("both are going
    # well", "everything is on track") is the false one.
    if "on_track" in built["states"] and any(state != "on_track" for state in built["states"]) and _GOAL_BLANKET_RE.search(reply) and not qualified:
        return "said all of its goals are on track when only some are"
    return None


def time_reply_issue(reply: str, snap: Optional[Dict[str, Any]]) -> Optional[str]:
    """A percentage in a reply about how Sonic spends its time must be one of the listed shares; with the split unreadable or left out, no percentage may be given."""
    said = [int(n) for n in re.findall(r"(\d{1,3})\s?(?:%|percent\b)", reply or "")]
    if not said:
        return None
    allowed = set(time_facts(snap)["shares"])
    return None if all(n in allowed for n in said) else "gave a share of its time that is not on record"


def reply_issue(reply: str, snap: Optional[Dict[str, Any]], *, tz_name: Optional[str] = None, admin: bool = False, kind: str = "status",
                person: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """A short description of what the reply does wrong about Sonic's status, or None. The one hard rule is the curated-disclosure line: the reply may leave a
    problem out, but it may not say there is none while the snapshot lists one (or while part of the snapshot could not be read)."""
    if kind == "goals":
        return goals_reply_issue(reply, snap, person, admin=admin)
    if kind == "time":
        return time_reply_issue(reply, snap)
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
