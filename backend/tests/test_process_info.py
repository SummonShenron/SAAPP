"""Which server process answered, and the warning when the environment asks for more than one (backend/utils/process_info.py; docs/public-launch-readiness.md item 3)."""
import os
import pathlib
from datetime import datetime, timedelta, timezone

from backend.utils import process_info as pi

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_one_worker_is_the_default_and_every_way_of_asking_for_more_is_seen():
    assert pi.configured_workers({}) == 1
    assert pi.configured_workers({"WEB_CONCURRENCY": "4"}) == 4
    assert pi.configured_workers({"UVICORN_WORKERS": "2"}) == 2
    assert pi.configured_workers({"GUNICORN_WORKERS": "3", "WEB_CONCURRENCY": "2"}) == 3          # the largest
    assert pi.configured_workers({"GUNICORN_CMD_ARGS": "--bind 0.0.0.0:10000 --workers 5"}) == 5
    assert pi.configured_workers({"GUNICORN_CMD_ARGS": "-w 2"}) == 2


def test_nonsense_zero_or_negative_values_read_as_one():
    for value in ("", "many", "0", "-3", "  "):
        assert pi.configured_workers({"WEB_CONCURRENCY": value}) == 1, value


def test_the_warning_appears_only_above_one_worker_and_says_what_breaks_and_what_to_do():
    assert pi.single_process_warning({}) is None and pi.single_process_warning({"WEB_CONCURRENCY": "1"}) is None
    text = pi.single_process_warning({"WEB_CONCURRENCY": "3"})
    assert "3 worker processes" in text and "wait for Apply" in text and "Run one worker" in text


def test_the_health_fields_name_the_process_and_how_long_it_has_been_up():
    fields = pi.health_fields(pi.STARTED_AT + timedelta(seconds=90, milliseconds=400))
    assert fields["pid"] == os.getpid() and fields["uptime_seconds"] == 90
    assert fields["started_at"] == pi.STARTED_AT.isoformat() and fields["workers_configured"] >= 1
    assert pi.health_fields(pi.STARTED_AT - timedelta(seconds=5))["uptime_seconds"] == 0       # never negative
    assert datetime.fromisoformat(fields["started_at"]).tzinfo is not None


def test_nothing_in_the_health_fields_is_about_a_person():
    assert set(pi.health_fields()) == {"pid", "started_at", "uptime_seconds", "workers_configured"}


def test_the_server_warns_at_start_up_and_the_health_check_carries_the_fields():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "single_process_notice = process_info.single_process_warning()" in source and "logger.warning(single_process_notice)" in source
    assert "**process_info.health_fields()," in source
