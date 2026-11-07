"""The guest's demo project (backend/utils/demo_project.py; docs/guest-mode-plan.md step 3)."""
import ast
import os
import re
import subprocess
import sys
import typing

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_workspace as lw
from backend.services import python_sandbox, sandbox_tests
from backend.state.graph_state import GraphState
from backend.tests.test_edit_hardening import _offered, _run_turn, run_async
from backend.utils import demo_project as dp

GUEST = "guest-recruiter@example.com"
FIXED_OVERDUE = dp.FILES["tallybox/overdue.py"].replace("days_past_due(invoice, today) > grace", "days_past_due(invoice, today) >= grace")


def run_unittest(root):
    """The project's own tests, on the host Python, in a directory written from the pack. Returns the unittest output."""
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=str(root), capture_output=True, text=True, timeout=120)
    return done.returncode, done.stderr


def write_pack(root, overrides=None):
    for path, text in {**dp.FILES, **(overrides or {})}.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


# --- the pack itself: three planted facts the welcome cards rely on -------------------------------------------------------------------------------------------------------------

def test_the_demo_has_exactly_one_failing_test_and_it_is_the_off_by_one_the_first_card_asks_about(tmp_path):
    write_pack(tmp_path)
    code, output = run_unittest(tmp_path)
    assert code != 0
    assert "FAIL: test_overdue_on_the_grace_day" in output
    assert output.count("FAIL:") + output.count("ERROR:") == 1
    assert "Ran 10 tests" in output


def test_changing_the_one_comparison_makes_every_test_pass(tmp_path):
    assert FIXED_OVERDUE != dp.FILES["tallybox/overdue.py"]
    write_pack(tmp_path, {"tallybox/overdue.py": FIXED_OVERDUE})
    code, output = run_unittest(tmp_path)
    assert code == 0, output


def test_the_rate_limit_is_set_in_two_places_and_only_one_of_them_is_read():
    assert "_int(\"RATE_LIMIT_PER_MIN\", 60)" in dp.FILES["tallybox/config.py"]
    assert "DEFAULT_PER_MIN = 100" in dp.FILES["tallybox/ratelimit.py"]
    readers = [path for path, text in dp.FILES.items() if "rate_limit_per_min" in text]
    assert readers == ["tallybox/config.py"]                   # the 60 is defined and never used; ratelimit.py reads the environment itself, with its own default


def test_the_report_has_no_late_fee_yet_so_the_second_card_has_something_to_add():
    assert "late" not in dp.FILES["tallybox/reports.py"].lower() or "late_fee" not in dp.FILES["tallybox/reports.py"]
    assert "fee" not in dp.FILES["tallybox/reports.py"].lower()


def test_the_pack_is_standard_library_only_so_the_sandbox_can_run_it():
    stdlib = set(sys.stdlib_module_names)
    for path, text in dp.FILES.items():
        if not path.endswith(".py"):
            continue
        for node in ast.walk(ast.parse(text)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            for name in names:
                top = (name or "").split(".")[0]
                assert top in stdlib or top == "tallybox", (path, name)


def test_no_file_in_the_pack_looks_like_a_secret_or_a_real_address():
    for path, text in dp.FILES.items():
        assert lw.path_rejection(path) is None, path
        assert not re.search(r"(?i)api[_-]?key|password|secret|token|@[a-z0-9-]+\.(com|org|net)", text), path


# --- who gets it ---------------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_guest_and_a_signed_in_person_with_no_folder_get_the_demo_when_they_are_on_a_demo_card():
    assert dp.applies(GUEST, True)
    assert dp.applies(GUEST.upper(), True)
    assert not dp.applies(GUEST, False) and not dp.applies(GUEST, None)       # a recruiter-lane question keeps reading Sonic's own repository
    assert dp.applies("jack@example.com", True) and not dp.applies("jack@example.com", False)         # a new account trying it before it has connected a project
    assert not dp.applies("jack@example.com", True, has_folder=True)                                   # their own folder always wins: it is never replaced by the demo
    for other in ("guest_bty", "guest", "", None, dp.OWNER):
        assert not dp.applies(other, True), other                                                       # the embedded view, the other shared names, nobody, and the demo's own owner name


def test_the_switch_turns_the_demo_off_without_a_deploy(monkeypatch):
    monkeypatch.setenv("GUEST_DEMO_PROJECT", "0")
    assert not dp.enabled() and not dp.applies(GUEST, True)
    assert lw.workspace_status(dp.OWNER) == {"connected": False}
    assert lw.get_workspace_handle(dp.OWNER) is None


# --- the server's copy ---------------------------------------------------------------------------------------------------------------------------------------------------------

def test_the_server_copy_is_written_on_first_use_and_looks_like_a_connected_folder():
    status = lw.workspace_status(dp.OWNER)
    assert status["connected"] and status["name"] == dp.NAME and status["file_count"] == len(dp.FILES)
    handle = lw.get_workspace_handle(dp.OWNER)
    assert (os.path.join(handle.root, "tallybox", "overdue.py")) and open(os.path.join(handle.root, "tallybox", "overdue.py"), encoding="utf-8").read() == dp.FILES["tallybox/overdue.py"]
    assert lw.snapshot_paths(dp.OWNER) == sorted(dp.FILES)


def test_a_changed_or_deleted_file_is_put_back_on_the_next_use():
    root = lw.get_workspace_handle(dp.OWNER).root
    with open(os.path.join(root, "tallybox", "overdue.py"), "w", encoding="utf-8") as handle:
        handle.write("# vandalised\n")
    os.remove(os.path.join(root, "README.md"))
    lw.workspace_status(dp.OWNER)
    assert open(os.path.join(root, "tallybox", "overdue.py"), encoding="utf-8").read() == dp.FILES["tallybox/overdue.py"]
    assert os.path.isfile(os.path.join(root, "README.md"))


def test_nobody_can_upload_into_it_and_the_guest_still_has_no_folder_of_their_own():
    with pytest.raises(PermissionError):
        lw.apply_sync(dp.OWNER, "x", [{"path": "a.py", "content": "x"}], [], reset=True)
    assert lw.workspace_status(GUEST) == {"connected": False}
    assert lw.get_workspace_handle(GUEST) is None


# --- the turn ------------------------------------------------------------------------------------------------------------------------------------------------------------------

@run_async
async def test_a_demo_turn_reads_the_demo_and_is_offered_the_edit_and_test_tools_without_a_folder_in_the_browser(monkeypatch):
    _, captured = await _run_turn(monkeypatch, "Why do invoices go overdue a day late?", attempts=[], extra_state={"username": GUEST, "demo_project": True, "folder_in_browser": False})
    assert {"propose_local_edits", "run_sandbox_tests", "read_repo_file"} <= _offered(captured)
    assert "propose_commit" not in _offered(captured) and "propose_git_commit" not in _offered(captured)
    note = captured["environment_note"]
    assert "DEMO PROJECT" in note and dp.NAME in note and "THIS BROWSER HAS NO FOLDER CONNECTED" not in note


@run_async
async def test_a_demo_edit_request_is_forced_to_end_in_a_proposal(monkeypatch):
    _, captured = await _run_turn(monkeypatch, "Add a late-fee line to the report and show me the tests pass", attempts=[], extra_state={"username": GUEST, "demo_project": True, "folder_in_browser": False})
    assert captured["required_action"] is aw._LOCAL_EDIT_REQUIRED_ACTION


@run_async
async def test_the_same_guest_without_a_demo_card_reads_github_and_gets_no_edit_tools(monkeypatch):
    _, captured = await _run_turn(monkeypatch, "Where in the code does Sonic decide which documents a person may read?", attempts=[], extra_state={"username": GUEST, "folder_in_browser": False})
    assert "propose_local_edits" not in _offered(captured) and "run_sandbox_tests" not in _offered(captured)
    assert "DEMO PROJECT" not in captured["environment_note"]


@run_async
async def test_a_signed_in_person_with_no_folder_gets_the_demo_and_is_told_the_way_forward_is_to_connect_their_own(monkeypatch):
    _, captured = await _run_turn(monkeypatch, "Why do invoices go overdue a day late?", attempts=[], extra_state={"username": "jack", "demo_project": True})
    note = captured["environment_note"]
    assert "DEMO PROJECT" in note and "connect their own project" in note and "sign up" not in note.split("DEMO PROJECT")[1][:900]
    assert {"propose_local_edits", "run_sandbox_tests"} <= _offered(captured)


@run_async
async def test_a_signed_in_person_who_has_their_own_folder_never_gets_the_demo_in_its_place(monkeypatch):
    from backend.tests.test_local_edits import _connect_folder

    _connect_folder()
    _, captured = await _run_turn(monkeypatch, "Why do invoices go overdue a day late?", attempts=[], extra_state={"username": "jack", "demo_project": True})
    assert "DEMO PROJECT" not in captured["environment_note"]
    assert "tallybox" not in captured["environment_note"]


@run_async
async def test_the_embedded_view_cannot_ask_for_the_demo(monkeypatch):
    _, captured = await _run_turn(monkeypatch, "Why do invoices go overdue a day late?", attempts=[], extra_state={"username": "guest_bty", "demo_project": True})
    assert "DEMO PROJECT" not in captured["environment_note"]


@run_async
async def test_the_diff_card_of_a_demo_turn_says_so_and_no_turn_waits_for_an_apply(monkeypatch):
    """Drives the real propose_local_edits through the loop's dispatcher: the event carries demo=True and awaiting_decision=False even when the page asked to report decisions."""
    events = []

    async def capture_event(name, payload=None, *a, **k):
        events.append((name, payload))

    from backend.tests.test_tool_agent_node import _setup_github_repo
    from backend.services import repo_checkout as rc

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "safe_emit_event", capture_event)
    holder = {}

    async def fake_native_run(**kwargs):
        holder.update(kwargs)
        edit = {"path": "tallybox/overdue.py", "old_string": "days_past_due(invoice, today) > grace", "new_string": "days_past_due(invoice, today) >= grace"}
        holder["observation"] = await kwargs["act"]({"tool_action": "propose_local_edits", "args": {"summary": "count the grace day", "edits": [edit]}})
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    from langchain_core.messages import HumanMessage

    state = {"username": GUEST, "demo_project": True, "folder_in_browser": False, "edit_decisions": True, "documents": [], "messages": [HumanMessage(content="Why do invoices go overdue a day late? Fix it.")]}
    await aw.tool_agent_node(state)
    proposals = [payload for name, payload in events if name == "local_edit_proposal"]
    assert holder["observation"].startswith("Proposed 1 edit(s) across 1 file(s): tallybox/overdue.py"), holder["observation"]
    assert len(proposals) == 1
    assert proposals[0]["demo"] is True and proposals[0]["awaiting_decision"] is False and proposals[0]["folder"] == dp.NAME


def test_the_event_and_the_waiting_rule_are_written_the_way_the_page_expects():
    source = open("backend/services/agent_workflow.py", encoding="utf-8").read()
    assert '"demo": demo_turn,' in source
    assert "local_edit_decisions.enabled() and not demo_turn" in source


# --- the state the page's flags travel in --------------------------------------------------------------------------------------------------------------------------------------

def test_every_key_the_chat_endpoint_puts_in_the_graph_state_is_declared_in_it():
    """LangGraph drops an input key the state does not declare, silently, and tests that call a node directly cannot see it: `folder_in_browser` was dropped this way, so a phone was never told it had no folder."""
    declared = set(typing.get_type_hints(GraphState))
    tree = ast.parse(open("app.py", encoding="utf-8").read())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "initial_state" and isinstance(node.value, ast.Dict):
            found = True
            keys = {key.value for key in node.value.keys if isinstance(key, ast.Constant)}
            assert {"demo_project", "folder_in_browser"} <= keys
            assert keys - declared <= {"force_web_search"}, sorted(keys - declared)           # force_web_search is set and never read (the rest of the keys all arrive)
    assert found


def test_the_request_field_exists_and_the_turn_uses_the_demo_owner_for_the_folder_status():
    source = open("app.py", encoding="utf-8").read()
    assert "demo_project: Optional[bool] = None" in source
    assert "demo_turn = demo_project.applies(username, request.demo_project, has_folder=has_own_folder)" in source
    assert 'has_own_folder = bool(local_workspace_status(username).get("connected")) if request.demo_project else False' in source
    assert "if folder_usable_here and not is_guest_username(username) and not demo_turn:\n        plan_turn" in source          # plan mode never runs on the demo project
    assert "local_workspace_status(demo_project.OWNER if demo_turn else username)" in source
    assert '"demo_project": demo_turn,' in source


# --- the sandbox: the claim the cards make is that the tests really run with the change in place ------------------------------------------------------------------------------

@pytest.mark.skipif(not python_sandbox.SANDBOX_WASM_PATH.exists(), reason="the sandbox interpreter is not downloaded (backend/sandbox/README.md)")
@run_async
async def test_the_sandbox_shows_the_failing_test_and_then_the_pass_with_the_fix_in_place():
    before = await sandbox_tests.run_tests(dp.OWNER, ["tests/test_overdue.py"], None)
    assert before["failures"] == 1 and "test_overdue_on_the_grace_day" in before["output"]
    after = await sandbox_tests.run_tests(dp.OWNER, ["tests/test_overdue.py", "tests/test_reports.py", "tests/test_ratelimit.py"], {"tallybox/overdue.py": FIXED_OVERDUE})
    assert after["finished"] and after["failures"] == 0 and after["errors"] == 0 and after["ran"] == 10


# --- the welcome cards that start a demo conversation (local/src/welcomeCards.ts) --------------------------------------------------------------------------------------------------

def demo_card_texts():
    source = open("local/src/welcomeCards.ts", encoding="utf-8").read()
    return [double or single for double, single in re.findall(r"text: (?:\"([^\"]+)\"|'([^']+)'), demo: true", source)]


def test_the_demo_cards_are_the_two_requests_to_change_something_and_one_question_about_a_setting():
    from langchain_core.messages import HumanMessage
    from backend.utils.agent_utils import looks_like_edit_request

    fix, feature, setting = demo_card_texts()
    assert looks_like_edit_request([HumanMessage(content=fix)]) and looks_like_edit_request([HumanMessage(content=feature)])     # both are forced to end in a proposed diff
    assert not looks_like_edit_request([HumanMessage(content=setting)])                                                            # a question: it reads and answers, nothing to apply
    assert "overdue" in fix and "late-fee" in feature and "rate limit" in setting


def test_what_each_card_asks_about_is_what_the_demo_project_holds():
    fix, feature, setting = demo_card_texts()
    assert "is_overdue" in dp.FILES["tallybox/overdue.py"] and "overdue" in fix                 # the bug is in the overdue check
    assert "reports.py" in " ".join(dp.FILES) and "late-fee" in feature and "def render" in dp.FILES["tallybox/reports.py"]
    assert dp.FILES["tallybox/config.py"].count("RATE_LIMIT_PER_MIN") == 1 and dp.FILES["tallybox/ratelimit.py"].count("RATE_LIMIT_PER_MIN") == 1 and "rate limit" in setting
