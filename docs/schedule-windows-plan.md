# Schedule windows: Sonic runs a job "sometime in a window", at a quiet moment

Status: DRAFT outline, discussed 2026-10-20, nothing built. Needs the decisions at the bottom.

## Goal
Instead of "run at 8 AM", a job gets a window ("between 1 and 3 PM") and runs at a real, varying moment inside it. When someone asks "what are you up to?", the
answer reflects real variation, and Sonic can truthfully say why a run landed when it did.

## What the code does today (checked)
- `backend/utils/user_tasks.py::next_run_at` is deterministic: cadence, hour, weekday, timezone in, one timestamp out. No window, no randomness.
- `backend/services/user_task_scheduler.py` wakes on a timer, claims due tasks one at a time with a short lease, runs at most `MAX_PER_PASS = 3` per pass, and
  gives a task back (reschedules, does not run) when the cost guard is in low-power mode. Two tasks due together do not collide: they run one after another.
- The reading shelf already floats: `shelf_refresh` checks hourly and runs when `REFRESH_DAYS` have passed since the last attempt.
- `backend/utils/routines.py` keeps one record per run (`sonic_routine_runs`: when, status, counts) and answers "what did you do today" from it, never from the model.
- `budget_guard.current_state(username=...)` gives the real, live budget level, shared and per person.

## Position (agreed in discussion, with one step further)
- Plain randomisation inside a window is honest and free, but nothing is "chosen".
- A model picking a time is only more than a dice roll if it has a real basis, and the real bases here are checks code can do: **has this person been idle**,
  **is there budget headroom**. So do not use a model at all: let each sweep decide *when to run* from those signals ("opportunistic window"). Cheaper,
  cannot invent a reason, and the variation comes from real conditions.
- Dropped as not real: "avoid overlapping another task" (the sweep already runs them in order).

## Design
1. A task (or a routine) may carry `window_start` / `window_end` (local hours, in the person's timezone; handle a window that crosses midnight and DST via `local_now`).
2. `next_run_at` returns the window start. At each sweep, a due task inside its window runs when quiet:
   - the person has not been active for N minutes (latest conversation activity for that username), and
   - the budget has headroom (`current_state(username=...)` is not throttled or near its cap; shared budget for shared jobs like the shelf).
   If not quiet, it is left for the next sweep. At `window_end` it runs anyway, unless low-power mode gives it back (existing behaviour).
3. Each run records `window`, `ran_at` and a **code-written reason** ("ran after 40 minutes with no activity from you", "waited for budget headroom", "window
   closed, ran anyway"). This extends the run record that routines already keep, and is the source the "what have you been up to" self-report reads.
4. Voice: first person, facts only. "I run it sometime between 1 and 3, at a quiet moment" is allowed. "I felt like it" is not (identity check).

## Scope
- Opt-in per task. Tasks people expect at a clock time (a morning digest) stay exact.
- Default for Sonic's own routines (reading refresh, health check), where nobody expects a clock time. The shelf already floats, so its change is only the quiet-moment gate.

## Risks to test
Starvation (always "not quiet" until the window ends), restart in the middle of a window, two servers claiming the same task (lease), window crossing midnight, DST days, a
person with no activity history, low-power mode at the window's end.

## Design for scale (assumed target: ~1,000 users, ~300 with tasks; revise; decided 2026-10-20: design for scale, it is best practice)
Existing limits: 3 tasks per person, 4 runs per person per day, `MAX_PER_PASS = 3` every 300 s (at most 864 runs a day), shared and per-person budget caps.

What the numbers say at that size:
- **Throughput is the first limit.** 300 people at their 3-task cap is ~900 runs a day, above the sweep's ceiling.
- **Budget is the second, and it binds first.** If a run costs 1-2 cents (assumption: measure it), 300 daily tasks cost $9-18/day against a shared cap of a few dollars.
- **Windows are needed, not decorative:** everyone picks "8 AM" and time zones cluster the bursts.
- **Scales for free:** Sonic's own routines (reading refresh, health check, tidy-ups) run once for everyone.
- **Correctness already scales:** one atomic claim per task, a lease, an index on `(status, next_run_at)`, the most overdue first.
- **Reasons stored per run** follow the existing 60-day run retention, so volume is bounded.

Build now (cheap, and wrong to retrofit):
- The headroom gate: scheduled work stops at ~60-70% of the shared budget, so it never starves live chat.
- Fairness: at most one task per person per pass (today one person with 3 overdue tasks can take all 3 slots).
- An indexed `last_active_at` on the user, written on a turn (throttled), never a scan of conversations per task per sweep.
- Windows, jittered start inside the window, and the stored reason per run.

Build when measured (do not guess the numbers):
- A higher per-pass limit or more than one sweep: add it when the queue actually fails to clear (record queue depth and lateness per run so this is visible on Sonic Health).
- A per-run cost figure: measure it first, then set the headroom percentage from it.

## Window size (recommended, 2026-10-20)
Rule: never wider than the job's own cadence, and for a user task never wider than what the person chose. A week-wide window is rejected: a weekday choice carries meaning
("Monday summary"), "sometime this week" says nothing checkable, and the quiet-moment gate usually passes within hours, so a week would add no real variation.
- Sonic's own daily jobs (reading refresh, the two tidy-ups): the day. The reading refresh already runs 24 hours after its last attempt, so this is a small change.
- Health check (every 6 hours): fixed, or at most ~1 hour of jitter. It is a safety check; floating it delays noticing a real problem.
- Daily user task (opt-in): about 2 to 3 hours around the hour they chose.
- Weekly user task (opt-in): the day they chose, with the same 2 to 3 hour window on that day.
- Day boundary: the one `budget_guard` already uses (looks like Pacific time; confirm), so "today" means the same thing everywhere Sonic reports.

## Decisions needed
1. Opportunistic window (no model) as above, or do you still want a model involved? (Cost says no.)
2. Accept the window sizes above (or change them)?
3. What counts as "quiet": 30 minutes idle? and is a "run anyway at the end" acceptable for user tasks?
4. Should users be able to see the reason a run landed when it did (task history)?
