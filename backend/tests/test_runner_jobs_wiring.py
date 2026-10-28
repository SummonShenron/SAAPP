"""app.py's local-runner endpoints are thin wrappers over backend/utils/runner_jobs.py and each has the right kind of sign-in (the handling itself is tested in test_runner_jobs.py and, over real HTTP,
in test_runner_end_to_end.py)."""
import pathlib

import pytest

SOURCE = (pathlib.Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8").replace("\r\n", "\n")


def block(route: str, length: int = 900) -> str:
    """The text of one definition: from `route` to the next route or helper, so one endpoint's checks never see its neighbour's code."""
    start = SOURCE.index(route)
    text = SOURCE[start:start + length]
    end = min([i for i in (text.find("\n@app.", 1), text.find("\n\n\n", 1)) if i > 0] or [len(text)])
    return text[:end]


RUNNER_SIDE = ['@app.post("/api/runner/hello")', '@app.get("/api/runner/next")', '@app.post("/api/runner/jobs/{job_id}/heartbeat")', '@app.post("/api/runner/jobs/{job_id}/result")']
PAGE_SIDE = ['@app.get("/api/runner/runners")', '@app.post("/api/runner/pairings")', '@app.delete("/api/runner/keys/{key_id}")', '@app.get("/api/runner/jobs/{job_id}")',
             '@app.post("/api/runner/jobs/{job_id}/approve")', '@app.post("/api/runner/jobs/{job_id}/cancel")']


@pytest.mark.parametrize("route", RUNNER_SIDE)
def test_the_runners_own_calls_are_authenticated_by_its_key_and_never_by_a_person(route):
    text = block(route)
    assert "Depends(get_runner_key)" in text and "get_current_user" not in text


@pytest.mark.parametrize("route", PAGE_SIDE)
def test_the_pages_calls_need_a_signed_in_non_guest_person(route):
    text = block(route)
    assert "Depends(get_current_user)" in text and "_runner_person(current_user)" in text and "get_runner_key" not in text


def test_pairing_is_the_one_call_with_no_sign_in_because_the_single_use_code_is_the_proof():
    text = block('@app.post("/api/runner/pair")', 400)
    assert "Depends" not in text and "runner_jobs.api_pair" in text


def test_guests_cannot_have_a_runner_and_a_bad_key_is_a_401():
    helper = block("def _runner_person", 420)
    assert "is_guest_username(username)" in helper and "status_code=403" in helper
    key = block("async def get_runner_key", 700)
    assert "runner_jobs.authenticate" in key and "status_code=401" in key


def test_the_wait_for_a_job_is_bounded_and_errors_map_through_the_one_status_table():
    nxt = block('@app.get("/api/runner/next")', 800)
    assert "le=30" in nxt and "runner_jobs.api_next" in nxt and "asyncio.sleep(1.0)" in nxt
    assert "runner_jobs.STATUS_FOR" in block("def _runner_http_error", 300)


def test_every_handler_does_its_database_work_off_the_event_loop():
    runner_section = SOURCE[SOURCE.index("class RunnerPairingRequest"):SOURCE.index('@app.get("/api/memory")')]
    assert runner_section.count("asyncio.to_thread(") >= 12 and "runner_jobs." in runner_section
