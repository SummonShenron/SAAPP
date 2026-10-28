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
PAGE_SIDE = ['@app.get("/api/runner/runners")', '@app.post("/api/runner/runners/{runner_id}/settings")', '@app.post("/api/runner/runners/{runner_id}/projects")',
             '@app.delete("/api/runner/runners/{runner_id}/projects/{project_id}")', '@app.post("/api/runner/pairings")', '@app.delete("/api/runner/keys/{key_id}")', '@app.get("/api/runner/jobs/{job_id}")',
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


def test_the_pairing_answer_carries_the_starter_scripts_only_when_an_install_address_is_configured():
    text = block('@app.post("/api/runner/pairings")', 2600)
    assert "runner_scripts.install_address()" in text and "SAAPP_RUNNER_INSTALL_URL" not in text and "runner_scripts.starter_scripts(" in text and "runner_scripts.terminal_commands(" in text
    assert "runner_scripts.public_server_url(" in text and "x-forwarded-proto" in text and '"install_problem"' in text and "except ValueError" in text
    assert '"scripts": None' in text and "body.folder_name" in text


def test_the_runner_list_tells_the_page_whether_an_install_address_is_set():
    text = block('@app.get("/api/runner/runners")', 900)
    assert '"install": {"configured"' in text and "runner_scripts.RUNNER_VERSION" in text


def test_the_pairing_code_in_the_answer_is_the_one_just_made_and_no_key_is_ever_in_it():
    text = block('@app.post("/api/runner/pairings")', 2600)
    assert "**made" in text and "authenticate" not in text and "exchange_pairing" not in text


def test_the_settings_call_takes_check_names_and_a_yes_or_no_and_nothing_that_could_be_run():
    model = block("class RunnerSettingsRequest", 800)
    fields = [line.split(":")[0].strip() for line in model.splitlines()[1:] if ":" in line and not line.strip().startswith("#")]
    assert fields == ["project_id", "disabled", "fake_keys", "auto_update", "read_only_auto"] and "project_id: str" in model and "disabled: List[str]" in model and "fake_keys: bool" in model and "auto_update: Optional[bool]" in model and "read_only_auto: Optional[bool]" in model and "exclude_none=True" in block('@app.post("/api/runner/runners/{runner_id}/settings")', 900)
    text = block('@app.post("/api/runner/runners/{runner_id}/settings")', 900)
    assert "runner_jobs.update_settings" in text and "asyncio.to_thread(" in text and "status_code=404" in text


def test_the_update_call_needs_a_signed_in_person_names_no_address_and_the_next_poll_ends_early_for_an_update():
    text = block('@app.post("/api/runner/runners/{runner_id}/update")', 1100)
    assert "Depends(get_current_user)" in text and "_runner_person(current_user)" in text and "runner_jobs.request_update" in text and "asyncio.to_thread(" in text and "status_code=404" in text
    assert "https://" not in text and "github" not in text.lower() and "install_url" not in text and ".zip" not in text      # nothing here names where to install from
    nxt = block('@app.get("/api/runner/next")', 900)
    assert 'answer["job"] or answer["update"]' in nxt


def test_a_project_is_requested_by_name_only_and_removed_by_id_and_the_next_poll_ends_early_for_either():
    model = block("class RunnerProjectRequest", 400)
    assert "name: str" in model and "path" not in model.split("#")[0].replace("never a path", "")                      # a folder NAME: the person chooses the folder on their own computer
    add = block('@app.post("/api/runner/runners/{runner_id}/projects")', 900)
    assert "runner_jobs.request_project" in add and "asyncio.to_thread(" in add and "status_code=404" in add and "body.name" in add
    remove = block('@app.delete("/api/runner/runners/{runner_id}/projects/{project_id}")', 900)
    assert "runner_jobs.request_remove_project" in remove and "status_code=404" in remove
    assert 'answer["add_project"] or answer["remove_project"]' in block('@app.get("/api/runner/next")', 900)
    pairing = block('@app.post("/api/runner/pairings")', 2600)
    assert "folder_name=folder_name" in pairing and "body.folder_name or" in pairing and "local_workspace_status(username)" in pairing                                                                     # the folder connected in chat becomes the first project the runner is asked to add
