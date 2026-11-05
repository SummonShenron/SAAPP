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
C. **Replace the two pauses.** DONE 2026-11-05 (uncommitted), the checks below still to decide. The tool agent no longer pauses: `_ClarificationNeeded`, `paused_clarification`, `paused_code_plan`, the resume route in `classify_intent`, the resume block in `tool_agent_node`, the `propose_code_plan` tool (menu line, approval card, handler) and the `needs_clarification` grade are gone, and `eligible`/`react_reason` lost their `resumed` input (so `engine_react_resumed` is gone too). The model's prompt no longer offers `clarify`: it asks its one question as its final answer, and the person's next message carries on the same conversation. A model that still returns `clarify` has its question shown as the answer (`react_loop.py`). A checkpoint written by an older version that still holds a pause is ignored (LangGraph builds channels from the current schema; proven in `test_checkpointer_resurrection_safety.py`). The unrelated getting-to-know-you questions (`backend/utils/clarifications.py`) are untouched.

   **The react-only after-answer checks: DECIDED by Jack 2026-11-05 (port the first three, drop the rest). The three are PORTED (`backend/utils/answer_checks.py`, called from `native_loop.run`, each sends an answer back at most once, counted as `answer_sent_back_<kind>`); the dropped ones stay in `react_loop.py` until stage E deletes it.** They all guard an answer that writes code in prose. With a folder connected, native edits go through `propose_local_edits`, which `local_edits.validate_edit_proposal` checks against the real files (and `act_not_suggest` sends code-in-prose back), so most of them guard a path native no longer takes.

   | Check | Cost | Proposed |
   |---|---|---|
   | Capability denial ("I can't edit/browse" while the tool is offered) | free, mechanical | **PORT**: a real, repeated failure (`test_edit_capability_denial`); native reads the offered tool names instead of the prompt text |
   | Diff disagrees with the file it was read from | free, mechanical | **PORT** for turns with no edit tool (stage D) |
   | No real read before code in the answer | free, mechanical | **PORT** with the one above |
   | Final-answer grounding (judge model) | one extra model call per answer | **DROP**: `claim_check` already holds "it passed" claims to a run; the extra call costs more than it saves |
   | Idiom grounding (judge model) | one extra model call | **DROP**: same reason |
   | Redeclares something that exists (repo lookup) | a repo search | **DROP**: `propose_local_edits` is checked against the real folder; no-folder code answers are rare |
   | Unresolved-truncation retry; mismatched start_line note | free | **DROP**: native re-reads shortened results itself (`MAX_REREADS`) |
   | Batched `queries` steps | n/a | **DROP**: native runs one reply's calls together |

D. **Edit turns with no folder.** DONE 2026-11-05 (uncommitted): the engine choice is gone, so every tool-agent turn runs on the native loop; an edit request with no folder is answered with code in prose, guarded by the three ported checks.
E. **Delete.** DONE 2026-11-05 (uncommitted): `react_loop.py`, `run_react_loop`, `TOOL_AGENT_PROMPT` (the old loop's prompt), `split_prompt.py` (cacheable blocks of that prompt) and the SplitPrompt branch in `claude_llm`, the routing (`enabled`, `eligible`, `needs_old_engine`, `old_engine_reason`, `react_reason` and the wording regexes and flags), the `engine_react_*` counters, the helpers only the old loop used (judge-model checks, redeclaration lookup, truncation and start-line notes, steering notes), the old-loop constants, and the `TOOL_AGENT_ENGINE` switch. Plan mode kept its rule that a plain question during it is not a plan turn. What changed in behaviour: (1) a tool the person is not offered is refused by the loop ("is not an available tool") where the old dispatcher said "not authorized"; (2) the repo, branch and any repo-resolution WARNING now reach the model on every turn (`environment_note=schema`; before, only with a connected folder); (3) the step footer appears only when the loop ran out of steps or a tool errored, as on the native engine before. Tests: the tests of deleted behaviour were deleted (react loop, its gates, routing, batching, split prompt), those that fake the loop now fake the native one (`backend/tests/legacy_loop.py`), and the ones that script the model the old JSON way run through `ScriptedDecisionLLM` (autouse in `conftest.py`).
F. **Prove it.** The full suite is the proof so far. NOT DONE: one real-model run of the behaviour eval on the native loop (costs money: only with Jack's go-ahead); it also needs `.env`/Render `TOOL_AGENT_ENGINE` removed (harmless if left).

## Risks
- The clarify and plan pauses are user-visible behaviour: a person who answers a clarifying question must still get the right continuation.
- Deleting checks nobody counted. Mitigation: stage C decides each one in writing; stage A adds the counters first for any we keep.
- About 45 test references name the react engine; most are mechanical conversions, some pin react-only behaviour and are deleted with it.
