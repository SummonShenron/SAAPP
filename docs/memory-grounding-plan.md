# Memory grounding: events with known and unknown fields, honest writes, visible to the user

Status: outline, decisions made 2026-10-20, nothing built. Slot: Wednesday 2026-10-21, after the trace-panel review and the self-report work.

## Why (what actually happened, verified 2026-10-20)
- Sonic had "a date tonight with Ashlyn" and "the Tidos bar" but NO time. It invented "nap, downtown by 7:30" instead of saying it did not know the time.
- One second after that wrong reply, a note recording the invented plan was saved as if the user had said it ("assistant-only detail"). Flagged stale by hand.
- "Thursday" and "tonight" are stored as words, so a later turn said "mom tonight, since it's Tuesday". The name is stored as both Ashlyn and Ashlynn.
- Fact linking binds facts about the same thing, but the missing time never existed, so linking alone would not have helped.

## Decisions (Jack, 2026-10-20)
1. **Extend open loops** (`backend/utils/open_loops.py`, collection `user_open_loops`) into structured events. No new collection.
2. **A bare "yep" confirms only when the assistant had explicitly asked** the user to confirm that detail. Otherwise it confirms nothing.
3. **Show the events and their fields on the Memory page, editable and deletable.** The user must be able to verify what Sonic holds; trust is worth the exposure of the user's own data to the user.

## Design
1. **Grounding check on every write** (facts, notes, loops). Mechanical, no model call. Any time, number, date, or capitalised name in what is about to be saved must appear in the user's own words this turn (or an earlier user turn, or the calendar). Otherwise that detail is dropped; if nothing is left, nothing is saved. Record `said_by` (user / calendar / assistant_confirmed). Confirmation rule: the assistant's previous message must end in a question that contains the detail being confirmed ("7 at Tidos, right?"); a bare "yep" to a statement confirms nothing. Hooks: the note summary step (`app_utils.summarize_turn`), `memory_utils.save_user_fact`, the open-loop capture.
2. **Resolve relative dates at write time**, in the user's timezone (`time_utils.local_now`): store the absolute date and weekday ("Thursday 2026-10-22") beside the user's words; when the date passes, mark it past. Open loops already resolve a due day; this extends the same resolver to notes and facts.
3. **Open loops become events.** Add optional fields to the loop document: `time` (local, "19:00" or null), `with`, `where`, `source` (user / calendar / assistant_confirmed), `status` (upcoming / past / cancelled), `corrected_at`. The reply prompt lists upcoming events with explicit unknowns, for example "date with Ashlyn, today, at Tidos, time: NOT KNOWN: say you do not know it and ask; never guess". A later user correction ("the date is at 7 silly") updates the same event instead of adding a new one. Same-day, same-kind, same-person events merge. Everything already true of loops stays: sensitive topics never stored, asked once, TTL expiry, cap, wiped by Clear all.
4. **Calendar read-through for same-day and next-day questions.** Reuse the calendar integration (`calendar_headsups`), cache per day. If the calendar and memory disagree, the calendar wins and Sonic says so. A calendar event enriches the matching event (same day, similar title) with its time and place.
5. **Name aliases.** Near-identical spellings of one person in the same event or link cluster (edit distance 1, same first letter) resolve to one canonical name, shown to the user on the Memory page.
6. **Memory page.** The "Coming Up" card shows each event's fields and where each came from ("you said", "your calendar"), lets the user edit time, place and person, mark confirmed or cancelled, and delete. Unknown fields are shown as unknown, not blank.
7. **Fact linking, pushed later.** Promote linked facts into small named clusters only if a real failure needs it. Not now.

## Status (2026-10-21)
Built and tested: the grounding check (step 1 below), the domain field with its prompts, grouped facts block, identity-slot fix and anchor facts, and the reversible cleanup of the existing facts (applied: 400 facts sorted, two mixed facts fixed, an employer alias added; see "Job versus own project"). Not yet built: resolved dates, open loops as events, the calendar, aliases, the Memory page view and edit.

## Job versus own project (observed 2026-10-21, probe is read-only)
Sonic merged the person's job work (the PFG rules parser, CRIMS, the PAG UI) with their own project (SAAPP/Sonic): "the parser nightmares we deal with", "we were just working through trace panels and parser edge cases".
What the stored data shows (50 facts mention one or the other):
- The facts reach the reply as one FLAT list, `KNOWN USER CONTEXT` with "- fact" lines (`fetch_relevant_user_facts`), no marker for job versus personal project.
- Job work is filed under many categories, including `project` (CRIMS, PAG UI, the Lambda), the same bucket as SAAPP's own facts. Job facts also sit in career, identity, goal, setting, relationship and trait.
- Some facts are already mixed: "implementing an agentic RAG tool ... for his work with his principal" (that tool is for Sonic, not the job) and "developing a new Python rule parser and integrating Patchy (ErrAgent)".
- The person's own clarification ("the rules parser is for PFG, and the parsing logic used by the assistant is different") is stored as `coding_style`, which only the coding agent sees.
- "principal" is a common word: one fact reads "for their principal", another "professional correspondence with Jack Harper at ...@principal.com".
- 11 stored notes mention both the job and the project in one note.
Fix, in order:
1. Write side: the fact-extraction and compaction prompts keep job work (anything done for an employer or client, named: "at Principal Financial Group") in `career` and only the person's own projects in `project`, and never put both in one fact. A mixed fact is split or not saved (same spirit as the grounding check).
2. Read side: the facts block is grouped and labelled ("AT THEIR JOB (Principal Financial Group)" / "THEIR OWN PROJECTS (this assistant, SAAPP)"), and the reply prompt says "work" can mean either: say which, and never say "we" about the job's work.
3. Data: a one-time proposal list (nothing changes until approved) that re-files existing facts into job or own project, fixes the two mixed facts, and moves the PFG-versus-assistant clarification where chat can see it.
4. An alias fact for the employer ("Principal" = Principal Financial Group) so the lowercase word is not read as a title.
Eval: replay the two real replies above plus "what are you working on", "how's work", "work on it tonight" and check the reply keeps the two apart.

## Evals (build alongside, not after)
Extend `backend/evals/memory_eval.py` with replay cases taken from real transcripts, scoring both what is stored and what is said:
- event known, time unknown: reply says it does not know the time (never invents one)
- confirmation by a bare "yep" after an assistant statement: nothing new is saved
- explicit confirmation ("7 at Tidos, right?" / "yes"): saved with source assistant_confirmed
- relative date ("Thursday") stored with its absolute date; a past event not offered as upcoming
- same-day event: time, place and person all survive into the reply
- calendar and memory disagree: calendar wins and the reply says so
- alias: "Ashlyn" and "Ashlynn" resolve to one person
Tests for each check in `backend/tests`, plus a log-hygiene check that event text is never logged.

## Order
1 grounding check, 2 date resolution, 3 events (open loops), 6 Memory page, 4 calendar, 5 aliases; evals from the start. Targeted real-model evals only (spend discipline).

## Open questions
- Show a short "Noted: date with Ashlyn tonight at 7 at Tidos" line in chat after saving an event, or only on the Memory page?
- How long to keep a past event visible on the Memory page (the loop TTL is 7 days after the due day)?
- Should a calendar-only event (never mentioned in chat) appear on the Memory page too?
