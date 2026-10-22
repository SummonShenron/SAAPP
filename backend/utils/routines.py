"""Sonic's routines: the jobs the server really runs on a schedule, and what Sonic can truthfully say about them.

Sonic has no life between conversations, and the identity rules (backend/components/sonic_profile.py) say so. But the server does run a few jobs on its own
(a health check, the reading refresh, two clean-ups), and "what are you doing later?" or "what did you do today?" can be answered honestly from them. The
whole design is about keeping that answer true:

- ONE LIST. The registry below is the schedule: what each job is, how often it runs. The scheduler runs from it (routine_scheduler.py) and the answer is
  built from it, so what Sonic says is planned is what is planned.
- ONE RECORD PER RUN. Every run writes a document (when, whether it worked, a few counts): the jobs that have their own loops (the reading shelf, the two
  clean-ups) write one too. What Sonic says a job found comes from that record, never from the model.
- THE WORDS ARE SCHEDULED JOBS, NOT A LIFE. A job "runs", a check "found nothing unusual". Nothing is said about being awake, tired, busy or looking
  forward to anything. "Late last night" is allowed, because it is a fact about when the job ran, worked out here in the person's OWN timezone (a run at
  2 AM Pacific is "this morning" to someone in New York), and handed to the model as finished words so it does no timezone arithmetic of its own.
- NOTHING INVENTED. No jobs without a registry entry, no results without a run record, no times beyond the interval. A job that has not run yet is
  described as not having run yet.
- DETAIL FOR ADMINS ONLY. What a health check found (incident counts, graph gaps) is shown to admins; everyone else hears only that it found nothing
  unusual or that it flagged something for the admins.
"""
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.utils.db_utils import get_db
from backend.utils.time_utils import local_now, part_of_day

logger = logging.getLogger("SASS Logger")

RUNS_COLLECTION = "sonic_routine_runs"
CLAIMS_COLLECTION = "sonic_routine_claims"
RUN_RETENTION_DAYS = 45
RECENT_WINDOW_HOURS = 48          # a run older than this is not "recent" when someone asks what it did today
UPCOMING_WINDOW_HOURS = 36        # a job due further away than this is not "coming up"

STATUS_OK = "ok"
STATUS_ATTENTION = "attention"    # the health check found something worth a look
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUSES = (STATUS_OK, STATUS_ATTENTION, STATUS_FAILED, STATUS_SKIPPED)


@dataclass(frozen=True)
class Routine:
    id: str
    label: str
    does: str           # what it is, in plain words that are true of every run
    every_hours: float
    uses_model: bool = False


def _hours(env: str, default: float) -> float:
    try:
        value = float(os.getenv(env, ""))
        return value if value > 0 else default
    except ValueError:
        return default


def registry() -> List[Routine]:
    """Every routine, with the cadence the real loop uses. Function, not a constant, so the intervals follow their environment settings."""
    from backend.utils import reading_shelf
    return [
        Routine("health_check", "Health check",
                "looks over its own incidents, the latest deploy, how many chat turns failed and whether the knowledge graph is complete, and flags anything unstable",
                _hours("ROUTINE_HEALTH_CHECK_HOURS", 6.0)),
        Routine("reading_shelf", "Reading refresh",
                "reads one page about a topic it leans toward and adds a short summary of it to its reading shelf",
                reading_shelf.REFRESH_DAYS * 24, uses_model=True),
        Routine("checkpoint_cleanup", "Conversation tidy-up",
                "clears out old conversation checkpoints so the database stays small",
                _hours("CHECKPOINT_RETENTION_INTERVAL_SECONDS", 24 * 3600) / 3600),
        Routine("attachment_cleanup", "Attachment tidy-up",
                "removes photos and files attached to old conversations once their retention period is over",
                _hours("ATTACHMENT_RETENTION_INTERVAL_SECONDS", 24 * 3600) / 3600),
    ]


def by_id(routine_id: str) -> Optional[Routine]:
    return next((r for r in registry() if r.id == routine_id), None)


# ---------------------------------------------------------------------------------------------------------------
# Run records
# ---------------------------------------------------------------------------------------------------------------

_index_ready = False


def _ensure_indexes(db) -> None:
    global _index_ready
    if _index_ready:
        return
    try:
        db[RUNS_COLLECTION].create_index([("routine", 1), ("started_at", -1)])
        db[RUNS_COLLECTION].create_index("expires_at", expireAfterSeconds=0)
        db[CLAIMS_COLLECTION].create_index("routine", unique=True)
        _index_ready = True
    except Exception:
        logger.warning("[routines] could not create the indexes; will try again later.", exc_info=True)


def _clean_counts(counts: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Only whole-number counts under short names, so a run record cannot hold text from anywhere."""
    out: Dict[str, int] = {}
    for key, value in (counts or {}).items():
        if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", str(key)) and isinstance(value, (int, float, bool)):
            out[str(key)] = int(value)
    return out


def record_run(db, routine_id: str, status: str, counts: Optional[Dict[str, Any]] = None,
               started_at: Optional[datetime] = None, finished_at: Optional[datetime] = None) -> None:
    """Writes one run. Raises on a database problem: callers that must never fail use record_run_safely."""
    finished = finished_at or datetime.now(timezone.utc)
    started = started_at or finished
    _ensure_indexes(db)
    db[RUNS_COLLECTION].insert_one({
        "routine": routine_id,
        "status": status if status in STATUSES else STATUS_FAILED,
        "counts": _clean_counts(counts),
        "started_at": started,
        "finished_at": finished,
        "expires_at": started + timedelta(days=RUN_RETENTION_DAYS),
    })


def record_run_safely(routine_id: str, status: str, counts: Optional[Dict[str, Any]] = None, started_at: Optional[datetime] = None) -> None:
    """For the loops that already exist: noting a run must never break the work itself."""
    try:
        db = get_db()
        if db is not None:
            record_run(db, routine_id, status, counts, started_at)
    except Exception:
        logger.warning("[routines] could not record a run of %s.", routine_id, exc_info=True)


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def latest_runs(db, now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """The most recent run of each routine (within the retention period)."""
    out: Dict[str, Dict[str, Any]] = {}
    for routine in registry():
        row = next(iter(db[RUNS_COLLECTION].find({"routine": routine.id}).sort("started_at", -1).limit(1)), None)
        if row and _aware(row.get("started_at")):
            out[routine.id] = {**row, "started_at": _aware(row["started_at"]), "finished_at": _aware(row.get("finished_at"))}
    return out


def next_due_at(routine: Routine, last: Optional[Dict[str, Any]], now: datetime) -> Optional[datetime]:
    """When the routine will next run, or None when it has never run (nothing says when it will). One that is overdue is due within the next check."""
    if not last:
        return None
    due = last["started_at"] + timedelta(hours=routine.every_hours)
    return due if due > now else now + timedelta(minutes=10)


# ---------------------------------------------------------------------------------------------------------------
# Saying when, in the person's own time
# ---------------------------------------------------------------------------------------------------------------

def _clock(local: datetime) -> str:
    """"2 AM", "6:30 PM": to the nearest half hour, because "around 6:07 PM" is not how anyone says it."""
    total = local.hour * 60 + local.minute
    rounded = int(round(total / 30.0)) * 30
    hour, minute = divmod(rounded % (24 * 60), 60)
    suffix = "AM" if hour < 12 else "PM"
    return f"{hour % 12 or 12}{':%02d' % minute if minute else ''} {suffix}"


def when_phrase(moment: datetime, tz_name: Optional[str], now: Optional[datetime] = None) -> str:
    """"this morning around 6 AM", "late last night around 2 AM", "tomorrow morning around 7 AM", "on Friday evening around 6 PM". Worked out in the person's
    own timezone, so the words are right for them and the model has no arithmetic to do."""
    now = now or datetime.now(timezone.utc)
    local, local_now_ = local_now(tz_name, moment), local_now(tz_name, now)
    delta = (local.date() - local_now_.date()).days
    part = part_of_day(local.hour)
    clock = _clock(local)
    future = moment > now
    if delta == 0:
        if part == "late night":
            return f"in the small hours around {clock}"
        if part == "night":
            return f"tonight around {clock}" if future else f"earlier tonight around {clock}"
        return f"later this {part} around {clock}" if future else f"this {part} around {clock}"
    if delta == 1:
        if local.hour < 5 and local_now_.hour >= 17:
            return f"after midnight tonight, around {clock}"
        return f"tomorrow {'night' if part in ('night', 'late night') else part} around {clock}"
    if delta == -1:
        if part == "late night":
            return f"late last night around {clock}"
        if part == "night":
            return f"last night around {clock}"
        return f"yesterday {part} around {clock}"
    return f"{'on' if future else 'last'} {local.strftime('%A')} {'night' if part in ('night', 'late night') else part} around {clock}"


# ---------------------------------------------------------------------------------------------------------------
# What a run found, in a fixed sentence
# ---------------------------------------------------------------------------------------------------------------

def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def describe_result(routine_id: str, status: str, counts: Dict[str, int], admin: bool) -> str:
    """One plain sentence about a run, from its counts only (no model). Details of what the health check found are for admins."""
    if status == STATUS_FAILED:
        return "did not finish"
    if status == STATUS_SKIPPED:
        return "was skipped"
    if routine_id == "health_check":
        if status == STATUS_OK:
            return "found nothing unusual"
        if not admin:
            return "flagged something for the admins to look at"
        parts = []
        if counts.get("incidents_new"):
            parts.append(_plural(counts["incidents_new"], "new incident"))
        if counts.get("turn_failures"):
            parts.append(f"{_plural(counts['turn_failures'], 'failed chat turn')} out of {counts.get('turns', 0)}")
        if counts.get("graph_gaps"):
            parts.append(f"{_plural(counts['graph_gaps'], 'document')} with no knowledge-graph relationships")
        if counts.get("deploy_failed"):
            parts.append("a deploy that did not go through")
        if counts.get("budget_limited"):
            parts.append("the usage budget at its limit")
        return "flagged " + (", ".join(parts) if parts else "something worth a look")
    if routine_id == "reading_shelf":
        return "added one piece to the shelf" if counts.get("items_added") else "found nothing worth adding"
    if routine_id == "checkpoint_cleanup":
        n = counts.get("deleted", 0)
        return f"cleared {_plural(n, 'old checkpoint')}" if n else "had nothing to clear"
    if routine_id == "attachment_cleanup":
        n = counts.get("deleted", 0)
        return f"removed {_plural(n, 'old attachment')}" if n else "had nothing to remove"
    return "finished"


# ---------------------------------------------------------------------------------------------------------------
# Being asked
# ---------------------------------------------------------------------------------------------------------------

_WHEN = r"(?:later|today|tonight|tomorrow|this (?:morning|afternoon|evening|weekend)|(?:over)?night|next|soon|this week)"
_ASKS_RE = re.compile(
    r"\bwhat(?:'s| is| are| do| have| did)?\s+(?:you|u)\s+(?:have\s+|got\s+)?(?:planned|going on|doing|up to|scheduled|on)\b[^.?!]{0,40}" + _WHEN +
    r"|\bwhat(?:'s| is)\s+(?:on\s+)?your\s+(?:schedule|agenda|plate|plans?)\b"
    r"|\bwhat(?:'s| is)\s+planned\s+for\s+you\b"
    r"|\bwhat(?:'ve| have)?\s+(?:you|u)\s+been\s+(?:up to|doing|working on)\b|\bwhat(?:'re| are)\s+(?:you|u)\s+up to\b|\bwhat(?:cha|'ve ya| ya)\s+(?:been\s+)?(?:up to|doing)\b"
    r"|\bwhat do you have (?:planned|on|scheduled|coming up)\b"
    r"|\bany plans\b[^.?!]{0,25}\b(?:later|today|tonight|tomorrow)\b"
    r"|\bwhat did you (?:do|get up to)\b[^.?!]{0,30}\b(?:today|last night|overnight|this morning|yesterday|earlier)\b"
    r"|\bwhat have you been (?:up to|doing|working on)\b[^.?!]{0,30}\b(?:today|lately|this morning|overnight)\b"
    r"|\bdo you (?:do|run|have)\b[^.?!]{0,40}\b(?:background|scheduled|routine|on your own|when i'?m (?:away|gone|not here))\b"
    r"|\bwhat do you do (?:when|while) (?:i'?m|i am|no ?one'?s|nobody'?s) (?:away|gone|not here|around|there)\b"
    r"|\b(?:your|any) (?:routines?|scheduled (?:jobs?|tasks?)|background (?:jobs?|tasks?|processes))\b",
    re.IGNORECASE,
)


def asks_about_routines(message: str) -> bool:
    """Whether the person is asking what Sonic has planned or what it did on its own."""
    return bool(_ASKS_RE.search(message or ""))


def _view(routine: Routine, last: Optional[Dict[str, Any]], now: datetime, tz_name: Optional[str], admin: bool) -> Dict[str, Any]:
    due = next_due_at(routine, last, now)
    return {
        "id": routine.id,
        "label": routine.label,
        "does": routine.does,
        "next": when_phrase(due, tz_name, now) if due else None,
        "last_when": when_phrase(last["started_at"], tz_name, now) if last else None,
        "last_recent": bool(last) and now - last["started_at"] <= timedelta(hours=RECENT_WINDOW_HOURS),
        "last_result": describe_result(routine.id, last.get("status", STATUS_OK), last.get("counts") or {}, admin) if last else None,
        "next_within": bool(due) and due - now <= timedelta(hours=UPCOMING_WINDOW_HOURS),
        "day_delta": (local_now(tz_name, due).date() - local_now(tz_name, now).date()).days if due else None,
        # a run in the small hours after midnight, for someone asking in the evening, is part of "tonight"
        "tonight": bool(due) and (local_now(tz_name, due).date() - local_now(tz_name, now).date()).days == 1
                   and local_now(tz_name, due).hour < 5 and local_now(tz_name, now).hour >= 17,
    }


def build_routines_context(db, tz_name: Optional[str], *, is_admin: bool, now: Optional[datetime] = None) -> str:
    """The prompt section for a "what do you have planned / what did you do" question: the real schedule and the real last results, with the rules for
    saying them. Always returns something when asked, so the model has the truth to hand and nothing to invent."""
    now = now or datetime.now(timezone.utc)
    runs = latest_runs(db, now)
    views = [_view(r, runs.get(r.id), now, tz_name, is_admin) for r in registry()]
    upcoming = [v for v in views if v["next"] and v["next_within"]]
    recent = [v for v in views if v["last_recent"]]
    today = [v for v in upcoming if v["day_delta"] == 0 or v["tonight"]]
    tomorrow = [v for v in upcoming if v["day_delta"] == 1 and not v["tonight"]]
    later = [v for v in upcoming if v["day_delta"] not in (0, 1)]
    lines = []
    # By day, with an explicit "nothing" for today, because a model summarising a plain list will move a job to an earlier day.
    lines.append("Later today and tonight (through the small hours after midnight):" + ("" if today else " nothing is scheduled."))
    lines += [f"- {v['label']}: {v['does']}. Next run {v['next']}." for v in today]
    if tomorrow:
        lines.append("Tomorrow:")
        lines += [f"- {v['label']}: {v['does']}. Next run {v['next']}." for v in tomorrow]
    if later:
        lines.append("Further out:")
        lines += [f"- {v['label']}: {v['does']}. Next run {v['next']}." for v in later]
    never = [v for v in views if not v["next"]]
    if never:
        lines.append("Not run yet since it was added (no time to promise):")
        lines += [f"- {v['label']}: {v['does']}." for v in never]
    if recent:
        lines.append("Done recently:")
        lines += [f"- {v['label']} ran {v['last_when']} and {v['last_result']}." for v in recent]
    if not upcoming and not never and not recent:
        lines = ["Nothing is scheduled or has run recently."]
    body = "\n".join(lines)
    return (
        "\nYOUR SCHEDULED ROUTINES (true; answer only from these):\n"
        f"{body}\n"
        "The server really does run these jobs on "
        "a schedule, and the times above are already in this person's own time. Just answer: do not add that you are not around, have no experience or spend no time between "
        "conversations unless they ask. Say what is scheduled and what a run found, plainly and in your usual dry "
        "voice (\"a health check runs around 6 PM\", \"this morning's check found nothing unusual\"). You may say what time of day a job ran or will run. "
        "Keep the day words exactly as written (today, tonight, tomorrow): never move a job to an earlier day. Never say you were awake, tired, up late, busy, or "
        "looking forward to anything, never describe the work as something you experienced, and never add a job, a time or a result that is not listed. If they ask about a time when nothing is listed, say nothing is scheduled then.\n"
    )


def routines_view(db, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """For the Sonic Health page (admins): each routine, its cadence, its last run and when it is next due. Counts only."""
    now = now or datetime.now(timezone.utc)
    runs = latest_runs(db, now)
    out = []
    for routine in registry():
        last = runs.get(routine.id)
        due = next_due_at(routine, last, now)
        out.append({
            "id": routine.id, "label": routine.label, "does": routine.does, "every_hours": round(routine.every_hours, 2), "uses_model": routine.uses_model,
            "last": ({"status": last.get("status"), "started_at": last["started_at"].isoformat(), "counts": last.get("counts") or {},
                      "result": describe_result(routine.id, last.get("status", STATUS_OK), last.get("counts") or {}, True)} if last else None),
            "next_due_at": due.isoformat() if due else None,
        })
    return out
