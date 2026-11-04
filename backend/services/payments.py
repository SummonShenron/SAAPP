"""The payment side of the paid tier (docs/paid-tier-plan.md), up to the edge of Stripe.

Stripe is the only thing not built here. Everything else is: what a provider must do (`PaymentProvider`), a FAKE provider for tests that signs and checks webhooks the way a real one does, and the two things
the endpoints need: starting a checkout and accepting a webhook. A real provider is one class that implements the two methods and is returned by `get_provider()`; nothing else changes.

- THE FAKE PROVIDER CANNOT BE SELECTED BY THE ENVIRONMENT. `get_provider()` returns None until a real provider exists, and `set_provider()` (tests only) is the one way to install another, so a deployment can
  never be switched onto a provider whose webhooks anyone could forge.
- A WEBHOOK IS TRUSTED ONLY AFTER ITS SIGNATURE IS CHECKED by the provider, credits only a COMPLETED payment, and is idempotent: the payment's own id is the ledger reference, so the same event twice is one credit.
- THE PROCESSING LINE (decided 2026-11-05: show it): a person chooses how much CREDIT they want, and the checkout charges the credit plus the processor's fee, shown as its own line (`credits.quote`). The
  event says how much credit it was for; if it does not, the credit is worked back from the payment (`credits.credit_from_paid`). The fee is not refundable; unused credit is, on request.
- A DISPUTED payment (a chargeback) freezes the account and takes its remaining credit off the balance (`credits.freeze`).
- NOTHING ABOUT WHAT A PERSON SAID is stored or sent: a checkout carries a person id, an amount and two return addresses.
"""
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional, Protocol, Tuple

from backend.utils import credits

logger = logging.getLogger("SASS Logger")

COMPLETED = "payment.completed"
DISPUTED = "payment.disputed"


class InvalidSignature(Exception):
    """The webhook's signature did not check out: nothing in it may be believed."""


class PaymentProvider(Protocol):
    def create_checkout(self, user: str, amount_micro: int, success_url: str, cancel_url: str, credit_micro: int = 0) -> str:
        """Starts a hosted checkout charging `amount_micro` (the credit plus the processing line) for this person, for `credit_micro` of credit, and returns the address to send them to. The credit travels with the
        payment so its event can say how much credit it was for. The person never enters a card in this app."""

    def parse_webhook(self, body: bytes, signature: Optional[str]) -> Optional[Dict[str, Any]]:
        """Checks the signature (raising InvalidSignature when it is wrong) and returns {"id", "type", "user", "amount_micro", "credit_micro"} for a completed or a disputed payment, or None for any other event."""


class FakeProvider:
    """A provider for tests. Its webhooks are JSON signed with HMAC-SHA256 under a secret, which is what makes the signature path real."""

    def __init__(self, secret: str = "test-secret") -> None:
        self.secret = secret
        self.checkouts: list = []

    def create_checkout(self, user: str, amount_micro: int, success_url: str, cancel_url: str, credit_micro: int = 0) -> str:
        self.checkouts.append({"user": user, "amount_micro": amount_micro, "credit_micro": credit_micro})
        return f"https://pay.invalid/checkout/{len(self.checkouts)}"

    def sign(self, body: bytes) -> str:
        return hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

    def parse_webhook(self, body: bytes, signature: Optional[str]) -> Optional[Dict[str, Any]]:
        if not signature or not hmac.compare_digest(self.sign(body), signature):
            raise InvalidSignature()
        try:
            event = json.loads(body.decode("utf-8"))
        except ValueError:
            return None
        if not isinstance(event, dict) or event.get("type") not in (COMPLETED, DISPUTED):
            return None
        return {"id": str(event.get("id") or ""), "type": event.get("type"), "user": str(event.get("user") or ""), "amount_micro": event.get("amount_micro"), "credit_micro": event.get("credit_micro")}


_provider: Optional[PaymentProvider] = None


def get_provider() -> Optional[PaymentProvider]:
    """The configured provider, or None (payments are not set up). There is no real one yet."""
    return _provider


def set_provider(provider: Optional[PaymentProvider]) -> None:
    """Installs a provider. For tests, and for the day a real one is written: not reachable from the environment."""
    global _provider
    _provider = provider


def credit_for_payment(amount_micro: int) -> int:
    """The credit for a payment of `amount_micro` when its event did not say (see credits.credit_from_paid)."""
    return credits.credit_from_paid(int(amount_micro))


def start_checkout(db: Any, user: str, amount_usd: Any, success_url: str, cancel_url: str) -> Tuple[int, Dict[str, Any]]:
    """Starts a top-up for `amount_usd` of CREDIT. (HTTP status, body). 503 when payments are not set up, 403 for a frozen account, 409 when top-ups are paused by the global paid ceiling, 400 for a credit below the
    minimum or that is not a number. The body carries the address and the quote: the credit, the processing line and the total the card is charged."""
    provider = get_provider()
    if provider is None:
        return 503, {"detail": "Payments are not set up yet."}
    try:
        micro = int(round(float(amount_usd) * credits.MICRO))
    except (TypeError, ValueError):
        return 400, {"detail": "That is not an amount."}
    if micro < credits.min_topup_micro():
        return 400, {"detail": f"The smallest top-up is ${credits.to_usd(credits.min_topup_micro()):.2f} of credit."}
    if credits.is_frozen(db, user):
        return 403, {"detail": "Top-ups are paused on this account. Please contact us."}
    if credits.topups_paused(db):
        return 409, {"detail": "New top-ups are paused for today. Please try again tomorrow."}
    priced = credits.quote(micro)
    try:
        url = provider.create_checkout(user, priced["total_micro"], success_url, cancel_url, micro)
    except Exception:
        logger.warning("[payments] the provider could not start a checkout.", exc_info=True)
        return 502, {"detail": "The payment service could not start a checkout. Please try again."}
    return 200, {"url": url, "credit_usd": credits.to_usd(priced["credit_micro"]), "fee_usd": credits.to_usd(priced["fee_micro"]), "total_usd": credits.to_usd(priced["total_micro"])}


def quote_view(amount_usd: Any) -> Tuple[int, Dict[str, Any]]:
    """What a top-up of `amount_usd` of credit would cost, for the page to show before anyone pays: (200, {credit_usd, fee_usd, total_usd}) or (400, ...)."""
    try:
        micro = int(round(float(amount_usd) * credits.MICRO))
    except (TypeError, ValueError):
        return 400, {"detail": "That is not an amount."}
    if micro < credits.min_topup_micro():
        return 400, {"detail": f"The smallest top-up is ${credits.to_usd(credits.min_topup_micro()):.2f} of credit."}
    priced = credits.quote(micro)
    return 200, {"credit_usd": credits.to_usd(priced["credit_micro"]), "fee_usd": credits.to_usd(priced["fee_micro"]), "total_usd": credits.to_usd(priced["total_micro"])}


def accept_webhook(db: Any, body: bytes, signature: Optional[str]) -> Tuple[int, Dict[str, Any]]:
    """Handles one webhook. 503 when payments are not set up, 400 for a bad signature, 200 {"status": "credited" | "frozen" | "duplicate" | "ignored"} otherwise. Credits only a COMPLETED payment of a positive amount
    for a named person, once per payment id, for the credit the event names (or the payment less the processing line); a DISPUTED payment freezes that person's account, once per dispute id."""
    provider = get_provider()
    if provider is None:
        return 503, {"detail": "Payments are not set up yet."}
    try:
        event = provider.parse_webhook(body, signature)
    except InvalidSignature:
        return 400, {"detail": "Bad signature."}
    except Exception:
        logger.warning("[payments] a webhook could not be read.", exc_info=True)
        return 400, {"detail": "Could not read the event."}
    if not event or event.get("type") not in (COMPLETED, DISPUTED):
        return 200, {"status": "ignored"}
    user, ref = event.get("user"), event.get("id")
    if not user or not ref:
        return 200, {"status": "ignored"}
    if event["type"] == DISPUTED:
        return 200, {"status": "frozen" if credits.freeze(db, str(user), str(ref)) else "duplicate"}
    amount, named = event.get("amount_micro"), event.get("credit_micro")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return 200, {"status": "ignored"}
    credit = named if isinstance(named, int) and not isinstance(named, bool) and 0 < named <= amount else credit_for_payment(amount)
    if credit <= 0:
        return 200, {"status": "ignored"}
    written = credits.apply_topup(db, str(user), credit, str(ref))
    return 200, {"status": "credited" if written else "duplicate"}
