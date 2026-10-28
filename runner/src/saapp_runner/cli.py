"""The `saapp-runner` command.

    saapp-runner pair --server https://your-saapp-address CODE    one time: trade the code from SAAPP's settings for a key
    saapp-runner init [--root .]                                   one time per project: choose which checks exist (asks first)
    saapp-runner start [--root .]                                  run: waits for jobs you approve in SAAPP and runs them here

The runner never listens on a port. It runs only the checks YOU defined for this folder, only after you press Run in SAAPP.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .client import Client, ServerError, Unreachable
from .config import ConfigError, append_project, command_shown, config_path, credentials_path, load_checks, load_credentials, save_credentials
from .loop import Runner


def detect_checks(root: Path) -> Dict[str, List[str]]:
    """Checks this project probably has, found by looking at its files. A suggestion for `init` to ask about, never something that runs by itself."""
    found: Dict[str, List[str]] = {}
    venv_python = next((p for p in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python") if p.exists()), None)
    has_pytest = (root / "pytest.ini").exists() or (root / "tests").is_dir() or "pytest" in _read(root / "pyproject.toml") or "pytest" in _read(root / "setup.cfg")
    if has_pytest:
        found["tests"] = [str(venv_python), "-m", "pytest", "-q", "{files}"] if venv_python else ["pytest", "-q", "{files}"]
    package = _read(root / "package.json")
    if package:
        try:
            scripts = json.loads(package).get("scripts") or {}
        except ValueError:
            scripts = {}
        if "test" in scripts:
            found["js-tests"] = ["npm", "test"]
    if (root / "tsconfig.json").exists():
        found["types"] = ["npx", "tsc", "-b"]
    return found


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def cmd_pair(args: argparse.Namespace) -> int:
    try:
        client = Client(args.server)
        made = client.pair(args.code)
    except (ValueError, ServerError, Unreachable) as problem:
        print(f"Could not pair: {getattr(problem, 'message', None) or problem}")
        return 1
    save_credentials(client.server, made["key"])
    print(f"Paired as {made.get('name') or 'this runner'}. Your key is saved in {credentials_path()} (readable only by you).")
    print("Next: saapp-runner init   (once per project),  then  saapp-runner start")
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"{root} is not a folder.")
        return 1
    suggestions = detect_checks(root)
    if not suggestions:
        print(f"Found nothing to suggest in {root}. Write your checks by hand in {config_path()} (see the README).")
        return 1
    chosen: Dict[str, List[str]] = {}
    print(f"Checks I can set up for {root}:")
    for name, words in suggestions.items():
        print(f"  {name}: {command_shown(words)}")
        if _ask(f"Add the check '{name}'?", args.yes):
            chosen[name] = words
    if not chosen:
        print("Nothing added.")
        return 0
    try:
        path = append_project(root, chosen)
    except ConfigError as problem:
        print(problem)
        return 1
    print(f"Saved to {path}. These are the ONLY commands this runner will ever run for this project.")
    return 0


def cmd_start(args: argparse.Namespace, say=print, stop=None) -> int:
    root = Path(args.root).resolve()
    credentials = load_credentials()
    if not credentials:
        print("This runner is not paired yet. Run: saapp-runner pair --server <address> <code>   (the code is in SAAPP's settings)")
        return 1
    try:
        checks = load_checks(root)
        client = Client(credentials["server"], credentials["key"])
    except (ConfigError, ValueError) as problem:
        print(problem)
        return 1
    if not checks:
        print(f"No checks are defined for {root}. Run: saapp-runner init --root \"{root}\"")
        return 1
    say(f"saapp-runner {__version__}   server {client.server}   folder {root}")
    for name, words in checks.items():
        say(f"  can run: {name} = {command_shown(words)}")
    say("It runs only these, only after you press Run in SAAPP. Press Ctrl+C to stop.")
    runner = Runner(client, root, checks, say=say, stop=stop, timeout=args.timeout)
    if not runner.hello():
        return 1
    say("Connected. Waiting for checks you approve in SAAPP...")
    try:
        runner.serve()
    except KeyboardInterrupt:
        say("Stopped.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="saapp-runner", description="Runs checks you approve in SAAPP on your own machine.")
    parser.add_argument("--version", action="version", version=f"saapp-runner {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="trade the one-time code from SAAPP's settings for a key")
    pair.add_argument("code")
    pair.add_argument("--server", required=True, help="the SAAPP address, for example https://example.onrender.com")
    init = sub.add_parser("init", help="choose which checks exist for a project (asks before adding each)")
    init.add_argument("--root", default=".")
    init.add_argument("--yes", action="store_true", help="add every suggestion without asking")
    start = sub.add_parser("start", help="wait for checks you approve in SAAPP and run them here")
    start.add_argument("--root", default=".")
    start.add_argument("--timeout", type=float, default=900, help="seconds a single check may run (default 900)")
    args = parser.parse_args(argv)
    return {"pair": cmd_pair, "init": cmd_init, "start": cmd_start}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
