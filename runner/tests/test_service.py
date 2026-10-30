"""Starting the runner by itself at login (service.py), the one-runner-per-computer lock (lock.py) and the cleaned log it writes when it has no window (runlog.py).

The text of every file and every command is checked without touching the operating system (a recording stand-in replaces the one function that runs commands). The Windows path is also run for real,
with a harmless stand-in program, because Task Scheduler is the one thing a unit test cannot imitate."""
import argparse
import configparser
import plistlib
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from saapp_runner import cli, lock, projects, runlog, service
from saapp_runner import config as cfg

needs_windows = pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler is Windows only")
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "my app"
    root.mkdir()
    return root


class Recorder:
    """A stand-in for running commands: remembers them and answers from a table of (exit code, output) by the command's first words."""

    def __init__(self, answers=None):
        self.commands, self.answers = [], answers or {}

    def __call__(self, command):
        self.commands.append(list(command))
        for key, answer in self.answers.items():
            if command[: len(key)] == list(key):
                return answer
        return 0, ""


# ---- names -------------------------------------------------------------------------------------------------------------------------------------

def test_the_entry_has_a_stable_short_name_of_its_own_per_runner_home(tmp_path, monkeypatch):
    first = service.service_id()
    assert first == service.service_id() and len(first) == 8 and service.label() == f"saapp-runner-{first}"
    monkeypatch.setenv("SAAPP_RUNNER_HOME", str(tmp_path / "another-account"))
    assert service.service_id() != first                                                       # another account's runner never shares an entry or a lock
    assert all(ch.isalnum() or ch in "-._" for ch in service.label())


def test_the_platform_is_told_apart():
    assert service.platform_kind("win32") == "windows" and service.platform_kind("darwin") == "mac" and service.platform_kind("linux") == "linux"


def test_the_service_uses_the_windowless_python_on_windows_when_there_is_one(tmp_path):
    exe = tmp_path / "python.exe"
    exe.write_text("")
    assert service.python_for_service(str(exe), windows=True) == str(exe)                  # no pythonw beside it: the plain one
    (tmp_path / "pythonw.exe").write_text("")
    assert service.python_for_service(str(exe), windows=True) == str(tmp_path / "pythonw.exe")
    assert service.python_for_service(str(exe), windows=False) == str(exe)


def test_the_entry_runs_the_runner_with_no_folder_and_no_key_in_it():
    assert service.arguments() == ["-m", "saapp_runner", "start", "--service"]                  # the projects are in projects.json, not in the entry


# ---- the text of each system's entry ----------------------------------------------------------------------------------------------------------

def test_the_windows_task_runs_at_logon_as_the_user_without_elevation_and_keeps_going_on_battery():
    xml = service.windows_task_xml(r"C:\Users\me\.saapp-runner\venv\Scripts\pythonw.exe", r"PC\me")
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == r"PC\me"
    assert root.find("t:Principals/t:Principal/t:RunLevel", NS).text == "LeastPrivilege" and root.find("t:Principals/t:Principal/t:LogonType", NS).text == "InteractiveToken"
    settings = root.find("t:Settings", NS)
    assert settings.find("t:DisallowStartIfOnBatteries", NS).text == "false" and settings.find("t:StopIfGoingOnBatteries", NS).text == "false"
    assert settings.find("t:ExecutionTimeLimit", NS).text == "PT0S" and settings.find("t:RestartOnFailure/t:Count", NS).text == "999" and settings.find("t:MultipleInstancesPolicy", NS).text == "IgnoreNew"
    exe = root.find("t:Actions/t:Exec", NS)
    assert exe.find("t:Command", NS).text.endswith("pythonw.exe") and exe.find("t:Arguments", NS).text == "-m saapp_runner start --service"


def test_odd_characters_in_a_path_or_a_user_name_are_escaped_so_the_task_file_stays_valid():
    xml = service.windows_task_xml(r"C:\p&q <x>\pythonw.exe", r"DOM\o'neil & co")
    root = ET.fromstring(xml.replace('encoding="UTF-16"', ""))                              # parses: nothing broke the markup
    assert root.find("t:Actions/t:Exec/t:Command", NS).text == r"C:\p&q <x>\pythonw.exe" and root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text == r"DOM\o'neil & co"


def test_no_entry_carries_a_key_a_folder_or_anything_from_the_server(project, runner_home):
    cfg.save_credentials("https://saapp.example.com", "SECRET.KEYVALUE")
    projects.add(project)
    texts = [service.windows_task_xml("py", "me"), service.mac_plist("py"), service.linux_unit("py")]
    assert all("SECRET" not in text and "saapp.example.com" not in text and "my app" not in text for text in texts)


def test_the_mac_launch_agent_is_a_valid_plist_that_starts_at_login_and_stays_up():
    data = plistlib.loads(service.mac_plist("/usr/bin/python3").encode("utf-8"))
    assert data["Label"] == f"com.saapp.{service.label()}" and data["RunAtLoad"] is True and data["KeepAlive"] is True
    assert data["ProgramArguments"] == ["/usr/bin/python3", "-m", "saapp_runner", "start", "--service"] and data["StandardOutPath"].endswith("runner.log")


def test_the_linux_user_unit_has_the_sections_it_needs():
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read_string(service.linux_unit("/usr/bin/python3"))
    assert parser["Install"]["WantedBy"] == "default.target" and parser["Service"]["Restart"] == "on-failure"
    assert parser["Service"]["ExecStart"] == '"/usr/bin/python3" "-m" "saapp_runner" "start" "--service"'


def test_each_systems_entry_lives_where_that_system_looks():
    assert service.entry_path("mac").parent.name == "LaunchAgents" and service.entry_path("mac").suffix == ".plist"
    assert service.entry_path("linux").parent.parts[-3:] == (".config", "systemd", "user") and service.entry_path("linux").suffix == ".service"


# ---- doing it (commands recorded, not run) ----------------------------------------------------------------------------------------------------

def test_installing_on_windows_registers_the_task_from_xml_then_starts_it_and_leaves_no_xml_behind(runner_home):
    run = Recorder()
    done, message = service.install(run=run, kind="windows", python="pythonw.exe", user="PC\\me")
    assert done and "start by itself" in message
    name = service.label()
    assert run.commands[0][:4] == ["schtasks", "/Create", "/TN", name] and run.commands[0][4] == "/XML" and run.commands[0][-1] == "/F" and run.commands[1] == ["schtasks", "/Run", "/TN", name]
    assert not list(Path(runner_home).glob("*.task.xml"))


def test_installing_on_mac_and_linux_writes_the_entry_and_loads_it(runner_home, monkeypatch):
    fake_home = Path(runner_home) / "fakehome"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    mac = Recorder()
    assert service.install(run=mac, kind="mac", python="/usr/bin/python3")[0]
    plist = service.entry_path("mac")
    assert plist.exists() and plistlib.loads(plist.read_bytes())["RunAtLoad"] is True
    assert mac.commands[-1][:2] == ["launchctl", "bootstrap"] and mac.commands[-1][-1] == str(plist)
    linux = Recorder()
    assert service.install(run=linux, kind="linux", python="/usr/bin/python3")[0]
    unit = service.entry_path("linux")
    assert unit.exists() and linux.commands[0] == ["systemctl", "--user", "daemon-reload"] and linux.commands[1] == ["systemctl", "--user", "enable", "--now", f"{service.label()}.service"]


def test_a_refusal_from_the_system_is_said_in_words_and_nothing_is_claimed(runner_home):
    done, message = service.install(run=Recorder({("schtasks", "/Create"): (1, "ERROR: Access is denied.")}), kind="windows", python="pythonw.exe", user="PC\\me")
    assert not done and "Task Scheduler would not accept the entry" in message and "Access is denied" in message
    done, message = service.install(run=Recorder({("systemctl", "--user", "enable"): (1, "Failed to connect to bus")}), kind="linux", python="/usr/bin/python3")
    assert not done and "may not use systemd" in message


def test_uninstalling_stops_and_removes_the_entry_and_a_missing_one_is_fine(runner_home, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(runner_home) / "fakehome"))
    run = Recorder()
    assert service.uninstall(run=run, kind="windows")[0] and run.commands[0][:2] == ["schtasks", "/End"] and run.commands[1][:2] == ["schtasks", "/Delete"]
    gone = Recorder({("schtasks", "/Delete"): (1, "ERROR: The system cannot find the file specified.")})
    assert service.uninstall(run=gone, kind="windows")[0] is True                 # nothing to remove is not a failure
    refused = Recorder({("schtasks", "/Delete"): (1, "ERROR: Access is denied.")})
    assert service.uninstall(run=refused, kind="windows")[0] is False
    service.install(run=Recorder(), kind="linux", python="/usr/bin/python3")
    assert service.uninstall(run=Recorder(), kind="linux")[0] and not service.entry_path("linux").exists()


def test_the_state_is_read_from_what_each_system_says():
    running = "TaskName: x\nStatus:                                Running\n"
    assert service.state(run=Recorder({("schtasks", "/Query"): (0, running)}), kind="windows") == {"installed": True, "running": True}
    assert service.state(run=Recorder({("schtasks", "/Query"): (0, "Status: Ready\n")}), kind="windows") == {"installed": True, "running": False}
    assert service.state(run=Recorder({("schtasks", "/Query"): (1, "ERROR")}), kind="windows") == {"installed": False, "running": False}
    assert service.state(run=Recorder({("launchctl", "print"): (0, "state = running")}), kind="mac") == {"installed": True, "running": True}
    assert service.state(run=Recorder({("systemctl", "--user", "is-active"): (3, "inactive")}), kind="linux") == {"installed": True, "running": False}

    def broken(command):
        raise RuntimeError("boom")

    assert service.state(run=broken, kind="windows") == {"installed": False, "running": False}            # it never raises


# ---- Task Scheduler, for real ------------------------------------------------------------------------------------------------------------------

@needs_windows
def test_task_scheduler_accepts_the_entry_and_it_can_be_found_and_removed(tmp_path, runner_home):
    program = str(Path(sys.executable).with_name("python.exe"))
    try:
        done, message = service.install(python=program)                                      # the real schtasks, with a program that starts and ends at once
        assert done, message
        assert service.state()["installed"] is True                                          # Task Scheduler knows it by the name made from this runner's home
        _, listing = service._run(["schtasks", "/Query", "/TN", service.label(), "/XML"])
        # what Task Scheduler stored, read back: at logon, no time limit, allowed on battery, restarted, and not elevated (it omits the least-privilege default and would print HighestAvailable otherwise)
        assert "<LogonTrigger>" in listing and "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>" in listing and "<DisallowStartIfOnBatteries>false" in listing and "<Count>999</Count>" in listing
        assert "<TimeTrigger>" in listing and "<Interval>PT5M</Interval>" in listing         # and the repeating start that brings back a runner that ended
        assert "HighestAvailable" not in listing and "--service" in listing
    finally:
        removed, text = service.uninstall()
    assert removed, text
    assert service.state()["installed"] is False


# ---- one runner per computer ---------------------------------------------------------------------------------------------------------------------

def test_a_second_runner_on_the_same_computer_cannot_start_and_a_released_lock_can_be_taken_again(runner_home):
    first, second = lock.InstanceLock(), lock.InstanceLock()
    assert first.acquire() is True and second.acquire() is False
    first.release()
    assert second.acquire() is True
    second.release()


def test_another_accounts_runner_has_its_own_lock(runner_home, tmp_path, monkeypatch):
    mine = lock.InstanceLock()
    assert mine.acquire()
    monkeypatch.setenv("SAAPP_RUNNER_HOME", str(tmp_path / "other-account"))
    theirs = lock.InstanceLock()
    assert theirs.acquire()
    mine.release(), theirs.release()


def test_a_lock_left_by_a_process_that_died_does_not_lock_anyone_out(runner_home):
    import os

    code = "import time; from saapp_runner import lock; l = lock.InstanceLock(); print('held' if l.acquire() else 'no', flush=True); time.sleep(60)"
    env = {**os.environ, "SAAPP_RUNNER_HOME": str(runner_home), "PYTHONPATH": str(Path(service.__file__).resolve().parents[1])}
    child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, env=env, text=True)
    try:
        assert child.stdout.readline().strip() == "held"
        mine = lock.InstanceLock()
        assert mine.acquire() is False                                                         # a live runner holds it
    finally:
        child.kill()
        child.wait(timeout=10)
    time.sleep(0.3)
    again = lock.InstanceLock()
    assert again.acquire() is True                                                             # killed: the operating system let go of it
    again.release()


# ---- the cleaned log ----------------------------------------------------------------------------------------------------------------------------

def test_the_log_keeps_events_not_paths_or_commands(project, tmp_path, runner_home):
    other = tmp_path / "other-project"
    other.mkdir()
    roots = [project.resolve(), other.resolve()]
    assert runlog.clean(f"saapp-runner 0.1.1   server https://s.example.com   folder {roots[0]}", roots) == "saapp-runner 0.1.1 server https://s.example.com folder <project>"
    assert runlog.clean(f"added {roots[1]}", roots) == "added <project>"                                  # every project's path, not just one
    assert runlog.clean(f"  can run: tests = {sys.executable} -m pytest -q", roots) == "" and runlog.clean("  note: the program for 'types' could not be found", roots) == ""
    assert runlog.clean(f"could not read {Path.home() / 'x' / 'config.toml'}: bad", roots).startswith("could not read ~") and str(Path.home()) not in runlog.clean(f"x {Path.home()}", roots)
    assert runlog.clean("Running tests in <name> on 2 file(s)...", roots) == "Running tests in <name> on 2 file(s)..."


def test_the_background_say_writes_time_stamped_cleaned_lines_follows_new_projects_and_never_raises(project, runner_home):
    current = []
    say = runlog.file_say(lambda: list(current))
    current.append(project.resolve())
    say(f"folder {project.resolve()}")                                                              # a project: cleaned
    say("  can run: tests = secret-command --flag")
    say("Connected.")
    lines = runlog.log_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and lines[0].endswith("folder <project>") and lines[1].endswith("Connected.") and lines[0][:4].isdigit()
    assert "secret-command" not in runlog.log_path().read_text(encoding="utf-8")
    assert runlog.tail(1).endswith("Connected.")
    runlog.file_say(lambda: 1 / 0)("still works")                                                    # a broken project list cannot stop a line being written
    assert runlog.tail(1).endswith("still works")


def test_the_log_is_cut_down_when_it_grows_too_large(runner_home):
    path = runlog.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("old line\n" * (runlog.MAX_BYTES // 5), encoding="utf-8")
    runlog.file_say(lambda: [])("newest")
    assert path.stat().st_size < runlog.MAX_BYTES and path.read_text(encoding="utf-8").rstrip().endswith("newest")


# ---- the commands -------------------------------------------------------------------------------------------------------------------------------

def test_install_service_needs_a_paired_computer_and_says_what_it_did(runner_home, monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "install", lambda: (True, "The runner will now start by itself whenever you log in, and it is starting now."))
    assert cli.main(["install-service"]) == 1 and "not paired" in capsys.readouterr().out
    cfg.save_credentials("http://127.0.0.1:9", "abc.def")
    assert cli.main(["install-service"]) == 0 and "start by itself" in capsys.readouterr().out
    monkeypatch.setattr(cli.service, "install", lambda: (False, "Task Scheduler would not accept the entry."))
    assert cli.main(["install-service"]) == 1
    assert "You can still run it by hand" in capsys.readouterr().out


def test_uninstall_service_says_what_it_did(runner_home, monkeypatch, capsys):
    monkeypatch.setattr(cli.service, "uninstall", lambda: (True, "The runner will no longer start by itself, and the background copy is stopped."))
    assert cli.main(["uninstall-service"]) == 0 and "no longer start by itself" in capsys.readouterr().out


def test_status_shows_pairing_the_login_entry_whether_it_is_running_the_projects_and_the_end_of_the_log(project, runner_home, monkeypatch, capsys):
    cfg.save_credentials("http://127.0.0.1:9", "abc.def")
    projects.add(project)
    monkeypatch.setattr(cli.service, "state", lambda: {"installed": True, "running": True})
    runlog.file_say(lambda: [])("Connected.")
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "paired with SAAPP:        yes" in out and "starts at login:          yes" in out and "running right now:        no" in out and "projects:                 1" in out and str(project.resolve()) in out and "Connected." in out
    holder = lock.InstanceLock()
    assert holder.acquire()
    try:
        cli.main(["status"])
        assert "running right now:        yes" in capsys.readouterr().out
    finally:
        holder.release()


def test_a_background_start_logs_instead_of_printing_and_says_it_was_started_by_itself(fake, project, runner_home, capsys):
    cfg.save_credentials(fake.url, "abc.def")
    projects.add(project)
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (401, {"detail": "stop"})
    assert cli.cmd_start(argparse.Namespace(root=None, timeout=5, service=True), stop=threading.Event()) == 0
    assert capsys.readouterr().out == ""                                                      # no window: nothing printed
    hello = next(call for call in fake.seen if call["path"].endswith("/hello"))
    assert hello["body"]["launch"] == "service"
    log = runlog.log_path().read_text(encoding="utf-8")
    assert "Connected." in log and str(project.resolve()) not in log                           # and no path in the log


def test_a_start_by_hand_says_so_and_a_second_start_does_nothing_while_one_is_running(fake, project, runner_home, capsys):
    cfg.save_credentials(fake.url, "abc.def")
    projects.add(project)
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (401, {"detail": "stop"})
    assert cli.cmd_start(argparse.Namespace(root=None, timeout=5), stop=threading.Event()) == 0
    assert next(call for call in fake.seen if call["path"].endswith("/hello"))["body"]["launch"] == "hand"
    fake.seen.clear()
    holder = lock.InstanceLock()
    assert holder.acquire()
    try:
        said = []
        assert cli.cmd_start(argparse.Namespace(root=None, timeout=5), say=said.append, stop=threading.Event()) == 0
        assert any("already running" in line for line in said) and not fake.seen              # it never even said hello
    finally:
        holder.release()


# ---- the background process itself, for real ----------------------------------------------------------------------------------------------------

def _wait_for(condition, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def test_the_runner_really_starts_in_the_background_with_no_console_and_reports_that_it_did(fake, project, runner_home):
    import os

    cfg.save_credentials(fake.url, "abc.def")
    projects.add(project)
    fake.routes[("POST", "/api/runner/hello")] = (200, {"ok": True})
    fake.routes[("GET", "/api/runner/next")] = (200, {"job": None})
    python = service.python_for_service()                                                    # pythonw.exe on Windows: there is no console, so printing has nowhere to go
    env = {**os.environ, "SAAPP_RUNNER_HOME": str(runner_home), "PYTHONPATH": str(Path(service.__file__).resolve().parents[1])}
    child = subprocess.Popen([python, *service.arguments()], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    try:
        assert _wait_for(lambda: any(call["path"].endswith("/hello") for call in fake.seen)), "the background runner never said hello"
        hello = next(call for call in fake.seen if call["path"].endswith("/hello"))
        assert hello["body"]["launch"] == "service" and hello["auth"] == "Bearer abc.def" and [p["name"] for p in hello["body"]["projects"]] == ["my app"]
        assert _wait_for(lambda: runlog.log_path().exists() and "Connected." in runlog.log_path().read_text(encoding="utf-8"))
        assert child.poll() is None                                                          # still running: it did not crash for want of a console
        second = subprocess.run([python, *service.arguments()], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        assert second.returncode == 0                                                        # a second copy steps aside, and quietly: the entry tries again every few minutes
        assert "already running" not in runlog.log_path().read_text(encoding="utf-8")
        assert str(project.resolve()) not in runlog.log_path().read_text(encoding="utf-8")                                            # and the log holds no path
    finally:
        child.kill()
        child.wait(timeout=10)


# ---- keeping it running, and the start link (2026-10-30: an update left the runner stopped for 16 hours) -------------------------------------------

def test_the_windows_task_also_starts_the_runner_every_few_minutes_whatever_ended_it():
    root = ET.fromstring(service.windows_task_xml("pythonw.exe", r"PC\me").replace('encoding="UTF-16"', ""))
    repeat = root.find("t:Triggers/t:TimeTrigger", NS)
    assert repeat.find("t:Repetition/t:Interval", NS).text == "PT5M" and repeat.find("t:Repetition/t:StopAtDurationEnd", NS).text == "false" and repeat.find("t:Enabled", NS).text == "true"
    assert root.find("t:Triggers/t:LogonTrigger", NS) is not None                           # still at logon too
    assert root.find("t:Settings/t:MultipleInstancesPolicy", NS).text == "IgnoreNew"        # and never a second copy while one runs


def test_the_start_link_entries_run_one_fixed_command_in_the_persons_own_registry_hive():
    commands = service.protocol_commands(r"C:\py\pythonw.exe")
    assert all(command[:2] == ["reg", "add"] for command in commands)
    assert all(command[2].startswith(r"HKCU\Software\Classes\saapp-runner") for command in commands)           # never machine-wide
    assert ["reg", "add", r"HKCU\Software\Classes\saapp-runner", "/v", "URL Protocol", "/d", "", "/f"] in commands
    assert commands[-1][-2] == r'"C:\py\pythonw.exe" -m saapp_runner start-link "%1"'                          # what it runs: start-link, and the link's text only as a spare argument


def test_only_the_persons_own_runner_on_windows_takes_or_gives_up_the_link(runner_home, monkeypatch):
    run = Recorder()
    assert service.register_protocol(run, "pythonw.exe", kind="windows") is False and run.commands == []      # SAAPP_RUNNER_HOME is set: a test, or a second runner in a folder of its own
    service.unregister_protocol(run, "windows")
    assert run.commands == []
    monkeypatch.setattr(service, "_own_home", lambda: True)
    run = Recorder()
    assert service.register_protocol(run, "pythonw.exe", kind="windows") is True and len(run.commands) == 3
    assert service.register_protocol(Recorder(), "p", kind="linux") is False and service.register_protocol(Recorder(), "p", kind="mac") is False
    assert service.register_protocol(Recorder({("reg", "add"): (1, "Access is denied")}), "p", kind="windows") is False
    gone = Recorder()
    service.unregister_protocol(gone, "windows")
    assert gone.commands == [["reg", "delete", r"HKCU\Software\Classes\saapp-runner", "/f"]]


def test_installing_registers_the_link_and_stamps_the_entry_and_uninstalling_removes_both(runner_home, monkeypatch):
    monkeypatch.setattr(service, "_own_home", lambda: True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(runner_home) / "fakehome"))
    run = Recorder()
    assert service.install(run=run, kind="windows", python="pythonw.exe", user="PC\\me")[0]
    assert sum(1 for command in run.commands if command[:2] == ["reg", "add"]) == 3
    assert (Path(runner_home) / "entry-version").read_text(encoding="utf-8") == str(service.ENTRY_VERSION)
    gone = Recorder()
    assert service.uninstall(run=gone, kind="windows")[0]
    assert ["reg", "delete", r"HKCU\Software\Classes\saapp-runner", "/f"] in gone.commands and not (Path(runner_home) / "entry-version").exists()


def test_an_older_entry_is_brought_up_to_date_once_without_starting_a_second_copy(runner_home, monkeypatch):
    monkeypatch.setattr(service, "_own_home", lambda: True)
    there = {("schtasks", "/Query"): (0, "Status: Running\n")}
    run = Recorder(there)
    assert service.refresh(run, kind="windows", python="pythonw.exe", user="PC\\me") is True
    assert sum(1 for command in run.commands if command[:2] == ["schtasks", "/Create"]) == 1
    assert not any(command[:2] == ["schtasks", "/Run"] for command in run.commands)          # the runner doing this is the one that is running: nothing is started
    assert sum(1 for command in run.commands if command[:2] == ["reg", "add"]) == 3
    again = Recorder(there)
    assert service.refresh(again, kind="windows", python="pythonw.exe", user="PC\\me") is False and again.commands == []       # stamped: nothing is asked of the system


def test_refresh_leaves_alone_a_computer_with_no_entry_another_system_and_a_refusal(runner_home, monkeypatch):
    monkeypatch.setattr(service, "_own_home", lambda: True)
    none = Recorder({("schtasks", "/Query"): (1, "ERROR: The system cannot find the file specified.")})
    assert service.refresh(none, kind="windows") is False and not any(command[:2] == ["schtasks", "/Create"] for command in none.commands)
    assert service.refresh(Recorder(), kind="linux") is False and service.refresh(Recorder(), kind="mac") is False
    refused = Recorder({("schtasks", "/Query"): (0, ""), ("schtasks", "/Create"): (1, "ERROR: Access is denied.")})
    assert service.refresh(refused, kind="windows", python="pythonw.exe", user="PC\\me") is False
    assert not (Path(runner_home) / "entry-version").exists()                                 # not stamped, so it is tried again next time


class Spawner:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, command, **kwargs):
        if self.fail:
            raise OSError("no")
        self.calls.append((list(command), kwargs))


def test_after_an_update_a_detached_helper_asks_task_scheduler_to_start_the_runner_again(runner_home):
    spawn, there = Spawner(), Recorder({("schtasks", "/Query"): (0, "")})
    assert service.restart_soon("windows", popen=spawn, run=there) is True
    command, kwargs = spawn.calls[0]
    assert command[:2] == ["cmd", "/c"] and f"schtasks /Run /TN {service.label()}" in command[2] and "ping -n" in command[2]      # a few seconds' wait, then the task is run
    assert kwargs["creationflags"] & 0x00000008 and kwargs["creationflags"] & 0x08000000                                           # detached, and no window
    assert service.restart_soon("linux", popen=Spawner(), run=there) is False and service.restart_soon("mac", popen=Spawner(), run=there) is False
    assert service.restart_soon("windows", popen=Spawner(), run=Recorder({("schtasks", "/Query"): (1, "ERROR")})) is False        # no entry: nothing to ask
    assert service.restart_soon("windows", popen=Spawner(fail=True), run=there) is False


def test_the_start_link_runs_the_entry_when_there_is_one_and_else_starts_a_copy_with_no_window(runner_home):
    run = Recorder({("schtasks", "/Query"): (0, "")})
    spawn = Spawner()
    assert service.start_now(run, spawn, "windows") is True and run.commands[-1] == ["schtasks", "/Run", "/TN", service.label()] and spawn.calls == []
    bare = Recorder({("schtasks", "/Query"): (1, "ERROR")})
    assert service.start_now(bare, spawn, "windows") is True
    command, kwargs = spawn.calls[0]
    assert command[1:] == service.arguments() and kwargs["creationflags"] & 0x08000000                                         # the same command the entry runs, windowless
    assert service.start_now(Recorder(), Spawner(), "linux") is False


def test_the_start_link_command_starts_the_runner_and_ignores_whatever_the_link_says(monkeypatch):
    asked = []
    monkeypatch.setattr(service, "start_now", lambda *a, **k: asked.append(1) or True)
    assert cli.main(["start-link", "saapp-runner://start/?run=anything&extra=%22--evil%22"]) == 0 and asked == [1]
    assert cli.main(["start-link"]) == 0 and asked == [1, 1]


def test_a_background_copy_that_finds_the_runner_going_steps_aside_and_says_nothing(fake, project, runner_home):
    cfg.save_credentials(fake.url, "abc.def")
    projects.add(project)
    holder = lock.InstanceLock()
    assert holder.acquire()
    try:
        assert cli.cmd_start(argparse.Namespace(root=None, timeout=5, service=True), stop=threading.Event()) == 0
        log = runlog.log_path()
        assert not fake.seen and (not log.exists() or "already running" not in log.read_text(encoding="utf-8"))
    finally:
        holder.release()
