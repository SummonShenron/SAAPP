"""Importing app.py in a test without a database (backend/tests/import_app.py): the stand-in has the real startup's keys, the helper hands every test file the same module, and a fresh process with the database
off and no cluster to reach imports the app without trying to connect (the CI failure of 2026-11-19: "HTTPException 500: Database connection unavailable" at collection)."""
import os
import re
import subprocess
import sys
from pathlib import Path

from backend.tests import import_app as helper

ROOT = Path(__file__).resolve().parents[2]


def test_the_stand_in_has_the_same_keys_as_the_real_startup_and_nothing_behind_them():
    source = (ROOT / "backend" / "services" / "orchestrator.py").read_text(encoding="utf-8")
    returned = set(re.findall(r'^\s+"(\w+)":', source[source.rindex("return {"):], re.MULTILINE))
    assert returned and set(helper.no_services()) == returned, "a key added to startup_services must be added to the stand-in"
    assert all(not value for value in helper.no_services().values())
    assert helper.no_services() is not helper.no_services(), "a fresh dict each time: a test that fills one in does not fill the next"


def test_every_test_file_gets_the_same_app_module_and_its_services_are_the_stand_in():
    app_module = helper.import_app()
    assert helper.import_app() is app_module is sys.modules["app"]
    assert set(app_module.services) == set(helper.no_services()) and not any(app_module.services.values())


def test_a_fresh_process_with_the_database_off_imports_the_app_without_trying_to_connect():
    env = {**os.environ, "USE_DB": "false", "MONGO_URI": "mongodb://127.0.0.1:1", "GOOGLE_API_KEY": "test-not-real"}
    code = "from backend.tests.import_app import import_app; import sys; m = import_app(); print('IMPORTED', sorted(m.services))"
    done = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-1500:]
    assert "IMPORTED" in done.stdout and "Database connection unavailable" not in done.stderr + done.stdout
