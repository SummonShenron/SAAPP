# A rolling summary of each conversation (built 2026-11-01, uncommitted, unproven on a real model)

Decided 2026-11-01 (Jack): "I have seen enough of these failures to consider it." His model: the conversation's older turns get folded into one summary that is rewritten at each compaction, "similar to how you [Claude Code] work", visible and editable, and careful around safety. Written up first, then built the same day on Jack's go-ahead.

## Status (what exists)
Phases 0 to 4 are built: backend/utils/conversation_summary.py (store, fold, checks, window, counters), wired in app.py (the turn's window and block, the background fold, four /api/conversation-summary routes, deletion with the conversation, a wipe of all, and "forget everything"), the tool agent and reasoner (`history_max`), the reply prompt, the "Conversation so far" control in the chat header (components/ConversationSummaryControl.tsx, next to the working notes rather than as a column card), and the Memory/Chat switch on the one ring (components/compactionMeterLogic.ts). Tests: backend/tests/test_conversation_summary.py (no model), frontend conversationSummaryLogic.test.mjs and compactionMeterLogic.test.mjs.
**Not done / differences from the plan below:** the real-model pilot and eval (phase 5) were not run (no spend without Jack's go-ahead); the conversation export does not include the summary (it is built from the page's messages); the tool agent and reasoner keep their old floor of 10 messages (they never see fewer than before, and see more when a turn sends more); the Sonic Health page has no rows for the new counters yet. The fold model is Haiku when a Claude key is set (`review_llm`), else the lite model, untested against either.

## Why
- A reply, the tool agent and the reasoner each get only the **last 10 messages** (`messages_state = chat_sessions[history_key][-10:]` in app.py; `TOOL_AGENT_HISTORY_MAX_MESSAGES` and `REASONER_HISTORY_MAX_MESSAGES`, both 10). Anything older is gone from that conversation unless something fetches it. That is why a context ring never fills, and why Sonic "forgets": the screenshot explained four times (2026-11-01) was one case; it was fixed by looking up the attachment text, which does not help with anything the person *said*.
- Memory already does the folding idea for the PERSON, across conversations: each turn becomes a note (`summarize_turn`, source_ref = the conversation id), more than 100 uncompacted notes are folded by similarity into summaries (clusters of at most 12; originals archived so a bad merge can be undone), and those are folded again past 500. But memory is **retrieved by meaning**, not always present, it is per person not per conversation, and it makes many summaries, not one that says where THIS conversation stands.
- Working notes (backend/utils/working_state.py) are the right shape for a coding thread (goal, decided, constraints, done, open) but only coding conversations write one.
- Not a cost saving: the cost lives in the fixed parts of the prompt and the tool results, not in history. This is a continuity feature. Cost: one small Haiku call per fold, roughly 3,000 tokens in, so about 0.03 to 0.05 cents (an estimate, unmeasured), plus a few hundred tokens in later prompts.

## What it is
**One summary per conversation** (`conversation_summaries`, one document per user and session), made of at most about 8 short items (1,500 characters in all, see the cost ceiling) (what the person told Sonic here, what was decided, what is still open, what was shared, by name only), each with a quote. It is **always put in the prompt**, as DATA, for the rest of the conversation. It is rewritten at each fold from: the old summary plus the messages being folded (everything after `folded_upto` except the kept tail). It lives as long as the conversation and goes with it (deleted when the conversation is deleted or cleared, and by "forget everything").

## Folding from the messages, not from the memory notes
The per-turn memory notes carry the conversation id, but memory compaction folds them across conversations and archives the originals, so a conversation summary built from them would silently lose what compaction had already taken. The fold reads the conversation's own stored messages (`conversations`, the same transcript the page shows), by position: the summary records `folded_upto`, the index of the last message folded. If the conversation is cleared or shortened, the summary is reset.

## When it folds (his answer 1: like Claude Code)
Claude Code compacts on its own when its context nears the limit, and on request. The same two:
- **Automatic:** Jack's model (2026-11-01): Sonic keeps every message word for word until the unfolded messages reach `FOLD_AT_MESSAGES` (proposed N = 14) or about `FOLD_AT_CHARS` (proposed 8,000 characters; one big pasted file counts for many messages), whichever comes first. Then everything except the last `KEEP_TAIL_MESSAGES` (proposed 6) is folded into the summary. It runs after the reply, off the response path, so the answer is never slower. These three numbers replace the earlier "10-message window plus a 6-message trigger", which had a gap (see below).
- **Manual:** a "Compact now" button on the summary card, like /compact.
- **Not** in low-power mode (add "keeping a conversation summary" to `budget_guard.PAUSED_FEATURES`, as working notes are), not for guests. The existing summary is still READ in low power (reading is free).

## No gap between the summary and the recent messages
Sonic always sees the summary (everything up to `folded_upto`) PLUS every message after it word for word. The verbatim part therefore grows from 6 to 14 messages between folds and drops back to 6 after each fold (today it is always 10; the average stays about 10, so the prompt is no longer on average; it is less cache-friendly between folds, to be measured from the counters). The kept tail exists so the exact wording of the last exchange is never lost right after a fold. Nothing is ever in neither place: a test pins that every message is either in the summary range or in the sent messages. `TOOL_AGENT_HISTORY_MAX_MESSAGES` and `REASONER_HISTORY_MAX_MESSAGES` (both 10 today) must follow the same rule for a conversation with a summary, or the tool agent would see a gap.

## Cost ceiling (Jack, 2026-11-01: "a tiny bit more, not a lot")
- Jack's days run closer to 100 turns, so the first lean version (2,500 chars, fold at 20) was still too much; leaner (2026-11-01): the summary has a hard size cap of about 1,500 characters (about 350 tokens) and about 8 items, the oldest model-written items dropped first when it is over. It never grows past that however long the conversation gets.
- The verbatim part runs 6 to 14 messages (fold at 14 or 8,000 chars, keep 6), averaging about 10, the same as today, so the window adds nothing.
- Estimated added cost: about 0.03 to 0.05 cent per turn, only after a conversation's first fold (so under 5 cents at 100 turns, less on short conversations); a fold is about 0.04 cent on Haiku (about 2,000 tokens in, 400 out), once per ~8 messages, about 0.01 cent per turn. All unmeasured; the counters report prompt tokens added per turn and cost per fold.
- A ceiling setting for added prompt tokens per turn: above it, the summary is trimmed instead of growing.
- **Deferred:** a lookup over the older messages of the conversation (a tool for the agent, and a passive lookup when someone says "I told you"). It is the biggest extra cost and build; add it only if the "you forgot" counter shows the summary alone is not enough. Likewise a bigger summary and a bigger verbatim window.

## The rules that keep it honest (the working notes' rules, reused)
- **Quoted.** Every item carries a short quote (4 to 12 words) that must appear word for word in a message the model was shown; an item whose quote is not found is dropped. Facts about the person's own situation and decisions must be quoted from THEIR words. Reuse `backend/utils/memory_grounding.py` and the working notes' validators.
- **Nothing new.** A name, number, file or path in an item must be in the allowed messages.
- **A patch, not a blind rewrite.** The model returns add / remove / replace; every add is checked item by item; the oldest model-written item makes room at the cap. The summary is rewritten at each fold from Jack's point of view, but a wholesale rewrite lets each fold distort the one before (a game of telephone): the patch keeps an earlier item word for word until it is removed on purpose.
- **Facts, not moods.** How the person seems to feel stays in the emotional state and the safety state, which are already carried and injected separately. The summary holds none, and nothing about what Sonic feels or wants (feelings policy).
- **Data, not orders.** Instruction-like lines, links and secrets are dropped; the block says it may be out of date, and that the person's latest message and the files win.

## Visible and editable (his answer 2: yes)
A **"Conversation so far"** card in the session column and in the phone strip's popover, like the working notes control: items listed, each editable or deletable, the person can add their own, and "Compact now". What the person writes or edits is theirs (`by: person`) and the model never removes or replaces it. A fold that changes the summary is shown as updated, not hidden.

## Safety and care (his answer 3: true)
- **Never a safety signal.** The safety plan is computed from the live safety state and the live message, as now. A summary never raises, lowers or replaces it; a test pins that the plan is identical with and without a summary.
- **No risk language in it.** Messages that `safety_utils.detect_risk_language` marks (any level) are left OUT of the fold input, so a statement about hurting oneself is never written into a stored summary. Continuity for those stays with the safety state, which already decays by turns and hours. The surrounding facts (what happened, who, when) can still be folded from the other messages.
- **Facts only about hard events** ("her partner ended it on 2026-11-01" if quoted), no interpretation, no diagnosis.
- **Under a safety context** the summary is still read, below the SAFETY block, as DATA, at every level including acute and imminent (decided); it never changes the ladder or the listening mode.
- **Privacy:** user content in a stored summary is the same kind as memory notes: no text in logs (counts only, log-hygiene rule), deleted with the conversation, included in the conversation export and in "forget everything".

## Where it is read
The reply composer and the tool agent on every turn of a conversation that has a summary (and the reasoner once it proves useful). It is put in the STABLE part of the prompt (it changes only at a fold, so the prompt cache keeps it between folds), labelled "EARLIER IN THIS CONVERSATION", after the guidance and before the recent messages.

## The meter
The compaction meter Jack described first ("how many messages it has been, filling, then tracing back when it compacts") IS this. Today the ring shows MEMORY notes (a count and a threshold of 100). Decided: one ring with a switch; the second reading is unfolded messages against `FOLD_AT_MESSAGES`, per conversation, tracing back at each fold. (Memory compaction stays as it is.)

## What is counted (no text)
`convsum_folds` (automatic and manual), `convsum_items_added`, `convsum_items_dropped` (failed grounding), `convsum_injected_turns`, `convsum_person_edits`. Sonic Health shows them. **Before anything is built, add one free counter:** how often a message says "I already told you", "as I said", "you forgot" (a pattern check, counts only, no text), so there is a baseline to show whether this helps.

## Phases
0. **Baseline (free, tiny):** the "you forgot" counter above.
1. **Backend, no UI:** the store, the fold (pure functions plus one model call), grounding reuse, the risk-language exclusion, `PAUSED_FEATURES`, deletion with the conversation; tests with a fake model, no real calls.
2. **Reading:** inject into the reply and the tool agent (stable part of the prompt); a test that the safety plan does not change.
3. **API and the card:** show, edit, delete, add, compact now; the phone popover.
4. **The meter.**
5. **A cents-level pilot** on a synthetic long conversation with Haiku, then the eval below. Spend only with Jack's go-ahead.

## Evaluation
Replay a long synthetic conversation (40+ messages) and ask questions about early turns, with and without the summary: mechanical string checks for the right fact, and the quote-check pass rate for invented details. Pass: recall of early facts clearly up, nothing invented that passes the checks, no change on the safety scenarios in character_eval, cost per fold measured from the counters. Re-check the "you forgot" counter after a week of use.

## Decisions (Jack, 2026-11-01)
- **Coding conversations get both** a summary and working notes. The notes stay the place for decisions and constraints, the summary is the narrative thread. Watch for duplicates and tune the fold prompt if they overlap.
- **Safety levels:** the summary is still read at acute and imminent, as DATA below the SAFETY block (Jack's answer was "yes it should"; read as "it should still be read"). It never changes the plan.
- **The meter is ONE ring with a switch** between memory notes (today's reading) and this conversation's fold progress (unfolded messages against `FOLD_AT_MESSAGES`). The switch choice is a per-viewer convenience (remembered in the browser).

## Open questions
- `FOLD_AT_MESSAGES` 14, `FOLD_AT_CHARS` 8,000 and `KEEP_TAIL_MESSAGES` 6 are guesses; tune from the counters (prompt size per turn vs "you forgot" rate).
