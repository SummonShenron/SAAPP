# saapp-runner

Runs checks (tests, type checks, builds) **you approve in SAAPP**, on **your own machine**, in **your own project**.

SAAPP's assistant can propose a check, such as "run the tests on these files". You see the exact command on a card in the chat and press **Run**. This program, running in your project folder, then runs that command and sends back the end of its output. Nothing runs without your click.

## What it will and will not do

- It runs **only checks it found in your project or you wrote yourself**. It finds the common ones by looking at the folder (pytest, `npm test`, the TypeScript compiler), and anything you write goes in a config file in **your home folder** (`~/.saapp-runner/config.toml`), never in the project. A cloned repository cannot add a command or choose what runs when you press Run.
- SAAPP never sends it a command. A job names a check, and the command comes from the runner. The only other thing a job carries is your yes or no on fake AI keys (below). If SAAPP asked for a check it does not have, or for a file outside the project, it refuses.
- It **never listens on a port**. It connects out to SAAPP and asks for jobs you approved.
- Files are passed as separate arguments (`./path`), with **no shell**, so nothing is expanded or chained.
- The command gets a **clean environment**: the secrets in your shell (API keys, tokens) are not passed to it.
- It has a time limit (15 minutes by default) and kills the whole process tree when it passes. Only the **end** of the output is kept (about 6,000 characters).
- Your key is stored only in `~/.saapp-runner/credentials.json`, readable only by you.
- **It is not a sandbox.** The command runs with your own privileges, because you wrote it and you pressed Run. Read the command on the card before you approve it.

## Install

Python 3.11 or newer. From a tagged release of the repository (the SAAPP settings page shows the exact address for the version it wants):

```bash
pipx install "https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.zip#subdirectory=runner"
```

or run it without installing: `uvx --from "<the same address>" saapp-runner ...`. An archive address needs no `git` on your machine.

## First time

1. In SAAPP, open Settings, **Run checks on your machine**, and choose **Add a runner**. You get a one-time code that works for a few minutes.
2. Pair this project with it (each project folder has its own runner and its own key):

   ```bash
   saapp-runner pair --server https://<your-saapp-address> --root . <CODE>
   ```

3. Start it, and leave it running while you work:

   ```bash
   saapp-runner start
   ```

It finds the project's checks by itself, prints them, and says **Connected** when SAAPP can see it. Stop it with Ctrl+C. There is nothing else to set up.

## Choosing in SAAPP

In SAAPP's settings, under your runner, you can:

- **turn a check off** (an unticked check is never offered to the assistant and cannot be run);
- **use a fake AI key while checks run** (on by default). `GOOGLE_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` are set to a made-up value for the run, so a test cannot spend real money even when your project's `.env` file holds a real key (a `.env` file does not replace a variable that is already set).

SAAPP can only send check names and that yes or no. It cannot add a check or change a command.

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

Version 0.1.0. The runner, the server side, SAAPP's Settings section (with a starter script that does the steps above for you) and the check card in the chat are built and tested. The design is in `docs/local-runner-plan.md`.

## Development

```bash
python -m pytest runner/tests
```
