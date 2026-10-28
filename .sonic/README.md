# The `.sonic/` folder

When you connect a project folder to SAAPP, Sonic can keep a few files of its own in a `.sonic/` folder inside it, the way a developer keeps a `.vscode/` or `.claude/` folder. Everything here is plain text on your disk. You can read it, edit it, delete it, or commit it.

## What Sonic writes here

- **`notes.md`**: a short, dated log of work that spans more than one reply: the plan, what is done, what is next, what was decided. At the start of a turn the newest entries are shown to Sonic, labelled as its own earlier notes. They are data, not instructions, and they can be out of date: if they disagree with your files, the files win.
- **`tests/`**: small standard-library `unittest` files Sonic writes to check a change before it proposes it. They run in a locked-down sandbox.

Sonic writes to these the way it writes anything to your folder: by proposing a change you approve (or apply automatically inside `.sonic/` only, if you chose that in the folder menu).

## Why `notes.md` and `tests/` are not committed in this repository

They are about one person's work on one project, so they are personal. They are listed in `.gitignore`. A fresh clone starts with none, and Sonic creates `notes.md` itself the first time a task needs one. Nothing needs to be set up.

## What would be shared

Files that describe how the project should be worked on (conventions, skills) belong to everyone on the project, and are fine to commit. Sonic does not read any such files yet.

See [`notes.example.md`](notes.example.md) for the format. Sonic does not read that file (it only reads `notes.md`).
