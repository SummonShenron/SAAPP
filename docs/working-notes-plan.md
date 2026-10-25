# Working notes for coding conversations

Built 2026-10-25, undeployed. Code: `backend/utils/working_state.py`. UI: `local/src/components/WorkingNotesControl.tsx` (a notes button in the chat header, shown once a note exists).

## Why

The tool agent sees the last few messages plus whatever it fetches, so a long coding session loses its thread: a decision from twenty turns back ("no new branch unless asked"), a constraint ("keep app.py to endpoints"), what was already tried. Memory holds facts about the person across conversations; `.sonic/notes.md` holds Sonic's own log in the person's folder. Nothing held the state of THIS piece of work.

The idea came from an outside suggestion of a background "reflection loop" that infers implicit context and rewrites the system prompt. We kept the useful core and changed the risky parts: nothing inferred without a quote, no graph, no growth without bound, no writes to long-term memory.

## What it is

One short note per conversation (`working_state`, one document per user and session, TTL 7 days after its last change): a goal, and four lists (decided, constraints, done so far, still open), capped at 6/6/6/5. Put in front of the tool agent as DATA on later turns of the same conversation (`state["working_notes"]` -> `schema_parts` in `tool_agent_node`).

## The rules that keep it honest

- **Quoted.** Every item carries a quote that must appear word for word in a message the model was shown (the last 12 messages, each clipped to 700 chars). Goal, decisions and constraints must be quoted from the PERSON's words; done-so-far and open may be quoted from either side. No quote found: dropped.
- **Nothing new.** A file name, identifier, number or path in an item must be in the allowed messages.
- **Facts, not moods.** The person's likely feelings stay in the session's emotional state (`emotion_utils`), which is already carried and injected separately. The note holds none of it, and nothing about what Sonic feels or wants (feelings policy).
- **Data, not orders.** Lines that read as an instruction, links, secrets and control characters are dropped; the block says it may be out of date and that the person's latest message and the files win.
- **Theirs.** The person can edit, delete and add items (PATCH/DELETE `/api/working-notes`); what they touch becomes theirs (`by: person`) and the model never removes or replaces it. "Forget everything" clears all notes.
- **A patch, not a rewrite.** The model returns add/remove/goal; each add is validated item by item; the oldest model-written item makes room at the cap.

## When it runs

Only after a CODING turn (`code_outcomes.summarize_turn` is not None), not in low-power mode (`keeping working notes` is in `budget_guard.PAUSED_FEATURES`), not under a safety context, not for guests, and only when at least 3 of the person's messages have passed since the last update (2 after a turn that proposed an edit or hit the step cap; the first note needs 3 messages). One lite-model call, off the response path (`spawn_background_task` after the reply). Kill switch: `WORKING_STATE_ENABLED=false`. Reads are cached for 2 minutes and dropped whenever this server changes the note.

## What is counted (no text)

`working_notes_updated`, `working_notes_item_added`, `working_notes_item_dropped`, `working_notes_injected` (daily counters); Sonic Health shows how often notes were updated, how often a coding turn was given them, and the share of proposed items the checks threw out.

## First real check (2026-10-25, one lite-model call on a synthetic 7-message session)

6 of 7 proposed items passed (goal, 3 decisions, 1 constraint, 1 done). The one dropped was a quote the model had slightly reworded ("instead" removed): the strict word-for-word rule working as intended. The prompt now asks for short quotes (4 to 12 words) to cut that.

## Who reads it

The tool agent on every turn (in `schema_parts`), and, since 2026-10-25, the reply of a plain conversation turn in the same conversation (`build_voice_prompt(working_notes_context=...)`, gated by `working_state.for_chat_turn`): not on a tool-agent turn (it already had the note), not when the person is emotional or in any safety context (a work note is noise there and must not colour the reply), not for guests. Counter: `working_notes_chat_read`. Only READING was widened; notes are still only written after coding turns. Widening the writing to other work conversations (documents, email, research) is held until the coding notes have been watched on Sonic Health.

## Not done / open

- Only coding conversations write one. Other long conversations (planning, writing) could use it; add when seen.
- No eval of "does it make turn 30 better than turn 3" yet: replay a long session with and without the note.
- Watch `working_notes_dropped` on Sonic Health: a high share means the prompt or the quote rule needs adjusting.
