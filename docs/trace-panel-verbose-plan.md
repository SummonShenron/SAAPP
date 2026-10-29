# Trace panel: expandable, verbose steps (no model calls)

Status: BUILT (by Jack with Copilot, from this plan) and reviewed 2026-10-21: empty results labelled "empty", facts capped and masked, subjects shown as host and path only, the chevron only on steps with facts, titles and
detail de-duplicated, nothing logged. Open: slices 2 and 3 were partly covered (tool steps, route, subjects); check against the slice list below before calling it finished.

## Goal
Click a step in the "How it works" panel and see what it actually did. Everything shown is measured in code, never generated, so it costs no model calls.

## Today
- Backend sends each step as `{node, title, detail}` (+ batch index/size) through `safe_emit_event("trace_detail", ...)`; app.py turns it into a `node_progress` SSE event.
- `local/src/components/TracePanel.tsx` draws nodes on a line. Nothing expands. Steps live in page state only (not stored).

## Design
1. Give each step an id (`step_id`) in the event. Send a second small event, `trace_facts` `{step_id, facts: [...]}`, when the step finishes. Facts are short lines: at most ~10, ~200 characters each.
2. `TraceStep` gets `facts?: string[]`. Each node becomes a button with a chevron and `aria-expanded`; the same component serves the mobile bottom sheet.
3. The panel plays steps back with a short delay (`nodeQueueRef`), so a facts event can arrive before its step is drawn: hold facts in a map keyed by `step_id` and merge when the step appears.

## What each step can show (all measured)
- **Route / plan:** which router flags fired and why (from `llm_turns.tool_agent_reasons`), and the plan queue. Would have answered "why did this go to the tool agent?" without a pasted trace.
- **Tool step:** tool name, shortened arguments (typed values masked), duration, ok / error / truncated, result size, retry count, batch info. One place in `react_loop.py` covers every tool; the native loop gets the same through its emit hook.
- **Retrieval:** libraries searched, chunks returned, files hit, graph connections, grading result, loop count.
- **Answer:** context blocks that went in (by name only: memory, shelf, task results), model used, time to first token.
- **Budget:** current level, steps left.
- The model's own "purpose" field is free: show it labelled "stated reason", never as fact.

## Rules
- Metadata and shortened arguments only. Never full prompts or result bodies, except one capped, redacted excerpt per step, shown only in the requester's live trace and never logged or saved (see build_tool_excerpt in agent_utils.py): up to 1,000 characters of a file for read_repo_file; for search_literal and search_code the matches grouped by file with line numbers (at most 10 files, 25 matches, 5 per file, 160 characters a line; lines from secrets files are left out and counted).
- Goes only to the requester's browser; not logged, not stored (the log-hygiene test is untouched).
- Admin-only data (logs, incidents) stays admin-only.
- Cannot do without a model call: a plain-language "why the model chose this". Not attempted.

## Build slices
1. Step ids, the facts event, the expandable panel, tool-step facts.
2. Route and plan step.
3. Retrieval and answer-composition facts.
Tests: payload size caps, masking, merge-before-step, keyboard toggle (`aria-expanded`), no content in logs.
