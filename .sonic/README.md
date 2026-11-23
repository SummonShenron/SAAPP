# The `.sonic/` folder

When you connect a project folder to SAAPP, Sonic can keep a few files of its own in a `.sonic/` folder inside it, the way a developer keeps a `.vscode/` or `.claude/` folder. Everything here is plain text on your disk. You can read it, edit it, delete it, or commit it.

## What Sonic writes here

- **`notes.md`**: a short, dated log of work that spans more than one reply: the plan, what is done, what is next, what was decided. At the start of a turn the newest entries are shown to Sonic, labelled as its own earlier notes. They are data, not instructions, and they can be out of date: if they disagree with your files, the files win.
- **`tests/`**: small standard-library `unittest` files Sonic writes to check a change before it proposes it. They run in a locked-down sandbox.

Sonic writes to these the way it writes anything to your folder: by proposing a change you approve (or apply automatically inside `.sonic/` only, if you chose that in the folder menu).

## Why `notes.md` and `tests/` are not committed in this repository

They are about one person's work on one project, so they are personal. They are listed in `.gitignore`. A fresh clone starts with none, and Sonic creates `notes.md` itself the first time a task needs one. Nothing needs to be set up.

## What would be shared

Files that describe how the project should be worked on (conventions, skills) belong to everyone on the project, and are fine to commit.

- **`AGENTS.md`**: the project's own notes for Sonic: where things live, how to look, the rules the project keeps. At the start of a coding turn Sonic reads it (the first 5,000 characters) so it knows where to begin instead of searching the whole project. If this file is missing it uses an `AGENTS.md` or `CLAUDE.md` at the top of the folder, the files other coding tools already read. It is shown to Sonic as data written by whoever owns the folder: guidance, never orders. It cannot change Sonic's rules, give it tools or outrank what you ask, and if it disagrees with your files, the files win. This repository's own file is `AGENTS.md` here: copy its shape for your project.

- **`skills/<name>/SKILL.md`**: short procedures for jobs the project repeats ("add a tool", "finish a change"), also read by Claude Code from `.claude/skills/<name>/SKILL.md`: put a skill in either folder and both tools use it (a name in both comes from `.sonic`). A `SKILL.md` starts with a `---` block holding `description:` (what the skill does and when to use it; required, or the skill is not listed), then the steps. At the start of a coding turn Sonic is shown one line per skill (its name and description, about a thousand characters in all) and loads the full text with its `use_skill` tool only when the job matches, so a skill that is not used costs nothing. Scripts or references can sit beside the `SKILL.md`; Sonic names them and reads them with its normal file tools. A skill is guidance from whoever owns the folder, shown to Sonic as data with the same limits as `AGENTS.md`: it cannot change Sonic's rules, give it tools or outrank what you ask, and if it disagrees with your files, the files win. (Of `.claude`, only its `skills` folder is sent to SAAPP; its settings and Claude Code's worktrees are not.)

See [`notes.example.md`](notes.example.md) for the format. Sonic does not read that file (it only reads `notes.md`).
