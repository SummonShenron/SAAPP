"""One-off: give every existing directory user a personal knowledge base.

New users get one when /api/me first creates their record; accounts that already existed never did.
    python -m backend.utils.personal_kb_backfill            # shows what would change, writes nothing
    python -m backend.utils.personal_kb_backfill --apply    # does it

Safe to re-run: see isolation_kb_utils.personal_kb_update. Accounts it cannot provision yet (no
clerk_id) are listed and get theirs automatically at their next login.
"""
import sys

from dotenv import load_dotenv

load_dotenv()

from backend.utils.db_utils import get_db  # noqa: E402  (after load_dotenv so the connection string is set)
from backend.utils.isolation_kb_utils import provision_personal_kbs  # noqa: E402


def main(argv) -> int:
    apply = "--apply" in argv
    db = get_db()
    if db is None:
        print("No database is configured, so there is nothing to provision.")
        return 1
    report = provision_personal_kbs(db, apply=apply)
    verb = "Provisioned" if apply else "Would provision"
    print(f"{verb} {len(report['provisioned'])}: {', '.join(report['provisioned']) or '-'}")
    print(f"Already had one: {len(report['already'])}")
    for label, reason in report["skipped"]:
        print(f"Skipped {label}: {reason}")
    if not apply and report["provisioned"]:
        print("\nNothing was written. Re-run with --apply to make these changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
