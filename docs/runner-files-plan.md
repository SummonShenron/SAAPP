# The runner as a second way to give Sonic a project's files (plan, 2026-11-12, not built)

Status: **stage 1 BUILT 2026-11-12 (uncommitted, runner 0.2.2 not tagged); stages 2 and 3 not built.** What stage 1 turned out to be, differing from the plan below: no per-project setting on the server. The consent is the window on the person's own computer (runner/src/saapp_runner/share_dialog.py), once per ask, and the server takes files only while a request that was handed to the runner is open and unanswered (backend/utils/runner_files.py), so a stolen runner key alone cannot upload. The page's "Let Sonic read this project's files" button (Settings, per project) creates the request (`runner_jobs.request_files`, same long-poll as "add a project" with `share_files`); a FULL staged upload follows (reset first, commit last); the manifest records `source`; "Stop sharing" is the existing disconnect. Rules live in three places pinned equal by backend/tests/test_runner_files_rules.py. NOT yet: a delta, sync at the start of each turn, the chat card showing the runner as the source, opening a runner project with no browser folder at all, and any edit applied by the runner (stage 3). The runner's old promise "no message ever carries file contents" is now "except this, after a yes" (client.py, runner/README.md, Privacy page). Changelog line owed at release.

It follows the "one folder card" work the same day (docs/local-runner-plan.md has the runner; `local/src/components/sessionColumnLogic.ts` `folderCard` has the card).

## The goal

Today the only way Sonic reads a person's folder is the browser: the File System Access API (Chromium on desktop only) walks the folder and uploads its text files to a per-person snapshot on the server (`backend/services/local_workspace.py`), and every file tool (`read_repo_file`, `search_literal`, `list_repo_tree`...) reads that snapshot. Editing is the same: Sonic proposes, the person applies in the browser, which writes through the folder handle.

The runner is already on the person's machine, already trusted to run commands, and works from any browser. Letting it be the uploader (and, later, the thing that applies edits) means:
- a person on Safari, Firefox or a phone's browser can still work on a project;
- the snapshot can be refreshed without the page being open and without the "not on the server yet, uploads when you send a message" wait;
- one pick of the folder, on the machine, instead of a browser pick plus a runner pick.

## What already exists and is reused

- `local_workspace.apply_sync(username, name, files, deleted, reset, upload_id, commit)`: the ONE write path into the snapshot. It never trusts its caller: it refuses secrets, vendored and build folders, long or odd paths, over-size files, and caps the total bytes (60 MB) and file count (8,000). A full upload is staged and swapped in atomically (`upload_id`, `commit`). Files travel as exact bytes (`text_b64`), so CRLF survives.
- The browser's rules in `local/src/localWorkspace.ts` (SKIP_DIRS, secret names and suffixes, lockfiles, `.min.js` / `.map`, 500 KB a file, Word and Excel sent whole so the server makes the text outline, batches of at most 300 files or 2.5 MB, a delta against the server's own state).
- The runner's key authentication, its long-poll to the server (`/api/runner/next`, which already carries "add a project", "update" and "remove a project" requests), per-project ids, and `projects.unsuitable`.
- Per-project settings the page already saves (`runner_jobs.save_settings`).

## The design: the runner is another uploader into the SAME snapshot

Not a second read path. Sonic's tools keep reading the server-side snapshot exactly as now; only who fills it changes. That keeps every file tool, the orientation ranking, the secrets refusal and the demo project untouched.

**Source.** The snapshot records who filled it: `browser` or `runner` (a field next to the name in the manifest). One person has ONE snapshot (`_workspace_dir(username)`), so one open folder at a time stays true; a sync from the other source replaces it, and the card says which one is feeding it.

**Consent (the part that is yours to decide).** A background program uploading project files is a bigger thing than a click in a folder picker. Proposed rule: a project's files go to the server only when the person turns on **"Let Sonic read this project's files"** for that project (a per-project setting, **off by default**, shown in the Connection card and in Settings, with the same wording as the browser folder's privacy text). The server checks the setting on every runner sync, so a revoked setting stops uploads at once. Turning it off clears the snapshot if the runner was its source.

**Trigger.** The server asks the runner to sync through the long-poll it already answers (`sync_request` beside `add_project`), at the start of a Sonic turn that will read files, and waits a bounded time (default 10 s, then it carries on with the last snapshot and says so in the trace). The runner sends a DELTA: it lists its files with sizes and hashes, asks the server for its manifest, and uploads only what differs (the browser does the same with file stamps).

**Rules in one place.** The skip lists exist in TWO places already (browser TypeScript, server Python, each saying the other must match). The runner would be a third. A test pins all three lists equal (parse the TypeScript constants and import the Python ones), so they cannot drift.

## Stages

1. **Read-only sync, behind the setting (smallest useful slice).**
   Server: `POST /api/runner/workspace/sync` and `GET /api/runner/workspace/status`, runner-key authenticated, calling `apply_sync` for the key's owner; the per-project setting `share_files`; manifest `source`; a counter per outcome (`runner_sync_ok`, `runner_sync_refused_setting`, `runner_sync_failed`). Runner: `workspace_sync.py` (walk, rules, hashing, batching, retry, never follows links or junctions, never reads outside the project). Page: the toggle and a "Files: from your runner" state on the card. Tests on all three layers, including a rules-equality test and a refusal test (a secret file placed in the project never reaches `apply_sync`, and the server refuses it anyway).
2. **Sync on demand.** The `sync_request` in the long-poll, the bounded wait before a turn, the delta, and "open a runner project" in the card for a person with no browser folder at all.
3. **Edits applied by the runner.** A new job kind: Sonic's proposal becomes a card the person approves (as today), and the runner, not the browser, writes the files inside the project, atomically, keeping a copy so Undo works, refusing paths outside the project and the secret files, never creating anything the proposal did not name. This is the biggest and riskiest stage and gets its own review pass before it is wired.
4. **Wording and help.** Privacy page, the Help text, the card. Say plainly what goes to the server (the project's text files, as with the browser folder), that it happens only for a project the person turned on, and how to turn it off.

## Decisions for Jack

1. Per-project opt-in, **off by default**, as above? (My recommendation: yes. The alternative, "adding a project to the runner also shares its files", is simpler but turns a click that today means "run checks here" into "upload this folder".)
2. One open folder at a time, whichever source filled it last? (Recommended: yes; per-project snapshots would multiply the server's storage and its 12-hour cleanup.)
3. Sync at the start of each file-reading turn with a 10 s wait, or only on a button? (Recommended: per turn with the delta, falling back to the last snapshot, because a stale snapshot silently gives wrong answers.)
4. Stage 3 (the runner writes edits) is wanted, or are read-only and browser-applied edits enough for now?

## Risks to watch

- **Secrets.** The one rule that must never differ between uploaders. Defence in depth: the runner filters, the server refuses again.
- **Big repos.** The 60 MB and 8,000-file caps are the server's; the runner should stop early and say so, not upload 59 MB to be refused.
- **Windows.** Exact bytes (CRLF), long paths, junctions, case-insensitive names.
- **Two uploaders racing** (browser tab and runner on the same account): the manifest's source and `upload_id` staging already let the later full upload win without a half-written mix; a test must show it.
- **Runner offline.** Reads fall back to the last snapshot, and the card says "last synced 20 min ago".
- **A compromised server.** It can already ask the runner to add a project but never name a path. It must not be able to make the runner upload anything the person did not turn on: the runner checks the setting itself (it holds the toggle in `~/.saapp-runner`, not only on the server).

## Size

Stage 1 is roughly one long session across server, runner and page, with the rules-equality test and a security review of the new endpoint. Stage 2 is a second session. Stage 3 is its own project.
