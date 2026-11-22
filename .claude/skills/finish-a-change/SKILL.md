---
name: finish-a-change
description: The checks to run, and the honest report to write, before saying a change to this repository (SAAPP backend, the React frontend in local/, or the runner) is done. Use at the end of any code change, when asked to verify, run the tests, "check your work" or wrap up, and before suggesting a commit.
---

# Finish a change

AGENTS.md ends with "End by saying what you checked and what you did not." This is how to earn that sentence.

## 1. Look at what you actually changed

```
git status --short
git diff --stat
```

- Other uncommitted work from earlier sessions may be in the tree. Leave it alone and do not describe it as yours.
- After any rename or bulk edit, read the diff of every touched file for names that should not have changed (a regex once renamed an unrelated function).
- A test that is not yet tracked is still part of the change: list new files too.

## 2. Run the checks that match what you touched

Backend (use the project's virtualenv; the system Python lacks the dependencies):

```
GOOGLE_API_KEY=test-not-real .venv/Scripts/python -m pytest backend/tests/test_the_thing.py -q -p no:cacheprovider
```

Run the narrow tests while working. Before finishing, run the whole suite once. It takes about 16 minutes, so run it in the background and keep working on things that do not edit code; do not poll or sleep:

```
GOOGLE_API_KEY=test-not-real .venv/Scripts/python -m pytest backend runner -q -p no:cacheprovider
```

Frontend, from `local/`:

```
npx tsc -b
npm test
```

(`npm test` adds `--experimental-test-module-mocks`; a bare `node --test` fails one file.)

Rules that bite:

- Never run evals, live model calls or anything that spends money unless asked. A script that calls a model must call `llm_usage.mark_as_eval()` first.
- Tests never touch the real database, embeddings or a model. Stub `get_db`.
- Do not start editing source while the full suite runs: tests that read source files can see half an edit.
- Pytest labels backend tests `runner\tests\...` on this machine; open the file that actually failed, do not trust the label or a summary.

## 3. Check the tests can fail

A test must fail without your change. For anything with a guard, limit, permission or counter, use the `mutation-check` skill. At minimum, undo the change once, watch the new test fail, then restore it.

## 4. A failure that is not yours

Run that one test with and without your change (`git stash -u`, run, `git stash pop`). If it fails without your change too, say so plainly and, if the fix is small and test-only, fix it and say you did. Never call it flaky without evidence.

## 5. Say it back honestly

End with two short lists:

- **Checked:** the exact commands and their counts (for example "backend 9,687 passed, 0 failed; tsc clean; frontend 754 passed; 41 of 42 mutations caught, the 42nd fixed").
- **Not checked:** anything that has not run against the real thing: a real outside service, a real model turn, the signed-in page in a browser, anything you could not run. If the full suite was still running, say so.

Do not write "this works" for what only offline tests exercised; write "the tests pass and it has not been tried live". Do not commit or push: the user handles git. If something was committed while you had files temporarily broken, say so at once.
