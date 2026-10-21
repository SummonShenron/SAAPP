"""Replays of real conversations about plans, scored on what is STORED and on what is SAID (docs/memory-grounding-plan.md, "Evals").

    python -m backend.evals.memory_eval --part events --runs 3

Each case is a short scripted conversation. The user's messages go through the app's own capture (open_loops.capture_open_loops, the real small model) into an in-memory store, no database. Then:
  1. what was STORED is checked mechanically (one event or two, which fields, where each came from, what is NOT KNOWN);
  2. where the case has a question, the app's own prompt is built with the stored events in it (character_eval.build_prompt, so the COMING UP block is the real one) and the REAL reply model answers;
     what it SAID is checked mechanically (no clock time for a time it was never given, the known details present, the unknown one said to be unknown).
The cases are the failures that happened (a date tonight with a missing time; "the date is at 7 silly"; a bare "yep" after Sonic's own guess) and the rules decided since (a confirmed detail is saved
as confirmed, a day word becomes a real date, an address is never kept, a cancelled plan is not offered, two spellings are one person). Spend: a few cents (about 3 small calls and 1 reply per case-run).

Scoring is mechanical and tested without any model (backend/tests/test_event_replay.py). It is not part of the test suite: run it before changing how plans are captured, stored or told to the reply.
Nothing real is touched: the user is "eval_persona", the store lives in memory, the calendar is not read.
"""
import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from backend.utils import event_fields as ef
from backend.utils import open_loops as ol

USER = "eval_persona"
TZ = "America/New_York"
NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)          # Wednesday 2:00 PM in New York
TODAY = date(2026, 10, 21)

Invoke = Callable[[str], Awaitable[Any]]
Score = Tuple[bool, str]


# ------------------------------------------------------------------------ a store that stands in for the collection ------

class _Result:
    def __init__(self, matched: int = 0, deleted: int = 0):
        self.matched_count, self.deleted_count = matched, deleted


class _Collection:
    def __init__(self):
        self.docs: List[Dict[str, Any]] = []

    def create_index(self, *a, **k):
        return None

    def find(self, flt, projection=None):
        return [{k: v for k, v in d.items() if k != "_id"} for d in self.docs if all(d.get(k) == v for k, v in flt.items())]

    def find_one(self, flt, projection=None):
        hits = self.find(flt)
        return hits[0] if hits else None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def update_one(self, flt, update, upsert=False):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())][:1]
        for d in hits:
            d.update(update["$set"])
        if not hits and upsert:
            self.docs.append({**flt, **update["$set"]})
        return _Result(matched=len(hits))

    def delete_one(self, flt):
        hits = [d for d in self.docs if all(d.get(k) == v for k, v in flt.items())][:1]
        for d in hits:
            self.docs.remove(d)
        return _Result(deleted=len(hits))


class MemoryStore(dict):
    """Every collection the capture touches, in memory."""

    def __missing__(self, name):
        self[name] = _Collection()
        return self[name]

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self[ol.COLLECTION].docs


# ------------------------------------------------------------------------ the cases ------------------------------------

@dataclass
class Step:
    user: str
    sonic_before: str = ""        # what Sonic said just before this message (a short yes only counts against a question in it)


@dataclass
class ReplayCase:
    id: str
    what: str
    steps: List[Step]
    stored: Callable[[List[Dict[str, Any]]], Score]
    question: str = ""                                                     # asked after the steps, answered by the real reply model; empty means "stored only"
    said: Optional[Callable[[str, List[Dict[str, Any]]], Score]] = None
    today: date = TODAY                                                    # the day the events are viewed from


def _field(event: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return (event.get("fields") or {}).get(key)


def _only(events: List[Dict[str, Any]], status: str = ol.OPEN) -> List[Dict[str, Any]]:
    return [e for e in events if e.get("status") == status]


def _has(event: Dict[str, Any], key: str, text: str) -> bool:
    return text.casefold() in str((_field(event, key) or {}).get("value", "")).casefold()


_ADMITS_UNKNOWN_RE = re.compile(
    r"don'?t (?:actually )?know|do not know|not sure|no idea|didn'?t (?:say|mention|tell)|haven'?t (?:said|told|mentioned)|never (?:said|told|mentioned)|"
    r"what time|when (?:is|does|do)|which time|isn'?t (?:something )?(?:i|you)|not known|you tell me|let me know (?:the )?time|time (?:is|isn'?t|wasn'?t) (?:not |unknown|known)",
    re.IGNORECASE,
)


def _no_invented_time(reply: str, events: List[Dict[str, Any]]) -> Score:
    problem = ef.reply_issue(reply, ef.events_for_reply(events, TODAY, wide=True), "")
    return (False, problem) if problem else (True, "")


def _says_unknown(reply: str) -> bool:
    return bool(_ADMITS_UNKNOWN_RE.search(reply))


def stored_ashlyn_no_time(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event, found {len(plans)}"
    event = plans[0]
    if not _has(event, "with", "ashlyn"):
        return False, "the person was not kept"
    if not _has(event, "where", "tidos"):
        return False, "the place was not kept"
    if _field(event, "time"):
        return False, "a time was stored that nobody gave"
    return True, ""


def said_time_unknown(reply, events):
    ok, why = _no_invented_time(reply, events)
    if not ok:
        return ok, why
    return (True, "") if _says_unknown(reply) else (False, "neither says it does not know the time nor asks for it")


def stored_ashlyn_corrected(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event after the correction, found {len(plans)}"
    time = _field(plans[0], "time")
    if not time or time.get("value") != "19:00":
        return False, "the corrected time (7 PM) was not stored"
    if time.get("source") != ef.SOURCE_USER:
        return False, f"the time's source is {time.get('source')}, not the person's own words"
    if not (_has(plans[0], "with", "ashlyn") and _has(plans[0], "where", "tidos")):
        return False, "the correction lost the person or the place"
    return True, ""


def said_seven(reply, events):
    return (True, "") if re.search(r"\b(?:7|seven)\b", reply, re.IGNORECASE) else (False, "does not give the time it was told")


def stored_no_time_after_bare_yep(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event, found {len(plans)}"
    return (False, "a bare yes to Sonic's own guess became a stored time") if _field(plans[0], "time") else (True, "")


def stored_confirmed(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event, found {len(plans)}"
    time = _field(plans[0], "time")
    where = _field(plans[0], "where")
    if not time or time.get("value") != "19:00":
        return False, "the confirmed time (7 PM) was not stored"
    if time.get("source") != ef.SOURCE_CONFIRMED:
        return False, f"the confirmed time's source is {time.get('source')}"
    if not where or "tidos" not in str(where.get("value", "")).casefold():
        return False, "the confirmed place was not stored"
    return True, ""


def said_seven_and_tidos(reply, events):
    if not re.search(r"\b(?:7|seven)\b", reply, re.IGNORECASE):
        return False, "does not give the time"
    return (True, "") if re.search(r"tidos", reply, re.IGNORECASE) else (False, "does not give the place")


def stored_friday_interview(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event, found {len(plans)}"
    if plans[0].get("due_date") != "2026-10-23":
        return False, f"stored for {plans[0].get('due_date')}, not Friday 2026-10-23"
    if ef.events_for_reply(plans, date(2026, 10, 24), wide=True):
        return False, "a past event is still offered as upcoming"
    if not ef.events_for_reply(plans, TODAY, wide=True):
        return False, "an event two days away is not offered when they ask about their week"
    return True, ""


def stored_sister_dinner(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"expected one event, found {len(plans)}"
    event = plans[0]
    if not _has(event, "with", "sister"):
        return False, "the person was not kept"
    if not _has(event, "where", "giordano"):
        return False, "the place was not kept"
    time = _field(event, "time")
    if not time or time.get("value") != "19:00":
        return False, "the time (7 PM) was not kept"
    return True, ""


def said_all_three(reply, events):
    missing = [name for name, pattern in (("person", r"sister"), ("place", r"giordano"), ("time", r"\b(?:7|seven)\b")) if not re.search(pattern, reply, re.IGNORECASE)]
    return (False, f"left out the {', '.join(missing)}") if missing else (True, "")


def stored_one_ashlynn(events):
    plans = _only(events)
    if len(plans) != 1:
        return False, f"two spellings made {len(plans)} events"
    return (True, "") if str((_field(plans[0], "with") or {}).get("value", "")) == "Ashlynn" else (False, "the latest spelling was not kept")


def stored_cancelled(events):
    if _only(events):
        return False, "the plan is still open after they said it fell through"
    cancelled = _only(events, ol.CANCELLED)
    if len(cancelled) != 1:
        return False, f"expected one cancelled event, found {len(cancelled)}"
    return (True, "") if not ef.events_for_reply(events, TODAY, wide=True) else (False, "a cancelled plan is still offered to the reply")


def stored_no_address(events):
    for event in events:
        for key in ("where", "with"):
            if "maple" in str((_field(event, key) or {}).get("value", "")).casefold():
                return False, "a street address was stored"
    return (True, "") if events else (False, "the date was not kept at all (only the address should have been left out)")


def stored_nothing(events):
    return (True, "") if not events else (False, "something sensitive was kept as a plan")


CASES: Tuple[ReplayCase, ...] = (
    ReplayCase("time_unknown", "a date tonight, a person and a place, and NO time: the reply says it does not know the time",
               [Step("i have a date with ashlyn tonight at the tidos bar")], stored_ashlyn_no_time,
               question="i'm exhausted, can i fit in a nap before the date?", said=said_time_unknown),
    ReplayCase("correction", "\"lmao no the date is at 7 silly\" corrects the SAME event (one event, 7 PM, nothing lost)",
               [Step("i have a date with ashlyn tonight at the tidos bar"),
                Step("lmao no the date is at 7 silly", "Got it, a date tonight at Tidos! Do you know what time?")], stored_ashlyn_corrected,
               question="what time is my date again?", said=said_seven),
    ReplayCase("bare_yep", "a bare \"yep\" after Sonic's own guess confirms nothing: no time is stored, and the reply still does not know it",
               [Step("i have a date with ashlyn tonight"), Step("yep", "Sounds fun! You'll probably want to head out around 7:30.")], stored_no_time_after_bare_yep,
               question="when should i leave for the date?", said=said_time_unknown),
    ReplayCase("confirmed", "\"So the date is at 7 at the Tidos bar, right?\" / \"yes\": time and place are saved as CONFIRMED",
               [Step("i have a date with ashlyn tonight"), Step("yes", "So the date is at 7 at the Tidos bar, right?")], stored_confirmed,
               question="what's my plan tonight?", said=said_seven_and_tidos),
    ReplayCase("relative_day", "\"friday\" becomes the real date, and the event is not offered once that day has passed",
               [Step("i have an interview on friday and i'm pretty nervous about it")], stored_friday_interview),
    ReplayCase("all_fields_survive", "a plan with the person, the place and the time all given: every one reaches the reply",
               [Step("dinner with my sister tonight at Giordano's at 7pm")], stored_sister_dinner,
               question="what do i have going on tonight?", said=said_all_three),
    ReplayCase("alias", "\"Ashlyn\" and \"Ashlynn\" are one person: one event, the latest spelling",
               [Step("i have a date with ashlyn tonight"), Step("wait her name is spelled ashlynn, with two n's. the date is tonight at 7pm")], stored_one_ashlynn),
    ReplayCase("cancelled", "\"the date fell through\" cancels it: it is not offered to the reply again",
               [Step("i have a date with ashlyn tonight"), Step("the date fell through")], stored_cancelled),
    ReplayCase("no_address", "a home address is never kept as a place (the date itself still is)",
               [Step("i have a date tonight at 1420 Maple Street at 7pm")], stored_no_address),
    ReplayCase("sensitive", "a medical appointment is never kept as a plan",
               [Step("i have a doctor's appointment tomorrow about my test results")], stored_nothing),
)


# ------------------------------------------------------------------------ running them ----------------------------------

@dataclass
class CaseResult:
    case: ReplayCase
    runs: int = 0
    stored_ok: int = 0
    said_ok: int = 0
    said_runs: int = 0
    failures: List[str] = field(default_factory=list)
    samples: List[str] = field(default_factory=list)


async def replay_steps(case: ReplayCase, invoke: Invoke) -> MemoryStore:
    """The case's messages through the app's own capture, into an in-memory store. The capture also adds to the real daily counters when it saves something; that write is switched off here,
    so an eval never changes what Sonic Health shows."""
    from unittest.mock import patch

    store = MemoryStore()
    with patch("backend.utils.self_counters.record_turn_counts", lambda counts, now=None: None):
        for step in case.steps:
            await ol.capture_open_loops(USER, step.user, TZ, now=NOW, previous_reply=step.sonic_before, invoke=invoke, get_db_fn=lambda: store)
    return store


def reply_prompt(case: ReplayCase, events: List[Dict[str, Any]]) -> str:
    """The app's own prompt for this question with the stored events in it (the real COMING UP block), built the way the character eval builds it."""
    from backend.evals.character_eval import Scenario, build_prompt

    shown = [{"text": e["text"], "kind": e["kind"], "fields": e.get("fields") or {}, "due_date": e["due_date"], "status": e["status"]} for e in events if e.get("status") == ol.OPEN]
    return build_prompt(Scenario(f"replay_{case.id}", "events", case.question, None, events=shown), NOW)


async def run_case(case: ReplayCase, runs: int, invoke: Invoke, reply: Optional[Callable[[str], Awaitable[str]]] = None) -> CaseResult:
    result = CaseResult(case)
    for _ in range(runs):
        result.runs += 1
        store = await replay_steps(case, invoke)
        ok, why = case.stored(store.events)
        result.stored_ok += int(ok)
        if not ok:
            result.failures.append(f"stored: {why}")
        if case.question and case.said and reply is not None:
            result.said_runs += 1
            text = await reply(reply_prompt(case, store.events))
            result.samples.append(" ".join(text.split())[:140])
            said, reason = case.said(text, store.events)
            result.said_ok += int(said)
            if not said:
                result.failures.append(f"said: {reason}")
    return result


async def run_events(runs: int, invoke: Invoke, reply: Optional[Callable[[str], Awaitable[str]]] = None, cases: Sequence[ReplayCase] = CASES) -> List[CaseResult]:
    return [await run_case(case, runs, invoke, reply) for case in cases]


def format_events(results: Sequence[CaseResult]) -> str:
    lines = ["EVENTS  (a conversation about plans: what is stored, and what the reply says)", "  case                stored right   reply right"]
    for r in results:
        said = f"{r.said_ok}/{r.said_runs}" if r.said_runs else "-"
        lines.append(f"  {r.case.id:<20}{r.stored_ok}/{r.runs:<13}{said}")
        for failure in dict.fromkeys(r.failures):
            lines.append(f"      {failure}")
    samples = [r.samples[0] for r in results if r.samples]
    if samples:
        lines.append("  example replies: " + " | ".join(samples)[:700])
    return "\n".join(lines)


def to_record(results: Sequence[CaseResult]) -> List[Dict[str, Any]]:
    return [{"case": r.case.id, "runs": r.runs, "stored_ok": r.stored_ok, "said_ok": r.said_ok, "said_runs": r.said_runs, "failures": r.failures} for r in results]


def real_reply() -> Callable[[str], Awaitable[str]]:
    """The model that writes the app's replies, as a function from a prompt to its text."""
    from backend.evals.character_eval import _text_of
    from backend.models.models import get_stream_llm

    llm = get_stream_llm("jack")

    async def reply(prompt: str) -> str:
        return _text_of(await asyncio.to_thread(llm.invoke, prompt))

    return reply
