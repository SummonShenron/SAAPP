"""The runner's side of the conversation with the SAAPP server. Standard library only.

The runner always connects OUT: it never listens on a port. The key goes only over https (or to this machine, for testing). A message carries a check's name, the paths asked for and the end of a command's
output, and ONE exception, `workspace_sync`: the text files of a project, sent only after the person said yes in a window on this computer (sharing.py), so Sonic can read the project.
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import __version__


class ServerError(Exception):
    """The server answered with an error. `status` is the HTTP status and the message is the server's own words."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class Unreachable(Exception):
    """The server could not be reached (no network, down, restarting). Worth trying again."""


def check_address(server: str) -> str:
    """The server address without a trailing slash, or a ValueError. The key is never sent over plain http except to this machine."""
    text = (server or "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(text)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme not in ("https", "http") or not parts.hostname or (parts.scheme == "http" and not local):
        raise ValueError("The server address must start with https:// (plain http is allowed only for this machine).")
    return text


class Client:
    def __init__(self, server: str, key: Optional[str] = None) -> None:
        self.server, self.key = check_address(server), key
        # What the last poll said besides a job. `update_to` is a version NUMBER (never an address); `add_project` is {"id", "name"} (a request to ask the person to choose that project's folder: a name, never
        # a path); `remove_project` is a project id to forget.
        self.update_to: Optional[str] = None
        self.add_project: Optional[Dict[str, str]] = None
        self.remove_project: Optional[str] = None
        self.share_files: Optional[Dict[str, str]] = None       # a request to ask the person whether to send a project's files: {"id", "project_id", "name"}; names and ids, never a path

    def _call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 40) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json", "User-Agent": f"saapp-runner/{__version__}"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        request = urllib.request.Request(self.server + path, data=None if body is None else json.dumps(body).encode("utf-8"), method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as problem:
            try:
                detail = json.loads(problem.read() or b"{}").get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise ServerError(problem.code, str(detail or f"the server answered {problem.code}")) from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as problem:
            raise Unreachable(f"could not reach {self.server}: {problem.__class__.__name__}") from None
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise ServerError(502, "the server sent something that was not JSON.") from None

    def pair(self, code: str) -> Dict[str, Any]:
        return self._call("POST", "/api/runner/pair", {"code": code})

    def hello(self, projects: List[Dict[str, Any]], status: str = "", launch: str = "", request_results: Optional[List[Dict[str, str]]] = None, browser: str = "") -> Dict[str, Any]:
        """Introduces the runner: the PROJECTS it serves (each with an id, a name, a status code and its checks, which say whether their program can be found on this computer, and the servers it
        can keep running), its own status (a code, never free text), how it was started ("service" at login, "hand"), what became of the projects SAAPP asked for, and which browser it found for
        looking at pages ("chrome", "edge", "chromium" or "custom"; only the name, never a path)."""
        body: Dict[str, Any] = {"version": __version__, "projects": projects, "status": status, "launch": launch, "request_results": request_results or []}
        if browser:
            body["browser"] = browser
        return self._call("POST", "/api/runner/hello", body)

    def next_job(self, wait: int = 25) -> Optional[Dict[str, Any]]:
        answer = self._call("GET", f"/api/runner/next?wait={int(wait)}", timeout=wait + 15)
        offered, add, forget = answer.get("update"), answer.get("add_project"), answer.get("remove_project")
        self.update_to = offered if isinstance(offered, str) else None
        self.add_project = {"id": str(add["id"]), "name": str(add["name"])} if isinstance(add, dict) and add.get("id") and add.get("name") else None      # only the two words, whatever else was sent
        self.remove_project = forget if isinstance(forget, str) else None
        share = answer.get("share_files")
        self.share_files = ({"id": str(share["id"]), "project_id": str(share["project_id"]), "name": str(share.get("name") or "project")}
                            if isinstance(share, dict) and share.get("id") and share.get("project_id") else None)
        return answer.get("job")

    def workspace_sync(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """One batch of a project's files (sharing.py, after the person said yes here). The server takes it only while a request for that project is open."""
        return self._call("POST", "/api/runner/workspace/sync", body, timeout=120)

    def workspace_clear(self) -> Dict[str, Any]:
        """Takes back the files this runner sent (a folder the browser connected is left alone)."""
        return self._call("DELETE", "/api/runner/workspace")

    def heartbeat(self, job_id: str, output: str = "") -> Dict[str, Any]:
        """Says the job is still running. `output` is the end of what it has printed so far, shown in the Terminal before it finishes (a server from before this ignores it)."""
        return self._call("POST", f"/api/runner/jobs/{urllib.parse.quote(job_id)}/heartbeat", {"output_tail": output} if output else {})

    def result(self, job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call("POST", f"/api/runner/jobs/{urllib.parse.quote(job_id)}/result", payload)
