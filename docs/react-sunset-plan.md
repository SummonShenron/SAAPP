# Sunsetting the ReAct loop (the "old engine")

Written 2026-11-05. Goal (Jack): the native tool-calling loop (`backend/services/native_loop.py`) is the only tool loop; `backend/services/react_loop.py` (1,000 lines) and everything that exists only for it is deleted.

## Where things stand (measured, not assumed)
- Production already runs native: `TOOL_AGENT_ENGINE=native` is set. Of the turns that used the tool agent: 11-04 63 of 63 native, 11-03 42 of 43, 11-02 22 of 22, 11-01 27 of 31, 10-31 46 of 49 (llm_turns_daily: `native_turns` against `tool_turns`). The react loop handles about one turn in fifty and falling.
- Tools: all but one now have a native schema. The one left is `propose_code_plan` (kept old-only on purpose: its approval resumes through `paused_code_plan`, and resumed turns are old-only).

## What still sends a turn to the react loop (`native_loop.eligible`)
1. The switch: `enabled()` defaults to "react" when `TOOL_AGENT_ENGINE` is unset (local dev without the variable; the whole test suite, `conftest.py` deletes the variable).
2. `edit_turn` with no `propose_local_edits` offered (an edit request with no connected folder).
3. `resumed`: a paused clarification (`_ClarificationNeeded`, `paused_clarification`) or a paused plan (`paused_code_plan`).
4. Wording and router flags for a tool that is not offered this turn: "run CI" without admin, mongo words without admin, the e-mail wording/flag without the menu line, "navigate/click" without the browser tools, test-run wording without a runner.
5. No tools offered at all.

## What the react loop does that native does not (each needs a keep/port/drop decision)
- Clarify pause and resume (`action: clarify`).
- `propose_code_plan` and its resume.
- After-answer checks: capability denial, answer-quotes-earlier-code, final-answer grounding, idiom grounding, "your code redeclares something that exists" gate, unresolved-truncation note, mismatched start_line note, diff-disagrees-with-fetched-content. None of these has a counter, so how often they fire is unknown. Native has its own: claim check, act-not-suggest, draft checkpoint, diff review, combo, reading-waste, loop rhythm.
- Batched "queries" steps (native runs a reply's calls together).

## Stages (each ends with the full suite green; nothing deleted until the one before it has run)
A. **See what is left.** BUILT 2026-11-05 (uncommitted): `native_loop.react_reason` names why a turn is not native (`switch_off`, `no_tools`, `edit_turn`, `resumed`, `flag`, `wording`; empty exactly when `eligible` is true, proven over a grid of inputs), and `tool_agent_node` counts it as `engine_react_<reason>` (`self_counters.ENGINE_KEYS`; the category only, never the matched words). Deploy, then read the daily counters after a few days of real traffic: they say which of 2-5 matter.
B. **Native by default.** `enabled()` defaults to native; `conftest.py` keeps the react loop for tests that name it until stage E deletes them.
C. **Replace the two pauses.** Clarify: the model asks in its answer and the next message carries the conversation (no paused state). Plan: retire `propose_code_plan` (plan mode is the replacement); remove its menu line, handler and `paused_code_plan`. Decide each after-answer check by reading its trigger and its tests: port it, or drop it with a reason written here.
D. **Edit turns with no folder.** Native answers them (no edit tool); the checks kept in C apply.
E. **Delete.** `run_react_loop` and `react_loop.py`; the old menu format text; helpers in `utils/agent_utils.py` that only it used; `TOOL_AGENT_ENGINE`; the tests that exist only for it (the rest are converted to native).
F. **Prove it.** Full suite; one real-model run of the existing behaviour eval on native (costs money: only with Jack's go-ahead).

## Risks
- The clarify and plan pauses are user-visible behaviour: a person who answers a clarifying question must still get the right continuation.
- Deleting checks nobody counted. Mitigation: stage C decides each one in writing; stage A adds the counters first for any we keep.
- About 45 test references name the react engine; most are mechanical conversions, some pin react-only behaviour and are deleted with it.
