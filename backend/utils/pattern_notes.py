"""Pattern stories: notes for Sonic's record that code writes from its own daily counters (docs/experience-templates-draft.md).

WHY. The record Sonic can cite (backend/utils/experiences.py) is almost all single mistakes at a task. A day on which setbacks piled up, or on which things mostly went right, is a different kind of
story, and it can be read straight off the counters (`sonic_counters`, one document a day, counts only). So a note is written by CODE from a fixed sentence with the day's real numbers filled in: no model
is involved, so nothing is invented, and the note says only what the counters say, never a feeling.

THE TEMPLATES (built in this order, after measuring them on 37 real days):
- `rough_day`     at least ROUGH_MIN_SETBACKS setbacks in coding work and at least as many setbacks as edits that landed (fired on 4 of 37 days);
- `almost_there`  a coding turn ran out of steps while its last step was still making progress (counter `turn_end_ran_out_progress`; only a few days of history yet);
- `good_day`      at least GOOD_MIN_LANDED edits landed and setbacks were at most a third of them (3 of 37 days, the same three at every floor from 4 to 6).
NOT BUILT, on purpose: "a rough patch that turned" (one instance so far: held until it has fired about three times, and it must never promise that a person's patch will turn) and "waited and nothing came back"
(no data: the runner has never expired a job and nothing counts a wait that ran out).

SETBACKS are counted as edits declined + edits undone + the larger of (pushback, frustrated) + step-limit hits + stalled plans. Pushback and frustrated are the larger of the two, not the sum, because one
message is often both. LANDED is `code_edit_applied`. Only days that are over (before today, UTC) are read, so a day is judged once it is complete.

APPROVAL. A person approves each TEMPLATE once on Sonic Health ("Its record", Pattern stories), not each note. Nothing is written for a template that is not approved. A note then appears in the record as an
approved `record` note with signal `pattern`, citable like any other (it is offered and cited by the same rules, so nothing new reaches a reply). Revoking a template stops new notes; the notes it made stay
until deleted. A note a person deletes is not written again: the days a template has handled are kept on its own document.

LIMITS. At most WEEKLY_CAP pattern notes a week and one note a day (the first template that fits, in the order above), only the last RECENT_DAYS days in the daily pass; a person can write the earlier days
once with `backfill`. A note cannot be edited (its words belong to the template: change the template or delete the note).
"""
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.utils import experience_capture, identity_checks, self_counters

logger = logging.getLogger("SASS Logger")

TEMPLATES_COLLECTION = "sonic_pattern_templates"
SIGNAL = "pattern"
KIND = "record"
ROUGH_MIN_SETBACKS = 8
GOOD_MIN_LANDED = 5
WEEKLY_CAP = 7
RECENT_DAYS = 14
BACKFILL_LIMIT = 10
HANDLED_KEEP = 200
MAX_WORDS, MAX_CHARS = 45, 260


def setbacks(counts: Dict[str, Any]) -> int:
    """Setbacks in coding work on a day: edits declined + undone + the larger of (pushback, frustrated) + step-limit hits + stalled plans. Pure."""
    def get(key: str) -> int:
        try:
            return max(int(counts.get(key, 0) or 0), 0)
        except (TypeError, ValueError):
            return 0

    return get("code_edit_declined") + get("code_edit_undone") + max(get("code_pushback"), get("code_frustrated")) + get("code_cap_hit") + get("code_plan_stalled")


def landed(counts: Dict[str, Any]) -> int:
    try:
        return max(int(counts.get("code_edit_applied", 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _rough_day(counts: Dict[str, Any], day: str) -> Optional[str]:
    bad, good = setbacks(counts), landed(counts)
    if bad >= ROUGH_MIN_SETBACKS and bad >= good:
        return f"On {day}, it had {bad} setbacks in its coding work (pushback, declined or undone edits, stalled plans, step limits) and {_plural(good, 'edit', 'edits')} that landed."
    return None


def _almost_there(counts: Dict[str, Any], day: str) -> Optional[str]:
    try:
        n = int(counts.get("turn_end_ran_out_progress", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 1:
        turns = "a coding turn ran out of steps while its last step was still making progress" if n == 1 else f"{n} coding turns ran out of steps while their last steps were still making progress"
        return f"On {day}, {turns}, so the work stopped before it was done."
    return None


def _good_day(counts: Dict[str, Any], day: str) -> Optional[str]:
    bad, good = setbacks(counts), landed(counts)
    if good >= GOOD_MIN_LANDED and bad * 3 <= good:
        return f"On {day}, {good} of its edits landed, with {_plural(bad, 'setback', 'setbacks') if bad else 'no setbacks'}."
    return None


# In the order they are tried: a day gets the first that fits (a day cannot be both rough and good).
TEMPLATES: List[Dict[str, Any]] = [
    {"id": "rough_day", "name": "A rough day", "rule": f"at least {ROUGH_MIN_SETBACKS} setbacks in coding work and at least as many setbacks as edits that landed",
     "key": "A day when a lot of what I tried ran into something, and not much got through.", "build": _rough_day,
     "careful": "for someone annoyed or stuck, not grieving: a day of small setbacks sits badly next to a real loss"},
    {"id": "almost_there", "name": "Almost there", "rule": "a coding turn ran out of steps while its last step was still making progress",
     "key": "Got almost all the way there and ran out before the last step.", "build": _almost_there,
     "careful": "low risk; fits ordinary overwhelm and running out of time"},
    {"id": "good_day", "name": "A good day", "rule": f"at least {GOOD_MIN_LANDED} edits landed and setbacks were at most a third of them",
     "key": "A day where things mostly went right and each step held.", "build": _good_day,
     "careful": "for good news, not for a vent: a success story lands as a comparison when someone is struggling"},
]
TEMPLATE_IDS = [t["id"] for t in TEMPLATES]
_BY_ID = {t["id"]: t for t in TEMPLATES}
_FORBIDDEN_RE = re.compile(r"https?://|[\"“”`]|[\\/]|@")


def note_issue(text: str) -> Optional[str]:
    """What is wrong with a generated note, or None. The text is built by code from a fixed sentence, so this is a safety net against a template edited into something it must not say (a quote, a link, a
    feeling, a claim about itself the identity check refuses, or a sentence that is not one). Numbers are allowed here (they come from the counters); the captured notes' rule against them is not. Pure."""
    body = (text or "").strip()
    if not body.startswith("On ") or not body.endswith(".") or "\n" in body:
        return "not one dated sentence"
    if len(body.split()) > MAX_WORDS or len(body) > MAX_CHARS:
        return "too long"
    if _FORBIDDEN_RE.search(body):
        return "contains a quote, link or path"
    if experience_capture._FEELING_RE.search(body):
        return "talks about feeling or intent"
    return identity_checks.identity_reply_issue(body)


def evaluate(counts: Dict[str, Any], day: str, approved: Optional[List[str]] = None) -> Optional[Dict[str, str]]:
    """The note a day's counters earn, or None: {"template", "text", "key", "day"} for the first APPROVED template (in order) whose rule fits. `approved` None means every template (the preview). Pure."""
    for template in TEMPLATES:
        if approved is not None and template["id"] not in approved:
            continue
        text = template["build"](counts or {}, day)
        if text and not note_issue(text):
            return {"template": template["id"], "text": text, "key": template["key"], "day": day}
    return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _template_docs(db: Any) -> Dict[str, Dict[str, Any]]:
    return {doc["_id"]: doc for doc in db[TEMPLATES_COLLECTION].find({})}


def approved_ids(db: Any) -> List[str]:
    return [tid for tid, doc in _template_docs(db).items() if doc.get("approved") and tid in _BY_ID]


def approve(db: Any, template_id: str, by: Optional[str] = None, now: Optional[datetime] = None) -> bool:
    """A person approves a template: from now on it may write notes. False for an unknown template. Blocking."""
    if db is None or template_id not in _BY_ID:
        return False
    moment = (now or _now()).isoformat()
    db[TEMPLATES_COLLECTION].update_one({"_id": template_id}, {"$set": {"approved": True, "approved_by": by, "approved_at": moment}, "$setOnInsert": {"handled": []}}, upsert=True)
    return True


def revoke(db: Any, template_id: str) -> bool:
    """Stops a template writing new notes. The notes it made stay until deleted. Blocking."""
    if db is None or template_id not in _BY_ID:
        return False
    db[TEMPLATES_COLLECTION].update_one({"_id": template_id}, {"$set": {"approved": False}}, upsert=True)
    return True


def view(db: Any) -> List[Dict[str, Any]]:
    """The templates for the dashboard: name, rule, what a note says, whether approved, how many notes it made and the latest day. Blocking."""
    docs = _template_docs(db) if db is not None else {}
    made: Dict[str, List[str]] = {tid: [] for tid in TEMPLATE_IDS}
    if db is not None:
        for row in db[experience_capture.COLLECTION].find({"signal": SIGNAL}, {"template": 1, "on": 1}):
            if row.get("template") in made:
                made[row["template"]].append(str(row.get("on") or ""))
    out = []
    for template in TEMPLATES:
        doc = docs.get(template["id"], {})
        days = sorted(d for d in made[template["id"]] if d)
        example = template["build"]({"code_edit_applied": 9, "code_pushback": 12, "code_cap_hit": 3, "code_edit_declined": 2, "code_plan_stalled": 1, "turn_end_ran_out_progress": 1}, "2026-01-01") \
            if template["id"] != "good_day" else template["build"]({"code_edit_applied": 9, "code_pushback": 2}, "2026-01-01")
        out.append({"id": template["id"], "name": template["name"], "rule": template["rule"], "key": template["key"], "careful": template["careful"], "example": example or "",
                    "approved": bool(doc.get("approved")), "approved_at": doc.get("approved_at"), "notes": len(days), "latest": days[-1] if days else None})
    return out


def _count(key: str, amount: int = 1) -> None:
    try:
        self_counters.record_turn_counts({key: amount})
    except Exception:
        logger.warning("[pattern notes] could not count %r.", key, exc_info=True)


def _recent_pattern_notes(db: Any, now: datetime) -> int:
    since = (now - timedelta(days=7)).isoformat()
    return db[experience_capture.COLLECTION].count_documents({"signal": SIGNAL, "created_at": {"$gte": since}})


def _write(db: Any, match: Dict[str, str], now: datetime) -> str:
    moment = now.isoformat()
    note_id = f"pattern-{uuid.uuid4().hex[:12]}"
    db[experience_capture.COLLECTION].insert_one({
        "_id": note_id, "on": match["day"], "kind": KIND, "signal": SIGNAL, "area": "general", "what": match["text"], "key": match["key"], "template": match["template"], "status": experience_capture.APPROVED,
        "count": 1, "embedding": [], "created_at": moment, "updated_at": moment, "approved_at": moment, "approved_by": f"template:{match['template']}",
    })
    db[TEMPLATES_COLLECTION].update_one({"_id": match["template"]}, {"$push": {"handled": {"$each": [match["day"]], "$slice": -HANDLED_KEEP}}}, upsert=True)
    return note_id


def _days(db: Any, today: str) -> List[Dict[str, Any]]:
    """The counters of every finished day (before `today`), oldest first."""
    return sorted((d for d in db[self_counters.COUNTERS_COLLECTION].find({}) if str(d.get("day") or "") < today), key=lambda d: str(d["day"]))


def run_pass(db: Any, now: Optional[datetime] = None, *, backfill: bool = False) -> Dict[str, int]:
    """One pass: writes the notes the approved templates earn from finished days. The daily pass reads the last RECENT_DAYS days and stops at WEEKLY_CAP notes a week; `backfill` reads every earlier
    day, ignores the weekly cap and stops at BACKFILL_LIMIT. A day already handled by a template (even if its note was deleted) is never written again. Returns counts: written, capped. Blocking."""
    result = {"written": 0, "capped": 0}
    if db is None:
        return result
    now = now or _now()
    approved = approved_ids(db)
    if not approved:
        return result
    docs = _template_docs(db)
    handled = {tid: set(doc.get("handled") or []) for tid, doc in docs.items()}
    today = now.strftime("%Y-%m-%d")
    first = "" if backfill else (now - timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    for doc in _days(db, today):
        day = str(doc["day"])
        if day < first:
            continue
        if any(day in handled.get(tid, set()) for tid in TEMPLATE_IDS):
            continue
        match = evaluate(doc.get("counts") or {}, day, approved)
        if not match:
            continue
        if backfill and result["written"] >= BACKFILL_LIMIT:
            result["capped"] += 1
            continue
        if not backfill and _recent_pattern_notes(db, now) >= WEEKLY_CAP:
            result["capped"] += 1
            continue
        _write(db, match, now)
        handled.setdefault(match["template"], set()).add(day)
        result["written"] += 1
    if result["written"]:
        _count("pattern_note_written", result["written"])
        try:
            from backend.utils import experiences

            experiences.forget_cached()
        except Exception:
            logger.warning("[pattern notes] could not refresh the record's cache.", exc_info=True)
    if result["capped"]:
        _count("pattern_note_capped", result["capped"])
    return result


CHECK_EVERY_SECONDS = 6 * 3600
FIRST_DELAY_SECONDS = 600


async def run_schedule(get_db: Callable[[], Any], *, check_every: float = CHECK_EVERY_SECONDS, first_delay: float = FIRST_DELAY_SECONDS) -> None:
    """Launched once from app.py's lifespan. Every few hours, writes whatever the approved templates earn from finished days (a pass is cheap: a few small reads, and nothing at all when no template is
    approved)."""
    import asyncio

    await asyncio.sleep(first_delay)
    while True:
        try:
            db = get_db()
            if db is not None:
                await asyncio.to_thread(run_pass, db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("[pattern notes] a pass failed.", exc_info=True)
        await asyncio.sleep(check_every)
