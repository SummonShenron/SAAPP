"""The `saapp-runner` command.

    saapp-runner pair --server https://your-saapp-address CODE     one time per computer: trade the code from SAAPP's settings for this computer's key
    saapp-runner start                                             run: waits for jobs you approve in SAAPP and runs them here (for every project of this computer)
    saapp-runner install-service                                   start it by itself, in the background, every time you log in (and now)
    saapp-runner uninstall-service                                 stop doing that, and stop the background copy
    saapp-runner add-project --root FOLDER                         let the runner work in this project folder (SAAPP can also ask: it then shows you a window to choose the folder)
    saapp-runner remove-project --root FOLDER                      stop serving a project (nothing in the folder is touched)
    saapp-runner projects                                          the projects this computer serves
    saapp-runner status                                            is it paired, set to start at login, running; the projects; and the end of its log
    saapp-runner init [--root .]                                   optional: write a project's detected checks into your config so you can edit them (asks first)

ONE RUNNER PER COMPUTER. It serves any number of project folders, each of which you allowed on THIS computer (by typing it, or by choosing it in a window the runner shows). The runner never listens on
a port. It runs only the checks it finds in a project (tests, type check) or you wrote in your config, only after you press Run in SAAPP. Which of them are turned on, and whether a fake AI key is used
while they run, is chosen in SAAPP's settings.
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from . import confirm as confirm_module
from . import picker as picker_module
from . import projects, runlog, service, update
from . import status as status_codes
from .client import Client, ServerError, Unreachable
from .config import ConfigError, append_project, command_shown, config_path, credentials_path, load_credentials, save_credentials
from .lock import InstanceLock
from .loop import Runner
from .presets import detect as detect_checks, scan


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
    print(f"Paired this computer as {made.get('name') or 'a runner'}. Its key is saved in {credentials_path()} (readable only by you).")
    if args.root:
        done, message = projects.add(Path(args.root))
        print(message)
        if not done:
            return 1
    print("Next: saapp-runner install-service   (or, to run it in this window: saapp-runner start)")
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


def cmd_add_project(args: argparse.Namespace) -> int:
    folder = args.root
    if not folder:
        try:
            folder = input("Project folder (type its path): ").strip().strip('"').strip("'")
        except EOFError:
            folder = ""
        if not folder:
            print("No folder was given. Nothing was added.")
            return 1
    done, message = projects.add(Path(folder))
    print(message)
    return 0 if done else 1


def cmd_remove_project(args: argparse.Namespace) -> int:
    if projects.remove(Path(args.root)):
        print(f"{Path(args.root).resolve().name} is no longer one of this computer's projects. Nothing in the folder was touched.")
        return 0
    print("That folder is not one of this computer's projects.")
    return 1


def cmd_projects(args: argparse.Namespace) -> int:
    listed = projects.describe()
    if not listed:
        print("No projects yet. In SAAPP's settings choose Add a project (a window here asks you to choose the folder), or run: saapp-runner add-project --root <folder>")
        return 0
    for item in listed:
        print(f"{item['id']}  {item['root']}" + ("" if item["exists"] else "   (folder not found)"))
    return 0


def cmd_pick_folder(args: argparse.Namespace) -> int:
    """The window that asks the person to choose a project's folder. Run by the runner in its own process (picker.py); prints one line of JSON."""
    print(json.dumps(picker_module.run_dialog(args.name)))
    return 0


def cmd_confirm_command(args: argparse.Namespace) -> int:
    """The window that asks the person to confirm a command that changes things. Run by the runner in its own process (confirm.py); prints one line of JSON."""
    try:
        notes = [str(n) for n in json.loads(args.notes or "[]")]
    except ValueError:
        notes = []
    print(json.dumps(confirm_module.run_dialog(args.line, notes)))
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Serves the runner's checks and commands to an MCP client over stdio (mcp_server.py). Needs no pairing and no key: it only uses the projects this computer allowed. stdout carries the protocol and
    nothing else; messages go to stderr."""
    if getattr(args, "print_config", False):
        from .mcp_server import client_config

        print(json.dumps(client_config(style=args.format), indent=2))
        return 0
    import io

    from .mcp_server import McpServer

    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", newline="\n")
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", newline="\n", write_through=True)       # one JSON message per line, always "\n" (never "\r\n" on Windows)
    server = McpServer(asker=confirm_module.new_asker(), timeout=args.timeout)
    try:
        return server.serve(stdin, stdout)
    except KeyboardInterrupt:
        return 0


def cmd_start(args: argparse.Namespace, say=print, stop=None) -> int:
    credentials = load_credentials()
    if not credentials:
        print("This computer is not paired yet. Run: saapp-runner pair --server <address> <code>   (the code is in SAAPP's settings, under Run checks on your machine)")
        return 1
    if getattr(args, "root", None):
        done, message = projects.add(Path(args.root))          # a folder typed on this computer is allowed; this is the same as add-project
        say(message)
        if not done:
            return 1
    try:
        client = Client(credentials["server"], credentials["key"])
    except ValueError as problem:
        print(problem)
        return 1
    background = bool(getattr(args, "service", False))
    if background:
        say = runlog.file_say(projects.load)               # no window to print to: a cleaned log instead (runlog.py)
    lock = InstanceLock()
    if not lock.acquire():
        say("A runner for this computer is already running (it may have started by itself when you logged in), so this one did nothing. See: saapp-runner status")
        return 0
    try:
        return _serve(args, client, say, stop, background)
    finally:
        lock.release()


def _serve(args: argparse.Namespace, client: Client, say, stop, background: bool) -> int:
    say(f"saapp-runner {__version__}   server {client.server}")
    roots = projects.load()
    for root in roots:
        checks, config_ok, problem = scan(root)
        say(f"project {root.name}   {root}")
        if problem:
            say(problem)
        for name, words in checks.items():
            say(f"  can run: {name} = {command_shown(words)}")
        _, ready = status_codes.assess(root, checks, config_ok)
        for name, found in ready.items():
            if not found:
                say(f"  note: the program for '{name}' could not be found on this computer, so it would fail.")
        if not checks:
            say(f"  Found no tests or type check in {root}. If it has some, add them in {config_path()} (see the README); this checks again every minute, so there is no need to restart.")
    if not roots:
        say("No project yet. In SAAPP's settings choose Add a project: a window here asks you to choose its folder. (Or run: saapp-runner add-project --root <folder>)")
    else:
        say("It runs only these, only after you press Run in SAAPP (turn any of them off in SAAPP's settings). Press Ctrl+C to stop.")
    runner = Runner(client, None, say=say, stop=stop, timeout=args.timeout, roots_provider=projects.load, launch="service" if background else "hand", updater=update.attempt,
                    picker=picker_module.new_picker(), asker=confirm_module.new_asker())
    if not runner.hello():
        return 1
    say("Connected. Waiting for checks you approve in SAAPP...")
    try:
        runner.serve()
    except KeyboardInterrupt:
        say("Stopped.")
        return 0
    return runner.exit_code


def _needs_pairing() -> bool:
    if load_credentials():
        return False
    print("This computer is not paired yet. Run: saapp-runner pair --server <address> <code>   (the code is in SAAPP's settings)")
    return True


def cmd_install_service(args: argparse.Namespace) -> int:
    if _needs_pairing():
        return 1
    done, message = service.install()
    print(message)
    if not done:
        print("You can still run it by hand: saapp-runner start")
    return 0 if done else 1


def cmd_uninstall_service(args: argparse.Namespace) -> int:
    done, message = service.uninstall()
    print(message)
    return 0 if done else 1


def cmd_status(args: argparse.Namespace) -> int:
    print(f"saapp-runner {__version__}")
    print("paired with SAAPP:        " + ("yes" if load_credentials() else "no (run the setup file from SAAPP's settings)"))
    entry = service.state()
    print("starts at login:          " + ("yes" if entry["installed"] else "no (saapp-runner install-service)"))
    probe = InstanceLock()
    free = probe.acquire()
    if free:
        probe.release()
    print("running right now:        " + ("no" if free else "yes"))
    listed = projects.describe()
    print("projects:                 " + (str(len(listed)) if listed else "none yet (SAAPP's settings, Add a project; or: saapp-runner add-project --root <folder>)"))
    for item in listed:
        print(f"  {item['root']}" + ("" if item["exists"] else "   (folder not found)"))
    recent = runlog.tail()
    if recent:
        print("\nThe end of the log (" + str(runlog.log_path()) + "):\n" + recent)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="saapp-runner", description="Runs checks you approve in SAAPP on your own machine.")
    parser.add_argument("--version", action="version", version=f"saapp-runner {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    pair = sub.add_parser("pair", help="trade the one-time code from SAAPP's settings for this computer's key")
    pair.add_argument("code")
    pair.add_argument("--server", required=True, help="the SAAPP address, for example https://example.onrender.com")
    pair.add_argument("--root", default=None, help="a project folder to allow right away (optional: you can add projects later, in SAAPP's settings)")
    init = sub.add_parser("init", help="optional: write the checks found in a project into your config so you can edit them (asks before adding each)")
    init.add_argument("--root", default=".")
    init.add_argument("--yes", action="store_true", help="add every suggestion without asking")
    start = sub.add_parser("start", help="wait for checks you approve in SAAPP and run them here")
    start.add_argument("--root", default=None, help="also allow this project folder (the same as add-project)")
    start.add_argument("--timeout", type=float, default=900, help="seconds a single check may run (default 900)")
    start.add_argument("--service", action="store_true", help="run in the background (no window): messages go to a cleaned log file instead; used by the start-at-login entry")
    add = sub.add_parser("add-project", help="let the runner work in a project folder")
    add.add_argument("--root", default=None, help="the project folder (asked for when left out)")
    sub.add_parser("remove-project", help="stop serving a project folder (nothing in it is touched)").add_argument("--root", required=True)
    pick = sub.add_parser("pick-folder", help=argparse.SUPPRESS)
    pick.add_argument("--name", required=True)
    mcp = sub.add_parser("mcp", help="serve this computer's projects, checks and the command language to an MCP client (Claude Desktop, Claude Code...) over stdio")
    mcp.add_argument("--timeout", type=float, default=300, help="seconds one call may run (default 300)")
    mcp.add_argument("--print-config", action="store_true", help="print the entry an MCP client's configuration needs, then exit")
    mcp.add_argument("--format", choices=("portable", "vscode"), default="portable", help="with --print-config: portable (mcpServers: Claude, Copilot's mcp-config.json) or vscode (servers, for .vscode/mcp.json)")
    ask = sub.add_parser("confirm-command", help=argparse.SUPPRESS)
    ask.add_argument("--line", required=True)
    ask.add_argument("--notes", default="[]")
    for name, text in (("install-service", "start the runner by itself, in the background, whenever you log in"), ("uninstall-service", "stop starting the runner by itself, and stop the background copy"),
                       ("status", "show whether this computer's runner is paired, set to start at login, running, and which projects it serves"), ("projects", "list the projects this computer serves")):
        sub.add_parser(name, help=text)
    args = parser.parse_args(argv)
    return {"pair": cmd_pair, "init": cmd_init, "start": cmd_start, "install-service": cmd_install_service, "uninstall-service": cmd_uninstall_service, "status": cmd_status, "add-project": cmd_add_project,
            "remove-project": cmd_remove_project, "projects": cmd_projects, "pick-folder": cmd_pick_folder, "confirm-command": cmd_confirm_command, "mcp": cmd_mcp}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
