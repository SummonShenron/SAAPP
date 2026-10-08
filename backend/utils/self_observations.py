"""Sonic's measured self-knowledge: a short list of observations about its own track record, each backed by numbers.

This is the "insights about itself" store. It is deliberately not a memory of anything that happened, and not part of
its personality (that is backend/components/sonic_profile.py, edited only by a person). It is calibration: facts
about how Sonic has actually been behaving, read from the aggregate counters in backend/utils/self_counters.py.

How it works
- `reflect` turns the counters into PROPOSED observations using fixed templates (no model call: free, cannot drift,
  and every number in the text is checked against the evidence behind it). Nothing here can contain a person or a
  conversation, because the counters cannot.
- A person APPROVES each one. Only approved text is ever shown to the model, so a changed proposal waits for
  approval while the approved wording stays as it was. A small shift in a rate just refreshes the evidence.
- An observation EXPIRES (90 days) unless a later reflection confirms it again.
- At reply time only the few observations that apply to this turn are used, as quiet calibration (see
  `observations_for_turn`): never every turn, never recited unless the user asks about Sonic itself.

    python -m backend.utils.self_observations reflect --days 30
    python -m backend.utils.self_observations list
    python -m backend.utils.self_observations approve emotional_fit
    python -m backend.utils.self_observations retire emotional_fit
"""
import argparse
import datetime
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timezone
from typing import Any, Dict, List, Optional

from backend.utils.identity_checks import identity_reply_issue
from backend.utils.self_counters import FAILURE_KINDS, ROUTES, REWARD_TAGS, fetch_counter_docs, summarize_counters

logger = logging.getLogger("SASS Logger")

COLLECTION = "sonic_self_observations"
EXPIRY_DAYS = 90
MIN_REPLIES = 30          # replies (or evaluations) needed before a rate is worth stating
MIN_TURNS = 50            # turns needed before a turn-level rate is worth stating
REFRESH_TOLERANCE = 0.01  # a rate that moved less than this keeps its approved wording
MAX_LINES = 3
MAX_TEXT_CHARS = 380
CACHE_SECONDS = 300

CONTEXTS = ("emotional", "closing", "grounded", "self_questions")
_PRIORITY = ["emotional_fit", "closing_fishing", "grounded_failures", "identity_drift", "outright_failures", "work_mix"]

_ASKS_SELF_RE = re.compile(
    r"how (?:reliable|accurate|good|trustworthy) are you|how often (?:do you|are you) (?:get|wrong|make|mess)"
    r"|what are your (?:weak|strong|limitations|flaws|mistakes|blind)|\b(?:your|you have a) (?:weakness|limitations|track record|error rate)"
    r"|how (?:well|good) do you (?:do|work|perform)|can i (?:really )?trust you|are you (?:ever |often )?wrong"
    r"|do you (?:make|get) (?:mistakes|things wrong)|what are you (?:good|bad|best|worst) at|tell me about yourself|how do you work",
    re.IGNORECASE,
)

_TAG_PHRASE = {
    "hallucination": "stating things the data doesn't support",
    "incorrect_filter": "using the wrong scope or filter",
    "formatting": "formatting",
    "incomplete": "leaving part of the answer out",
    "other": "a mix of other problems",
}
_FAILURE_PHRASE = {
    "quota": "the model service was at its usage limit",
    "overloaded": "the model service was busy or slow",
    "other": "an unexpected error",
}
_ROUTE_PHRASE = {
    "conversational": "plain conversation",
    "kb_strict": "answers strictly from a knowledge base",
    "kb_open": "knowledge-base answers with general knowledge allowed",
    "web": "web lookups",
    "tool_output": "tool and repository work",
}


def asks_about_itself(message: str) -> bool:
    """Whether the user is asking how reliable or good Sonic is, which is when its own numbers may be stated."""
    return bool(_ASKS_SELF_RE.search(message or ""))


def _pct(rate: float) -> str:
    value = rate * 100
    return f"{value:.0f}%" if value >= 10 else f"{value:.1f}%"


def _trend(rate: float, previous: Optional[float]) -> str:
    if previous is None:
        return ""
    diff = rate - previous
    if abs(diff) < 0.01:
        return ", about the same as before"
    return f", {'up' if diff > 0 else 'down'} from {_pct(previous)}"


@dataclass
class Proposal:
    key: str
    text: str
    evidence: Dict[str, Any]
    applies_to: List[str] = field(default_factory=list)


def _numbers_in(text: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?%", text)


def validate_text(text: str, evidence: Dict[str, Any]) -> bool:
    """A mechanical check that the wording is allowed to exist: short, every percentage in it matches a number in its
    evidence, and it makes no claim the identity check would reject (no feelings, no stake, no invented past)."""
    if not text or len(text) > MAX_TEXT_CHARS or "!" in text:
        return False
    allowed = {_pct(v) for k, v in evidence.items() if isinstance(v, float) and k in ("rate", "previous_rate")}
    figures = _numbers_in(text)
    if not figures or any(f not in allowed for f in figures):
        return False
    return identity_reply_issue(text) is None


def _rate_proposal(key, rate, previous, denominator, minimum, threshold, template, applies_to, extra=None):
    if rate is None or denominator < minimum or rate < threshold:
        return None
    # The text and the evidence are built from the same rounded numbers, so they cannot disagree at a boundary.
    rate = round(rate, 4)
    previous = round(previous, 4) if previous is not None else None
    evidence = {"rate": rate, "previous_rate": previous, "denominator": denominator, **(extra or {})}
    text = template.format(p=_pct(rate), trend=_trend(rate, previous))
    return Proposal(key, text, evidence, applies_to) if validate_text(text, evidence) else None


def build_proposals(summary: Dict[str, Any]) -> List[Proposal]:
    """The observations this period's counters support. A metric below its threshold, or with too little data behind it,
    produces nothing."""
    totals, rates, previous = summary["totals"], summary["rates"], summary["previous_rates"]
    checked, turns = totals.get("reply_fit_checked", 0), totals.get("turns_total", 0)
    evaluated = totals.get("reward_evaluated", 0)
    out: List[Optional[Proposal]] = []

    out.append(_rate_proposal(
        "emotional_fit", rates.get("revised_emotional"), previous.get("revised_emotional"), checked, MIN_REPLIES, 0.03,
        "In about {p} of recent replies my first draft missed what the person needed (advice when they wanted to be "
        "heard, or no real question about good news){trend}. Slow down and check before answering someone who is sharing something.",
        ["emotional"]))
    out.append(_rate_proposal(
        "closing_fishing", rates.get("revised_closing"), previous.get("revised_closing"), checked, MIN_REPLIES, 0.03,
        "In about {p} of recent replies my first draft tried to keep a finished conversation going{trend}. When someone is "
        "wrapping up, close cleanly.",
        ["closing"]))
    out.append(_rate_proposal(
        "identity_drift", rates.get("revised_identity"), previous.get("revised_identity"), checked, MIN_REPLIES, 0.02,
        "In about {p} of recent replies my first draft claimed a feeling or a stake in the person{trend}. I describe tastes "
        "as leanings, and say plainly that I'm an AI when asked.",
        ["self_questions"]))

    reward_counts = {t: totals.get(f"reward_failed_{t}", 0) for t in REWARD_TAGS}
    top_tag = max(reward_counts, key=reward_counts.get) if any(reward_counts.values()) else "other"
    out.append(_rate_proposal(
        "grounded_failures", rates.get("reward_failed"), previous.get("reward_failed"), evaluated, MIN_REPLIES, 0.03,
        "About {p} of my recent grounded answers failed the answer check, most often for " + _TAG_PHRASE[top_tag] + "{trend}. "
        "Stay inside the retrieved material and say so when it doesn't cover the question.",
        ["grounded", "self_questions"], {"top_issue": top_tag}))

    failure_counts = {k: totals.get(f"turn_failed_{k}", 0) for k in FAILURE_KINDS}
    top_failure = max(failure_counts, key=failure_counts.get) if any(failure_counts.values()) else "other"
    out.append(_rate_proposal(
        "outright_failures", rates.get("turn_failed"), previous.get("turn_failed"), turns, MIN_TURNS, 0.01,
        "About {p} of recent turns failed outright, most often because " + _FAILURE_PHRASE[top_failure] + "{trend}.",
        ["self_questions"], {"top_cause": top_failure}))

    route_shares = {r: rates.get(f"route_{r}") for r in ROUTES if rates.get(f"route_{r}") is not None}
    if route_shares and turns >= MIN_TURNS:
        top_route = max(route_shares, key=route_shares.get)
        out.append(_rate_proposal(
            "work_mix", route_shares[top_route], previous.get(f"route_{top_route}"), turns, MIN_TURNS, 0.4,
            "Recently about {p} of my turns were " + _ROUTE_PHRASE[top_route] + "{trend}.",
            ["self_questions"], {"top_route": top_route}))
    return [p for p in out if p]


# ---------------------------------------------------------------------------------------------------------------
# Lifecycle: proposed -> approved -> (confirmed again | expired | retired)
# ---------------------------------------------------------------------------------------------------------------

def _iso(now: datetime.datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _parse(value: Any) -> Optional[datetime.datetime]:
    try:
        parsed = datetime.datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def merge_proposal(existing: Optional[Dict[str, Any]], proposal: Proposal, now: datetime.datetime) -> Dict[str, Any]:
    """The stored document after a reflection produced `proposal` for its key. Pure.

    - A new key starts pending.
    - If it already has approved wording and the rate barely moved, the approved wording stands and is simply
      confirmed again (no re-approval for a number that drifted by a point).
    - If it moved materially, the new wording waits as a proposed update while the approved wording stays live.
    - A retired key that the data supports again starts over as pending."""
    doc = dict(existing) if existing else {"key": proposal.key, "status": "pending", "created_at": _iso(now)}
    if doc.get("status") == "retired":
        doc = {"key": proposal.key, "status": "pending", "created_at": _iso(now)}
    doc["applies_to"] = list(proposal.applies_to)
    doc["last_confirmed_at"] = _iso(now)
    doc["updated_at"] = _iso(now)
    approved_rate = ((doc.get("approved_evidence") or {}).get("rate"))
    new_rate = proposal.evidence.get("rate")
    if doc.get("approved_text") and approved_rate is not None and new_rate is not None and abs(new_rate - approved_rate) < REFRESH_TOLERANCE:
        doc["proposed_text"] = None
        doc["proposed_evidence"] = None
    else:
        doc["proposed_text"] = proposal.text
        doc["proposed_evidence"] = proposal.evidence
    return doc


def is_live(doc: Dict[str, Any], now: datetime.datetime) -> bool:
    """Approved, not retired, and confirmed by a reflection within the expiry window."""
    if doc.get("status") != "approved" or not doc.get("approved_text"):
        return False
    confirmed = _parse(doc.get("last_confirmed_at"))
    return confirmed is not None and (now - confirmed) <= datetime.timedelta(days=EXPIRY_DAYS)


def approve_doc(doc: Dict[str, Any], now: datetime.datetime, by: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The document with its proposed wording made live, or None if there is nothing to approve or the wording fails
    validation (it is re-checked here, so a bad edit to the database cannot reach a prompt). `by` records who approved
    it (the operator's own id, never anything about a user)."""
    text, evidence = doc.get("proposed_text"), doc.get("proposed_evidence") or {}
    if not text or not validate_text(text, evidence):
        return None
    out = dict(doc)
    out.update(status="approved", approved_text=text, approved_evidence=evidence, approved_at=_iso(now),
               approved_by=by, proposed_text=None, proposed_evidence=None, updated_at=_iso(now))
    return out


def retire_doc(doc: Dict[str, Any], now: datetime.datetime, by: Optional[str] = None) -> Dict[str, Any]:
    out = dict(doc)
    out.update(status="retired", retired_by=by, proposed_text=None, proposed_evidence=None, updated_at=_iso(now))
    return out


def describe_observation(doc: Dict[str, Any], now: datetime.datetime) -> Dict[str, Any]:
    """One observation as the admin page shows it: where it stands (pending approval, live, expired or retired), the
    wording that is live and any proposed change waiting, the numbers behind each, and when it lapses unless confirmed."""
    confirmed = _parse(doc.get("last_confirmed_at"))
    status = doc.get("status")
    if status == "retired":
        state = "retired"
    elif is_live(doc, now):
        state = "live"
    elif status == "approved":
        state = "expired"
    else:
        state = "pending"
    expires = (confirmed + datetime.timedelta(days=EXPIRY_DAYS)).isoformat() if confirmed and doc.get("approved_text") else None
    return {
        "key": doc.get("key"),
        "state": state,
        "applies_to": list(doc.get("applies_to") or []),
        "approved_text": doc.get("approved_text"),
        "approved_evidence": doc.get("approved_evidence"),
        "approved_at": doc.get("approved_at"),
        "approved_by": doc.get("approved_by"),
        "proposed_text": doc.get("proposed_text"),
        "proposed_evidence": doc.get("proposed_evidence"),
        "last_confirmed_at": doc.get("last_confirmed_at"),
        "expires_at": expires,
    }


def live_observations(docs: List[Dict[str, Any]], now: datetime.datetime) -> List[Dict[str, Any]]:
    """Live observations in a fixed priority order."""
    live = [d for d in docs if is_live(d, now)]
    live.sort(key=lambda d: _PRIORITY.index(d["key"]) if d.get("key") in _PRIORITY else len(_PRIORITY))
    return live


# ---------------------------------------------------------------------------------------------------------------
# Choosing what applies to this turn
# ---------------------------------------------------------------------------------------------------------------

def active_contexts(*, asks_self: bool, closing: bool, grounded: bool, emotional: bool) -> List[str]:
    return [name for name, on in (("self_questions", asks_self), ("closing", closing), ("grounded", grounded), ("emotional", emotional)) if on]


def select_observations(
    docs: List[Dict[str, Any]], now: datetime.datetime, *, contexts: List[str], limit: int = MAX_LINES
) -> List[str]:
    """The approved wording of at most `limit` live observations that apply to the active contexts, in priority order."""
    if not contexts:
        return []
    chosen = [d["approved_text"] for d in live_observations(docs, now) if set(d.get("applies_to") or []) & set(contexts)]
    return chosen[:limit]


def build_self_knowledge_block(lines: List[str], asks_self: bool) -> str:
    """The prompt section ("" when there is nothing)."""
    if not lines:
        return ""
    block = "\nYOUR OWN TRACK RECORD (measured, quiet calibration): " + " ".join(lines) + "\n"
    if asks_self:
        block += (
            "They are asking about how reliable or good you are. You may state the relevant parts plainly, with their "
            "numbers, without overselling or underselling. Say only what the lines above say: do not add that you are "
            "'highly reliable' elsewhere, that you are 'working on' or 'dialing back' anything, or any cause or "
            "improvement they don't state. If you don't have a number for something, say you don't have one.\n"
        )
    else:
        block += "Let this change how carefully you answer. Don't recite it or bring it up unless they ask about you.\n"
    return block


_cache: Dict[str, Any] = {"at": 0.0, "docs": []}


def _approved_docs_cached(now_monotonic: Optional[float] = None) -> List[Dict[str, Any]]:
    """The stored documents, re-read at most every few minutes so a reply never waits on the database for this."""
    current = time.monotonic() if now_monotonic is None else now_monotonic
    if current - _cache["at"] < CACHE_SECONDS and _cache["at"] > 0:
        return _cache["docs"]
    docs: List[Dict[str, Any]] = []
    try:
        from backend.utils.db_utils import get_db

        db = get_db()
        if db is not None:
            docs = [d for d in db[COLLECTION].find({"status": "approved"}, {"_id": 0})]
    except Exception:
        logger.warning("[self observations] could not read the approved observations.", exc_info=True)
        docs = _cache["docs"]
    _cache["at"], _cache["docs"] = current, docs
    return docs


def observations_for_turn(
    message: str, *, source_type: str, closing: bool, emotional_active: bool, risk_active: bool,
    now: Optional[datetime.datetime] = None,
) -> List[str]:
    """The observation lines to give the model this turn. Nothing under a safety context. Blocking only when the cache
    is stale (a small read), so call it off the event loop. Never raises."""
    try:
        if risk_active:
            return []
        contexts = active_contexts(
            asks_self=asks_about_itself(message), closing=closing,
            grounded=source_type in ("kb_strict", "kb_open", "tool_output"), emotional=emotional_active,
        )
        if not contexts:
            return []
        return select_observations(_approved_docs_cached(), now or datetime.datetime.now(timezone.utc), contexts=contexts)
    except Exception:
        logger.warning("[self observations] could not choose observations for this turn.", exc_info=True)
        return []


# ---------------------------------------------------------------------------------------------------------------
# Database operations and the command line
# ---------------------------------------------------------------------------------------------------------------

def load_all(db: Any) -> List[Dict[str, Any]]:
    return list(db[COLLECTION].find({}, {"_id": 0}))


def _save(db: Any, doc: Dict[str, Any]) -> None:
    db[COLLECTION].replace_one({"key": doc["key"]}, doc, upsert=True)


def reflect(db: Any, days: int, now: Optional[datetime.datetime] = None) -> Dict[str, List[str]]:
    """Turns the counters into proposals and stores them. Returns what happened, by key."""
    now = now or datetime.datetime.now(timezone.utc)
    summary = summarize_counters(fetch_counter_docs(db, days, now), now, days)
    existing = {d["key"]: d for d in load_all(db)}
    result: Dict[str, List[str]] = {"proposed": [], "confirmed": [], "unsupported": []}
    supported = set()
    for proposal in build_proposals(summary):
        supported.add(proposal.key)
        before = existing.get(proposal.key)
        doc = merge_proposal(before, proposal, now)
        _save(db, doc)
        result["proposed" if doc.get("proposed_text") else "confirmed"].append(proposal.key)
    result["unsupported"] = [k for k in _PRIORITY if k not in supported]
    return result


def approve(db: Any, key: str, now: Optional[datetime.datetime] = None, by: Optional[str] = None) -> bool:
    now = now or datetime.datetime.now(timezone.utc)
    doc = db[COLLECTION].find_one({"key": key}, {"_id": 0})
    approved = approve_doc(doc, now, by) if doc else None
    if approved is None:
        return False
    _save(db, approved)
    _cache["at"] = 0.0
    return True


def retire(db: Any, key: str, now: Optional[datetime.datetime] = None, by: Optional[str] = None) -> bool:
    now = now or datetime.datetime.now(timezone.utc)
    doc = db[COLLECTION].find_one({"key": key}, {"_id": 0})
    if not doc:
        return False
    _save(db, retire_doc(doc, now, by))
    _cache["at"] = 0.0
    return True


def list_observations(db: Any, now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Every observation as the admin page shows it, in priority order."""
    now = now or datetime.datetime.now(timezone.utc)
    docs = sorted(load_all(db), key=lambda d: _PRIORITY.index(d["key"]) if d.get("key") in _PRIORITY else len(_PRIORITY))
    return [describe_observation(d, now) for d in docs]


def format_listing(docs: List[Dict[str, Any]], now: Optional[datetime.datetime] = None) -> str:
    now = now or datetime.datetime.now(timezone.utc)
    if not docs:
        return "No observations yet. Run `reflect` once the counters have a few days of data."
    lines = []
    for d in sorted(docs, key=lambda d: _PRIORITY.index(d["key"]) if d.get("key") in _PRIORITY else 99):
        state = "LIVE" if is_live(d, now) else d.get("status", "?").upper()
        lines.append(f"[{state}] {d['key']}  (applies to: {', '.join(d.get('applies_to') or [])})")
        if d.get("approved_text"):
            lines.append(f"    approved: {d['approved_text']}")
        if d.get("proposed_text"):
            lines.append(f"    PROPOSED (needs `approve {d['key']}`): {d['proposed_text']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sonic's measured self-knowledge.")
    sub = parser.add_subparsers(dest="command", required=True)
    reflect_parser = sub.add_parser("reflect", help="turn the counters into proposed observations")
    reflect_parser.add_argument("--days", type=int, default=30)
    sub.add_parser("list", help="show every observation")
    approve_parser = sub.add_parser("approve", help="make a proposed observation live")
    approve_parser.add_argument("key")
    retire_parser = sub.add_parser("retire", help="stop using an observation")
    retire_parser.add_argument("key")
    args = parser.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    from backend.utils.db_utils import get_db

    db = get_db()
    if db is None:
        print("No database is configured.")
        return 2
    if args.command == "reflect":
        result = reflect(db, args.days)
        print(f"proposed: {result['proposed'] or '-'}\nconfirmed (unchanged): {result['confirmed'] or '-'}\n"
              f"not supported by the data yet: {result['unsupported'] or '-'}")
    elif args.command == "list":
        print(format_listing(load_all(db)))
    elif args.command == "approve":
        print("approved" if approve(db, args.key) else f"nothing to approve for {args.key!r}")
    elif args.command == "retire":
        print("retired" if retire(db, args.key) else f"no observation {args.key!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
