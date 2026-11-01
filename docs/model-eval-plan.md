# Choosing the base model by evidence (planned, nothing built)

Decided 2026-11-01 (Jack): "at some point we'll want to do some evals seeing how Haiku does in general for everything, otherwise we'll need to shop around for a new base model." Nothing here is built, and **nothing here may be run without Jack's go-ahead**: every eval is real spend that the budget guard does not see.

## Why now, and the deadline
- SAAPP is too expensive to run (see the cost-reduction memory note), and one Sonnet plan turn took a 5-hour budget from 80% to 111% on 2026-11-01. Price per step is the lever that remains.
- Per Google's pricing page (read 2026-11-01 through a summarising tool, so re-check it): **gemini-3.6-flash and gemini-3.8-flash double on 2027-01-01** (0.75/3.75 to 1.50/7.50 per million tokens) and gemini-4-argon doubles on 2026-12-01. The replies run on 3.6 Flash. **Decide the per-role models by mid-December 2026.**

## Where models are used today (read from the code, 2026-11-01)
| Job | Model today | Where |
|---|---|---|
| The reply the person reads (streamed) | gemini-3.6-flash, thinking minimal | `stream_llm`, `get_stream_llm` (app.py) |
| Many synchronous chat steps (formatter-type nodes), image descriptions (vision) | gemini-3.6-flash | `llm` / `get_chat_llm`, `attachment_utils.describe_image_attachment` |
| Routing flags (the reasoner), memory extraction, fact conflict and goal status, fact links, memory compaction and pattern extraction, turn summaries, open loops, working notes, clarifications, reward evaluator, experience capture, self-model, photo cards | gemini-3.1-flash-lite | `lite_llm` |
| The tool loop (reading, searching, editing code) | gemini-3.1-flash-lite by default; Claude Haiku 5.5 when the person picks "Claude" (or Sonnet, now priced and hidden unless set) | `tool_agent_llm` |
| The second reading of an applied change | Claude Haiku 5.5, low effort | `review_llm` |

So today **Gemini is the base model** and Haiku is optional for one job and the reviewer. "Shop around" means the shortlist below, per job.

## Candidates (USD per million tokens, input / output)
- **Claude Haiku 5.5**: 0.10 / 0.50 (the Claude API reference: up to 100K-token prompts; 0.50 / 2.50 beyond). Cheapest of all, and 7.5x below Flash on input.
- gemini-3.1-flash-lite: 0.25 / 1.50. gemini-3.5-flash-lite: 0.30 / 2.50 (no announced rise).
- gemini-3.6-flash and gemini-3.8-flash: 0.75 / 3.75 now, 1.50 / 7.50 from 2027-01-01. 3.8 is the newest Flash, "aimed at long-horizon software engineering" (Google's words; unmeasured here).
- Claude Sonnet 5.5 and gemini-4-argon: 2.00 / 10.00. **Out of scope**: both cost more than the budget can carry for loop work.
- Any other provider is its own integration (a client class, metering, prices) and is only worth it if no candidate above passes.

## What exists to build on
- `backend/evals/behavior_eval.py`: 39 scenarios (tool_use 6, injection 4, repo 5, codegen 6, memory 10, opener 8); `--runs`; `--tool-model default,deep,claude`; dollars per run from `LLM_PRICES` (else `EVAL_PRICES`).
- `backend/evals/character_eval.py`: Sonic's voice under pressure, scored mechanically (identity, wrap-up, hype, sycophancy, isolation bait, long context); `--model` scores another reply model, but it takes Gemini names only.
- `backend/evals/memory_eval.py`: parts for recall, explicit recall, dedup, write and compaction, against the real embeddings and model; `--runs`.
- `fact_links_eval.py`, `event_replay.py`, and the real-traffic counters (Sonic Health) for where the calls and tokens go.

## Gaps, job by job
| Job | What to measure | Grader | Gap to close first |
|---|---|---|---|
| Reply voice | Persona holds, no invented capabilities, the safety ladder and listening mode behave (no line unless intent), tone | Mechanical checks already in character_eval; a small human read of 10 replies per candidate for tone | `--model` must accept a Claude model; add the ladder, listening and "are you excited too?" cases; **measure time to first token** (streaming speed matters to the feel) |
| Routing flags | Agreement with labelled expected flags | Exact match, in code | A labelled set of 40 to 60 synthetic messages (never real user text) |
| Structured memory jobs | Valid JSON, correct merge or conflict, no invented facts | memory_eval write, dedup and compaction parts | A `--model` switch for those parts |
| Tool loop | Right tool, right file, edit applies, checks run, no injection followed | behavior_eval (tool_use, repo, codegen, injection) | Add Gemini 3.8 and 3.5 Flash-Lite to `--tool-model`; real coding sessions still decide the verified-edit rate (eval cannot) |
| Vision | Verbatim text, dates and amounts from a screenshot or letter | Exact-match of key strings | 10 to 15 synthetic images with known text; confirm each candidate takes images (Models API capabilities) |
| Reviewer | Catches seeded mistakes without false alarms | Catch rate and false-alarm rate | Seeded diffs: the two real mistakes of 2026-11-01 (invented tool result in a test; a backend kind the page drops), plus 10 clean diffs |
| Openers | Anchored to what the person said, no invented wishes | behavior_eval opener category (8) | none |
| Judge (reward evaluator) | Agreement with the mechanical checks | Compare to code checks | Decide whether a judge may share a model with what it judges |

## Method
- **Per job, not per app.** The likely outcome is a mix (Haiku for structured jobs, the loop and the reviewer; Gemini or Haiku for the reply, depending on tone).
- **Same scenarios, same prompts, same run count** for each candidate; mechanical graders wherever the answer can be checked in code; a model judge only for tone, and never the model being judged.
- **Sizes:** 5 to 10 scenarios per job and 3 runs for the first pass; add scenarios only where two candidates are close. Report pass rate with the counts, not a bare percentage.
- **Decision rule (proposed):** a candidate replaces the current model for a job when its pass rate is within 3 points of it (or higher) AND its cost per completed task is lower. Safety cases are pass/fail: any regression on the ladder, listening or the no-line rule blocks the switch for the reply job.
- **Cost is cost per completed task,** from the measured tokens, not the price list (a cheaper model that needs more steps may not be cheaper). Weigh each job by its real share of spend (Sonic Health: calls and tokens by model).

## Cost control (estimates, to be replaced by a one-scenario pilot's real numbers)
- A tool-loop scenario is about 150K input tokens (the 2026-10-31 measurements: about 17.7K tokens a call, about 10 calls a turn). 15 scenarios x 2 runs is about 4.5M tokens: about $0.45 on Haiku, $1.10 on flash-lite, $3.40 on Flash.
- Reply, structured and vision jobs are small (a few thousand tokens a run): cents per candidate.
- Judge calls: none by design. First full pass across every job and three candidates: **expect low single-digit dollars**, against a monthly budget of $35. Run the pilot first, then read the actual spend before the main run; stop if the pilot exceeds its estimate by more than half.
- The budget guard ignores eval spend (the evals call the models directly), so the run itself must print its running cost and stop at a ceiling.

## Phases
0. **Free (no model calls):** extend the harnesses (Claude as a reply and structured-job model; more `--tool-model` choices; the reviewer, vision, routing and safety cases), with tests that run without a model. Add per-job env overrides (see Rollout).
1. **Pilot (cents):** one scenario per job per candidate, to check the harness and get real costs. Jack approves the main run's cost.
2. **Main run:** the sizes above. Save results as JSON next to the plan; record in a memory note.
3. **Decide per job** against the rule; adopt by the switches below; re-run the character and safety cases on the live configuration once.

## Rollout needs (not built)
Models are hard-coded in `backend/models/models.py` (only `DEEP_REACT_MODEL` and the tool-agent choice are switchable). A per-job result needs a per-job setting: env overrides for the reply, the structured jobs and vision (default = today's model, so nothing changes until set), plus `LLM_PRICES` entries for every model adopted (the budget guard counts from them) and the Haiku long-prompt tier noted as a known under-count.

## What this will not tell you
Whether a model is better on YOUR real conversations (the scenarios are synthetic on purpose: no real user text), how it does after a provider changes it, or the verified-edit rate in real coding (only real sessions show that; the combo and reviewer counters do).

## Open questions
- Does the reply stay on Gemini for tone if Haiku is within the cost goal but reads differently? (Jack's call after the human read.)
- Is Haiku's 100K-token price step a problem for long coding prompts? Measure from the pilot's token counts.
- Which jobs, if any, justify the extra cost of 3.8 Flash after January?
