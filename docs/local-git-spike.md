# Local git without git: bringing Sonic's commit into your own repository (spike)

Built 2026-10-25, undeployed, behind a switch. Code: `local/src/localGit/` (browser), `backend/services/git_import.py` (server), `local/src/components/LocalGitUpdate.tsx` (the button).

## The problem

Sonic commits through GitHub's API, so the person's own `.git` has never seen the commit. Afterwards they had to run `git fetch`, `git stash`, `git checkout` by hand (Sonic now prints those commands, `commit_flow.catch_up_after_commit`). This spike removes the commands: a button under Sonic's "Committed" reply writes the commit into the connected folder's `.git` from the browser.

## How it works

1. The reply contains the commit link; the page recognises it (`localGit/commitResult.ts`, a wording contract pinned by a backend test).
2. The page asks the server for a bundle (`POST /api/local-git/bundle`): the commit's fields, the listing of every folder that holds a change, and the changed files. The server reads with the person's OWN token (the browser never has it) and changes nothing.
3. In the browser, `planImport` checks everything BEFORE writing: every blob, every folder listing and the commit itself must hash to the id GitHub reports; the folder's `origin` must be this repository; git must not be busy (`index.lock`, merge, rebase, cherry-pick, revert, bisect); HEAD must be a branch whose tip is exactly the commit Sonic's starts from; the new branch must not already exist; the index must be version 2 or 3 with no conflict. Any failure is returned in words and nothing is written.
4. The person sees the plan ("write N objects, move/create branch X, update the index; your working files are not touched") and confirms.
5. `applyImport` takes `.git/index.lock` (the lock git itself takes), writes the loose objects, rewrites the index entries of the changed files (everything else in the index is kept, extensions are dropped for git to rebuild), writes `refs/remotes/origin/<branch>` and `refs/heads/<branch>`, and, for a new branch, points HEAD at it. The lock is released in every case.

Working files are never written. A file that was part of the commit and is byte-identical (or identical after Windows line endings are normalised) is recorded with its own size and times, so git sees it as clean; any other file gets zero times so git reads it and shows a difference. Things that were NOT in the commit stay ordinary local changes, so the stash dance is unnecessary.

## What is tested

`local/src/localGit/localGit.test.mjs` builds real repositories with real `git`, makes Sonic's commit in a clone, builds the bundle from real git objects, runs the import over the first repository's `.git` through a Node file-system adapter, and then asks git: `status` is clean, `fsck --strict` passes, `cat-file commit` is byte-identical, `origin/<branch>` and HEAD are right, leftovers stay modified, staged files stay staged, autocrlf repositories end clean, and a long list of unsafe situations is refused. `backend/tests/test_git_import.py` covers the bundle and the reply wording.

NOT tested: the browser file-system adapter (`gitFs.ts` `folderGitFs`) against a real Chrome folder. Try it on a COPY of a repository first.

## Turning it on

In the browser console: `localStorage.setItem('sonic.localGitSpike', '1')`, reload. It appears under a "Committed" reply when a local folder is connected. The browser asks once for permission to change the folder. Chrome or Edge only (the same File System Access API the folder sync uses).

## Known limits

- One-parent commits that add or change files only (no deletes, links, submodules); at most the commit limits (40 files, 4 MB).
- No upstream config (`branch.<name>.remote/merge`) is written, so `git status` will not show ahead/behind until `git push -u` or `git branch -u origin/<branch>`.
- A file the person had staged with different content has its index entry replaced (the working file is untouched).
- Not for a repository using a split index, SHA-256 objects, worktrees or submodule folders (refused).
- Reflogs are not written. A concurrent git command in a terminal is guarded only by `index.lock`.
- Merges (after Sonic merges a pull request) are not handled yet; the reply still prints the commands for that.

## Update 2026-10-25 (later): merges, a setting, and automatic mode

- **Merges.** After Sonic merges a pull request, the reply carries the merge commit's link (`**Merged** pull request #9 into `main` (squash) on `owner/repo`: [`abc1234`](...)`) and the same button works for it. The branch the merge went into must be at exactly the commit the merge started from, and the branch the person is on must hold EXACTLY the merged content (the server returns `compare_tree`, the folder-listing id of the commit they are on, and the browser compares it with the merge commit's): then moving `main` forward and switching to it changes no file of theirs. Otherwise it refuses and the commands apply. Squash and rebase merges (one parent) only.
- **A setting, not a console switch.** The folder menu has "Local git: ..." which cycles `button` (the default: a button, a plan, "Do it"), `auto` and `off` (`localStorage sonic.localGitMode`).
- **Automatic mode.** When the newest "Committed" or "Merged" reply arrives, the page checks and, if everything is safe, does the update at once (the commit was already approved on the card). It acts only once per commit per tab, only on the latest reply, and only when the browser has ALREADY been allowed to change the folder (the Apply buttons ask for that); otherwise it shows the button. Anything unsafe shows the reasons and the commands still work.
- **Already there.** A commit the repository already has is recognised ("Your repository already has this commit"), not refused.
- Tested against real git, including the squash merge from a feature branch, a merge when already on `main`, and the refusals.
