# Practice app: scope for a first version

Written 2026-11-14 with Jack. A standalone app (not part of SAAPP) where Jack practises coding problems with hints and next-line suggestions, never full implementations, and where the practice is tailored to how he learns. Build it AFTER the Monday 2026-11-16 on-site, not before.

## Decisions already made
- **Standalone, not inside SAAPP.** SAAPP is big enough; this stays separate so neither grows the other. (Reuse nothing from it at runtime. Ideas such as "approve before it is shown" and "a test must fail without the change" carry over; code does not.)
- **A local web app, not a packaged thick client.** A small Node server on localhost plus a browser page. It runs Jack's own code on his machine, so there is no sandboxing, hosting, sign-in or abuse protection to build. An Electron wrapper can be added later if he wants an icon to double-click; it is an add-on, not a rewrite.
- **TypeScript end to end** (the language of the interviews, and practice in itself). React + Vite for the page, Node for the server, Vitest for running problem tests.
- **The API key lives on the local server only**, never in the browser. A monthly spend cap is set at the provider.
- **Goes with a portfolio site** that links to it (code on GitHub, a README, a short screen recording). The practice app is for Jack; the portfolio is for everyone else.

## What the first version has
1. **A problem is a folder:** `problem.md` (the statement), `starter.ts`, `solution.ts` (the reference, never shown), `tests.test.ts` (hidden from the page, run on demand), `meta.json` (topic, difficulty, expected minutes).
2. **The page:** the statement, a code editor with autocomplete and inline suggestions OFF, a Run button that shows which tests pass and fail (names and messages, not the test code), and a timer mode that copies the interview clock.
3. **Hints on a ladder,** each step costing something that is recorded: (1) a nudge about what to look at, (2) the idea in words, (3) the next step in words, (4) one next line. Nothing past one line, ever.
4. **A hint guard, in code, not just in the prompt:** a hint is thrown away and asked again if it holds more than two lines of code, or if code taken from it passes the tests when put into the starter. The model never gets to hand over the solution however it is phrased.
5. **An attempt log:** per problem the time to first pass, hints used at each level, runs, and whether it was solved with no hint at all. Plain files or SQLite on disk.
6. **A problem bank Jack approves.** Problems are generated in batches, checked by code (below), then shown in a review list where he approves or removes each one. Nothing generated reaches practice unreviewed.

## How problems are generated and checked
- **Model:** the stronger one (Sonnet) for problems and tests; the cheaper one is fine for hints. Roughly a few cents a problem.
- **Inputs it is given:** a topic from a fixed list (so problems do not repeat), a difficulty, the interview shape (for example "a small REST API in TypeScript with 404 and validation handling, 30 minutes").
- **It writes:** the statement, the starter, a reference solution and the tests.
- **Code checks before Jack sees it:** (a) the reference solution passes every test; (b) a handful of deliberately wrong versions of it (made by changing the reference: dropped 404 case, off-by-one, wrong status code) each fail at least one test; (c) the starter fails the tests (so a problem is never already solved); (d) a second model pass lists anything ambiguous in the statement, and an ambiguous one is held back. A wrong or unclear test is the worst failure here, because it reads as Jack's own failure.

## Tailoring to how he learns (later, but record the signals from day one)
- Autocomplete off by default, and "unaided solve rate" shown over time, because the stated pain is failing without suggestions.
- Problems he struggled with come back later (spaced repetition), by topic.
- A timed mode, because the clock ran out in the last exercise.
- What counts as "how I learn best" is for Jack to say, not to guess: after two weeks of the log, look at what actually moved (hint level that unstuck him, problem types, time of day) and tune from that.

## Left out of the first version
Accounts, hosting, other languages, an Electron wrapper, leaderboards, anything that explains a whole solution, adaptive difficulty, voice. Each can come later.

## Build order
1. Problem folder format + a runner that runs one problem's tests and returns pass/fail.
2. The page: statement, editor (no autocomplete), Run, results.
3. The attempt log and the timer.
4. The hint ladder with the guard (needs a model; set the spend cap first).
5. The generator with the four checks, and the review list.
6. Portfolio site and the README/recording.

## Before Monday (no tool needed)
Write or have Sonnet write 5 to 8 TypeScript API problems by hand in the folder format above, with tests, and work through them with a timer. They become the first problems in the bank.

## Open questions for Jack
- Is TypeScript the only language, or Python too? (Everything above assumes TypeScript.)
- Which interview shapes matter most: REST APIs, data-structure problems, debugging a broken file, something else?
- Should a solved problem be shown with the reference solution afterwards, or never?
