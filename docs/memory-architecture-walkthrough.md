# How Sonic's memory works (interview walkthrough)

Written 2026-10-23 from the code, not from memory. File names are real; numbers are the current defaults. Where something is an env var it says so.

## The one-paragraph version

Sonic keeps three kinds of memory, with different lifetimes and different ways of being read back:

1. **Ephemeral (the conversation).** The messages of the current chat session, plus the graph state that LangGraph checkpoints for it. It is what makes "it" and "that" make sense in a reply. It is read every turn, whole, and it expires.
2. **Durable facts.** Short statements about the person ("works at X", "prefers tabs", "daughter's name is Y"), each with a category, a confidence and an embedding. Saved when the person tells Sonic something worth keeping. A few are always injected; the rest compete by relevance.
3. **Semantic episodic memory.** One summarized note per conversation turn, embedded in an Atlas vector index. Searched on every conversational turn, but only injected when a note is genuinely close to what was just said.

Two jobs connect the layers: a **write path** (what gets saved, and the checks that stop it saving things the person never said) and a **maintenance path** (merging, decay, pruning, staleness). Everything is built so that a failure in memory never breaks a chat turn.

## The three layers, one by one

### 1. Ephemeral: the conversation

- `chat_sessions[username::session_id]` in `app.py` holds the messages. It is loaded from the saved conversation the first time a session is used (`load_session_messages`), appended to as the turn runs, and saved back after each turn (`save_conversation_turn`).
- The graph sees the **last 10 messages** (`messages_state = chat_sessions[history_key][-10:]`). Each is compacted when formatted for a prompt (`format_history`, with a longer limit for the newest message), so a long paste does not eat the context.
- LangGraph's `MongoDBSaver` checkpointer stores the graph's state per thread (`thread_id = "username::session_id"`). That is what lets a paused clarification or a half-finished plan resume. Checkpoints are pruned after every turn and by a retention loop, so this layer genuinely expires.
- Per-turn attachments are summarized into the prompt for that turn only.

What it is for: coherence inside one conversation. What it is not for: remembering anything next week.

### 2. Durable facts: what Sonic knows about you

**Shape.** A `UserFact` (`backend/utils/memory_utils.py`) has: text, `category` (identity, preference, career, project, goal, relationship, trait, setting, pattern, coding_style), `domain` (work, own, personal, general), `source` (explicit, inferred, pattern, user_edit, safety_support), `confidence`, timestamps, an embedding, a goal status, and `related` (ids of facts about the same real thing).

**Storage.** One document per person holding the fact list, with the embeddings split out into a second collection so the facts document stays small. The list is capped at 1,500 facts (`MAX_STORED_FACTS`, raised from 400 on 2026-10-23) because it lives in a single Mongo document (16 MB limit): a fact costs about 2 KB with its packed embedding, so 1,500 is about 3 MB and the limit would take about 8,000. It is read through an in-memory copy; a refresh first reads only a revision stamp on the document and re-reads the list only if another instance wrote since.

**Write path.**
1. The reasoner (one small-model call per turn, which also does routing) sets a flag, `needs_memory_save`, when the message states something worth keeping.
2. `memory_save_node` asks a small model to extract `{category, domain, fact}` from the message.
3. **Honest writes** (`memory_grounding.ground_fact`): if the fact the model wrote contains a time, number, weekday, month or a name the person did not say, the fact is replaced by the person's own words. A bare "yep" confirms nothing unless the assistant had asked an explicit question.
4. **Dates are resolved at save time** (`date_resolve.annotate_dates`): "tonight" becomes "tonight (Wednesday 2026-10-21)" in the person's timezone, so the fact is still correct next week.
5. `save_user_fact` embeds it and compares it with existing facts. If the best match is at cosine 0.80 or above, a small model judges the pair as **duplicate** (reinforce), **supersede** (replace) or **distinct** (add). Cosine only picks the candidate; the judge decides, because "builds a frontend in Next.js" and "Sonic's frontend" are near each other and different things.
6. Duplicate: confidence goes up by 0.15 (capped at 1.0). Supersede: confidence resets, and any older recalled notes that said the old thing are flagged stale. A person's own edit on the Memory page keeps its wording.
7. New facts are linked to related facts (`fact_links`, a small-model judgment with a strict second look), then the store is pruned if it is over the cap.

**Where else facts come from.** Compaction (below) promotes facts from clusters of notes, saved as `source="inferred"` at confidence 0.7. A periodic pass extracts **patterns** that at least three facts support. Both go through the same `save_user_fact`, so they get the same dedupe for free.

**Read path (every turn).** `fetch_relevant_user_facts`:
- **Always included:** up to 8 foundational identity facts (name, pronouns), plus one "anchor" fact for the person's job and one for their own projects, so "how's work going?" is never ambiguous.
- **Relevant:** 5 more, ranked by `cosine(question, fact) x effective_confidence`.
- **Linked:** facts bound to a chosen fact come along.
- Shown grouped under headings (job / own projects / personal / general), with a note that the job and the person's own projects are separate things. Relative days in older facts are shown with their real date.

So the right mental model is not "all facts are injected every turn". It is "a small foundation is always there; everything else has to be relevant."

**Confidence and decay.** `effective_confidence` is a read-time lens: confidence x 0.5^(days since update / 90). Nothing stored changes. Identity facts never decay. Reinforcement is how a fact becomes trusted; decay is how a stale one stops outranking a fresh one.

**Pruning.** Over the cap, identity facts and safety-support contacts are never pruned. Among the rest, inferred facts go first, then patterns, then what the person stated; within a source, the least confident go first. Everything evicted is archived, not deleted.

### 3. Semantic episodic memory: what happened

**Write path (every turn, fire-and-forget).** After the reply is sent, `index_conversation_turn` asks a small model for a one-sentence note about the turn (`summarize_turn`). The same grounding check applies: a sentence with a time, number, weekday, month, a made-up name, or a relative day ("today") the person did not give is dropped, and a note with nothing left is not saved. A surviving note gets its relative days resolved to real dates, is embedded and stored in the `user_memory_chunks` Atlas collection (index `user_memory_vector_index`) with the person's id, source type and creation date.

**Read path (conversational turns).** `retrieve_relevant_memory_items`:
- Vector-search the person's own notes only (`pre_filter` on username), excluding any flagged stale. Top 3.
- **A score cutoff of 0.80** on Atlas's normalized scale (that is a cosine of only 0.6). It was 0.75 until `memory_eval` showed that questions about nothing stored still scored 0.76 to 0.82 against the closest notes, so every turn was handed three unrelated "recalled" lines.
- **Short follow-ups** ("make it funnier") also search the previous message joined to the question, with an extra bar, so "it" resolves.
- **Second look:** if nothing cleared the cutoff but some notes fell just short, and the message speaks of the user ("my", "remember"), a small model strictly picks which near misses really are what the person means. Paraphrases ("my pup" for "greyhound named Biscuit") score just under the cutoff, and so do unrelated questions, so a score cannot tell them apart. It has a 2-second time limit.
- Each recalled note carries its age ("about 3 weeks ago"), and the prompt tells the model to weave it in only if it fits.
- The question is embedded once per turn and reused by both the fact lookup and this one.

**Maintenance: compaction.**
- **Tier 1:** at 100 uncompacted notes, cluster them by cosine similarity (0.85, greedy, at most 12 per cluster), have a small model summarize each cluster and extract facts, then store the summary with the oldest date of what it replaced (so folding an old memory in never makes it look new). **Copy to an archive first, insert the summary second, delete the originals last**, so a crash can never lose content and any merge can be undone. Notes with nothing similar are tagged so they are not rescanned forever.
- **Tier 2 (the self-sustaining part):** checked right after a tier-1 run completes. When the pool of already-consolidated notes (tier-1 summaries plus earlier tier-2 summaries) passes 500, it pools them together and re-clusters and re-summarizes the whole pool, **replacing** its own earlier output instead of adding to it. That is what keeps storage bounded without ever needing a tier 3. The same checkpoint also triggers the pattern extraction over the facts.
- **Staleness:** when a fact is superseded, notes that still say the old thing (similarity 0.90 on the Atlas scale) are flagged stale and stop being recalled, so the corrected fact and its stale twin cannot both be "genuinely recalled".

## One turn, end to end

1. Message arrives. The last 10 messages of the session are loaded. The reasoner sets flags, including whether to save a fact.
2. The graph routes (retrieve, tool agent, conversational, ...). If `needs_memory_save`, `memory_save_node` writes the fact (and a "manual" note).
3. The reply prompt is assembled: persona, then lessons-learned guardrails, then the **facts block**, then (on a conversational turn) the **recalled notes**, then other context (open loops and events, goals, reading shelf and so on), then the history and the question.
4. The reply streams. After it ends: the turn is summarized, grounded, embedded and stored; open loops are captured; compaction is triggered if a threshold was crossed. All of it off the response path.

## What sits next to the three layers

- **Open loops and events** (`open_loops.py`): things with a date ("interview Tuesday"), each with a time, person and place that are kept only if the person said them and are otherwise marked unknown. The date itself must come from the person's words (found the hard way on 2026-10-23, when a guessed "today" was said back as fact).
- **Corrections / lessons learned** and **coding preferences** (`coding_style` facts are injected into the coding agent, not the reply).
- **Controls the person has:** the Memory page lists, edits (an edit outranks any model rewrite) and deletes facts and notes; a viewer shows which notes a reply used; "forget everything" removes the facts, embeddings and notes.

## Why it is built this way (the answers to "why")

- **Facts and notes are separate because they answer different questions.** Facts are small, edited and corrected ("what is true now"); notes are a growing log ("what happened"). Merging them would force every fact through vector search, or every episode through the correction logic.
- **Cosine proposes, a model disposes.** Every place two items are judged "the same thing" (fact dedupe, fact links, second look, compaction), similarity only shortlists and a small model with a strict "default to no" prompt decides. Thresholds were chosen by measurement (`backend/evals/memory_eval.py`), not feel: a tier-2 threshold that looked right merged nothing on the real store.
- **Saving is the risky step, so it is checked mechanically.** A prompt that says "do not record details the user did not give" is only prose; `memory_grounding` is the code behind it, with no model call, and it logs counts only.
- **Decay is read-time, not a write.** Nothing is lost to a bad decay constant, and it can be re-tuned without a migration.
- **Never destroy, always archive.** Pruned facts and merged notes go to archive collections first; compaction inserts before it deletes.
- **Memory must never break chat.** Every read and write is wrapped, runs off the event loop where it blocks, and degrades to "no memory this turn".
- **Cost.** One embedding per turn shared by both lookups; the second look runs only when nothing cleared the bar and the message is about the user; summaries and judges use the small model.
- **Privacy.** Logs carry counts and ids, never message text (an AST test enforces it); users can see, edit and erase what is stored.

## Honest weaknesses (good to say before you are asked)

- Facts live in one document (cap 1,500). That is fine for a personal assistant; past a few thousand the design should be one document per fact with a server-side vector index. (It was capped at 400 until 2026-10-23, when the busiest account was full and had had 597 facts evicted.)
- Passive recall is only on conversational turns. The tool agent gets no memory by default (`TOOL_AGENT_MEMORY` ships off), but it can now search it itself with the `search_memory` tool, mid-turn, with the same filtering and guard rules as the optional memory block.
- Greedy clustering chains at lower similarity thresholds, which is why the max cluster size exists.
- The relevance cutoff is a single global number on a score scale where unrelated and related notes overlap. The second look patches this; a learned reranker would be the next step.
- A "supersede" judgment is only as good as the small model; the guard is that originals are archived and the person can edit.
- Eval coverage is for recall quality and linking; there is no end-to-end eval of "did the reply use memory well".

## Numbers worth remembering

| What | Value |
|---|---|
| Fact dedupe similarity | 0.80 cosine |
| Reinforcement per re-observation | +0.15 confidence |
| Decay half-life | 90 days (not for identity) |
| Always-on identity facts | up to 8 |
| Relevant facts per turn | 5 (+ linked, + 2 anchors) |
| Fact cap per person | 1,500 (about 2 KB a fact) |
| Recall cutoff | 0.80 (Atlas scale), top 3 |
| Near-miss band / second look | 0.06 under cutoff, at most 3, 2 s limit |
| Compaction | tier 1 when more than 100 raw notes are waiting; tier 2 when the consolidated pool passes 500; cluster similarity 0.85, max 12 |
| Inferred-fact confidence | 0.7 |
| Stale-note similarity | 0.90 |
| Session history to the graph | last 10 messages |
