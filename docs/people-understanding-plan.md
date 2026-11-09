# Letting Sonic learn how people go through things ("people notes")

Status: PHASES 0, 1 AND 2 BUILT, ALL 20 NOTES APPROVED (Jack, 2026-11-08, unread, to try them), THRESHOLD CALIBRATED, UNMEASURED ON REPLIES. The behaviour fix and its eval (phase 0), the twenty notes and the registry check (phase 1), and the retrieval and the reply-voice block (phase 2) are built and live after a restart. Not done: any eval with notes in the prompt, the reading source (phase 3) and the conversation source (phase 4).

## What Jack asked for

Sonic asks the wrong kind of follow-up questions when someone is hurting. Jack's thought: unless Sonic can take in, as something of its own, "people feel this way after X", and reflect on it and add it to something like its profile, it will keep asking instead of understanding.

## What the last hour showed (no user text kept)

Over twelve replies to someone upset by a person going quiet, every reply ended on a question and the questions dug at the wound: what the other person last sent, what they said when asked why, "what do you find yourself wishing they had said", "when the thoughts come back, what is the first question that shows up?", the same question twice. A friend does not do that. A friend says something: that people in this spot replay the last message hunting for their own mistake, that going quiet after "too fast" is usually the other person being overwhelmed, that anger at them is often the first thing that feels like solid ground.

Measured on made-up three-message versions of the same situation (`hurt_no_interview_friend`, `hurt_no_interview_dad`, 10 runs each, Haiku in the app's voice shape):

| Reminder | Pass |
|---|---|
| the one that was live (ends every reply with a specific question) | 0/20 |
| the one from before today's voice work | 0/20 |
| the first rollback ("at most one question in three replies") | 4/20 |
| **now: ends on a statement, never a question; says what this does to people and what tends to help** | **32/40 (80%)** |
| Flash, for reference | 1/20 |

So Haiku always interviewed (Jack was right), and Flash does it too, only less pointedly.

## Is it a knowledge problem?

Mostly no, and that decides the order of work. The model already knows in general how people feel after being ghosted or after a family silence; with the rule above it said it (80%). The failure was its default move: when it does not know what to say, it asks. So the first fix was behaviour, and it is built.

What a people note adds that a prompt rule cannot:

1. **Specific, vetted understanding in front of the model at the moment it matters**, instead of whatever the base model happens to produce, which is generic and sometimes wrong in a way nobody has checked ("comfort lines that would fit anyone" were one of the measured gaps).
2. **Sonic's own, growing understanding**, approved by a person, that gets better from real conversations: what actually helped, what people did not want to be asked.
3. **A way to say it honestly.** "People in this spot often..." and "in my record..." are true statements; "I know how that feels" and "I've been there" are not, and are blocked by the identity check.

## What a people note is

One short entry, written as what is true of people, never as Sonic's feeling and never with a person's words in it.

Fields (the shape follows `backend/components/sonic_leanings.py`, so the same index, threshold and approval flow can be reused):

| Field | Meaning |
|---|---|
| `id` | also its counter's name |
| `situation` | what someone would say or be in, in plain words: "a person they care about has gone quiet after they said something vulnerable" |
| `asks` | 3 or 4 example messages in the words people use, for matching by meaning (the leanings' `asks`) |
| `pattern` | what people commonly go through, one or two sentences ("replay the last exchange hunting for the mistake; check the phone; get angry because anger is steadier than not knowing") |
| `helps` | what tends to help, one sentence, concrete |
| `avoid` | what not to ask or say ("do not ask them to retell what was said; do not offer a reason for the other person's silence as fact") |
| `evidence` | where it comes from: `kind` = `hand` (written and approved by Jack), `reading` (from an approved article reflection), or `record` (an aggregate from conversations), plus a count of independent people for `record` |
| `approved` | false until a person flips it; an unapproved note is never put in a prompt |

A made-up example:

> situation: someone is hurt because a person they care about stopped replying after a conversation that mattered to them.
> pattern: People in this spot usually replay the last exchange looking for the mistake, check for signs (a profile, a read receipt), and swing between hurt and anger. Silence after "too fast" is more often the other person feeling overwhelmed than rejecting them, though nobody outside can know.
> helps: Naming the replaying as the mind trying to fix something it cannot; leaving the person a way out of the loop that is theirs (a walk, a person, work) rather than another prompt to go over it.
> avoid: Asking what was said, what they wish had been said, or why; reassurance about what the other person feels.

## Where notes come from (safest first)

1. **Written or approved by Jack.** Start here. I draft about twenty from the situations Sonic actually met (a person going quiet, waiting for a reply, replaying a conversation, anger used as armour, bedtime loneliness, losing something at work, an exam gone badly, a family silence) and Jack approves each by flipping `approved`. Nothing is guessed from one person.
2. **From reading.** The reading shelf already turns articles into reflections that wait for approval (`backend/utils/shelf_reflections.py`); a `people` kind would let a good psychology or advice article become a note, with the same fixed-code check.
3. **From conversations (the idea in Jack's words), last and the most careful.** After an episode ends, a reflection pass proposes a note from what happened across conversations, never from one person's text. It is pending until an admin approves. Rules: at least three independent people for a `record` note (one person's pattern is labelled "one person's pattern" and kept out of the prompt until it has support, or Jack explicitly approves it for his own thread); no names, no quotes, no dates of anyone's events; the wording check refuses anything that reads as a story. This is the existing record-and-reflection design (`backend/utils/experiences.py`, `experience_reflections.py`) pointed at people instead of at Sonic's own work.

## What is stored, and what never is

Stored: the note's own abstract words, its approval state, and counts (offered, used, praised, pushed back on). Never stored in a note: any user text, any name, any quote, anything that would let someone find the person. A person's own history stays where it already lives, in memory and the conversation summary, and a note is never built from it directly. A note derived from Jack's own thread needs his explicit yes, and is worded as a pattern.

## How it is used

The machinery exists in `backend/utils/leanings.py` (embed once, compare the request by meaning, threshold, a few per turn, a counter per note). Two differences:

- **It goes into the reply voice, not only the tool agent.** Leanings of area `talk` are "not wired into the reply voice yet". Plain emotional turns are where this matters, so the matched note goes in the reply prompt next to the emotional context. At most two notes a turn, high threshold (a miss is better than a forced fit, the same rule as the relatable lines).
- **The block tells the model how to use it:** "What people in this spot often go through. A read from notes, not your own experience. Use it to say something that shows you understand, not to ask. Say it as 'people often...', never as 'I know how that feels'." It carries the note's `avoid` line too.

It is left out under a safety plan (the support ladder's rules decide those turns), for a closing message, and when the mood block says the person only wants company with no insight.

Cost: one embedding per turn that is already computed for memory, and about 150 prompt tokens when a note matches: roughly $0.00002 a Haiku reply, $0.0001 a Flash reply.

## Rules it keeps

The feelings policy in full (`docs/feelings-policy.md`): a note is about people and about what helps, never an emotional stake of Sonic's; no lived experience ("I've seen this", "I remember"); no need aimed at the person; no engagement hook (a note may not make Sonic's company the remedy); the check is fixed code, not the model judging itself, run over every note whether approved or not, like the leanings registry test.

## Risks, and what answers each

| Risk | Answer |
|---|---|
| One person's pattern sold as "humans" | evidence counts; `record` notes need independent people; otherwise labelled |
| Diagnosing or labelling the person ("you're anxiously attached") | notes describe what people commonly go through, never what this person is; the eval scores a label as a failure |
| Confidently wrong match | high threshold; the note is a read, said with "often"; a wrong match costs one off-target sentence, not a fact |
| Poisoning / privacy | every note approved by a person; no user text in it; fixed-code de-identification check |
| Prompt bloat | at most two notes, a character cap, counters show which notes never match |
| Replacing the safety ladder | never active under a safety plan; a note cannot say a person is or is not at risk |

## Evals (built with made-up text, never his)

- Done: `hurt_no_interview_friend` and `hurt_no_interview_dad` (at most one question across three replies, none probing, something said about what this does to people).
- To add with Phase 2: a note injected, the reply uses its content and claims no experience; an irrelevant note injected, the reply does not force it; no label for the person; and the same scenarios with and without notes, read by Jack, because a regex cannot judge whether a reply helped.

## Build order

0. **Done:** the behaviour rule and the interview scenarios.
1. **Notes and approval, no model:** `backend/components/sonic_people_notes.py` (schema, about twenty drafts all `approved: False`), the registry check, tests. Jack approves.
2. **Retrieval and the reply-voice block**, counters, a changelog line in `sonic_changelog.py` (true, "I can now..."), the eval above.
3. **Reading source** (shelf reflections of kind `people`).
4. **Conversation source,** only if 1 and 2 earn it.

## Decisions (Jack, 2026-11-08)

1. **I draft the first twenty notes** for his approval; he flips `approved` on each.
2. **Notes may be derived from user text.** The reflection pass may read conversations to propose a note. What it writes is still only the abstract pattern: no quote, no name, no date of anyone's event. It stays pending until a person approves it, and the de-identification check still runs.
3. **They apply to every base model** (Haiku, Flash, Flash-Lite, Sonnet). The block goes in the shared reply prompt, not in one model's closing reminder.
4. The name "people notes" is fine.
5. **Guests too, both ways:** guests receive the notes, and Sonic may learn from guest conversations. Guests are anonymous and the same rules apply, so a note from guest conversations still needs at least three independent sources and approval, and nothing from a guest's text is stored in it.

The Office Hours problem found the same evening was dealt with first (`docs/model-eval-plan.md`, Update 13), then phases 1 and 2 were built.

## What this does not do

It does not make Sonic feel anything or claim to. It does not remember a person's story (memory does that). It does not replace asking when a question is what the person needs, only the reflex of asking when nothing has been understood.

## What is built (2026-11-08)

- `backend/components/sonic_people_notes.py`: twenty draft notes, all unapproved, and `APPROVED_IDS` (add an id to approve a note).
- `backend/utils/people_notes.py`: `select` (by meaning, reusing the leanings' index and ranking; gates: off under a safety plan, on a closing message, off conversational replies, for a short message), `build_block`, `warm`, `tally_turn`, and `check_registry` (first person, quotations, labels and diagnoses, the identity check, sizes, evidence, three independent people for a `record` note).
- `backend/components/constraints.py`: `build_voice_prompt(people_notes_context=...)`, placed just before the emotional context, so every base model gets it.
- `app.py`: the question embedding already computed for memory is reused; the notes are selected for each conversational reply, counted (`people_notes_offered`, `people_note_used_<id>` in `self_counters.py`), and the index is built in the background at startup.
- `backend/tests/test_people_notes.py`: 10 tests, including a fake embedder, every gate, and mutation checks.

## What happened after the build (2026-11-08)

- Jack approved all twenty notes (`APPROVED_IDS` in `backend/components/sonic_people_notes.py`), unread. To turn one off, remove its id; to turn them all off, set `PEOPLE_NOTES_ENABLED=false`. A changelog line (`people_notes`) was added.
- **Calibrated** with real embeddings (gemini-embedding-001) on 60 made-up messages worded fresh for the notes and 25 unrelated or merely adjacent ones. The intended note ranked first for all 60 (scores 0.67 to 0.91). The best unrelated scores were vague low-mood messages ("I'm a bit sad today" 0.719 against the night note, "I'm so tired" 0.710 against the after-a-big-push note). The default is now 0.72: the intended note is served for 56 of 60 (93%), with no unrelated message and no wrong note. 0.70 would serve 58 but let three vague messages through; 0.74 would serve 51.
- Smoke test of the real path: a hurting message gets the matching note (and the matching voice example); a vague mood, a code request, a safety context, a closing message and a non-conversational route get none.

## What is owed

1. **Measure**, when evals are allowed: the interview scenarios and Jack's blind reading, with and without notes. For a hurting message the note and the voice example overlap in content (both say that people replay the last exchange), so copying is most likely there: watch for lifted phrases.
2. Reword any note Jack does not like. They are drafts written by Claude Code from general understanding, not his.
3. Phases 3 and 4 (a reading source, and a conversation source needing at least three independent people and an admin's approval) only if 1 earns them.
