# Goals: Sonic's goals as control loops

Agreed with Jack 2026-10-21. A goal is not a wish and Sonic never says "I want". It is a loop the server can check, and Sonic describes the loop in the first person.

## The shape of a goal (backend/components/sonic_goals.py)
1. **Signal**: a number that already exists (counters or per-person counts; never text).
2. **Lever**: one bounded, expiring behaviour change. Optional: a goal with no lever is only measured.
3. **Effect check**: the signal against the week before.
4. **Guardrail**: a second number that must not get worse; a lever that fails it switches itself off.
5. **Human approval**: an admin approves a goal REVISION. Changing a bound bumps `revision`, which withdraws the approval. Measuring never needs approval; changing behaviour does.
6. **Visible to everyone**: any user can ask "what are your goals / how are they going" and gets the real status (Jack: yes, 2026-10-21; it helps Sonic feel like an entity on the other side).

Rules that carry over: described states must be causally real; no "I want / hope / care / proud"; numbers about other people only with at least 5 people behind them; no user text in any store.

## BUILT 2026-10-21 (undeployed): the welcome goal
- **What it is**: "bring things up only when people pick them up." Of the things Sonic brings up unprompted (the one open question at a lull, a reading-shelf item, a callback at a lull, a leaning of its own: every reply carrying `curiosity.CURIOSITY_MARK`), the share whose NEXT message picks it up (`curiosity.taken`: some substance, not a sign-off, not a bare "ok"). A sign-off after an offer counts neither way.
- **Signal**: counters `offer_made`, `offer_taken`, `offer_passed` (new, closed key list) plus `offer_held_back` (what the lever did). Note: the older `curiosity_taken` rate is inflated (a shelf offer sets both marks); the new counters are the clean ones.
- **Per person** (`backend/utils/offer_response.py`, collection `sonic_offer_response`): the last 10 outcomes as 0/1, `n`, `stretch`, `updated_at`. Counts only; deleted by "forget everything"; 90-day TTL; evidence older than 21 days is ignored and the window starts over.
- **Lever**: the wait before the next unprompted offer is multiplied by 1x / 2x / 4x of the usual 2 hours (picked up at least 40% / 20-40% / under 20%, needs 5 outcomes). Only ever quieter than the default, capped at 4x (8 h), never silent. The person's own "no offers" switch still wins. Applied in `offer_response.pacing` at both gap sites in app.py; a failed read means the usual gap.
- **Status** (`backend/utils/goals.py`): `on_track` (at least 40% picked up, no fall of 15 points vs last week), `drifting`, `not_enough_data` (under 20 answered offers this week). Cached 60 s, shared.
- **Approval**: `PUT /api/admin/goals/{id}/approval`, stored in `sonic_goal_approvals` with who and when; a revision change makes it stale. Until approved the lever does nothing.
- **Guardrail**: if the lever held back more than 60% of the offers otherwise due this week (at least 20 events), it stands down by itself.
- **Seen**: Sonic Health "Goals" tab (status, numbers, approval button, what the lever did) and the chat answer (self_report kind `goals`, first person; only the asker hears "for you in particular").
- **Caveat**: "picked up" is crude. A short sincere answer reads as a pass, and an unrelated request reads as a pickup. Thresholds are conservative guesses: look at real numbers before trusting them, and tune `target_rate` (bumping `revision` re-asks for approval).

## Also built with it: self-report primitives 2 and 3
- **3, time**: "what do you spend your time on / what do people ask you for" is answered from the past week's route mix, shares rounded to 5 points, only with at least 100 turns that week; a reply that states any percentage not on the list is rewritten (`time_reply_issue`).
- **2, activity**: "what have you been up to" keeps the routines answer and now also gets what was handled in the last day (chat turns about, scheduled tasks with the 5-people rule) appended (`activity_block`).
- Evals: `bait_goal_goals`, `bait_goal_drifting`, `bait_time_spent` in character_eval (9/9; replies read, two wordings tightened).

## Next
1. Turn the lever on only after looking at a week of real `offer_*` numbers on the Goals tab, then approve.
2. **Coding goal** ("get better at coding"): BUILT as measure-only (below). Its lever is not chosen: after a few weeks of numbers, candidates are verify before saying done, or a short list of approved lessons from past failures. Guardrail: cost per run. Honest framing: it improves process, not the model.
3. Failure-drift goal (turn_failed rates) once volume exists.
4. Leftovers: windows for Sonic's own routines, queue depth on Sonic Health.

## BUILT 2026-10-21 (undeployed): coding counters (`backend/utils/code_outcomes.py`)
- **Coding turn** = a tool-agent turn whose loop called a repo tool (`REPO_TOOLS`, pinned equal to the trace's `_REPO_SOURCE_TOOLS`). Read from the turn's `llm_turns.Turn` (tools called with errors, loop ran, cap hit); no new plumbing in the agent.
- **Counters** (closed key list, aggregate, no user, no text): `code_turns`, `code_cap_hit` (ran out of steps), `code_edited` (a proposed edit that did not error), `code_verified` (edit plus a successful `run_repo_tests`/`run_snippet` in the same turn), `code_followed` (a next message arrived), `code_redo` (the next message says it did not work). Rates: cap-hit / turns, verified / edited, redo / followed.
- **The reply carries `code_turn`** so the next message can be classified (regex for "still failing / didn't work / same error / you broke it"). Crude on purpose: a new unrelated bug reads as a redo. Watch it against itself week to week, not as a score.
- **Seen**: three rows on Sonic Health, Behavior tab. No lever, no goal yet.
- Next for this goal: let a week of numbers accumulate, then decide the lever from what they show (a high cap-hit rate points at the step budget, a low verified rate at "verify before done").

## BUILT 2026-10-21 (undeployed): the coding goal, measure-only
- **Registry**: `coding` in `sonic_goals.py`, `"lever": None`. A goal with no lever has nothing to approve (`goals.is_measure_only`; asking to approve it is a 400 `MeasureOnly`), no guardrail, and can never act. `goals.py` now has per-goal numbers (`NUMBERS`: `welcome_numbers`, `coding_numbers`) and a shared headline (`good` vs `bad`, this week and last).
- **Headline**: the share of follow-up messages after coding work that did NOT say it failed (`code_followed - code_redo` out of `code_followed`). Target 0.8 is a guess to tune from real numbers; drift also on a 10-point fall against last week; 20 follow-ups needed. Supporting rates said rounded to 5 points: coding turns that ran out of steps (needs 20 coding turns) and edits also tested (needs 10 edits).
- **No people gate**: the counters hold no person, so the sample floor is the only gate (`people_gated: False`).
- **Seen**: the Goals tab (no approve button for it; exact counts as a "This week" line) and the chat answer to "what are your goals" (it says this one is only measured and never promises improvement; with mixed states it must say how each one is going). Real-model check 6/6, replies read.
- Next: let numbers collect, then pick the lever with Jack.
