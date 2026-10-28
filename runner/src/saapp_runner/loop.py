"""The runner's main loop: say hello, ask the server for approved jobs, run each one, report back.

Everything it will do is limited by what is on THIS machine: a job names a check, the check's command comes from your own config file, the files are checked again here, and a job that names anything
else is refused. The server cannot make this run a command you did not define.
"""
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import execute
from .client import Client, ServerError, Unreachable
from .config import command_shown
from .files import Refusal, clean_files


class Runner:
    def __init__(self, client: Client, root: Path, checks: Dict[str, List[str]], say: Callable[[str], None] = print, stop: Optional[threading.Event] = None,
                 timeout: float = 900, poll_wait: int = 25, heartbeat_every: float = 10.0, max_chars: int = 6000, idle_wait: float = 1.0) -> None:
        self.client, self.root, self.checks, self.say = client, Path(root).resolve(), checks, say
        self.stop = stop or threading.Event()
        self.timeout, self.poll_wait, self.heartbeat_every, self.max_chars, self.idle_wait = timeout, poll_wait, heartbeat_every, max_chars, idle_wait

    def hello(self) -> bool:
        """Introduces this runner. False (with the reason said) when the server turns it away: a key that is not valid, or a version it no longer accepts."""
        try:
            self.client.hello(self.root.name, [{"name": name, "command": command_shown(words)} for name, words in self.checks.items()])
            return True
        except ServerError as problem:
            self.say(f"The server did not accept this runner: {problem.message}")
            return False
        except Unreachable as problem:
            self.say(str(problem))
            return False

    def serve(self) -> None:
        """Polls until stopped. Network trouble is waited out; a revoked key or an outdated version ends it."""
        delay = 2.0
        while not self.stop.is_set():
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
            elif self.poll_wait == 0:
                self.stop.wait(self.idle_wait)             # the server was asked not to hold the poll open, so pause here instead of asking again at once

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
        job_id, name = str(job.get("job_id") or ""), str(job.get("check") or "")
        words = self.checks.get(name)
        if not job_id:
            return
        if words is None:
            self.say(f"Refused a job for a check called {name!r}: you have not defined it here.")
            self._report(job_id, {"exit_code": None, "error": f"This runner has no check called {name}."})
            return
        try:
            files = clean_files(self.root, job.get("files"))
            if files and not execute.safe_for_files(words):
                raise Refusal(f"{words[0]} runs through cmd.exe on Windows, which can act on special characters in file names, so files are not passed to it. Define this check without {{files}}.")
            argv = execute.build_argv(words, files)
        except (Refusal, ValueError) as problem:
            self.say(f"Refused the {name} job: {problem}")
            self._report(job_id, {"exit_code": None, "error": f"The runner refused it: {problem}"})
            return
        self.say(f"Running {name}" + (f" on {len(files)} file(s)" if files else "") + "...")
        cancel, holder = threading.Event(), {}
        worker = threading.Thread(target=lambda: holder.update(execute.run(argv, self.root, self.timeout, self.max_chars, cancel)), daemon=True)
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
        result = holder or {"exit_code": None, "error": "The runner could not run it."}
        self.say(f"{name}: " + ("timed out" if result.get("timed_out") else "stopped" if result.get("cancelled") else f"exit code {result.get('exit_code')}" if result.get("exit_code") is not None
                                else result.get("error") or "no result") + f" ({result.get('duration', 0)} s)")
        self._report(job_id, {key: result.get(key) for key in ("exit_code", "output_tail", "truncated", "duration", "timed_out", "error")})
