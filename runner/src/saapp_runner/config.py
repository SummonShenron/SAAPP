"""Where the runner keeps its key and the checks YOU define, and how a check's command is read.

THE CHECKS LIVE IN YOUR HOME FOLDER, NOT IN THE PROJECT. `~/.saapp-runner/config.toml`, one section per project folder. A file inside a project would let a cloned repository decide which command runs when
you press Run, the same attack as a malicious `.vscode/tasks.json` or git hook. The command on the card in SAAPP is always one you chose.

    [projects."C:\\Users\\you\\work\\myapp"]
    tests = ["C:\\Users\\you\\work\\myapp\\.venv\\Scripts\\python.exe", "-m", "pytest", "-q", "{files}"]
    types = ["npx", "tsc", "-b"]

A command is a LIST of words (a plain string is split like a shell would, but a list is safer on Windows paths). `{files}` may appear once, as a whole word: it becomes the files SAAPP asked for, or nothing.
There is no shell: the words are run directly, so nothing in them is expanded, piped or chained.
"""
import json
import os
import re
import shlex
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Optional

PLACEHOLDER = "{files}"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ConfigError(Exception):
    """A problem with the config, said in words."""


def home() -> Path:
    return Path(os.environ.get("SAAPP_RUNNER_HOME") or (Path.home() / ".saapp-runner"))


def credentials_path() -> Path:
    return home() / "credentials.json"


def config_path() -> Path:
    return home() / "config.toml"


def _private(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass                         # a filesystem without permissions (some Windows setups): the folder is still in the person's own profile


def project_key(root: Path) -> str:
    """The name a project's section goes under: its full path, in the form the operating system compares paths in."""
    return os.path.normcase(str(Path(root).resolve()))


def _read_credentials() -> Dict[str, Any]:
    try:
        data = json.loads(credentials_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_credentials(server: str, key: str) -> None:
    """Keeps the server address and THIS COMPUTER's key where only the owner can read them. There is one key per computer (one runner serves all of the computer's projects), and pairing again replaces it.
    An older runner kept one key per project folder under `projects`; that file is not read any more, so a computer set up that way pairs once more."""
    folder = home()
    folder.mkdir(parents=True, exist_ok=True)
    _private(folder, 0o700)
    path = credentials_path()
    path.write_text(json.dumps({"server": server.rstrip("/"), "key": key}), encoding="utf-8")
    _private(path, 0o600)


def load_credentials() -> Optional[Dict[str, str]]:
    """The server address and key this computer was paired with, or None."""
    data = _read_credentials()
    return {"server": data["server"], "key": data["key"]} if isinstance(data.get("server"), str) and isinstance(data.get("key"), str) and data["server"] and data["key"] else None


def parse_command(value: Any) -> List[str]:
    """A check's command as a list of words, or a ConfigError. `{files}` must be a whole word and appear at most once."""
    if isinstance(value, str):
        try:
            words = shlex.split(value)
        except ValueError as problem:
            raise ConfigError(f"could not read the command {value!r}: {problem}") from None
    elif isinstance(value, list) and all(isinstance(w, str) for w in value):
        words = list(value)
    else:
        raise ConfigError("a command is a list of words, for example [\"pytest\", \"-q\", \"{files}\"].")
    if not words or not words[0].strip():
        raise ConfigError("a command cannot be empty.")
    if words.count(PLACEHOLDER) > 1:
        raise ConfigError(f"{PLACEHOLDER} can appear only once in a command.")
    if any(PLACEHOLDER in word and word != PLACEHOLDER for word in words):
        raise ConfigError(f"{PLACEHOLDER} must be a whole word of its own, not part of another word.")
    return words


def load_checks(root: Path) -> Dict[str, List[str]]:
    """The checks defined for this project folder: {name: words}. {} when there is no config or no section for it. Raises ConfigError for a file or a command it cannot read."""
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as problem:
        raise ConfigError(f"could not read {path}: {problem}") from None
    section = (data.get("projects") or {}).get(project_key(root))
    if not isinstance(section, dict):
        return {}
    checks: Dict[str, List[str]] = {}
    for name, value in section.items():
        if not _NAME_RE.match(str(name)):
            raise ConfigError(f"{name!r} is not a good check name: use lower-case letters, digits, dashes and underscores (up to 32).")
        checks[name] = parse_command(value)
    return checks


def command_shown(words: List[str]) -> str:
    """What the card in SAAPP shows for a command. For reading only: it is never run through a shell."""
    return " ".join(shlex.quote(w) if re.search(r"\s", w) else w for w in words)


def _toml_string(text: str) -> str:
    return json.dumps(text)          # a JSON string is a valid TOML basic string


def append_project(root: Path, checks: Dict[str, List[str]]) -> Path:
    """Adds this project's section to the config (creating the file). Appends text, so nothing you wrote by hand is rewritten or lost. Refuses when the project already has a section."""
    path = config_path()
    key = project_key(root)
    if path.exists():
        try:
            existing = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as problem:
            raise ConfigError(f"could not read {path}: {problem}") from None
        if key in (existing.get("projects") or {}):
            raise ConfigError(f"{path} already has a section for this project. Edit it by hand.")
    for name, words in checks.items():
        if not _NAME_RE.match(name):
            raise ConfigError(f"{name!r} is not a good check name.")
        parse_command(words)
    lines = [f"[projects.{_toml_string(key)}]"] + [f"{name} = [{', '.join(_toml_string(w) for w in words)}]" for name, words in checks.items()]
    home().mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(("\n" if path.exists() and path.stat().st_size else "") + "\n".join(lines) + "\n")
    _private(path, 0o600)
    return path
