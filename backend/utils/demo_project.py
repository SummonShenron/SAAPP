"""The demo project the shared guest can watch Sonic work on (docs/guest-mode-plan.md, step 3).

A guest has no folder, so a guest turn that starts from a demo card reads and tests a small made-up project held on the server instead: "tallybox", an invoicing core written with the
standard library only, so `run_sandbox_tests` can run its tests. It is DERIVED from the eval's fixture (backend/evals/fixture_repo.py, which stays frozen; its answers depend on it): same
name and same settings story, but pure logic, because the sandbox has no Flask.

It sits in the same place a signed-in person's folder copy does, under a server-owned name no account can have (`OWNER`), written by the server and never by an upload. Nothing a guest
does can change it: a proposed edit is shown as a diff card whose Apply is replaced by the sign-up button, and the sandbox runs on a copy.

Three planted things to find, each with a card on the welcome screen (local/src/welcomeCards.ts):
- An invoice goes overdue a day late: tallybox/overdue.py uses `>` where the README (and tests/test_overdue.py::test_overdue_on_the_grace_day, which fails) say the grace day itself counts.
- The report has no late-fee line (tallybox/reports.py), a small feature to add with a test.
- RATE_LIMIT_PER_MIN is set in two places with different defaults: tallybox/config.py says 60 and nothing reads it; tallybox/ratelimit.py says 100 and is what runs.

The text of the files is data: a test (backend/tests/test_demo_project.py) runs the project's own tests and pins the three facts, so editing it cannot silently break a card.
"""
import os
from typing import Dict, Optional

# Who the demo belongs to in the folder store. It is not a guest name and not a value an account can have (a space and brackets), so local_workspace treats it like any other person's
# folder copy, and an upload or a sign-in can never reach it.
OWNER = "demo project (server copy)"
NAME = "tallybox (demo project)"
GUEST = "guest-recruiter@example.com"          # the shared portfolio guest; the embedded widget's guest (a client's deployment) does not get a demo


def enabled() -> bool:
    """On unless GUEST_DEMO_PROJECT is set to 0 / off / false (a switch to turn it off without a deploy)."""
    return os.getenv("GUEST_DEMO_PROJECT", "1").strip().lower() not in {"0", "off", "false", "no"}


def applies(username: Optional[str], requested: object, has_folder: bool = False) -> bool:
    """Whether this turn works on the demo: the page said the person is on a demo card and the demo is switched on, AND either they are the shared guest, or they are a signed-in person with NO folder of their own
    (a new account trying it before connecting a project: their own folder always wins, so it is never replaced). The embedded view never gets it."""
    if not (bool(requested) and enabled()):
        return False
    name = (username or "").strip().lower()
    if name == GUEST:
        return True
    from backend.services.local_workspace import is_guest_username

    return bool(name) and name != OWNER and not is_guest_username(name) and not has_folder


FILES: Dict[str, str] = {
    "README.md": '''# tallybox (demo project)

A small invoicing core: invoices, an overdue check, a report and a rate limit. Made up for the Sonic demo; plain Python, standard library only.

## Running the tests

    python -m unittest discover -s tests

## Behaviour

- An invoice is **overdue once it is GRACE_DAYS days past its due date**: with the default of 7, an invoice due on the 1st is overdue on the 8th.
- `reports.render` prints the open and overdue totals.
- Requests are limited per client per minute.

## Configuration

Settings come from environment variables:

| variable | default | what it does |
|---|---|---|
| GRACE_DAYS | 7 | days past the due date before an invoice counts as overdue |
| RATE_LIMIT_PER_MIN | 60 | requests per minute per client |
''',
    "tallybox/__init__.py": '''"""tallybox: a small invoicing core."""
''',
    "tallybox/config.py": '''import os


def _int(name, default):
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


class Settings:
    grace_days = _int("GRACE_DAYS", 7)
    rate_limit_per_min = _int("RATE_LIMIT_PER_MIN", 60)
    currency = os.environ.get("CURRENCY", "USD")


settings = Settings()
''',
    "tallybox/money.py": '''def format_cents(cents):
    """12345 -> "$123.45". Negative amounts keep their sign in front of the dollar sign."""
    sign = "-" if cents < 0 else ""
    dollars, remainder = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{remainder:02d}"
''',
    "tallybox/invoices.py": '''from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Invoice:
    number: str
    customer: str
    cents: int
    due: date
    paid_on: Optional[date] = None
    status: str = "open"          # open, overdue or paid

    def pay(self, when):
        self.paid_on = when
        self.status = "paid"
''',
    "tallybox/overdue.py": '''from tallybox.config import settings


def days_past_due(invoice, today):
    return (today - invoice.due).days


def is_overdue(invoice, today, grace_days=None):
    """True once an unpaid invoice is `grace_days` days past its due date."""
    grace = settings.grace_days if grace_days is None else grace_days
    if invoice.paid_on is not None:
        return False
    return days_past_due(invoice, today) > grace


def mark_overdue(invoices, today):
    """Sets status to "overdue" on every unpaid invoice that is, and returns the ones it changed."""
    changed = []
    for invoice in invoices:
        if invoice.status == "open" and is_overdue(invoice, today):
            invoice.status = "overdue"
            changed.append(invoice)
    return changed
''',
    "tallybox/reports.py": '''from tallybox.money import format_cents
from tallybox.overdue import is_overdue


def summary(invoices, today):
    """Counts and totals (in cents) for the unpaid invoices."""
    unpaid = [invoice for invoice in invoices if invoice.paid_on is None]
    overdue = [invoice for invoice in unpaid if is_overdue(invoice, today)]
    return {
        "open_count": len(unpaid),
        "open_cents": sum(invoice.cents for invoice in unpaid),
        "overdue_count": len(overdue),
        "overdue_cents": sum(invoice.cents for invoice in overdue),
    }


def render(invoices, today):
    """The report as text, one line per figure."""
    figures = summary(invoices, today)
    return "\\n".join([
        f"Open invoices: {figures['open_count']} ({format_cents(figures['open_cents'])})",
        f"Overdue: {figures['overdue_count']} ({format_cents(figures['overdue_cents'])})",
    ])
''',
    "tallybox/ratelimit.py": '''import os

DEFAULT_PER_MIN = 100


def limit_per_minute():
    raw = os.environ.get("RATE_LIMIT_PER_MIN")
    return int(raw) if raw else DEFAULT_PER_MIN


def allow(requests_this_minute):
    """True while a client is under the limit for the current minute."""
    return requests_this_minute < limit_per_minute()
''',
    "tests/test_overdue.py": '''import unittest
from datetime import date

from tallybox.invoices import Invoice
from tallybox.overdue import is_overdue, mark_overdue


def invoice(**kwargs):
    return Invoice(number="T-1", customer="Acme", cents=4000, due=date(2026, 3, 1), **kwargs)


class OverdueTests(unittest.TestCase):
    def test_not_overdue_before_the_grace_days_are_up(self):
        self.assertFalse(is_overdue(invoice(), date(2026, 3, 7), grace_days=7))

    def test_overdue_on_the_grace_day(self):
        # The README: due on the 1st with 7 grace days means overdue on the 8th.
        self.assertTrue(is_overdue(invoice(), date(2026, 3, 8), grace_days=7))

    def test_overdue_after_the_grace_day(self):
        self.assertTrue(is_overdue(invoice(), date(2026, 3, 20), grace_days=7))

    def test_a_paid_invoice_is_never_overdue(self):
        self.assertFalse(is_overdue(invoice(paid_on=date(2026, 3, 2)), date(2026, 4, 1), grace_days=7))

    def test_mark_overdue_changes_only_the_late_ones(self):
        late, early = invoice(), Invoice(number="T-2", customer="Bo", cents=900, due=date(2026, 3, 30))
        changed = mark_overdue([late, early], date(2026, 4, 5))
        self.assertEqual([item.number for item in changed], ["T-1"])
        self.assertEqual((late.status, early.status), ("overdue", "open"))


if __name__ == "__main__":
    unittest.main()
''',
    "tests/test_reports.py": '''import unittest
from datetime import date

from tallybox.invoices import Invoice
from tallybox.money import format_cents
from tallybox.reports import render, summary


INVOICES = [
    Invoice(number="T-1", customer="Acme", cents=4000, due=date(2026, 3, 1)),
    Invoice(number="T-2", customer="Bo", cents=900, due=date(2026, 3, 30)),
    Invoice(number="T-3", customer="Cy", cents=1250, due=date(2026, 2, 1), paid_on=date(2026, 2, 3)),
]


class ReportTests(unittest.TestCase):
    def test_summary_counts_only_unpaid_invoices(self):
        figures = summary(INVOICES, date(2026, 4, 5))
        self.assertEqual((figures["open_count"], figures["open_cents"]), (2, 4900))
        self.assertEqual((figures["overdue_count"], figures["overdue_cents"]), (1, 4000))        # T-2 is only 6 days past due

    def test_render_has_a_line_per_figure(self):
        lines = render(INVOICES, date(2026, 3, 5)).splitlines()
        self.assertEqual(lines[0], "Open invoices: 2 ($49.00)")
        self.assertEqual(lines[1], "Overdue: 0 ($0.00)")

    def test_format_cents(self):
        self.assertEqual(format_cents(5), "$0.05")
        self.assertEqual(format_cents(123456), "$1,234.56")
        self.assertEqual(format_cents(-250), "-$2.50")


if __name__ == "__main__":
    unittest.main()
''',
    "tests/test_ratelimit.py": '''import os
import unittest
from unittest import mock

from tallybox import ratelimit


class RateLimitTests(unittest.TestCase):
    def test_a_client_under_the_limit_is_allowed(self):
        with mock.patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "10"}):
            self.assertTrue(ratelimit.allow(9))

    def test_a_client_at_the_limit_is_refused(self):
        with mock.patch.dict(os.environ, {"RATE_LIMIT_PER_MIN": "10"}):
            self.assertFalse(ratelimit.allow(10))


if __name__ == "__main__":
    unittest.main()
''',
}
