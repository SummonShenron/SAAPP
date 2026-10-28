# saapp-runner

Runs checks (tests, type checks, builds) **you approve in SAAPP**, on **your own machine**, in **your own project**.

SAAPP's assistant can propose a check, such as "run the tests on these files". You see the exact command on a card in the chat and press **Run**. This program, running in your project folder, then runs that command and sends back the end of its output. Nothing runs without your click.

## What it will and will not do

- It runs **only the checks you defined**, in a config file in **your home folder** (`~/.saapp-runner/config.toml`), never in the project. A cloned repository cannot decide what runs when you press Run.
- SAAPP never sends it a command. A job names a check, and the command comes from your config. If SAAPP asked for a check you did not define, or for a file outside the project, it refuses.
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
2. Pair this machine with it:

   ```bash
   saapp-runner pair --server https://<your-saapp-address> <CODE>
   ```

3. In your project folder, choose which checks exist. It looks at your files, suggests some, and asks before adding each:

   ```bash
   saapp-runner init
   ```

4. Start it, and leave it running while you work:

   ```bash
   saapp-runner start
   ```

It prints the checks it can run and says **Connected** when SAAPP can see it. Stop it with Ctrl+C.

## The config file

`init` writes this for you; you can edit it by hand. A command is a list of words.

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

Version 0.1.0. The runner and the server side are built and tested. The Settings section, the chat card and the assistant's `run_local_check` tool are the next stage: until they exist, there is no way to ask for a check from the page. The design is in `docs/local-runner-plan.md`.

## Development

```bash
python -m pytest runner/tests
```
