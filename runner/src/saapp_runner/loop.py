"""The runner's main loop: say hello, ask the server for approved jobs, run each one, report back.

ONE RUNNER SERVES A COMPUTER'S PROJECTS. It reports each project (a folder the person allowed on THIS machine: projects.py) with the checks it found there, and a job names one project and one check.

Everything it will do is limited by what is on THIS machine: a job names a project this runner serves and a check of that project (found in the folder, or written in your own config file), the files are
checked again here, and a job that names anything else is refused. The server cannot make this run a command of its own, or work in a folder the person did not allow: it can ASK for a project to be added
(by name), and the runner then shows the person a dialog on their own computer. The only other thing a check job carries is the person's choice of fake AI keys (a yes or a no).

A COMMAND job (kind "command") carries a line of the small command language (commands.py) that the person saw, word for word, on a card in SAAPP and approved there. The runner does not trust that: it
classifies the line again with its own rules, refuses it if it is refused or if it is more than the card said, applies the person's own refuse list (confirm.py), and for a line that changes things asks
the person once more in a window of its own, here. Only then does shell.py run it.
"""
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import commands, confirm, execute, presets, projects, shell, status, update
from .client import Client, ServerError, Unreachable
from .config import command_shown
from .files import Refusal, clean_files


class Project:
    """One folder this runner serves, with what was found in it."""

    def __init__(self, root: Path, checks: Dict[str, List[str]], config_ok: bool = True) -> None:
        self.root, self.checks, self.config_ok = Path(root).resolve(), checks, config_ok
        self.id = projects.project_id(self.root)

    @property
    def name(self) -> str:
        return self.root.name


class Runner:
    def __init__(self, client: Client, root: Optional[Path] = None, checks: Optional[Dict[str, List[str]]] = None, say: Callable[[str], None] = print, stop: Optional[threading.Event] = None,
                 timeout: float = 900, poll_wait: int = 25, heartbeat_every: float = 10.0, max_chars: int = 6000, idle_wait: float = 1.0, config_ok: bool = True,
                 scanner: Optional[Callable[[Path], Tuple[Dict[str, List[str]], bool, str]]] = None, roots_provider: Optional[Callable[[], List[Path]]] = None, scan_every: float = 60.0,
                 launch: str = "hand", updater: Optional[Callable[[str], Any]] = None, picker: Optional[Callable[[str], Any]] = None,
                 add_project: Optional[Callable[[Path], Tuple[bool, str]]] = None, remove_project: Optional[Callable[[str], bool]] = None,
                 asker: Optional[Callable[[str, List[str]], Any]] = None, rules_provider: Optional[Callable[[], "confirm.Rules"]] = None) -> None:
        self.client, self.say = client, say
        self.stop = stop or threading.Event()
        self.timeout, self.poll_wait, self.heartbeat_every, self.max_chars, self.idle_wait = timeout, poll_wait, heartbeat_every, max_chars, idle_wait
        self.scan_every, self.launch = scan_every, launch
        # THE PROJECTS. `roots_provider()` is the current list of folders the person allowed (projects.load); with none given the runner serves `root` (kept so a test or a caller can run one folder). When
        # `checks` is given for `root` those are its checks and are not looked for; otherwise each folder is scanned (`scanner`, presets.scan), again every `scan_every` seconds, and SAAPP is told when
        # anything changed (a check was added, a folder went missing), so nobody has to restart the runner to fix a problem.
        self._roots_provider = roots_provider
        self._static_roots: List[Path] = [Path(root).resolve()] if root is not None else []
        self._fixed: Dict[Path, Tuple[Dict[str, List[str]], bool]] = {Path(root).resolve(): (checks, config_ok)} if root is not None and checks is not None else {}
        self._scanner = scanner or presets.scan
        self._config_said: Dict[Path, bool] = {}
        self.projects: Dict[str, Project] = {}
        self._add_project, self._remove_project = add_project or projects.add, remove_project or projects.remove_id
        # `updater(version)` installs a newer release and returns (done, message); None means this runner does not update itself. `exit_code` is what the process should exit with: after an update that
        # was started at login it is EXIT_RESTART, so the operating system starts the new version.
        self.updater, self.exit_code = updater, 0
        # `picker(name)` starts the folder dialog on this computer for a project SAAPP asked for and returns a handle with poll() and cancel() (picker.py); None means this computer cannot show one.
        self.picker = picker
        # `asker(line, notes)` starts the "run this command?" window for a line that changes things and returns a handle with poll() and cancel() (confirm.py); None means this computer cannot show one,
        # and such a line is then refused. `rules_provider()` is the person's own commands.toml.
        self.asker, self._rules = asker, rules_provider or confirm.load_rules
        self._dialog: Optional[Tuple[Dict[str, Any], Any]] = None
        self._waiting_requests: List[Dict[str, Any]] = []
        self._results: List[Dict[str, str]] = []
        self.status = ""
        self._sent: Any = None
        self._last_scan = time.monotonic()
        self._load_projects()

    # ---- what this runner serves ---------------------------------------------------------------------------------------------------------------

    def _roots(self) -> List[Path]:
        return [Path(r).resolve() for r in (self._roots_provider() if self._roots_provider is not None else self._static_roots)]

    def _load_projects(self) -> None:
        """Looks at each allowed folder again (and at the list itself). Says once when a folder's config cannot be read."""
        found: Dict[str, Project] = {}
        for root in self._roots():
            if root in self._fixed:
                checks, ok, problem = (*self._fixed[root], "")
            else:
                checks, ok, problem = self._scanner(root)
            if problem and self._config_said.get(root, True) != ok:
                self.say(f"{root.name}: {problem}")
            self._config_said[root] = ok
            project = Project(root, checks, ok)
            found[project.id] = project
        self.projects = found

    def _introduction(self) -> Tuple[str, List[Dict[str, Any]]]:
        """What the hello says: the runner's own status ("update_failed" or nothing), and each project with its status and its checks (command text, and whether its program can be found)."""
        listed = []
        for project in self.projects.values():
            code, ready = status.assess(project.root, project.checks, project.config_ok)
            listed.append({"id": project.id, "name": project.name, "status": code, "checks": [{"name": n, "command": command_shown(w), "ready": ready.get(n, True)} for n, w in project.checks.items()]})
        return (status.UPDATE_FAILED if update.failed_recently() else ""), listed

    def hello(self) -> bool:
        """Introduces this runner and what became of the projects SAAPP asked for. False (with the reason said) when the server turns it away: a key that is not valid, or a version it no longer accepts."""
        runner_status, listed = self._introduction()
        sent = list(self._results)
        try:
            self.client.hello(listed, runner_status, self.launch, sent)
            self.status, self._sent = runner_status, (runner_status, listed)
            self._results = [r for r in self._results if r not in sent]
            return True
        except ServerError as problem:
            self.say(f"The server did not accept this runner: {problem.message}")
            return False
        except Unreachable as problem:
            self.say(str(problem))
            return False

    def refresh(self) -> None:
        """Looks at the project list and the folders again. When anything SAAPP shows changed (a project added or forgotten, a check added, a program installed, a folder gone) it says so and tells SAAPP.
        Never raises."""
        try:
            known = {item["id"]: item["status"] for item in (self._sent[1] if self._sent else [])}              # what SAAPP was last told, so a change is noticed once
            self._load_projects()
            runner_status, listed = self._introduction()
            if (runner_status, listed) != self._sent:
                for item in listed:
                    old = known.get(item["id"])
                    if old is not None and old != item["status"]:
                        self.say(f"{item['name']}: " + (status.describe(item["status"]) if item["status"] != status.OK else "ready again: " + ", ".join(c["name"] for c in item["checks"]) + "."))
                self.hello()
        except Exception:
            self.say("Could not look at the project folders again; trying again later.")

    # ---- the loop ------------------------------------------------------------------------------------------------------------------------------

    def serve(self) -> None:
        """Polls until stopped. Network trouble is waited out; a revoked key or an outdated version ends it."""
        delay = 2.0
        while not self.stop.is_set():
            if time.monotonic() - self._last_scan >= self.scan_every:
                self._last_scan = time.monotonic()
                self.refresh()
            self._check_dialog()
            try:
                job = self.client.next_job(self.poll_wait)
                delay = 2.0
            except Unreachable as problem:
                self.say(f"{problem}; trying again in {int(delay)} s.")
                self.stop.wait(delay)
                delay = min(delay * 2, 60.0)
                continue
            except ServerError as problem:
                if problem.status in (401, 426):
                    self.say(f"Stopping: {problem.message}")
                    return
                self.say(f"The server answered an error ({problem.status}); trying again shortly.")
                self.stop.wait(5)
                continue
            if job:
                self.handle(job)
                continue
            acted = self._instructions()
            if not acted and self.poll_wait == 0:
                self.stop.wait(self.idle_wait)             # the server was asked not to hold the poll open, so pause here instead of asking again at once

    def _instructions(self) -> bool:
        """What SAAPP said besides a job: add a project (by name), forget one (by id), update (to a version number). True when it did something."""
        client = self.client
        add, forget, offered = getattr(client, "add_project", None), getattr(client, "remove_project", None), getattr(client, "update_to", None)
        acted = False
        if isinstance(add, dict) and add.get("id") and add.get("name"):
            self._ask_for_project(add)
            acted = True
        if isinstance(forget, str) and forget:
            if self._remove_project(forget):
                self.say("A project was removed here because you removed it in SAAPP.")
            self.refresh()
            acted = True
        if offered and self.updater is not None and self._dialog is None and not acted:
            self._update_to(offered)
            acted = True
        return acted

    # ---- adding a project: a dialog on THIS computer --------------------------------------------------------------------------------------------

    def _ask_for_project(self, request: Dict[str, Any]) -> None:
        """SAAPP asked for a project by name. Shows the person a dialog on this computer; they choose the folder or say no. Without a way to show one the answer is no_picker, and the person is told to
        add the folder by typing a command instead."""
        if any(r["id"] == request["id"] for r in self._waiting_requests) or (self._dialog is not None and self._dialog[0]["id"] == request["id"]):
            return
        if self.picker is None:
            self._results.append({"id": request["id"], "result": "no_picker"})
            self.say(f"SAAPP asked for a project called {request['name']}, but this computer cannot show a folder dialog. Add it by typing: saapp-runner add-project --root <the project folder>")
            self.hello()
            return
        self._waiting_requests.append(request)
        self._start_next_dialog()

    def _start_next_dialog(self) -> None:
        if self._dialog is not None or not self._waiting_requests:
            return
        request = self._waiting_requests.pop(0)
        try:
            self._dialog = (request, self.picker(request["name"]))
            self.say(f"SAAPP asked for a project called {request['name']}. A window on this computer asks you to choose its folder.")
        except Exception:
            self._results.append({"id": request["id"], "result": "no_picker"})
            self.hello()

    def _check_dialog(self) -> None:
        """Takes the person's answer when the dialog has closed: a chosen folder is added here and reported as added; anything else is reported as declined, no_picker or error."""
        if self._dialog is None:
            self._start_next_dialog()
            return
        request, handle = self._dialog
        answer = handle.poll()
        if answer is None:
            return
        self._dialog = None
        result = {"chosen": "added", "declined": "declined", "no_picker": "no_picker"}.get(answer.get("result"), "error")
        if answer.get("result") == "chosen":
            chosen = str(answer.get("path") or "").strip()
            if not chosen:                                          # "chosen" with no folder is not a choice (an empty path would mean the current folder)
                result = "error"
            else:
                done, message = self._add_project(Path(chosen))
                self.say(message)
                result = "added" if done else "error"
        self._results.append({"id": request["id"], "result": result})
        self._load_projects()
        self.hello()
        self._start_next_dialog()

    # ---- updating ------------------------------------------------------------------------------------------------------------------------------

    def _update_to(self, offered: str) -> None:
        """SAAPP says this runner should update (the person pressed Update, or turned on automatic updates, and no check is running). Installs it, and on success stops so the new version can start. On a
        failure it says why once, tells SAAPP, and does not try again for a few hours."""
        try:
            done, message = self.updater(offered)
        except Exception:
            done, message = False, "The update stopped unexpectedly. The current version keeps running."
        if message:
            self.say(message)
        if done:
            self.say("Restarting to use the new version..." if self.launch == "service" else "Start the runner again to use the new version.")
            self.exit_code = update.EXIT_RESTART if self.launch == "service" else 0
            self.stop.set()
        elif message:
            self.hello()                                                         # the status now says the update failed, so SAAPP can tell the person

    def _report(self, job_id: str, payload: Dict[str, Any]) -> None:
        for attempt in range(4):
            try:
                self.client.result(job_id, payload)
                return
            except Unreachable:
                time.sleep(min(2 ** attempt, 8))
            except ServerError as problem:
                self.say(f"The server did not take the result: {problem.message}")
                return
        self.say("Could not report the result: the server could not be reached.")

    def handle(self, job: Dict[str, Any]) -> None:
        """Runs one job the server handed out, or says why not, and reports. Never raises."""
        job_id, name, wanted = str(job.get("job_id") or ""), str(job.get("check") or ""), str(job.get("project_id") or "")
        if not job_id:
            return
        project = self.projects.get(wanted) if wanted else (next(iter(self.projects.values())) if len(self.projects) == 1 else None)
        if project is None:
            self.say("Refused a job for a project this computer does not serve.")
            self._report(job_id, {"exit_code": None, "error": "This runner does not serve that project (it may have been removed here)."})
            return
        if str(job.get("kind") or "") == "command":
            self._handle_command(job, job_id, project)
            return
        words = project.checks.get(name)
        if words is None:
            self.say(f"Refused a job for a check called {name!r}: {project.name} has no such check here.")
            self._report(job_id, {"exit_code": None, "error": f"The project {project.name} has no check called {name}."})
            return
        try:
            files = clean_files(project.root, job.get("files"))
            if files and not execute.safe_for_files(words):
                raise Refusal(f"{words[0]} runs through cmd.exe on Windows, which can act on special characters in file names, so files are not passed to it. Define this check without {{files}}.")
            argv = execute.build_argv(words, files)
        except (Refusal, ValueError) as problem:
            self.say(f"Refused the {name} job: {problem}")
            self._report(job_id, {"exit_code": None, "error": f"The runner refused it: {problem}"})
            return
        fake_keys = (job.get("settings") or {}).get("fake_keys") is True             # the one thing the page can ask for besides a name: a yes or a no, never a value
        env = execute.clean_env({key: presets.FAKE_KEY_VALUE for key in presets.FAKE_KEY_NAMES} if fake_keys else None)
        self.say(f"Running {name} in {project.name}" + (f" on {len(files)} file(s)" if files else "") + (" with fake AI keys" if fake_keys else "") + "...")
        result = self._supervise(job_id, lambda cancel: execute.run(argv, project.root, self.timeout, self.max_chars, cancel, env))
        self._finish_job(job_id, name, result)

    def _supervise(self, job_id: str, work: Callable[[threading.Event], Dict[str, Any]]) -> Dict[str, Any]:
        """Runs `work(cancel)` in a thread while telling SAAPP it is alive; a cancel from SAAPP sets `cancel`."""
        cancel, holder = threading.Event(), {}
        worker = threading.Thread(target=lambda: holder.update(work(cancel)), daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(self.heartbeat_every)
            if not worker.is_alive():
                break
            try:
                if self.client.heartbeat(job_id).get("cancel"):
                    cancel.set()
                    self.say("Stopping it: you cancelled it in SAAPP.")
            except (Unreachable, ServerError):
                pass                                   # a missed heartbeat is not a reason to stop the run
        return holder or {"exit_code": None, "error": "The runner could not run it."}

    def _finish_job(self, job_id: str, name: str, result: Dict[str, Any]) -> None:
        self.say(f"{name}: " + ("timed out" if result.get("timed_out") else "stopped" if result.get("cancelled") else f"exit code {result.get('exit_code')}" if result.get("exit_code") is not None
                                else result.get("error") or "no result") + f" ({result.get('duration', 0)} s)")
        self._report(job_id, {key: result.get(key) for key in ("exit_code", "output_tail", "truncated", "duration", "timed_out", "error")})

    # ---- command lines -------------------------------------------------------------------------------------------------------------------------

    def _refuse_command(self, job_id: str, why: str) -> None:
        self.say("Refused a command (the reason is shown in SAAPP).")           # the reason can quote part of the line, so it is reported to SAAPP and kept out of the log
        self._report(job_id, {"exit_code": None, "error": f"The runner refused it: {why}"})

    def _handle_command(self, job: Dict[str, Any], job_id: str, project: Project) -> None:
        """A line the person approved on a card. The runner decides again on its own: the line must be allowed by the rules here, no stronger than the card said, and not on the person's own refuse list;
        a line that changes things also needs the person's yes in a window on this computer. Never raises."""
        rules = self._rules()
        if rules.problem:
            self.say(rules.problem + " (using the safe defaults)")
        line = str(job.get("line") or "")
        try:
            plan = commands.classify(line, project.root, rules.allow)
        except commands.CommandError as problem:
            self._refuse_command(job_id, str(problem))
            return
        said = str(job.get("class") or "")
        if said not in commands._ORDER or commands._ORDER[plan.klass] > commands._ORDER[said]:
            self._refuse_command(job_id, "the card described this line as less than the runner finds it to be, so it is not run. Ask for it again.")
            return
        why = confirm.refused_by_person(plan, rules)
        if why:
            self._refuse_command(job_id, why)
            return
        shown = commands.render(plan)
        fake_keys = (job.get("settings") or {}).get("fake_keys") is True
        env = {key: presets.FAKE_KEY_VALUE for key in presets.FAKE_KEY_NAMES} if fake_keys else None
        ask = plan.klass == commands.CHANGE and rules.confirm_changes
        if ask and self.asker is None:
            self._refuse_command(job_id, "this line changes things and this computer cannot show the window that asks you to confirm it.")
            return
        self.say(f"Running a {plan.klass} command in {project.name}" + (" (asking you here first)" if ask else "") + "...")

        def work(cancel: threading.Event) -> Dict[str, Any]:
            if ask:
                answer = self._ask_here(shown, plan.notes, cancel)
                if answer != "yes":
                    return {"exit_code": None, "error": {"no": "You said no in the window on your computer, so nothing was run.", "cancelled": "Stopped before anything was run.",
                                                          "none": "The runner could not ask you here (no window could be shown), so nothing was run."}[answer]}
            return shell.run_line(line, project.root, self.timeout, self.max_chars, cancel, env, rules.allow)

        self._finish_job(job_id, "command", self._supervise(job_id, work))

    def _ask_here(self, shown: str, notes: List[str], cancel: threading.Event) -> str:
        """The window on this computer: "yes", "no", "cancelled" (stopped in SAAPP meanwhile) or "none" (no window)."""
        try:
            handle = self.asker(shown, notes)
        except Exception:
            return "none"
        while True:
            answer = handle.poll()
            if answer is not None:
                return {"chosen": "yes", "declined": "no"}.get(answer.get("result"), "none")
            if cancel.is_set() or self.stop.is_set():
                handle.cancel()
                return "cancelled"
            time.sleep(0.2)
