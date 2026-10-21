# Tool-step budget: measure, allow what the work needs, and an optional Max tier

Status: step 1 (counters) and step 2 (library allowance) BUILT 2026-10-21, undeployed; step 3 (Max tier) not built.

## Today
- Normal budget `TOOL_AGENT_MAX_ITERATIONS` = 4 on Render (code default 7); deep thinking `TOOL_AGENT_MAX_ITERATIONS_DEEP` = 6 (default 14). The last step is reserved for the final answer, so 4 means 3 tool steps and 6 means 5.
- Edit turns with a connected folder have a floor (`TOOL_AGENT_LOCAL_EDIT_MIN_STEPS` = 6). Low-power mode caps everything at `THROTTLED_TOOL_STEPS` = 4.
- A 7-file library review used the whole normal budget (list, reads 5 + 2, final). 8 or more files would hit the forced final with weeks unread.
- Concern: a higher cap lets a confused turn burn more steps (the retry nudge encourages using the steps that remain). Typical turns end on their own (~4-5 calls uncapped in the repo evals).

## 1. Measure first (BUILT 2026-10-21)
Built: per tool turn the loop's steps used and whether it ran into its last step (`llm_turns.note_tool_loop`, from `react_loop` and `native_loop`), saved in the daily turn totals and shown on Sonic Health, Turns tab, "Does the step limit matter?" (share of tool turns that used every step, average steps, spread, deep turns on their own). Read it after a week or two of real use before changing any cap.

(Original plan:)
Add counters (closed key list in `backend/utils/self_counters.py`, no user id or text): tool turns, steps used (bucketed), turns that reached the forced final step without a final, and which tier ran. Show them on Sonic Health. Decide on any cap change from these, not from a guess.

## 2. Allowance that follows the work (BUILT 2026-10-21, undeployed: `backend/utils/library_allowance.py`)
Built differently from the sketch below in one way: the grant is the SHORTFALL, not `ceil(N / 5)` outright. Reads needed = `ceil(files / 5)` (the loops batch five per step); the grant is what the budget left does not already cover, once per turn after a successful listing, at most 4 (`LIBRARY_EXTRA_STEPS_MAX`), and none in low-power mode. So the 7-file review that fit gets nothing; on Render's budget of 4, the react loop fits 10 files and the native loop 15, and a bigger library gets the steps it needs. The model is told (a note on the listing result / a message) to read five at a time. If the budget still runs out, the reply gets an authoritative note naming the listed files that were never read. Both loops take an optional `library_allowance`; `agent_workflow` makes one per turn for people with a library. Not run against a real library with more than seven files yet (the tests use a scripted model).

(Original sketch:)
After `list_library_files` returns N files, grant `ceil(N / 5)` extra steps, once per turn, capped (about 4) and never above the tier's ceiling. Reuse the loop's existing bounded extra-step mechanism (the one steering messages use). On a forced final with unread files, the answer names what it read and what it did not.

## 3. Max tier (decided 2026-10-20)
- 12 steps (`TOOL_AGENT_MAX_ITERATIONS_MAX`, env-tunable like the others). Setting becomes three levels: standard / deep / max; the stored boolean `deep_thinking` maps to "deep" so nobody changes by accident.
- **A persistent toggle is fine** (its cost and thinking effect are visible in the menu). A one-message-at-a-time mode stays available as an option if it is ever needed.
- **Budget-gated:** low-power mode still caps at 4; refuse or downgrade when the person's remaining daily allowance is too small; the menu says it uses more of the allowance. Guests and locked accounts do not get it (existing lock lists).
- **The tier sets steps and nudges only, not the model.** The reasoning model stays whatever the Code model toggle says (`tool_agent_llm(deep, choice)`): Claude Haiku 5.5 when Claude is chosen, otherwise Gemini (3.6-flash for deep and max, flash-lite otherwise). With Claude selected, deep and normal already share one model, so deep only changes steps. Before relying on this, re-run a small targeted coding comparison (Haiku vs 3.6-flash); the saved A/B logs have no results tables.
- **A way out:** at about half the steps, a notice, "if you can answer now, answer"; uses the same mechanism as the "steps left" notice.
- **Open to everyone** (budget-gated), same metrics; no admins-only stage.
- Per-user cost already shows on Sonic Health, so the cost of the tier is visible.

## Open questions
1. Which counters should the Max tier add beyond steps used and forced finals (for example, how often the half-way nudge was followed by a final)?
