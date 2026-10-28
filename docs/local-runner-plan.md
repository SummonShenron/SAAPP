# The local runner: a separate install that lets Sonic run checks on the person's own machine

Decided in principle 2026-10-27 (Jack): "I imagined this would be its own separate install, and yes, let's write it up before building. This could potentially unlock a lot of abilities for SAAPP."

## Build status (2026-10-27, undeployed)
**Stages A and B are built and tested: the runner, the server queue and endpoints (A); Sonic's `run_local_check` tool, the chat card, the Settings section and the starter scripts (B). Nothing is deployed, and no real model has yet been asked to use the tool.** What is left to do is under "Stage B: what was built, found and left" below.
- **`runner/`** (package `saapp-runner` 0.1.0, standard library only, Python 3.11 or newer): `config.py` (key and checks in `~/.saapp-runner/`, never in the project; commands are word lists; the file is only appended to, never rewritten), `files.py` (plain relative paths that exist inside the project; resolves links and Windows junctions, so a way out is refused), `execute.py` (no shell, clean environment, time limit that kills the whole process tree, capped output, cancel; files are never passed to a `.cmd` or `.bat` program on Windows, because cmd.exe acts on special characters), `client.py` (https only, except to this machine), `loop.py`, `cli.py` (`pair`, `init`, `start`). `runner/README.md` is the page a person reads before installing.
- **`backend/utils/runner_jobs.py`**: keys (shown once, stored as a hash, five per person), single-use pairing codes (ten minutes), the runners and whether they are connected, and the job lifecycle (`awaiting_approval`, `approved`, `running`, `done`, `cancelled`, `expired`, `failed`), with one open job per runner, the oldest approval handed out first, and stale jobs closed when next looked at. The `api_*` functions are the handling of each runner request, with one table mapping errors to HTTP statuses.
- **`app.py`**: thin endpoints (runner side authenticated by the runner's key; page side by the signed-in non-guest person; pairing by the code), all database work off the event loop. The wait for a job is bounded at 30 seconds.
- **Tests (156 in the runner, queue, wiring and end-to-end files, plus the whole backend suite, 6,257 passed and 1 skipped):** `backend/tests/test_runner_jobs.py` (the queue and every rule above), `test_runner_jobs_wiring.py`, `runner/tests/` (config, files, execution with real processes, the client against a real local server, the loop, the commands), and `test_runner_end_to_end.py` (the real runner over HTTP against the real queue running real commands). Two checks were proved by breaking the code on purpose: the child-process kill (a test that cannot fail was rewritten to check the child is really dead) and, earlier in the session, the other mutation tests. The runner's own tests are run with `python -m pytest runner/tests`; they are not under `backend/`.
- **What the end-to-end test proves, including against a wrong server:** nothing runs before approval and a job runs once; cancelling stops a running check at the next heartbeat; removing the runner ends it and cancels its job; an old runner is turned away with the reason; one person's runner never receives another's job; and the runner refuses a check the person never defined, and files outside the project, EVEN WHEN THE SERVER HANDS THEM OUT.
- **Known limits to remember:** the Windows symlink test is skipped here (this account cannot create links), though the junction test covers the same protection; each connected runner costs a few small database operations a second while it waits (fine for a handful of people, to be revisited with more); the pairing endpoint has no rate limit beyond the code's strength (about 50 bits, single-use, ten minutes) and each person's cap of five waiting codes; no licence file has been chosen for `runner/` (it needs one, since it is to be open source).

## Stage B: what was built, found and left (2026-10-27, undeployed)
**Built**
- **Sonic's tool `run_local_check(check, files?)`** (`agent_workflow._run_local_check`, `native_tools.REGISTRY`): offered only when the page can show its card (the chat request carries `runner_cards`), the person is signed in, and exactly one runner of theirs is connected (or the one in the folder they connected). The menu line names the runner's checks and shows each one's command, so Sonic asks by NAME; it never supplies a command. It creates the job (or returns the same still-open one, so asking again waits on the same card), streams a `runner_check` event, then waits on the database for the person to press Run and for the run (`runner_jobs.wait_for_result`, 10 minutes by default, `SAAPP_RUNNER_WAIT_SECONDS`), and returns `runner_jobs.format_result` (PASSED, FAILED with exit code, TIMED OUT, CANCELLED, NOT RUN, with the output labelled as data). If the turn is stopped while waiting, the check is cancelled so it cannot run later unseen. It is not batchable, is refused on a plan turn, counts as a test run and as an on-plan step, and (its output being text a command printed) is on the untrusted-tools lists, so no commit is allowed in that turn and no experience note is written from it.
- **Engine routing.** Asking to "run the tests" or mentioning pytest used to force the OLD engine (its test runners were GitHub Actions and a snippet runner). When the native engine is offering `run_local_check`, those phrases now stay native; "run CI", MongoDB, the browser, drafting email and implementation plans still go to the old engine (`native_loop.needs_old_engine(..., test_runs_native)`).
- **The chat card** (`RunnerCheckCard.tsx`): the exact command and the files, "this runs on your own computer, with your own privileges", Run and Cancel, then Stop while it runs, the verdict, and the end of the output. It asks the server how the check is going only until it has an outcome.
- **The Settings section** on the Account page (`RunnerSettingsCard.tsx`, signed-in people only): the runners with Connected or Not connected, folder, version and check names; Add a runner (a one-time code, the starter script for the person's system with the others one click away, "View the script before you run it", the same setup as commands to type); Remove. While a code is showing it watches for the runner connecting.
- **Starter scripts** (`backend/utils/runner_scripts.py`): `.cmd` for Windows, bash for macOS and Linux. Each carries only the one-time code, never a key; says what it will do and asks first; checks for Python 3.11 or newer; installs nothing system-wide; makes a private environment in the home folder; installs the PINNED release archive (no `git` needed); asks for the project folder (quoted, padded, dragged-in or escaped-space paths all work; warns when its name differs from the connected folder); pairs that project, offers `init`, then starts the runner. Every value put into a script is checked against a strict character list first, and a value that fails makes no script.
- **Per-project keys.** The runner now keeps one key per project folder (`pair --root`, `start --root`), so a second project never replaces the first one's. This matches "several runners per person, one key each".
- **Licence:** `runner/LICENSE` and `license = { text = "Unlicense" }`, as `erragent-sdk` declares.
- **Settings to set on Render:** `SAAPP_RUNNER_INSTALL_URL` (the https address of the tagged release archive, for example `https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.zip#subdirectory=runner`; until it is set the page says there is no starter script), `SAAPP_PUBLIC_URL` (this server's public address; otherwise it is taken from the request), and optionally `SAAPP_RUNNER_WAIT_SECONDS`.
- **Tests added in Stage B:** `test_run_local_check_tool.py` (20: the tool end to end with a scripted model, a thread playing the person and the runner, offered or not, refusals, engine routing), more in `test_runner_jobs.py` (52 in all: waiting, cancelling on stop, reusing an open job), `test_runner_scripts.py` and `test_runner_scripts_run.py` (75: validation, what the scripts say and do, and REAL RUNS of the bash script and the Windows script against stand-in commands), `runnerLogic.test.mjs` (9), and the wiring tests.
- **Looked at in a browser** with a temporary preview page and a faked network: the cards in every state in both themes, pressing Run took a card from waiting to running to passed, the pairing panel, and a phone width with no sideways scroll. The preview was deleted.

**Found while testing (real bugs, now fixed and pinned by tests)**
- The Windows script did not use `call`, so a `.bat` or `.cmd` Python shim (`pyenv-win` installs `python.bat`) would have silently ended it. Every program is now started with `call`.
- The bash script treated "y" followed by a stray carriage return (from some Windows terminals) as "no". It now strips the carriage return.
- An Enter at the folder prompt on Windows expanded to garbage; it is now checked first.
- The script's code pattern accepted letters the server never makes; it now takes the server's own alphabet.
- `cmd.exe` gives only the first `set /p` any data when its input is a pipe, so the Windows script's tests read answers from a file (a harness finding, not a script bug).

**Left to do**
- **Install address.** Tag a release and set `SAAPP_RUNNER_INSTALL_URL`; until then the real `pip install` from the archive has not been run end to end.
- **macOS has not been run.** The bash script was run on Windows with Git Bash. A downloaded `.command` file is not marked runnable, so the page tells macOS and Linux people to run `bash ~/Downloads/<file>`.
- **No real model has used the tool yet.** The first check after deploying should be read, as with every new tool: does Sonic pick the right check, wait, and report the real result without claiming more? A small targeted eval is worth writing then (spend discipline applies).
- **Waiting inside a turn is 10 minutes** and the run's wall time is the runner's own limit (15 minutes by default); a check longer than the turn waits comes back as NOT FINISHED, and asking again waits on the same card.
- **Not built:** running a check WITHOUT a click for checks the person marks read-only (the plan's phase 4), `{files}` suggestions for the files just edited (phase 2), read-only git checks and local git writes (phase 3).

## Why
Sonic cannot run this project's real tests today, and the reasons are structural, not fixable by a setting:
- **The WASI sandbox** (`backend/services/sandbox_tests.py`) has only the standard library, no network, and no working `asyncio.run`. Measured 2026-10-27 with pytest vendored into it: `test_leanings.py` 134 of 138 passed, `test_code_plan.py` 52 of 57, but any async test or any import of langchain, pymongo or fastapi fails (`test_experience_capture.py` 19 of 38). Memory was 72 to 84 MB and 9 to 13 s, so resources were not the problem.
- **Running pytest on the production server** would execute model-written code with the server's secrets, network and memory (512 MB, about 195 MB free at idle). It is not an option on this instance.
- **GitHub Actions** (`run_repo_tests`, `.github/workflows/patchy-tests.yml`) is real and isolated but needs the change pushed to a branch, takes minutes, and spends free minutes.

The person's own machine already has the real environment, and their folder is already the thing Sonic edits. The missing piece is a way to run an approved command there.

## The decision: the same shape as errAgent's local daemon
errAgent's SDK (`C:\Users\jackh\errAgent\sdk`, package `erragent-sdk` 0.5.0, command `erragent`, extra `[local]`) already does the hard parts, and this copies its design:
- **A pip-installable, version-pinned package in a monorepo subfolder**, with its own tests. Installable from git before any PyPI release.
- **A daemon that is confined to a root folder** (`--root`): it resolves paths and refuses any that escape the root, re-verifies hashes before writing, and asks a human before changing anything.
- **The outbound model.** The daemon does not accept calls from the cloud. It POLLS the cloud for work (`_poll_for_proposal`, a "tool bridge" that services `read_local_file` and `list_local_tree` requests) and returns the result. No open port is needed for the cloud to reach in.
- **Per-app secrets shown once and stored only as a hash** (`manage_app_read_access`).

What differs: errAgent's daemon asks for approval in the terminal. SAAPP's people are in the browser, so approval belongs on a card in the chat, and the runner only executes jobs the person approved there. It is a SEPARATE package from `erragent-sdk`, so SAAPP's people do not install an operations tool that carries ingest credentials.

Proposed: folder `runner/` in this repo, package and command `saapp-runner`, Python 3.11 or newer (same as the SDK), standard library plus a small HTTP client only.

## How it works
1. The person installs `saapp-runner`, creates a runner key in SAAPP settings (shown once, stored as a hash), and runs `saapp-runner --root .` inside their project.
2. The runner reads its own local config (`.saapp-runner.toml` in the project or in the home folder) that maps NAMES to commands: `tests = "pytest -q {files}"`, `types = "npx tsc -b"`. It reports its version and the check names (with the command text) to the server on a hello and on each poll.
3. Sonic calls a new tool, `run_local_check(name, files?)`. The server creates a JOB in the database and shows a card in the chat with the exact command and the files. The person presses Run or Cancel.
4. The runner long-polls `next`, receives only jobs that person approved, runs the command in a subprocess, and posts the result: exit code, duration, the last few thousand characters of output.
5. The waiting turn reads the result from the database and goes on.

Jobs live in the database, not in server memory. That also avoids the in-process handoff problem (live steering and edit approvals) that blocks running more than one instance.

### Job record (`runner_jobs`)
`{_id, username, runner_id, check, command_shown, files, status, created_at, expires_at, approved_at, started_at, finished_at, exit_code, output_tail, truncated}`.
`status`: `awaiting_approval` then `approved` then `running` then `done`, or `cancelled`, `expired`, `failed`. Jobs expire in minutes, and finished output is kept for days at most, never logged.

### Endpoints
- Runner (authenticated by its key): `POST /api/runner/hello`, `GET /api/runner/next?wait=25`, `POST /api/runner/jobs/{id}/result`, `POST /api/runner/jobs/{id}/heartbeat`.
- Page (signed-in person): `GET /api/runner/status`, `POST /api/runner/jobs/{id}/approve`, `POST /api/runner/jobs/{id}/cancel`, `POST/DELETE /api/runner/keys`.
- The hello carries `version`; the server answers with a minimum version and refuses an older runner in words.

## Safety rules (each one mechanical, each one tested)
- **Commands come from the person, never from Sonic or the server.** Sonic can only name a check. The server never sends command text.
- **`{files}` is the one placeholder.** Paths are validated to exist under the root and are passed as separate arguments (never through a shell). No `shell=True` anywhere.
- **A runner key belongs to one person** and can only claim that person's approved jobs. A wrong or revoked key gets a plain 401.
- **Approval is per job**, tied to the job id and the command text the runner reported. A job not approved is never handed out.
- **Limits on the run:** one job at a time, a wall-clock timeout with the whole process tree killed (Windows included), an output cap, a clean environment (no inherited secrets).
- **Root confinement** copied from the errAgent daemon: the working directory and every path resolve inside `--root`.
- **The output is untrusted text.** It goes back to the model as data, capped and stripped of control characters, and the tool is added to the untrusted-tools list so no experience note is written from a turn that used it.
- **It is NOT a sandbox.** The command runs with the person's own privileges. The control is their approval and that they wrote the command. The card must say so plainly, and show the files changed since the last run.
- **No content in logs** (the AST log-hygiene test already scans every logger call in the backend).

## What it unlocks (each is a named check or one new tool, with the same approval)
- Real test runs, type checks, linters and builds in the project's own environment, including async and third-party-dependent tests the sandbox cannot run.
- Non-Python projects: a JavaScript person defines `npm test` as their check.
- Read-only git checks (status, diff) as named checks, and later local git writes through the same channel, which is cleaner than the browser-writes-`.git` spike (`docs/local-git-spike.md`, "daemon deferred").
- Reading files that were never uploaded (large or excluded ones), with the same root confinement.
- The person's own scripts (a data check, a report) with an approval each time.

## Phases
1. **Core.** Package, config, hello, poll, run, result. Server jobs, endpoints, runner keys. Settings section (status, install command with the pinned version, create or revoke a key). Chat card (Run, Cancel, result). The `run_local_check` tool, admin-only first.
2. **Placeholders and judgment.** `{files}`, suggest the checks relevant to the files just edited, show what changed since the last run.
3. **More abilities.** Read-only git checks, local git writes, reading un-uploaded files.
4. **Maybe, much later.** An opt-in "run without asking" for checks the person marks as read-only. Not planned.

## Falls back to what exists
No runner connected: the tool says so and says how to start one, then Sonic uses `run_repo_tests` (needs a pushed branch) or asks the person to run it.

## Risks and unknowns
- **Process control on Windows** (killing the whole tree on a timeout) and unusual shells need real testing there first, since Windows is where this is developed.
- **Distribution is a product concern:** versioning, updates, docs, uninstall, and a person without Python (a bundled binary may be needed later).
- **Waiting inside a turn.** A turn waits for approval and then the run, so the waits and the progress shown to the person need designing (the edit-approval wait is the model to follow).
- **Cost of results.** Output returned to the model costs tokens; the cap keeps it small.
- **Many people at once.** Long-polling holds a connection per runner; fine for a handful, to be revisited at scale.
- **Guests** never get it.

## Tests that must exist before it ships
A fake runner against the real endpoints (every status transition, expiry, cancel); a wrong person's key, a revoked key, an old version, a replayed result; path escape and unknown-check refusals in the runner; the output cap and control-character stripping; the card and settings UI; the untrusted-tools list.

## Installing it, and why a web button cannot do it
Asked 2026-10-27: can Sonic or the app run `pip install` from a confirmation click, so nobody has to open a terminal first? **No, not literally.** A web page, and Sonic (which runs on the server), cannot execute anything on a person's computer: browsers forbid it on purpose, and anything that could would be the kind of capability attackers look for. To run a local command you already need a local program, which is the thing being installed. What IS possible, from least to most friction removed:
- **A downloadable starter script (CHOSEN for phase 1, Jack 2026-10-27).** The settings page generates a small script for the person's operating system (`.cmd` for Windows, `.command` for macOS, `.sh` for Linux) that they double-click. Windows (SmartScreen: "More info, Run anyway") and macOS (Gatekeeper: right-click, Open) warn about downloaded scripts; that is acceptable here because the people setting this up are developers who know what it is for. Details that make it safe and pleasant:
  - **It carries a one-time pairing code, never the runner key.** A key on disk in a Downloads folder is a leak. The code is single-use and expires in about ten minutes; the runner exchanges it for a key on its first hello and stores the key in `~/.saapp-runner/` with owner-only permissions.
  - **It is short, readable and shown first.** The page has "View script" before "Download". It checks for a suitable Python (3.11 or newer) and for `uv` or `pipx`, says plainly what it is about to run, and asks Y or N. It does not install system software on its own; if something is missing it says how to get it and stops.
  - **It installs from a pinned release archive of a tag, not from `git`.** `pip install` of a `git+https` address needs the `git` program on the machine; an archive address of the tag (`.../archive/refs/tags/<tag>.zip#subdirectory=runner`) does not. The page shows the exact tag; a SHA-256 check of the archive can be added.
  - **It asks which project folder** (drag the folder into the window), warns if the folder's name differs from the one connected in the page, remembers it, and then keeps the window open showing the runner, so the person can see what it does and stop it with Ctrl+C.
  - **The page watches for the runner's hello** and turns green ("Connected").
- **The pasted command stays as a second way** next to the download: one command with a copy button (`uvx --from "<archive>#subdirectory=runner" saapp-runner --root .`, or `pipx run`), for people who prefer a terminal.
- **A real installer plus a link that opens it (later, the Zoom or Steam way).** A signed installer per operating system registers a `saapp-runner://start?...` link; after that, one click on the page launches the runner (the system asks "open this app?"). Costs signing certificates, per-OS builds and an update path. A product-stage investment, not phase 1.
- **After the first install, updates can be offered from the app.** The page says "version X is required", the person confirms, and the runner updates itself. This is also a remote-code risk (whoever controls the source controls every runner), so updates must be to a PINNED tag or hash the person can see, never "latest", and never silent.
- **Rejected: writing a file that auto-runs** (for example a VS Code `tasks.json` with `runOn: folderOpen`) through the connected folder. It would let the page, or a prompt-injected Sonic, put an executable trigger on someone's machine. VS Code asks before running automatic tasks, which shows how risky it is.

**The repository is open source (Jack, 2026-10-27), so people can install it and read what runs on their machine.** Two follow-ons: install from a tag's ARCHIVE address (needs no `git` on the person's machine), and keep each release tagged so the page can name a pinned version. A separate repository or PyPI stays an option for later, not a need.

## Decisions
Made by Jack (2026-10-27):
- **Name:** `saapp-runner` (folder `runner/`, package `saapp_runner`).
- **Install from the open-source repository, like `erragent-sdk`.** From a pinned tag, shown on the settings page.
- **How a person sets it up: a downloadable starter script** (with the pasted command as a second way). People installing it are developers who know what it is for and will click through the operating system's warning; it is not something a non-developer would turn on.
- **Opt in for everyone.** Nobody has a runner unless they set one up; any signed-in non-guest person may.

Left to Claude, with reasons:
- **Where the checks are defined: the person's own home folder, not the project.** `~/.saapp-runner/config.toml`, with a section per project path, written by `saapp-runner init` (which detects pytest, `npm test`, `tsc` and asks in the terminal). A project-level file would let a cloned repository define the command that runs when you approve a card, the same attack as a malicious `.vscode/tasks.json`, a git hook or a `Makefile`. Keeping the definitions outside the repository means the command on the card is one the person chose. A project-level file may be allowed later only with a terminal prompt to trust each command by its hash (trust on first use, asked again when it changes).
- **Several runners per person, one key each.** A runner is started per project folder, so a person with two projects runs two. Each has its own key (so one can be revoked alone) and its own `runner_id`, named at creation ("laptop, saapp"). The key is created in settings ("Add a runner"), at most five per person. A job goes to the runner whose reported folder name matches the connected folder; if two match, the card asks which.

Still open: PyPI later or a separate public repository (see above); the settings wording; how long a turn waits for approval and for the run.
