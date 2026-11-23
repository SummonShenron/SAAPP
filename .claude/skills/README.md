# Skills for Claude Code in this repository

A skill is a folder with a `SKILL.md`. The few lines at its top (`name`, `description`) are always in Claude's context; the body below them is loaded only when the task matches the description. That is the difference from `AGENTS.md`, which is read every time: rules that always apply go there, procedures that only matter sometimes go here.

Sonic (the assistant inside SAAPP) reads these same files when this folder is its connected project: it is shown each skill's name and description, and loads the full text with its `use_skill` tool only when the job fits (`backend/utils/skills.py`; the sync sends `.claude/skills` and nothing else of `.claude`). Skills can live in `.sonic/skills/` instead or as well; a name in both comes from `.sonic`. So write a skill for either tool and the other can use it, with a plain `description:` and steps that name real files rather than anything specific to one tool.

| Skill | Use it when |
|---|---|
| `add-a-sonic-tool` | giving Sonic a new tool or a new thing it proposes for approval (the ten places a tool is wired, and the shared Apply/Decline card) |
| `finish-a-change` | ending any code change: what to run, how to check the tests can fail, and how to report what was and was not checked |
| `mutation-check` | proving new tests fail when the code is broken (`mutate.py` works on a throwaway copy and never edits the real files) |

## Writing a new one

1. `mkdir .claude/skills/<name>` (lowercase letters, digits and hyphens; the folder name is the skill's `name`).
2. `SKILL.md` starts with `---`, a `name:` line and a `description:` line, then `---`. The description is what decides whether the skill is used, so say WHAT it does and WHEN to use it, in the words someone would actually type.
3. Keep the body to the steps and the reasons. Put long reference material or a script in a file beside it and link to it from the body: those are read only if needed.
4. Add a line to the table above.

Skills are plain files: change one when a real task shows it was wrong or missing a step. Before trusting a new one, give it a real task and watch whether it is picked and followed.
