# saapp-runner

Runs checks (tests, type checks, builds) **you approve in SAAPP**, on **your own machine**, in **your own projects**. One runner per computer serves all of that computer's projects.

SAAPP's assistant can propose a check, such as "run the tests on these files". You see the exact command on a card in the chat and press **Run**. This program, running on your computer, then runs that command in the right project folder and sends back the end of its output. Nothing runs without your click.

## What it will and will not do

- It works **only in project folders you chose on this computer** (see "Projects" below), and runs **only checks it found in them or you wrote yourself**. It finds the common ones by looking at the folder (pytest, `npm test`, the TypeScript compiler), and anything you write goes in a config file in **your home folder** (`~/.saapp-runner/config.toml`), never in the project. A cloned repository cannot add a command or choose what runs when you press Run.
- A **check** job names a check, and the command comes from the runner; the only other thing it carries is your yes or no on fake AI keys (below). If SAAPP asked for a check it does not have, or for a file outside the project, it refuses.
- A **command** job (see "Commands" below) carries one line you read, word for word, on a card in SAAPP. The runner does not trust the card: it decides again on your computer, and for anything that changes things it asks you again in a window of its own.
- It **never listens on a port**. It connects out to SAAPP and asks for jobs you approved.
- Files are passed as separate arguments (`./path`), with **no shell**, so nothing is expanded or chained.
- The command gets a **clean environment**: the secrets in your shell (API keys, tokens) are not passed to it.
- It has a time limit (15 minutes by default) and kills the whole process tree when it passes. Only the **end** of the output is kept (about 6,000 characters).
- Your key (one per computer) is stored only in `~/.saapp-runner/credentials.json`, readable only by you.
- **It is not a sandbox.** The command runs with your own privileges, because you wrote it and you pressed Run. Read the command on the card before you approve it.

## Install

Python 3.11 or newer. From a tagged release of the repository (the SAAPP settings page shows the exact address for the version it wants):

```bash
pipx install "https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.zip#subdirectory=runner"
```

or run it without installing: `uvx --from "<the same address>" saapp-runner ...`. An archive address needs no `git` on your machine.

## First time

1. In SAAPP, open Settings, **Run checks on your machine**, and choose **Add a runner**. You get a one-time code that works for a few minutes, and a setup file for your system that does steps 2 and 3 for you (it asks one question: `Continue? y`).
2. Pair this computer with it (one key per computer):

   ```bash
   saapp-runner pair --server https://<your-saapp-address> <CODE>
   ```

3. Set it to start by itself, in the background, now and every time you log in:

   ```bash
   saapp-runner install-service
   ```

4. Add a project (see below). It shows as **Connected** in SAAPP's settings within a minute.

## Projects

A runner serves any number of project folders, and **a folder gets onto the list only through something you do on this computer**. SAAPP can *ask* for a project by name, but it never sends a path.

- **From SAAPP:** under your runner, type the project's folder name (for example `my-app`) and choose **Add a project**. A window opens on your computer: it says what SAAPP asked for and that checks only ever run after you press Run, you choose the folder (or say no), and if the folder's name differs from the one asked for it checks once more. The folder connected in the chat is asked for automatically when you set up a computer from that chat.
- **From a terminal:** `saapp-runner add-project --root <folder>` (or `start --root <folder>`). If this computer cannot show a window (no screen, or no tkinter), SAAPP says so and shows this command.
- **Remove one:** **Remove project** in SAAPP, or `saapp-runner remove-project --root <folder>`. Nothing in the folder is touched.
- `saapp-runner projects` lists them. They are kept in `~/.saapp-runner/projects.json`. A project is the project's own folder: not a drive, and not your whole home folder.

Each project has its own checks, its own on/off choices and its own fake-key setting in SAAPP. When you chat with a folder connected, Sonic's check goes to the project with that folder's name.

## Running it by itself at login

`install-service` makes one entry per computer in your own account, never an administrator's: a Task Scheduler task on Windows (no console window, runs on battery, restarts if it ends), a LaunchAgent on macOS, a `systemd --user` unit on Linux. The entry runs the same thing you would type (`python -m saapp_runner start --service`) and holds no key and no folder.

```bash
saapp-runner status             # paired? set to start at login? running now? the projects, and the end of its log
saapp-runner uninstall-service  # stop starting it at login, and stop the background copy
saapp-runner start              # run it in this window instead (Ctrl+C stops it)
```

- Only one runner per computer runs at a time: starting a second one by hand while the background one is running does nothing and says so.
- In the background it writes `~/.saapp-runner/runner.log`: events only (started, connected, which check ran and its exit code, the status it reported). Every project's path and the commands are left out, and no output from a check is ever written there, so the log is safe to paste when asking for help.
- macOS and Linux entries are written and checked as text, but have **not** been run on a real Mac or Linux machine yet; Windows has. The same goes for the folder window: it is plain tkinter, tested here with a stand-in for the window itself.

## Commands

Besides the project's named checks, Sonic can ask to run a **command line** in your project, the way a terminal would: `git status`, `git diff | head -50`, `cd src && pytest -q`, `grep -rn TODO src`, `git commit -m "fix the parser"`. There is **no shell** behind it. The runner reads the line itself, in a small language that means the same on Windows, macOS and Linux:

- **Allowed:** programs by name (git, python, pytest, node, npm, npx, pip, tsc, eslint, ruff, black, cargo, go, make and similar), the built-ins `cd`, `pwd`, `echo`, `ls`, `dir`, `cat`, `type`, `head`, `tail`, `wc`, `grep`, joined with `&&`, `;` and `|`, and `>` or `>>` into a file in the project.
- **Not understood, so refused:** variables (`$HOME`), `$( )`, backticks, wildcards (`*.py`), `~`, `&`, `||`, `<`, subshells and comments. Nothing is expanded, so nothing can sneak in.
- **Refused outright:** shells and `sudo`, `curl` and anything else that reaches the network by itself, `rm` and `del`, `docker`, `ssh`; `git reset --hard`, `git clean`, `git push --force`, deleting a branch; `python -c` and `node -e` (code typed on the line); web addresses; any path outside the project folder (also through a link); and files that hold secrets (`.env`, keys, `.ssh`, `secrets.*`), which `grep -r` also skips.
- **Three classes**, shown on the card: *looks only* (`git status`, `ls`, `cat`), *runs a program* (`pytest`, `npm test`, `tsc`), and *changes things* (`git add` and `git commit`, `git push`, `npm install`, `pip install`, a formatter that rewrites files, `>` into a file). A line is as strong as its strongest command.
- **What you do:** a *looks only* or *runs a program* line needs your **Run** on the card. A line that **changes things** needs a second, stronger confirmation on the card, and then the runner opens **its own window on your computer** with the exact line and asks again. No web page can click that window. If your computer cannot show one, the line is refused, not run.
- **Skip the click for the harmless ones (off by default):** in SAAPP's settings, under your computer, "Run commands that only look around without asking" lets *looks only* lines start without a click. Nothing else is ever started without one.
- **git** runs with a few safety settings the card does not show: no hooks, no pager, no external diff program, no prompts. The project's own `.venv` or `venv` is put first on the search path, as if you had activated it, so `python`, `pip` and `pytest` mean the project's own.
- It is **not a sandbox**: an approved line runs with your privileges. The control is the language, the card, the window and your yes.

### Your own rules, on this computer

`~/.saapp-runner/commands.toml` (never sent to SAAPP, never changed by it):

```toml
confirm_changes = true                 # ask in a window here before a line that changes things (default true; false skips the window, the rules still apply)
allow = ["terraform-lint"]             # extra programs a line may use; they count as changes. The built-in never-list still wins over this.
refuse = ["git push", "npm install"]   # line beginnings that are always refused, whatever SAAPP says: a program name, or a program and its first words
```

A file the runner cannot read falls back to the safe defaults (ask, allow nothing extra) and says so in the log. The log never contains a command line, a path or a refusal's reason.

## Use it from other AI tools (MCP)

The runner can also serve its projects, checks and the command language to any [MCP](https://modelcontextprotocol.io) client (Claude Desktop, Claude Code, an editor), over stdio. It needs no SAAPP account, no pairing and no key: it only uses the projects this computer allowed (`saapp-runner add-project --root <folder>`).

```bash
saapp-runner mcp --print-config
```

prints the entry to put in the client's configuration (Claude Desktop: `claude_desktop_config.json`; Claude Code: `.mcp.json`, or `claude mcp add saapp-runner -- <that command>`; GitHub Copilot: `~/.copilot/mcp-config.json`, or the workspace `.mcp.json`). VS Code's own `.vscode/mcp.json` names things differently (`servers`, and a `type` on each entry), so for that file use `saapp-runner mcp --print-config --format vscode`. The portable form looks like:

```json
{ "mcpServers": { "saapp-runner": { "command": "<this Python>", "args": ["-m", "saapp_runner", "mcp"] } } }
```

| Tool | Hint to the client | What it does |
|---|---|---|
| `list_projects` | read only | the projects this computer allowed, with the named checks found in each |
| `run_check` | runs code | one of a project's named checks (its tests, type check), optionally on some files |
| `look` | read only | one command line that only looks (`git status`, `git diff`, `ls`, `cat`, `grep -rn`); a stronger line is refused with the reason |
| `run_command` | may change things | any line the command language allows; a line that changes things opens the runner's own window on this computer and runs nothing unless you say yes there |

The rules are the runner's, unchanged: a model names a project, never a folder; no shell; nothing outside the project; no secrets files; your own `commands.toml` refuse list; a clean environment with made-up AI keys (`fake_keys = false` in `commands.toml` passes no key at all); a time limit (`--timeout`, 300 s); a capped output. Your client's own approval prompt is the first confirmation and the window is the second for anything that changes things. Every result says its output is text produced on your computer, to be read as data. One command runs at a time, and a call the client cancels is stopped.

`stdout` carries only the protocol. Events go to `stderr` and never contain a command, a path or any output. It speaks protocol versions 2025-06-18, 2025-03-26 and 2024-11-05, and is written with the standard library only (`mcp_server.py` says what each message does).

## Choosing in SAAPP

In SAAPP's settings, under each project of your runner, you can:

- **turn a check off** (an unticked check is never offered to the assistant and cannot be run);
- **use a fake AI key while checks run** (on by default). `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are set to a made-up value for the run, so a test cannot spend real money even when your project's `.env` file holds a real key (a `.env` file does not replace a variable that is already set).

SAAPP can only send check names, a project's name to ask for, and yes-or-no choices. It cannot add a check, change a command, or choose a folder. (An older runner, one key per project, still works until its computer runs the setup file again.)

## The config file (optional)

You only need this for a check the runner does not find, or to change one it does. A check written here **replaces a detected check of the same name**. `saapp-runner init` writes the detected checks into it as a starting point (it asks before adding each); you can edit it by hand. A command is a list of words.

```toml
[projects."C:\\Users\\you\\work\\myapp"]
tests = ["C:\\Users\\you\\work\\myapp\\.venv\\Scripts\\python.exe", "-m", "pytest", "-q", "{files}"]
types = ["npx", "tsc", "-b"]
```

`{files}` may appear once, as a whole word. It becomes the files SAAPP asked for, or nothing (so the check runs everything). A check without `{files}` takes no files.

## Remove it

Remove the runner in SAAPP's settings (its key stops working at once), then:

```bash
pipx uninstall saapp-runner
```

and delete `~/.saapp-runner`.

## Status

Version 0.1.1. The runner, the server side, SAAPP's Settings section (with a starter script that does the steps above for you) and the check card in the chat are built and tested. The design is in `docs/local-runner-plan.md`.

## Updating

A runner updates itself only when the person asks in SAAPP's settings (the **Update** button, or the **Update this runner automatically** tick, which is off by default), and only while no check is waiting or running. **SAAPP only sends a version number.** The runner builds the address itself, from where it was installed (pip records that), for the same owner and repository, and only for a strictly newer plain version; a runner that was not installed from a release archive says so and does not update itself. After installing it checks that a fresh process reports the new version, and if not it puts the old one back. A runner started at login then exits so the system starts the new one; one started by hand says to start it again. A failed update leaves the old runner running, is not retried for a few hours, and shows in SAAPP as "The last update didn't work". Known limit: the archive is not checked against a published hash.

## Making a release (for maintainers)

The version lives in three places that a test keeps equal: `runner/pyproject.toml`, `runner/src/saapp_runner/__init__.py`, and `WANTED_RUNNER_VERSION` in `backend/utils/runner_jobs.py`. The server builds the install address from that version, so the tag the starter script installs can never disagree with it. To release:

1. Bump the version in those three places and commit.
2. Tag it with the same version and push the tag:

   ```bash
   git tag runner-v0.1.2
   git push origin runner-v0.1.2
   ```

3. Deploy the server. Its Settings page now offers the new version: the starter script installs it with `pip install --upgrade`, and a runner that is already installed (0.1.1 or newer) shows an **Update** button, or updates itself when the person has turned on automatic updates.

A fork points at its own repository with `SAAPP_RUNNER_REPO=owner/name` on the server. Never move a tag that people may have installed: make a new version instead.

## Development

```bash
python -m pytest runner/tests
```
