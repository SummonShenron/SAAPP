"""Prepaid credit for the paid tier (docs/paid-tier-plan.md), built SWITCHED OFF: nothing here does anything unless BILLING_ENABLED is true.

The ideas, in the order the plan states them:
- PREPAID, NEVER OWED. A person's balance is the sum of their ledger rows. A top-up adds, a spend subtracts, a refund subtracts. A person with a balance above zero is "paid": for their own chat turns the free
  limits do not apply (backend/utils/budget_guard.py `paid_bypass`). At zero they are back under the free limits.
- THE LEDGER IS APPEND-ONLY AND IDEMPOTENT. One row per event, `_id` = "kind:ref" (a payment id for a top-up, a turn id for a spend), so a repeated webhook or a retried flush cannot count twice. Amounts are
  integers in millionths of a dollar (the unit of `cost_micro` in backend/utils/user_usage.py). A row holds an amount, a kind, a reference, a time and a billing day. NO prompt, reply, file name or any other
  text is ever stored here.
- A SPEND IS THE METERED COST. At the end of a paid turn its `cost_micro` times BILLING_RATE (1.0: one to one) is queued, and saved with the other usage counts (llm_usage.flush). A turn lost to a crash is not
  charged: the error is in the person's favour.
- LIMITS ON PAID PEOPLE TOO (plan section 3b), kept LOW to cap what a chargeback can cost: a daily ceiling per person, lower for a new account, raised only by an admin for one person, and a ceiling the person may
  set lower themselves; plus a global paid-spend ceiling past which top-ups pause. A ceiling is a pause, never a charge.
Every function that reads or writes the database takes `db`; none raises into a chat turn (the callers that run on a turn catch).
"""
import logging
import math
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from backend.utils import billing_time

logger = logging.getLogger("SASS Logger")

COLLECTION = "billing_ledger"
ACCOUNTS_COLLECTION = "billing_accounts"
KINDS = ("topup", "spend", "refund", "adjust")
MICRO = 1_000_000
RECENT_SHOWN = 10

_lock = threading.Lock()
_pending: List[Dict[str, Any]] = []          # spends queued by finished paid turns and not yet saved: {"user", "ref", "micro", "at"}


# ---------------------------------------------------------------------------------------------------------------
# Settings (all from the environment; nothing is read until billing is on)
# ---------------------------------------------------------------------------------------------------------------

def enabled() -> bool:
    return os.getenv("BILLING_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _usd(name: str, default: float) -> int:
    """An environment value in dollars as millionths of a dollar; the default when unset or not a non-negative number."""
    try:
        value = float(os.getenv(name, "").strip())
        if value >= 0:
            return int(round(value * MICRO))
    except ValueError:
        pass
    return int(round(default * MICRO))


def rate() -> float:
    """What a metered dollar is charged as (BILLING_RATE, default 1.0: one to one). Not a number above zero reads as 1.0."""
    try:
        value = float(os.getenv("BILLING_RATE", "").strip())
        return value if value > 0 else 1.0
    except ValueError:
        return 1.0


def min_topup_micro() -> int:
    return _usd("BILLING_MIN_TOPUP_USD", 5.0)


def daily_ceiling_micro() -> int:
    """The most one paid person may spend in a billing day, unless an admin has raised theirs. Low on purpose (Jack, 2026-11-05): the cap on what one chargeback can cost."""
    return _usd("BILLING_DAILY_CEILING_USD", 5.0)


def new_account_ceiling_micro() -> int:
    return _usd("BILLING_NEW_ACCOUNT_CEILING_USD", 2.0)


def new_account_days() -> int:
    try:
        return max(int(os.getenv("BILLING_NEW_ACCOUNT_DAYS", "").strip()), 0)
    except ValueError:
        return 7


def global_paid_ceiling_micro() -> int:
    """The most ALL paid people may spend in a billing day before new top-ups pause. Paying bypasses Jack's own cap but not the providers' (Google's monthly cap, Anthropic's rate limits)."""
    return _usd("BILLING_GLOBAL_PAID_CEILING_USD", 25.0)


def fee_percent() -> float:
    """The payment processor's percentage fee, which the top-up page shows as a processing line (BILLING_FEE_PERCENT, default 2.9; from 0 up to 20). Check it against the processor's real price when it is set up."""
    try:
        value = float(os.getenv("BILLING_FEE_PERCENT", "").strip())
        return min(max(value, 0.0), 20.0)
    except ValueError:
        return 2.9


def fee_fixed_micro() -> int:
    """The processor's fixed fee per payment (BILLING_FEE_FIXED_USD, default $0.30)."""
    return _usd("BILLING_FEE_FIXED_USD", 0.30)


def quote(credit_micro: int) -> Dict[str, int]:
    """What a top-up of `credit_micro` of credit costs: the credit, the processing line, and the total charged to the card. The total is what makes the processor's fee come out of the person's payment and not
    out of Jack's pocket (one to one on the credit): total = (credit + fixed fee) / (1 - percentage), rounded up to a whole cent. A processing line, never hidden in the price."""
    credit = max(int(credit_micro), 0)
    if credit == 0:
        return {"credit_micro": 0, "fee_micro": 0, "total_micro": 0}
    total = int(math.ceil((credit + fee_fixed_micro()) / (1.0 - fee_percent() / 100.0)))
    total = int(math.ceil(total / 10_000.0)) * 10_000          # a whole cent
    return {"credit_micro": credit, "fee_micro": total - credit, "total_micro": total}


def credit_from_paid(paid_micro: int) -> int:
    """The credit for a payment whose event did not say (it normally does): the payment less the processing line, by the same formula run backwards. Never more than the payment, never below zero."""
    paid = max(int(paid_micro), 0)
    credit = int((paid * (1.0 - fee_percent() / 100.0) - fee_fixed_micro()) // 10_000) * 10_000          # down to a whole cent: never a fraction of a cent too much
    return min(max(credit, 0), paid)


def to_usd(micro: int) -> float:
    return round(micro / MICRO, 4)


def _aware(moment: Optional[datetime]) -> datetime:
    moment = moment or datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------------------------------------------

def _signed(kind: str, micro: int) -> int:
    """A top-up adds; a spend and a refund (money going back to the card) subtract; an adjustment is signed as given."""
    if kind == "topup":
        return abs(micro)
    if kind in ("spend", "refund"):
        return -abs(micro)
    return micro


def _insert(db: Any, user: str, kind: str, micro: int, ref: str, now: Optional[datetime] = None) -> bool:
    """Appends one ledger row. True when written; False when it was already there (same kind and ref) or the arguments are not valid. RAISES when the database fails, so a caller that must not lose the event
    (flush) can tell "already counted" from "not saved"."""
    if kind not in KINDS or not isinstance(user, str) or not user.strip() or not isinstance(ref, str) or not ref.strip() or isinstance(micro, bool) or not isinstance(micro, int) or micro == 0:
        return False
    row_id = f"{kind}:{ref.strip()[:200]}"
    if db[COLLECTION].find_one({"_id": row_id}) is not None:
        return False
    at = _aware(now)
    row = {"_id": row_id, "user": user.strip()[:200], "kind": kind, "micro": _signed(kind, micro), "ref": ref.strip()[:200], "at": at, "day": billing_time.day(at)}
    try:
        db[COLLECTION].insert_one(row)
    except Exception as problem:
        if type(problem).__name__ == "DuplicateKeyError":
            return False                      # the same event arrived twice at once: one of them won
        raise
    return True


def record(db: Any, user: str, kind: str, micro: int, ref: str, now: Optional[datetime] = None) -> bool:
    """Appends one ledger row (see _insert). Never raises: a database problem is logged and reads as False. Stores nothing but the amount, the kind, the reference, the person and the time."""
    try:
        return _insert(db, user, kind, micro, ref, now)
    except Exception:
        logger.warning("[credits] could not write a ledger row.", exc_info=True)
        return False


def _pending_for(user: str) -> int:
    with _lock:
        return sum(item["micro"] for item in _pending if item["user"] == user)


def balance_micro(db: Any, user: str) -> int:
    """The person's balance: every saved row, less what finished paid turns have queued and not yet saved. Never raises (0 on a read problem)."""
    try:
        saved = sum(int(row.get("micro") or 0) for row in db[COLLECTION].find({"user": user}))
        return saved - _pending_for(user)
    except Exception:
        logger.warning("[credits] could not read a balance.", exc_info=True)
        return 0


def queue_debit(user: Optional[str], turn_id: str, cost_micro: int, now: Optional[datetime] = None) -> bool:
    """Queues the charge for a finished paid turn: its metered cost times the rate. False (nothing queued) when billing is off, there is no person, or the turn cost nothing (a model with no price in LLM_PRICES adds
    no dollars, so a paid turn on one would be free: the plan's unpriced-model test watches for that). In memory only: it is written by flush()."""
    try:
        if not enabled() or not user or int(cost_micro) <= 0:
            return False
        micro = int(round(int(cost_micro) * rate()))
        if micro <= 0:
            return False
        with _lock:
            _pending.append({"user": str(user), "ref": str(turn_id), "micro": micro, "at": _aware(now)})
        return True
    except Exception:
        return False


def flush(db: Any = None) -> int:
    """Saves the queued spends. Returns how many were written. A spend that fails to save goes back to be tried again with the next flush. Blocking: call it off the event loop. Never raises."""
    with _lock:
        batch = list(_pending)
        _pending.clear()
    if not batch:
        return 0
    written, failed = 0, []
    try:
        if db is None:
            from backend.utils.db_utils import get_db

            db = get_db()
        if db is None:
            raise RuntimeError("no database")
        for item in batch:
            try:
                if _insert(db, item["user"], "spend", item["micro"], item["ref"], item["at"]):
                    written += 1
            except Exception:
                failed.append(item)
        return written
    except Exception:
        failed = batch
        logger.warning("[credits] could not save spends; keeping them for the next flush.")
        return written
    finally:
        if failed:
            with _lock:
                _pending[:0] = failed


def reset() -> None:
    """Forgets the queued spends (tests, and a clean shutdown)."""
    with _lock:
        _pending.clear()


def recent(db: Any, user: str, limit: int = RECENT_SHOWN) -> List[Dict[str, Any]]:
    """The person's latest ledger rows, newest first, as {kind, usd, at, day}: amounts and times only."""
    try:
        rows = sorted(db[COLLECTION].find({"user": user}), key=lambda row: row.get("at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)[:limit]
        return [{"kind": row.get("kind"), "usd": to_usd(int(row.get("micro") or 0)), "at": _aware(row.get("at")).isoformat() if row.get("at") else None, "day": row.get("day")} for row in rows]
    except Exception:
        return []


# ---------------------------------------------------------------------------------------------------------------
# Accounts: when the first top-up was, and the ceilings an admin or the person has set
# ---------------------------------------------------------------------------------------------------------------

def get_account(db: Any, user: str) -> Dict[str, Any]:
    try:
        return db[ACCOUNTS_COLLECTION].find_one({"_id": user}) or {}
    except Exception:
        return {}


def _save_account(db: Any, user: str, fields: Dict[str, Any]) -> None:
    if db[ACCOUNTS_COLLECTION].find_one({"_id": user}) is None:
        db[ACCOUNTS_COLLECTION].insert_one({"_id": user, **fields})
    else:
        db[ACCOUNTS_COLLECTION].update_one({"_id": user}, {"$set": fields})


def apply_topup(db: Any, user: str, micro: int, ref: str, now: Optional[datetime] = None) -> bool:
    """Credits a payment (idempotent by `ref`) and notes the day of the person's first one, which starts their new-account period. True when it was new."""
    at = _aware(now)
    written = record(db, user, "topup", micro, ref, at)
    if written and "first_topup_at" not in get_account(db, user):
        _save_account(db, user, {"first_topup_at": at})
    return written


def ceiling_micro(account: Dict[str, Any], now: Optional[datetime] = None) -> int:
    """The most this person may spend today. An admin's override for them if there is one; otherwise the ordinary daily ceiling, or the lower new-account one until `new_account_days` have passed since their
    first top-up (an account with none on record counts as new). Then never more than the ceiling they set themselves. Pure."""
    moment = _aware(now)
    override = account.get("ceiling_override_micro")
    if isinstance(override, int) and override > 0:
        base = override
    else:
        base = daily_ceiling_micro()
        first = account.get("first_topup_at")
        if first is None or (moment - _aware(first)) < timedelta(days=new_account_days()):
            base = min(base, new_account_ceiling_micro())
    own = account.get("own_ceiling_micro")
    if isinstance(own, int) and own > 0:
        base = min(base, own)
    return base


def set_own_ceiling(db: Any, user: str, usd: Optional[float], now: Optional[datetime] = None) -> Tuple[bool, str, int]:
    """The person sets their own daily ceiling LOWER than the one the server allows them (None clears it). (ok, what to tell them, the ceiling now in force)."""
    account = get_account(db, user)
    server = ceiling_micro({key: value for key, value in account.items() if key != "own_ceiling_micro"}, now)
    if usd is None:
        _save_account(db, user, {"own_ceiling_micro": None})
        return True, "Your own limit is cleared.", server
    try:
        micro = int(round(float(usd) * MICRO))
    except (TypeError, ValueError):
        return False, "That is not an amount.", ceiling_micro(account, now)
    if micro <= 0:
        return False, "Choose an amount above zero, or clear the limit.", ceiling_micro(account, now)
    if micro > server:
        return False, f"Your daily limit can't be set above ${to_usd(server):.2f}. Ask an admin if you need more.", ceiling_micro(account, now)
    _save_account(db, user, {"own_ceiling_micro": micro})
    return True, "Saved.", min(server, micro)


def admin_set_ceiling(db: Any, admin: str, user: str, usd: Optional[float], now: Optional[datetime] = None) -> Tuple[bool, str]:
    """An admin raises (or lowers) one person's daily ceiling; None removes the override. Saved with who and when. Nothing else raises a ceiling."""
    if usd is None:
        _save_account(db, user, {"ceiling_override_micro": None, "ceiling_override_by": admin, "ceiling_override_at": _aware(now)})
        return True, "Override removed."
    try:
        micro = int(round(float(usd) * MICRO))
    except (TypeError, ValueError):
        return False, "That is not an amount."
    if micro <= 0:
        return False, "Choose an amount above zero."
    _save_account(db, user, {"ceiling_override_micro": micro, "ceiling_override_by": admin, "ceiling_override_at": _aware(now)})
    return True, "Saved."


def is_frozen(db: Any, user: str) -> bool:
    return bool(get_account(db, user).get("frozen"))


def freeze(db: Any, user: str, ref: str, now: Optional[datetime] = None) -> bool:
    """A payment was DISPUTED (a chargeback): the account stops being paid and cannot top up, and whatever credit is left is taken off the balance (one `adjust` row, by the dispute's reference, so a repeated
    event counts once). That is the whole policy: the money the card owner is asking back is no longer spendable. True when this froze it, False when this dispute was already handled."""
    account = get_account(db, user)
    if account.get("frozen") and account.get("frozen_ref") == ref:
        return False
    at = _aware(now)
    balance = balance_micro(db, user)
    if balance > 0:
        _insert(db, user, "adjust", -balance, f"dispute:{ref}", at)
    _save_account(db, user, {"frozen": True, "frozen_ref": ref, "frozen_at": at})
    return True


def unfreeze(db: Any, admin: str, user: str, now: Optional[datetime] = None) -> bool:
    """An admin lifts a freeze (after looking at it). Saved with who and when. False when the account was not frozen."""
    if not is_frozen(db, user):
        return False
    _save_account(db, user, {"frozen": False, "unfrozen_by": admin, "unfrozen_at": _aware(now)})
    return True


def refund(db: Any, admin: str, user: str, usd: Any, ref: Optional[str] = None, now: Optional[datetime] = None) -> Tuple[bool, str, int]:
    """Records a refund of UNUSED credit that a person asked for by email (the policy: used credit and the processing line are not refundable). It takes the amount off their balance; the card refund itself is
    made in the payment processor's own dashboard. Never more than the balance. (ok, what to tell the admin, the balance after.)"""
    try:
        micro = int(round(float(usd) * MICRO))
    except (TypeError, ValueError):
        return False, "That is not an amount.", balance_micro(db, user)
    balance = balance_micro(db, user)
    if micro <= 0:
        return False, "Choose an amount above zero.", balance
    if micro > balance:
        return False, f"That is more than their unused balance (${to_usd(balance):.2f}).", balance
    if not _insert(db, user, "refund", micro, (ref or uuid.uuid4().hex), now):
        return False, "That refund reference was already used.", balance
    return True, "Recorded. Refund the card in the payment processor's dashboard.", balance - micro


def spent_today_micro(db: Any, user: str, now: Optional[datetime] = None) -> int:
    """What the person has spent in the current billing day: saved spend rows plus queued ones. Never raises."""
    try:
        today = billing_time.day(_aware(now))
        saved = sum(-int(row.get("micro") or 0) for row in db[COLLECTION].find({"user": user, "kind": "spend", "day": today}))
        with _lock:
            queued = sum(item["micro"] for item in _pending if item["user"] == user and billing_time.day(item["at"]) == today)
        return saved + queued
    except Exception:
        return 0


def global_spent_today_micro(db: Any, now: Optional[datetime] = None) -> int:
    """What ALL paid people have spent in the current billing day."""
    try:
        today = billing_time.day(_aware(now))
        saved = sum(-int(row.get("micro") or 0) for row in db[COLLECTION].find({"kind": "spend", "day": today}))
        with _lock:
            queued = sum(item["micro"] for item in _pending if billing_time.day(item["at"]) == today)
        return saved + queued
    except Exception:
        return 0


def topups_paused(db: Any, now: Optional[datetime] = None) -> bool:
    """Whether new top-ups wait because all paid spend today has reached the global paid ceiling (people who already have a balance keep it)."""
    return enabled() and global_spent_today_micro(db, now) >= global_paid_ceiling_micro()


# ---------------------------------------------------------------------------------------------------------------
# What the guard and the page are told
# ---------------------------------------------------------------------------------------------------------------

def paid_status(db: Any, user: str, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """None when billing is off or the person is not paid (no balance above zero): the free limits apply as always. Otherwise {"balance_micro", "ceiling_micro", "spent_today_micro", "at_ceiling"}. Never raises."""
    try:
        if not enabled() or not user:
            return None
        if get_account(db, user).get("frozen"):
            return None                                   # a disputed payment froze this account (see freeze)
        balance = balance_micro(db, user)
        if balance <= 0:
            return None
        ceiling = ceiling_micro(get_account(db, user), now)
        spent = spent_today_micro(db, user, now)
        return {"balance_micro": balance, "ceiling_micro": ceiling, "spent_today_micro": spent, "at_ceiling": spent >= ceiling}
    except Exception:
        return None


def public_view(db: Any, user: str, now: Optional[datetime] = None, provider_ready: bool = False) -> Dict[str, Any]:
    """What /api/billing/me tells the person about THEIR OWN account: amounts in dollars, never anything about anyone else."""
    account = get_account(db, user)
    status = paid_status(db, user, now)
    ceiling = ceiling_micro(account, now)
    return {
        "enabled": True,
        "balance_usd": to_usd(max(balance_micro(db, user), 0)),
        "paid": status is not None,
        "daily_ceiling_usd": to_usd(ceiling),
        "own_ceiling_usd": to_usd(account["own_ceiling_micro"]) if isinstance(account.get("own_ceiling_micro"), int) and account["own_ceiling_micro"] > 0 else None,
        "spent_today_usd": to_usd(spent_today_micro(db, user, now)),
        "min_topup_usd": to_usd(min_topup_micro()),
        "fee_percent": fee_percent(),
        "fee_fixed_usd": to_usd(fee_fixed_micro()),
        "frozen": bool(account.get("frozen")),
        "topups_paused": topups_paused(db, now),
        "can_top_up": bool(provider_ready) and not topups_paused(db, now),
        "recent": recent(db, user),
    }
