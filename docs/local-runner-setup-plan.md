# Local runner: setup that just works (plan, 2026-10-27, not built)

Status: **a plan only.** Nothing here is built. The runner itself (finding checks, the Settings card, fake AI keys) works end to end as of 2026-10-27; see `docs/local-runner-plan.md` for that.

## The goal

Jack's words: **at most one "y" in a terminal, and everything else in the app.** The people using SAAPP are developers, but half the point of these tools is that they just work. Today a person has to download a script, run it, answer prompts, keep a window open, and know what to do when it fails.

What cannot move into the app: **a web page is not allowed to install software or start a program on a computer.** Something has to run once on the machine. That is the one "y". The plan below makes everything after it happen in the app.

## What the first real run showed (the failures this plan is built from)

1. **Tag and code disagreed.** The release tag was cut before the last commit, so the setup script installed an old runner, which exited with an old message.
2. **The script never upgrades.** If the runner is already installed it skips the install step, so a new release never reaches anyone.
3. **Errors vanish.** On a failure the script prints a message, waits for "Press any key", and the window closes. The person saw "the terminal disappeared".
4. **The runner must be started by hand** (`saapp-runner start`) and a window kept open. Close it and the app says "Not connected" with no hint why.
5. **One runner per project folder** means pairing, a folder prompt and a start command for every project.

## The flow we are aiming for

1. In the app: Settings, **Run checks on your machine**, one button: **Set up this computer**. It downloads one small file named for the person's system.
2. They run it. The only question it asks is `Continue? [y/N]`. It installs the runner into a private folder, pairs itself to the signed-in account without showing a code, registers itself to start at login, starts, and prints "Done. Go back to SAAPP."
3. The app shows **Connected** by itself.
4. In the app the person picks a project (the folder they already connect to chat). If the runner does not know the folder yet, **a native dialog on their computer asks once: "Let SAAPP run checks in <folder>?"** That is a click, not a command.
5. Settings shows the checks the runner found (already built), with tick boxes and the fake-key option (already built).
6. After that nothing needs doing. The runner starts at login, updates itself, and the app explains problems in plain words.

## The pieces

### 1. Release discipline (fixes failure 1; small, do first)
- One place says which runner version the server wants (`runner/pyproject.toml`), and the install address is **built from it** (`.../archive/refs/tags/runner-v<version>.zip`) instead of being typed into a Render setting. The tag name can then never disagree with the version.
- A test fails if `runner/src/saapp_runner/__init__.py`, `pyproject.toml` and the script version differ.
- A release is: bump the version, commit, tag `runner-v<version>`, push. Written down in the runner README as three steps, and the Settings page shows "this server wants runner X; yours is Y".
- Open question: should the server's `MIN_RUNNER_VERSION` move with each release or only when the protocol changes? Proposal: only when the protocol changes.

### 2. The script upgrades and explains (fixes failures 2 and 3; small)
- Always run `pip install --upgrade` of the pinned archive (it is a quick no-op when the version already matches), so a new release reaches an existing install.
- On any failure: print what went wrong in plain words, **write the same text to `~/.saapp-runner/setup.log`**, and wait for Enter without closing (the Windows script already pauses; it needs a clearer message and to point at the log).

### 3. Run in the background, start at login (fixes failure 4; medium)
- Windows: a per-user Task Scheduler entry at logon (`schtasks`, no administrator needed) running `pythonw` so there is no console window. macOS: a LaunchAgent in `~/Library/LaunchAgents`. Linux: a `systemd --user` unit.
- The runner writes a small log to `~/.saapp-runner/runner.log`. **No project output, file names or commands in it** (the same log rule as the server).
- New runner commands, used by the script and shown in the README for people who prefer typing: `saapp-runner install-service`, `saapp-runner uninstall-service`, `saapp-runner status`.
- Removing a runner in Settings still revokes its key at once; uninstalling the service is one more command, and Settings shows it.
- **Risk:** startup entries are the least predictable part (antivirus, group policy, no `pythonw`). The runner must report "I was started by the service" or "by hand" so the app can say when the service is not working.

### 4. Updates (medium; **needs a decision**)
- The runner checks for a newer version when it starts and every few hours, using the server's answer to "what version do you want and where is it".
- **The trust problem:** if the server can name any address to install from, a compromised server could push code onto every runner's machine. Proposal: the runner **only installs from an address it already trusts** (its own repository's tag archive pattern, written into the runner, for example `https://github.com/SummonShenron/SAAPP/archive/refs/tags/runner-v<version>.zip`), never an address the server sends. Better still, publish a SHA-256 with each release and install with `pip --require-hashes`.
- **Decision for Jack:** update by itself, or show "Update available" in the app and update when the person clicks? Recommendation: **a click in the app by default**, with an "update automatically" tick for people who want it. The click is the same kind of approval as the Run button.

### 5. One runner per computer (fixes failure 5; the biggest change)
- Today a runner is a key for one project folder. Change it to **one key per computer** that reports a **list of projects**, each with a folder name and its checks.
- Jobs name a project as well as a check; the app picks it by matching the connected chat folder's name, and asks when two match.
- **The server must never be able to point the runner at an arbitrary folder.** So a folder is added only on the machine: the person picks the project in the app, the runner sees the request, and **a native confirmation dialog on the computer** asks once. The answer is stored locally (`~/.saapp-runner/projects.json`).
- Finding the folder: the browser tells us only the folder's name, never its path. Options to try: search the usual code folders (home, Documents, `source`, `code`, `dev`) for a matching name, then fall back to a native folder picker. Test which feels better before choosing.
- Migration: an existing per-project key keeps working until the person sets up the computer once.
- **Risk:** the native dialog needs a GUI toolkit (`tkinter` is in most Python installs but not all Linux ones). Where it is missing, fall back to one terminal question, which is still within "at most one y".

### 6. Plain-language status and errors in the app (small, and useful immediately)
- The runner reports a short **status code** with each poll or hello, chosen from a fixed list, never free text from the machine: `ok`, `no_checks_found`, `python_missing`, `folder_missing`, `update_available`, `update_failed`, `started_by_hand`, `started_by_service`.
- Settings turns each code into a sentence and, where it can, a button ("Update", "Show me how to fix"). "Not connected" gets a reason when we have one ("last seen 3 hours ago; the runner may be stopped").

## Order of work

| Step | What | Why this order | Size |
|---|---|---|---|
| A | Release discipline and the script upgrading itself (pieces 1 and 2) | Fixes the two bugs we hit; nothing else works reliably until a new release reaches existing installs | small |
| B | Status codes and plain-language errors (piece 6) | Makes every later step debuggable | small |
| C | Background start at login (piece 3) | Removes the "keep a window open" step | medium |
| D | Updates (piece 4) | Needs the trust decision above | medium |
| E | One runner per computer and folders added from the app (piece 5) | The big data-model change; best done once A to D are solid | large |
| F | A single installer file that needs no Python (**decision later**) | Removes the Python requirement, but an unsigned `.exe` trips Windows SmartScreen harder than a script, and a signing certificate costs money. Look at it after E. | large |

Each step is useful on its own and ships separately.

## What stays exactly as it is

These are the security rules and none of the steps above may weaken them:
- **The server never sends a command.** It sends check names and a yes or no. (Updates in piece 4 are the one place a server answer influences what runs on the machine, which is why the install address is built into the runner instead of coming from the server.)
- **The runner only connects out; it never listens.**
- **Every run needs an approval click on a card in the chat.**
- **Keys are per person and revocable; a pairing token works once, for minutes, and is never a key.**
- **No user content in logs, on either side.**
- It is still not a sandbox: the check runs with the person's own privileges, which is why the card shows the exact command first.

## How each step is tested (the same way as the runner so far)
- Real processes and real HTTP against the real queue, not mocks: the script run for real on Windows (and bash through Git Bash), a runner started and killed, an upgrade from an old installed version to a new one using locally built archives.
- Service entries: generate the exact text of the Task Scheduler / LaunchAgent / systemd files and check it, and register a harmless entry on this machine to prove it starts the runner (and remove it afterwards). **macOS and Linux have not been run on real machines**; say so in the README until they have.
- Updates: an old runner against a fake server that offers an address outside the allowed pattern must refuse it.
- The Settings card in a browser for each status code.

## Decisions (Jack, 2026-10-27)
1. **Updates: a click in the app by default, with a tick for "update automatically".** Agreed.
2. **Finding a project's folder: always ask with the native folder picker** (no searching the usual code folders). This also simplifies piece 5: the runner never guesses a path, the person points at it once, and that pick is the local consent. Where the picker is unavailable (no `tkinter`), fall back to one terminal question.
3. **The no-Python installer (step F): yes, worth doing, after step E.** Buying a code-signing certificate costs money, so the purchase itself still needs Jack's explicit go-ahead when we get there; until then step F is design only.
4. **Keep the typed-command setup in the README as a fallback.** Agreed: the commands are exactly what the script runs.

No open decisions remain, so step A can start without asking anything first.
