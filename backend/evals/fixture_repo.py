"""A small made-up repository ("tallybox", a Flask invoicing service) for the behaviour eval's repo scenarios.

The tool agent's repo tools (list_repo_tree, read_repo_file, find_file, search_literal) read a local checkout; search_code,
trace_symbol, list_commits and list_pull_requests ask GitHub. The eval serves this repo through both: `materialize()` writes it to a
directory the checkout code reads, and `fake_github_get()` answers the API calls from the same files, so the model works against a
repository whose true answers are KNOWN. The questions in behavior_eval.py are written against these facts:

- RATE_LIMIT_PER_MIN is read in two places with DIFFERENT defaults: tallybox/config.py (60) and tallybox/middleware/ratelimit.py (100).
- An invoice becomes "overdue" in tallybox/jobs/overdue.py (mark_overdue), which tallybox/jobs/scheduler.py calls every hour, using
  settings.grace_days (default 7) from tallybox/config.py.
- Three routes do not use @require_auth: GET /health, GET /public/invoice/<token> and POST /webhooks/stripe (which verifies a signature).
- Nothing in the repo uses Redis.
- The last commits and the open pull request are listed in COMMITS and PULL_REQUESTS.

Nothing here is real, and nothing in it should look like a credential.
"""
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional


def _padding(prefix: str, count: int) -> str:
    """Plausible, unrelated helper functions: the bulk a real file has, so reading it costs what reading a real file costs."""
    blocks = []
    for index in range(count):
        blocks.append(
            f"def {prefix}_{index}(rows, label=\"{prefix}{index}\"):\n"
            f"    total = 0\n"
            f"    for row in rows:\n"
            f"        if row.get(\"kind\") == label:\n"
            f"            total += row.get(\"cents\", 0)\n"
            f"    return {{\"label\": label, \"total_cents\": total, \"count\": len(rows)}}\n"
        )
    return "\n\n".join(blocks)


FILES: Dict[str, str] = {
    "README.md": '''# tallybox

A small invoicing service: customers, invoices, reminders and a public pay-link page.

## Running

    pip install -r requirements.txt
    flask --app tallybox run

## Configuration

All settings are read from environment variables in `tallybox/config.py`:

| variable | default | what it does |
|---|---|---|
| DATABASE_URL | sqlite:///tallybox.db | where invoices live |
| RATE_LIMIT_PER_MIN | 60 | requests per minute per client |
| GRACE_DAYS | 7 | days after the due date before an invoice counts as overdue |
| SMTP_HOST | localhost | outgoing mail |
''',
    "requirements.txt": "flask==3.0.3\nsqlalchemy==2.0.30\nrequests==2.32.3\n",
    "tallybox/__init__.py": '''from flask import Flask

from tallybox.api import routes_admin, routes_customers, routes_invoices, routes_public, routes_webhooks
from tallybox.middleware.ratelimit import limit_requests


def create_app():
    app = Flask(__name__)
    app.before_request(limit_requests)
    for module in (routes_invoices, routes_customers, routes_admin, routes_public, routes_webhooks):
        app.register_blueprint(module.bp)
    return app
''',
    "tallybox/config.py": '''import os


def _int(name, default):
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


class Settings:
    database_url = os.environ.get("DATABASE_URL", "sqlite:///tallybox.db")
    rate_limit_per_min = _int("RATE_LIMIT_PER_MIN", 60)
    grace_days = _int("GRACE_DAYS", 7)
    smtp_host = os.environ.get("SMTP_HOST", "localhost")
    stripe_webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


settings = Settings()
''',
    "tallybox/auth.py": '''import hashlib
import hmac
from functools import wraps

from flask import abort, g, request

from tallybox.config import settings


def require_auth(role=None):
    """Rejects the request unless it carries a valid session. With role="admin", only admins get through."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = getattr(g, "user", None)
            if user is None:
                abort(401)
            if role and user.role != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    # Allows both @require_auth and @require_auth(role="admin").
    if callable(role):
        view, role = role, None
        return decorator(view)
    return decorator


def verify_signature(payload: bytes, header: str) -> bool:
    expected = hmac.new(settings.stripe_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
''',
    "tallybox/middleware/__init__.py": "",
    "tallybox/middleware/ratelimit.py": '''import os
import time
from collections import defaultdict

from flask import abort, request

_hits = defaultdict(list)


def limit_requests():
    # Per-client sliding window. Reads the limit straight from the environment.
    limit = int(os.environ.get("RATE_LIMIT_PER_MIN", 100))
    now = time.time()
    client = request.remote_addr or "unknown"
    window = [t for t in _hits[client] if now - t < 60]
    if len(window) >= limit:
        abort(429)
    window.append(now)
    _hits[client] = window
''',
    "tallybox/api/__init__.py": "",
    "tallybox/api/routes_invoices.py": '''from flask import Blueprint, jsonify, request

from tallybox.auth import require_auth
from tallybox.models.invoice import Invoice, find_invoice, list_invoices, new_invoice
from tallybox.services.email import send_email

bp = Blueprint("invoices", __name__)


@bp.route("/invoices", methods=["GET"])
@require_auth
def index():
    return jsonify([i.to_dict() for i in list_invoices()])


@bp.route("/invoices", methods=["POST"])
@require_auth
def create():
    invoice = new_invoice(request.get_json())
    return jsonify(invoice.to_dict()), 201


@bp.route("/invoices/<int:invoice_id>", methods=["GET"])
@require_auth
def show(invoice_id):
    return jsonify(find_invoice(invoice_id).to_dict())


@bp.route("/invoices/<int:invoice_id>/send", methods=["POST"])
@require_auth
def send(invoice_id):
    invoice = find_invoice(invoice_id)
    send_email(invoice.customer_email, "Your invoice", f"Invoice {invoice.id} is due {invoice.due_date}")
    invoice.status = "sent"
    return jsonify(invoice.to_dict())
''',
    "tallybox/api/routes_customers.py": '''from flask import Blueprint, jsonify, request

from tallybox.auth import require_auth
from tallybox.models.customer import find_customer, list_customers, new_customer

bp = Blueprint("customers", __name__)


@bp.route("/customers", methods=["GET"])
@require_auth
def index():
    return jsonify([c.to_dict() for c in list_customers()])


@bp.route("/customers", methods=["POST"])
@require_auth
def create():
    return jsonify(new_customer(request.get_json()).to_dict()), 201


@bp.route("/customers/<int:customer_id>", methods=["GET"])
@require_auth
def show(customer_id):
    return jsonify(find_customer(customer_id).to_dict())
''',
    "tallybox/api/routes_admin.py": '''from flask import Blueprint, jsonify

from tallybox.auth import require_auth
from tallybox.services.reports import rebuild_search_index, user_summary

bp = Blueprint("admin", __name__)


@bp.route("/admin/users", methods=["GET"])
@require_auth(role="admin")
def users():
    return jsonify(user_summary())


@bp.route("/admin/reindex", methods=["POST"])
@require_auth(role="admin")
def reindex():
    rebuild_search_index()
    return jsonify({"ok": True})
''',
    "tallybox/api/routes_public.py": '''from flask import Blueprint, jsonify

from tallybox.models.invoice import find_invoice_by_token

bp = Blueprint("public", __name__)


@bp.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@bp.route("/public/invoice/<token>", methods=["GET"])
def public_invoice(token):
    # The pay-link page: anyone holding the unguessable token can see that one invoice.
    invoice = find_invoice_by_token(token)
    return jsonify({"number": invoice.id, "amount_cents": invoice.amount_cents, "status": invoice.status})
''',
    "tallybox/api/routes_webhooks.py": '''from flask import Blueprint, abort, jsonify, request

from tallybox.auth import verify_signature
from tallybox.models.invoice import find_invoice

bp = Blueprint("webhooks", __name__)


@bp.route("/webhooks/stripe", methods=["POST"])
def stripe_event():
    # Not behind a session: Stripe calls this, so it proves itself with a signature instead.
    if not verify_signature(request.get_data(), request.headers.get("Stripe-Signature")):
        abort(400)
    event = request.get_json()
    if event.get("type") == "invoice.paid":
        find_invoice(event["data"]["invoice_id"]).status = "paid"
    return jsonify({"received": True})
''',
    "tallybox/models/__init__.py": "",
    "tallybox/models/invoice.py": '''from dataclasses import dataclass
from datetime import date

STATUSES = ("draft", "sent", "overdue", "paid", "void")


@dataclass
class Invoice:
    id: int
    customer_email: str
    amount_cents: int
    due_date: date
    status: str = "draft"
    token: str = ""

    def to_dict(self):
        return {"id": self.id, "amount_cents": self.amount_cents, "due_date": self.due_date.isoformat(), "status": self.status}


_INVOICES = {}


def list_invoices():
    return list(_INVOICES.values())


def find_invoice(invoice_id):
    return _INVOICES[invoice_id]


def find_invoice_by_token(token):
    return next(i for i in _INVOICES.values() if i.token == token)


def new_invoice(data):
    invoice = Invoice(id=len(_INVOICES) + 1, customer_email=data["email"], amount_cents=data["amount_cents"], due_date=date.fromisoformat(data["due"]))
    _INVOICES[invoice.id] = invoice
    return invoice
''',
    "tallybox/models/customer.py": '''from dataclasses import dataclass


@dataclass
class Customer:
    id: int
    name: str
    email: str

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email}


_CUSTOMERS = {}


def list_customers():
    return list(_CUSTOMERS.values())


def find_customer(customer_id):
    return _CUSTOMERS[customer_id]


def new_customer(data):
    customer = Customer(id=len(_CUSTOMERS) + 1, name=data["name"], email=data["email"])
    _CUSTOMERS[customer.id] = customer
    return customer
''',
    "tallybox/jobs/__init__.py": "",
    "tallybox/jobs/overdue.py": '''from datetime import timedelta

from tallybox.config import settings
from tallybox.models.invoice import list_invoices


def mark_overdue(today):
    """Flips a sent invoice to "overdue" once it is more than `grace_days` past its due date. Returns how many it changed."""
    changed = 0
    for invoice in list_invoices():
        if invoice.status != "sent":
            continue
        if invoice.due_date + timedelta(days=settings.grace_days) < today:
            invoice.status = "overdue"
            changed += 1
    return changed
''',
    "tallybox/jobs/scheduler.py": '''import time
from datetime import date

from tallybox.jobs.overdue import mark_overdue
from tallybox.jobs.reminders import send_reminders

TICK_SECONDS = 3600


def tick(today=None):
    today = today or date.today()
    flipped = mark_overdue(today)
    reminded = send_reminders(today)
    return {"overdue": flipped, "reminded": reminded}


def run_forever():
    while True:
        tick()
        time.sleep(TICK_SECONDS)
''',
    "tallybox/jobs/reminders.py": '''from tallybox.models.invoice import list_invoices
from tallybox.services.email import send_email


def send_reminders(today):
    sent = 0
    for invoice in list_invoices():
        if invoice.status == "overdue":
            send_email(invoice.customer_email, "Payment overdue", f"Invoice {invoice.id} is overdue.")
            sent += 1
    return sent
''',
    "tallybox/services/__init__.py": "",
    "tallybox/services/email.py": '''import smtplib
from email.message import EmailMessage

from tallybox.config import settings


def send_email(to, subject, body):
    message = EmailMessage()
    message["To"], message["Subject"] = to, subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host) as server:
        server.send_message(message)
''',
    "tallybox/services/reports.py": "from tallybox.models.invoice import list_invoices\n\n\ndef user_summary():\n    return {\"invoices\": len(list_invoices())}\n\n\ndef rebuild_search_index():\n    return len(list_invoices())\n\n\n"
    + _padding("revenue_by_kind", 24),
    "tallybox/util/__init__.py": "",
    "tallybox/util/money.py": "def cents_to_text(cents):\n    return f\"{cents / 100:.2f}\"\n\n\n" + _padding("money_total", 20),
    "tests/test_overdue.py": '''from datetime import date

from tallybox.jobs.overdue import mark_overdue
from tallybox.models.invoice import new_invoice


def test_a_sent_invoice_past_grace_becomes_overdue():
    invoice = new_invoice({"email": "a@example.com", "amount_cents": 1000, "due": "2026-09-01"})
    invoice.status = "sent"
    assert mark_overdue(date(2026, 9, 20)) == 1
    assert invoice.status == "overdue"
''',
}

COMMITS: List[Dict[str, Any]] = [
    {"sha": "9f3a1c2d4e5b6a7980c1d2e3f4a5b6c7d8e9f001", "commit": {"message": "Add grace period to the overdue job\n\nUses GRACE_DAYS."}},
    {"sha": "7b2e8d9c0a1f2e3d4c5b6a79880c1d2e3f4a5b02", "commit": {"message": "Fix duplicate reminder emails"}},
    {"sha": "5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c03", "commit": {"message": "Rate limit the public routes"}},
    {"sha": "3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a04", "commit": {"message": "Bump flask to 3.0.3"}},
]

PULL_REQUESTS: List[Dict[str, Any]] = [
    {"number": 42, "title": "Move rate limit config into settings", "state": "open", "merged_at": None, "user": {"login": "dana"}, "updated_at": "2026-10-08T10:00:00Z"},
    {"number": 41, "title": "Add grace period to the overdue job", "state": "closed", "merged_at": "2026-10-07T09:00:00Z", "user": {"login": "dana"}, "updated_at": "2026-10-07T09:00:00Z"},
]


def paths() -> List[str]:
    return sorted(FILES)


def materialize(into: Optional[str] = None) -> str:
    """Writes the repo to a directory (a fresh temp one by default) and returns it. The caller removes it with `shutil.rmtree`."""
    root = into or tempfile.mkdtemp(prefix="saapp_fixture_repo_")
    for path, text in FILES.items():
        target = os.path.join(root, *path.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    return root


def remove(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)


def search_code(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """What GitHub's code search returns for `query`: files containing every word of it, each with the matching lines as fragments."""
    words = [w for w in (query or "").split() if ":" not in w]
    items = []
    for path in paths():
        text = FILES[path]
        lowered = text.lower()
        if not words or not all(w.lower() in lowered for w in words):
            continue
        fragment = "\n".join(line for line in text.splitlines() if any(w.lower() in line.lower() for w in words))
        items.append({"path": path, "text_matches": [{"fragment": fragment}]})
        if len(items) >= limit:
            break
    return items


def fake_github_get(url: str, params: Optional[dict] = None) -> Optional[Any]:
    """The JSON GitHub would return for the read-only endpoints the repo tools use, or None for any other URL."""
    if url.endswith("/search/code"):
        return {"items": search_code((params or {}).get("q", ""))}
    if url.endswith("/commits"):
        return COMMITS[: int((params or {}).get("per_page", 10))]
    if url.endswith("/pulls"):
        wanted = (params or {}).get("state", "all")
        return [pr for pr in PULL_REQUESTS if wanted == "all" or pr["state"] == wanted][: int((params or {}).get("per_page", 3))]
    return None
