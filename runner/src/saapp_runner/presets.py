"""The checks the runner works out for itself from a project's files, so nobody has to write a config to start.

THE COMMANDS COME FROM THIS FILE AND FROM WHAT IS ON THIS MACHINE, NEVER FROM THE SERVER. A preset is a fixed template (pytest, `npm test`, the TypeScript compiler); the runner fills in the project's own
Python from its virtual environment and nothing else. The SAAPP page can turn a check on or off and choose whether a fake AI key is used, and that is all it can say about a command.

A check you write yourself in `~/.saapp-runner/config.toml` still works, and replaces a preset of the same name.
"""
import json
from pathlib import Path
from typing import Dict, List, Optional

from .config import load_checks

# The AI provider keys a project's tests might read. While a check runs with "fake keys" on, each is set to this value, so a test can never spend real money even when the project's own `.env` file holds a
# real key (a `.env` file does not replace a variable that is already set).
FAKE_KEY_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
FAKE_KEY_VALUE = "test-not-real"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def project_python(root: Path) -> Optional[Path]:
    """The Python inside the project's own virtual environment (`.venv` or `venv`), where its test tools are installed. None when it has none."""
    candidates = [root / folder / part for folder in (".venv", "venv") for part in ("Scripts/python.exe", "bin/python")]
    return next((path for path in candidates if path.exists()), None)


def detect(root: Path) -> Dict[str, List[str]]:
    """The checks this project probably has, found by looking at its files: {name: words}. Nothing is run to find them."""
    root = Path(root)
    found: Dict[str, List[str]] = {}
    python = project_python(root)
    has_pytest = (root / "pytest.ini").exists() or (root / "tests").is_dir() or "pytest" in _read(root / "pyproject.toml") or "pytest" in _read(root / "setup.cfg")
    if has_pytest:
        found["tests"] = [str(python) if python else "pytest", *(["-m", "pytest"] if python else []), "-q", "-p", "no:cacheprovider", "{files}"]
    package = _read(root / "package.json")
    if package:
        try:
            scripts = json.loads(package).get("scripts") or {}
        except (ValueError, AttributeError):
            scripts = {}
        if isinstance(scripts, dict) and "test" in scripts:
            found["js-tests"] = ["npm", "test"]
    if (root / "tsconfig.json").exists():
        found["types"] = ["npx", "tsc", "-b"]
    return found


def effective_checks(root: Path) -> Dict[str, List[str]]:
    """What this runner can run for the project: the detected presets, with anything written in the config replacing a preset of the same name. Raises ConfigError for a config it cannot read."""
    return {**detect(root), **load_checks(root)}
