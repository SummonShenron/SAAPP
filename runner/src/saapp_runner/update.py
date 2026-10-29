"""Updating the runner to a newer release, when the person asked for it in SAAPP (a click on Update, or the tick for automatic updates).

THE SERVER NEVER NAMES WHERE TO INSTALL FROM. It sends a version NUMBER, and that is all. The runner builds the address itself, from where IT was installed: pip records the address a package was
installed from in its `direct_url.json`, and for a release that is `https://github.com/<owner>/<repo>/archive/refs/tags/runner-v<version>.zip` with the sub-folder `runner`. The new address is the same
owner, the same repository, the same shape, with the new version. So a compromised SAAPP server can at most ask for a newer release that the repository's own owner published, and a version that is
not strictly newer (a downgrade, the same version, text that is not a version) is refused. A runner that was not installed from such an address (a source checkout, an editable install) does not update
itself and says so.

AFTER AN UPDATE the runner stops. When it was started at login the operating system starts it again (it exits with `EXIT_RESTART`, a code every system's entry treats as "start it again": Task
Scheduler's restart on failure, launchd's keep-alive, systemd's restart on failure); when a person started it by hand it says so and they start it again. An update is only ever done while no check is
running (SAAPP does not offer one then).

A FAILED UPDATE leaves the old runner in place (pip builds the new one before it removes the old), is remembered for a few hours so it is not retried every few seconds, and is reported to SAAPP as the
status `update_failed`, which the page turns into a sentence.

KNOWN LIMIT: the archive is not checked against a published hash. GitHub archives are not guaranteed to be byte-identical from one request to the next, so a hash would need a published artifact; the
protection today is the address (the owner's own repository, over https) and that only a newer release is accepted.
"""
import json
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import __version__
from .config import home
from .execute import no_window

EXIT_RESTART = 75                 # EX_TEMPFAIL: "try again" to every system's start-at-login entry
RETRY_AFTER_FAILURE_HOURS = 6.0
_VERSION_RE = re.compile(r"^(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
_SOURCE_RE = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})/archive/refs/tags/runner-v\d{1,4}\.\d{1,4}\.\d{1,4}\.zip$")

Run = Callable[[List[str]], Tuple[int, str]]


def parse_version(text: Any) -> Optional[Tuple[int, int, int]]:
    match = _VERSION_RE.match(str(text or "").strip())
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def accept(current: str, offered: Any) -> Optional[str]:
    """The version to update to, normalised, or None when the offer is not a plain version that is strictly newer than this one. Pure."""
    have, want = parse_version(current), parse_version(offered)
    if have is None or want is None or want <= have:
        return None
    return ".".join(str(part) for part in want)


def _direct_url_text() -> Optional[str]:
    try:
        from importlib import metadata

        return metadata.distribution("saapp-runner").read_text("direct_url.json")
    except Exception:
        return None


def source_repo() -> Optional[Tuple[str, str]]:
    """(owner, repository) this runner was installed from, read from what pip recorded, or None when it was not installed from a GitHub release archive of this shape."""
    text = _direct_url_text()
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    match = _SOURCE_RE.match(str(data.get("url") or "")) if isinstance(data, dict) else None
    if not match or (data.get("subdirectory") or "") != "runner":
        return None
    return match.group(1), match.group(2)


def archive_url(owner: str, repo: str, version: str) -> str:
    return f"https://github.com/{owner}/{repo}/archive/refs/tags/runner-v{version}.zip#subdirectory=runner"


# ---------------------------------------------------------------------------------------------------------------
# What was tried (so a failure is not retried every few seconds, and the page can say it failed)
# ---------------------------------------------------------------------------------------------------------------

def _record_path():
    return home() / "update.json"


def record_attempt(version: str, ok: bool) -> None:
    try:
        home().mkdir(parents=True, exist_ok=True)
        _record_path().write_text(json.dumps({"version": version, "from": __version__, "ok": ok, "at": time.time()}), encoding="utf-8")
    except OSError:
        pass


def _last_attempt() -> Dict[str, Any]:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def failed_recently(version: Optional[str] = None, now: Optional[float] = None) -> bool:
    """Whether the last update attempt from THIS version failed within the last few hours (for `version` when given). A runner that has since been replaced by hand is no longer the one that failed."""
    last = _last_attempt()
    if not last or last.get("ok") or last.get("from") != __version__ or (version is not None and last.get("version") != version):
        return False
    return ((now if now is not None else time.time()) - float(last.get("at") or 0)) < RETRY_AFTER_FAILURE_HOURS * 3600


# ---------------------------------------------------------------------------------------------------------------
# Doing it
# ---------------------------------------------------------------------------------------------------------------

def _pip(command: List[str]) -> Tuple[int, str]:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=900, errors="replace", **no_window())
    except (OSError, subprocess.SubprocessError) as problem:
        return 127, problem.__class__.__name__
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def _installed_version() -> str:
    """The version a fresh Python process finds installed (not the one already loaded in this process)."""
    code, output = _pip([sys.executable, "-I", "-c", "import saapp_runner; print(saapp_runner.__version__)"])
    return output.strip().splitlines()[-1].strip() if code == 0 and output.strip() else ""


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else ""


def apply(offered: Any, run: Run = _pip, verify: Callable[[], str] = _installed_version, url_for: Optional[Callable[[str], str]] = None) -> Tuple[bool, str]:
    """Installs the offered version over this one. (True, "Updated 0.1.1 -> 0.1.2.") or (False, why). `url_for` exists for tests and is never given by the runner itself. Never raises."""
    target = accept(__version__, offered)
    if target is None:
        return False, "Not updating: the offered version is not newer than this one."
    if url_for is not None:
        url = url_for(target)
    else:
        repo = source_repo()
        if repo is None:
            return False, "Not updating by itself: this runner was not installed from a SAAPP release archive. Run the setup file again to update it."
        url = archive_url(repo[0], repo[1], target)
    code, output = run([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", url])
    if code != 0:
        record_attempt(target, False)
        return False, f"The update to {target} did not install (pip exit code {code}). {_last_line(output)} The current version keeps running."
    installed = verify()
    if installed != target:
        record_attempt(target, False)
        if url_for is None and (repo := source_repo()):
            run([sys.executable, "-m", "pip", "install", "--upgrade", "--disable-pip-version-check", archive_url(repo[0], repo[1], __version__)])       # put the old one back, best effort
        return False, f"The update to {target} installed but the runner reports {installed or 'no version'}, so it was put back."
    record_attempt(target, True)
    return True, f"Updated {__version__} -> {target}."


def attempt(offered: Any, **kwargs: Any) -> Tuple[bool, str]:
    """`apply`, except that an offer that failed recently is not tried again yet (it says so quietly). This is what the runner's loop calls."""
    target = accept(__version__, offered)
    if target is not None and failed_recently(target):
        return False, ""
    return apply(offered, **kwargs)
