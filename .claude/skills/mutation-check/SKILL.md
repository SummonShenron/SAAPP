---
name: mutation-check
description: Prove that new or changed tests actually fail when the code is broken. Use after writing tests for a guard, limit, permission, default, counter or error path, before claiming a change is tested, or when asked "do these tests really test anything" or to "mutation test" or "break it on purpose". Runs on a throwaway copy, so the real files are never touched.
---

# Mutation check

A green test run only says the tests pass. It does not say they would fail if the code were wrong. This skill breaks the code on purpose, one change at a time, and checks that a test notices. In practice it finds real gaps: on one feature it found a test that never checked the style it claimed to, a limit that could be off by one, and a duplicate-guest bug that no test covered.

## How to run it

1. Write a JSON file listing the breakages (see `example-mutations.json` next to this file):
   `[{"file": "backend/utils/thing.py", "old": "exact existing text", "new": "broken text", "label": "what this breaks"}]`
2. Run the script from the repository root with the tests that should catch them:

```
.venv/Scripts/python .claude/skills/mutation-check/mutate.py my-mutations.json -- backend/tests/test_thing.py
```

It prints `caught`, `SURVIVED` or `MISSING` per mutation. Exit code 0 means all caught; 1 means a survivor or a missing pattern; 2 means the baseline was already red.

- **SURVIVED**: no test checks that behaviour. Write the test, confirm it passes, rerun just that mutation (a one-entry file).
- **MISSING**: the text to change is not in the file. The code moved or you mistyped; fix the pattern. Do not ignore it, it means that mutation tested nothing.
- A baseline that is red in the copy usually means the tests need a file the copy left out: add it with `--include path`. `backend/sandbox` is left out for speed (`--include backend/sandbox` brings it back).

## What to break

Choose mutations that a bug could really be, not random edits. For each thing you added, ask what mistake would hurt:

- every `if` that refuses something (a limit, a missing id, a wrong owner, a guest): turn it into `if False:`;
- every boundary: `>=` to `>`, `<` to `<=`, a limit to a huge number, a default to 0;
- every permission or trust check, and every place something is marked untrusted or careful;
- the order of two things that must not swap (checking before acting, registering before showing);
- every counter key (record it under the wrong name) and every message the person reads;
- what must NOT happen: a write before approval, a second press applying twice, a notification sent when nobody should be told.

Do not mutate logging text or comments, except to prove the test for "never log user content" can fail.

## Rules

- Never mutate the real files in place. That is why this script copies. A commit made while a run was active once captured a deliberately broken line, and CI would have failed on it. If you ever must edit in place, say so and tell the user not to commit until it finishes.
- A run needs to be fast to be repeatable: point it at the narrowest tests that should catch the change, not the whole suite.
- Report the count (`caught 41, survived 1`) and what you did about each survivor. Do not say "mutation tested" without the numbers.
