"""Sending a project's files to SAAPP when asked, once the person has said yes: ask, consent window, upload (docs/runner-files-plan.md).

The loop (loop.py) hands each "let Sonic read this project's files" request to `offer`, and collects the finished ones with `poll`. For each request: scan the project (filerules.py), show the person the
window (share_dialog.py), and only on a yes upload in a background thread, so the runner keeps polling while a big project is sent. Anything else is reported back by name only: shared, declined,
no_picker or error. The loop reports it to SAAPP, which closes the request; a runner whose person never answered sends nothing.
"""
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import workspace_sync
from .client import Client


class Sharing:
    def __init__(self, client: Client, say: Callable[[str], None] = print, starter: Optional[Callable[[str, Path, str], Any]] = None,
                 scanner: Callable[[Path], workspace_sync.Scan] = workspace_sync.scan, uploader: Callable[..., workspace_sync.Outcome] = workspace_sync.upload) -> None:
        self.client, self.say, self.starter, self._scan, self._upload = client, say, starter, scanner, uploader
        self._asking: List[Dict[str, Any]] = []            # waiting for the person's answer to the window
        self._sending: List[Dict[str, Any]] = []           # said yes: uploading in a thread
        self._done: List[Dict[str, str]] = []

    def busy(self) -> bool:
        return bool(self._asking or self._sending)

    def offer(self, request: Dict[str, Any], root: Optional[Path]) -> None:
        """A request to send `root`'s files. Reports error at once for a folder that is gone or has nothing to send, no_picker where no window can be shown (and nothing is sent)."""
        name = str(request.get("name") or "project")
        if root is None or not Path(root).is_dir():
            self._done.append({"id": request["id"], "result": "error"})
            self.say(f"SAAPP asked to share the files of {name}, but this runner does not have that folder.")
            return
        found = self._scan(Path(root))
        if not found.entries:
            self._done.append({"id": request["id"], "result": "error"})
            self.say(f"SAAPP asked to share the files of {name}, but there is nothing in it to send (secrets, build folders and lock files are never sent).")
            return
        if self.starter is None:
            self._done.append({"id": request["id"], "result": "no_picker"})
            self.say(f"SAAPP asked to share the files of {name}, but this computer cannot show a window to ask you. Nothing was sent.")
            return
        try:
            handle = self.starter(name, Path(root), workspace_sync.summary(found))
        except Exception:
            self._done.append({"id": request["id"], "result": "no_picker"})
            return
        self.say(f"SAAPP asked to let Sonic read the files of {name}. A window on this computer asks you whether to send them.")
        self._asking.append({"request": request, "root": Path(root), "found": found, "handle": handle})

    def poll(self) -> List[Dict[str, str]]:
        """The requests that finished since the last call, as [{"id", "result"}]. Moves an answered window on: yes starts the upload, anything else is reported."""
        for item in list(self._asking):
            answer = item["handle"].poll()
            if answer is None:
                continue
            self._asking.remove(item)
            request = item["request"]
            if answer.get("result") == "chosen":
                self._start_upload(item)
            else:
                self._done.append({"id": request["id"], "result": {"declined": "declined", "no_picker": "no_picker"}.get(answer.get("result"), "error")})
        for item in list(self._sending):
            if not item["thread"].is_alive():
                self._sending.remove(item)
                self._done.append({"id": item["request"]["id"], "result": "shared" if item["outcome"].get("ok") else "error"})
        finished, self._done = self._done, []
        return finished

    def _start_upload(self, item: Dict[str, Any]) -> None:
        item["outcome"] = {}

        def work() -> None:
            try:
                outcome = self._upload(self.client, str(item["request"].get("project_id") or ""), item["root"], item["found"], self.say)
                item["outcome"] = {"ok": outcome.ok}
                self.say(outcome.message)
            except Exception:
                item["outcome"] = {"ok": False}
                self.say("Sending the files failed.")

        item["thread"] = threading.Thread(target=work, daemon=True)
        self._sending.append(item)
        item["thread"].start()

    def cancel(self) -> None:
        """Closes any window still open (the runner is stopping)."""
        for item in self._asking:
            try:
                item["handle"].cancel()
            except Exception:
                pass
