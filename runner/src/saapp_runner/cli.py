"""The `saapp-runner` command.

    saapp-runner pair --server https://your-saapp-address [--root .] CODE    one time per project: trade the code from SAAPP's settings for that project's key
    saapp-runner start [--root .]                                  run: waits for jobs you approve in SAAPP and runs them here
    saapp-runner init [--root .]                                   optional: write the detected checks into your config so you can edit them (asks first)

The runner never listens on a port. It runs only the checks it finds in this folder (tests, type check) or you wrote in your config, only after you press Run in SAAPP. Which of them are turned on,
and whether a fake AI key is used while they run, is chosen in SAAPP's settings.
"""
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .client import Client, ServerError, Unreachable
from .config import ConfigError, append_project, command_shown, config_path, credentials_path, load_credentials, save_credentials
from .loop import Runner
from .presets import detect as detect_checks, effective_checks


def _ask(question: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    try:
        return input(f"{question} [Y/n] ").strip().lower() in ("", "y", "yes")
    except EOFError:
        return False


def cmd_pair(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"{root} is not a folder.")
        return 1
    try:
        client = Client(args.server)
        made = client.pair(args.code)
    except (ValueError, ServerError, Unreachable) as problem:
        print(f"Could not pair: {getattr(problem, 'message', None) or problem}")
        return 1
    save_credentials(client.server, made["key"], root)
    print(f"Paired {root} as {made.get('name') or 'this runner'}. Its key is saved in {credentials_path()} (readable only by you).")
    print(f"Next: saapp-runner start --root \"{root}\"")
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
    credentials = load_credentials(root)
    if not credentials:
        print(f"This project is not paired yet. Run: saapp-runner pair --server <address> --root \"{root}\" <code>   (the code is in SAAPP's settings)")
        return 1
    try:
        checks = effective_checks(root)
        client = Client(credentials["server"], credentials["key"])
    except (ConfigError, ValueError) as problem:
        print(problem)
        return 1
    if not checks:
        print(f"Found no tests or type check to run in {root}. If it has some, write them in {config_path()} (see the README).")
        return 1
    say(f"saapp-runner {__version__}   server {client.server}   folder {root}")
    for name, words in checks.items():
        say(f"  can run: {name} = {command_shown(words)}")
    say("It runs only these, only after you press Run in SAAPP (turn any of them off in SAAPP's settings). Press Ctrl+C to stop.")
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
    pair.add_argument("--root", default=".", help="the project folder this runner is for (each project has its own runner and key)")
    init = sub.add_parser("init", help="optional: write the checks found in a project into your config so you can edit them (asks before adding each)")
    init.add_argument("--root", default=".")
    init.add_argument("--yes", action="store_true", help="add every suggestion without asking")
    start = sub.add_parser("start", help="wait for checks you approve in SAAPP and run them here")
    start.add_argument("--root", default=".")
    start.add_argument("--timeout", type=float, default=900, help="seconds a single check may run (default 900)")
    args = parser.parse_args(argv)
    return {"pair": cmd_pair, "init": cmd_init, "start": cmd_start}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
