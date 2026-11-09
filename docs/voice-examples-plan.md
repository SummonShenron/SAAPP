# Voice examples: keeping the standard voice's feel on the cheaper model

Status: BUILT, ALL 19 APPROVED (Jack, 2026-11-08, unread, to try the voice), CALIBRATED, UNMEASURED ON REPLIES. Step 2 of the Flash-voice work (step 1 is `docs/model-eval-plan.md`, Update 14).

## Why

The standard voice (Gemini 3.6 Flash) will be migrated away from, and Jack wants its voice kept on the cheaper model rather than falling back to it. Rule after rule in the Haiku reminder fixed one gap and moved another, and the differences that remained were "hard to express". Mining his thread (Flash-era replies against his next message) found what the replies he reacted well to had: energy matched to the moment (real exclamation marks, humor, an emoji now and then), short replies for light moments, a light tease, a specific from the moment, a social close, a verdict with a reason first when asked for one, an instant stop when told to stop, and fewer questions and name uses than the ones he complained about. Haiku had almost none of the first three (exclamation 3% against 31% in casual chat, humor 0% against 13%), opened with "Got it" 36% of the time against 5%, and overused "I'm right here" and "I hear you".

An example shows that better than a sentence about it.

## What it is

- `backend/components/sonic_voice_examples.py`: 19 invented examples (9 light, 7 hurting, 3 either), each an invented message and an invented reply that shows one thing. All are drafts. Jack approves one by adding its id to `APPROVED_IDS`.
- `backend/utils/voice_examples.py`: `select` (by meaning, reusing the leanings' index and ranking), `build_block`, `warm`, `tally_turn`, `check_registry` and `check_set`.
- `backend/components/constraints.py`: `build_voice_prompt(voice_examples_context=...)`, after the people notes and before the emotional context, so every base model gets it.
- `app.py`: reuses the question embedding already computed for memory; counts the turn (`voice_examples_offered`, `voice_example_used_<id>`); builds the index at startup.
- `backend/tests/test_voice_examples.py`: 12 tests, with a fake embedder, every gate and every check mutation-tested.

## Rules it keeps

- NOTHING IN AN EXAMPLE IS FROM A PERSON'S CONVERSATION. They were written from the patterns, never copied. `BLOCKED_TERMS` refuses the people and projects of Jack's real thread, and the product's own names, in any example. A person's conversation must never be put in a prompt other people see.
- No claimed feeling or action ("I'm dying to know", "I've updated my records": the feelings policy), no "Got it" or "Understood" opener, no list, no em dash, at most one question per example, and across the set at most 30% end on a question, "I'm right here" and "I hear you" together appear at most twice, and most of the light ones carry an exclamation mark.
- Silent under a safety plan. In an emotional context (a mood was read) the light examples are never served, so someone in pain is not shown how to answer good news. A closing message is allowed (the goodnight example is for it). At most two examples a turn; the block tells the model to match the register, the energy and the length and never the wording or the details.

## What happened after the build (2026-11-08)

- Jack approved all nineteen (`APPROVED_IDS`), unread. To turn one off, remove its id; to turn them all off, set `VOICE_EXAMPLES_ENABLED=false`. A changelog line (`voice_examples`) was added.
- **Calibrated** with real embeddings (gemini-embedding-001) on 57 made-up messages worded fresh for the examples and 25 unrelated ones. At the default 0.70 the intended example is served for 48 of 57 (84%), no wrong example is returned for a message that has its own, and the one unrelated message above it ("thanks", 0.727) is shorter than the minimum message length. 0.65 would serve 52 but let 5 of 25 unrelated messages through; 0.74 would serve only 28. The lowest intended scores were the practical ones (a quick opinion, "stop with the long explanations", a groan about a calendar), so those fire least.
- Smoke test of the real path: the first turn after a restart gets no example (the index is built in the background), then a good-news message gets the win example, "see you tomorrow" the goodnight one, a hurting message the "went quiet" one, an unrelated code request none, and a good-news message in an emotional context none.

## What is owed

1. **Measure**, when evals are allowed: Haiku with the examples against Haiku without and against Flash, on held-out real turns, read blind by Jack. Watch for COPYING (phrases lifted from an example, the same example every time).
2. The "I'm right here" and "I hear you" rules overshoot outside hurting moments. Tighten them (leave them out of a light or practical reply) and measure it with the examples in place; not changed yet because it could not be measured the same night and the presence scenario expects the line in both turns of a hurting pair.
3. Reword any example that does not sound like the original voice: they are my rewrites of a pattern, not his.

## Attributing feedback: the reply stamp (2026-11-08)

Jack runs a mix of the standard voice and Haiku and rates turns to keep tuning. A feedback record had no model on it, so a rating could not be traced to a voice, an example or a note. Each streamed reply is now stamped (`backend/utils/reply_stamp.py`): `label` (flash, gpt, haiku, lite or other), `tier` (the tier that actually wrote it, so a Haiku or Lite voice that was not offered counts as standard), `reason` (why the reply tier was chosen), `choice` (the person's setting), and the ids of the voice examples and people notes it was shown (at most two of each). Labels and ids only: no text.

- The stamp is saved with the reply (`_serialize_messages` / `_deserialize_messages`), so it survives a restart.
- When a reply is rated, `/api/chat/feedback` finds the reply by its text among the person's conversations held in memory (`reply_stamp.find`: most recent first, only the last 80 messages of each, only that person's) and copies the stamp onto the feedback record (`corrections.voice`). A reply written before this, or one no longer in memory, has no stamp: its thumb is counted as "not stamped".
- Sonic Health, Feedback tab, "By voice": a person's thumbs by voice, by voice example and by people note (the answer checker's redos are left out, because they are not a reading of the voice).

Not done: the stamp is only set on the streamed voice path (every ordinary reply); a reply written another way has none. Feedback given before 2026-11-08 stays unattributed.
