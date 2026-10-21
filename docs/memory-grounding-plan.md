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

**Added 2026-10-21 (later):**
- Domain editing on the Memory page: each saved memory has a small select (Work / My own projects / Personal life / General; "Not sorted yet" for the six untagged) and the list has a "part of your life"
  filter. `PUT /api/memory/{id}` accepts `{domain}` alone. A change touches only the label (no re-embedding, no model call, same source and confidence) and sets `domain_locked`, so a model
  re-observing the fact, the compaction pass, and `classify-domains --redo` never move it again. `edit_user_fact(..., text=None, domain=...)`.
- Step 2 (relative dates) built: `backend/utils/date_resolve.py::annotate_dates`, no model. At write time, in the person's own timezone, "tonight / tomorrow / yesterday / last night / this morning" and a weekday
  ("Friday", "on Friday", "this Friday", "last Friday") get the real date right after the words ("tonight (Wednesday 2026-10-21)", "Friday (2026-10-23)"). Anything with no single answer is left as
  written: a weekday said on that weekday, "next Friday", "every Monday", "this weekend", "next week". Idempotent, at most four per note. Hooked in `app_utils.index_conversation_turn` (notes) and
  `memory_save_node` (explicit facts), after the grounding check so the date is never mistaken for an invented detail.
- NOT built from step 2: marking a note's date as past when it passes (the prompt already carries today's date, and the model can now compare it with the date in the note). Open loops keep their own `due_date`
  and are not annotated.
- Next in order: 3 (open loops as events with explicit unknown fields), 6 (Memory page: events view and edit), then 4 (calendar) and 5 (aliases).

## Step 3 BUILT 2026-10-21 (undeployed), decisions taken
Jack's answers: (1) names ARE used in the follow-up; (2) the person's own words win over the calendar and Sonic says the calendar differs; (3) a bare hour for a SOCIAL plan is assumed PM and says so ("7 PM (assumed)");
(4) a "Noted:" line after saving; (5) past events shown greyed for the 7 days kept; (6) no home addresses.
Built: `backend/utils/event_fields.py` (fields, time parsing, grounding, merge, block, reply check, noted line), `open_loops.py` (`save_events`, cancelled, 60 s cached read, detail-cue gate, fields in the
follow-up), the extraction prompt, `app.py` (the block in the reply prompt, the reply check, an SSE `noted` event waited for at most 3 s after the reply), the Chat "Noted" line, and the Coming Up card
(fields with their source, "not known" in italics, past events greyed with a "past" badge). Real-model replay of the Ashlyn conversation: the date, the correction "no the date is at 7 silly" (same event, 7 PM
assumed), a second event, and the cancellation all stored correctly; `character_eval --category events` 20/20 and the replies read right (name and place known, time said to be unknown, asked for).
**Step 6 BUILT (same day): editing events on the Memory page.** `PATCH /api/open-loops/{id}` takes `{time, with, where, due_date, status, confirm}` (`open_loops.edit_event`, `EventEditError` with a
reason for the person). What they write is stored with source `user_edit` and shown as "you wrote this"; an empty box clears the detail back to NOT KNOWN; a typed time must say AM or PM (a bare "7" is
refused: the page is the one place nothing is assumed) or be a 24-hour time, which is what the page's time box sends; the day must be today to 60 days ahead (the expiry moves with it); an address, link,
sensitive value or bare pronoun is refused with the reason. "Cancel it" / "Restore" (cancelled events stay listed, struck through, so they can come back; one Sonic has already asked about cannot be changed),
and "Looks right" sets `confirmed_at` (shown as "checked by you"). Replacing a known value sets `corrected_at`; clearing does not. The cached list is dropped on every edit, so the next message sees it.
**Step 4 BUILT (same day): the calendar read-through** (`backend/utils/event_calendar.py`). Read-only and applied at the moment of the reply; nothing from the calendar is stored. Only for someone who
turned CALENDAR HEADS-UPS on (the same switch, the same hour-cached read, and `eligible_events`' filters: nothing private, declined, sensitive beyond what they allowed, or from a stranger's invite);
in discreet mode only a time is used, never a place. A stored plan is matched to a calendar event on the same day only when it is clearly the same thing (the person or place they named is in the title or
place, with the best match unique; or the plan's own key word is in exactly one calendar title and no other plan of theirs shares it); a doubtful match is none. The calendar can give a TIME (never from an
all-day event) and a PLACE (never a street address: the part before the first comma is tried), not who it is with. It FILLS a gap (shown "(your calendar)", and the block tells the reply to say "your
calendar has..."), SETTLES an unclear "7" or an assumed PM when it agrees, and on a real disagreement the person's value stays and the reply is told both and to mention the difference once (Jack's
decision). Real-model check: 10/10, replies read right ("Your calendar has it down for 7 PM tonight", and for a clash "...7 PM tonight, though your calendar has it down for 6 PM"). The heads-ups settings
card now says the switch also covers this. Microsoft calendars are not read (the heads-ups are Google only).
**Step 5 BUILT (aliases) and the REPLAY EVALS BUILT (2026-10-21).** `backend/utils/name_aliases.py`: two names are variants only when near-identical (same first letter, at least 5 letters each, one edit
apart: added, dropped, changed or swapped letter; a several-word name may differ in one word). Used for: an event's person (so "Ashlynn" is the same event as "Ashlyn", the latest spelling wins and the chat
line says "Updated"), the calendar match ("Dinner with Ashlynn" matches a plan with Ashlyn), and facts, only INSIDE clusters of facts already linked to each other and only for capitalised words in the middle of
a sentence: the reply prompt gets one line saying they are almost certainly one person and which spelling to use (the latest), and the Memory page shows the same under the note with a "They're different
people" button (POST /api/memory/name-aliases; stored as a sorted pair on the person's settings, honoured everywhere, wiped by "forget everything"). Narrow on purpose: Mark/Marc and Sam/Sean never match.
Also fixed while writing the evals: a bare "yes" to Sonic's own question ("So the date is at 7 at Tidos, right?") never reached the extractor, so a confirmed detail could not be saved; the capture gate now lets
a short yes through when something is coming up (a "yes" to a statement still confirms nothing).
Replay evals: `python -m backend.evals.memory_eval --part events --runs 3` (`backend/evals/event_replay.py`): 10 scripted conversations through the real extractor into an in-memory store (no database, the real
counters are not touched), then the real reply model; stored 30/30 and reply 15/15 on 2026-10-21 (time unknown, "no the date is at 7 silly", a bare yep, a confirmed question, "friday" to a real date and not
offered once past, every field reaching the reply, Ashlyn/Ashlynn, a cancelled plan, an address, a sensitive plan). Note: an ordinary dinner is deliberately NOT captured (the extraction prompt skips everyday plans).
Not built: calendar-only events on the Memory page, a link from the chat "Noted" line to the card; the "Noted" line is not kept after a reload.

## Step 3 DRAFT: the event shape (2026-10-21; kept for the reasoning)

### What exists today, and why it was not enough
- A loop is `{id, username, text, due_date, kind, status: open|asked|resolved, created_at, asked_at, expires_at}`. `text` is a name-free phrase of at most 12 words ("has a first date"); the capture prompt
  forbids names and detail on purpose. One lite-model call, only when a cheap future-cue regex hits.
- The reply NEVER sees a loop until its day has passed (then one "how did it go?" block, once). So on the day itself, "date tonight" reached the reply only through a note and a fact, which carry no
  fields and no "unknown". That is the Ashlyn failure: the gap was a missing time that no store had ever marked as missing.
- So step 3 has two halves: a richer stored shape (write side) and a block that puts today's and tomorrow's events in front of the reply with their unknowns spelled out (read side). The read side is new.

### The shape (additive: every old loop stays valid, with all fields unknown)
```
{
  id, username,
  text:     "has a date",              # unchanged: short, name-free; still what "how did it go?" is built from
  kind:     social | work | travel | errand | event,
  due_date: "2026-10-21",              # unchanged name (the local calendar day, from the date resolver)
  fields: {                            # a key is present ONLY when it was said or confirmed; absent = NOT KNOWN
    time:  {"value": "19:00" | null, "said": "7", "ambiguous": false, "source": "user"},
    with:  {"value": "Ashlyn",                    "source": "user"},
    where: {"value": "Tidos",                     "source": "user"}
  },
  status:   open | asked | resolved | cancelled, # "past" is derived (due_date < today), never stored
  corrected_at, confirmed_at,           # set when a later message changed or confirmed a field
  created_at, asked_at, expires_at      # unchanged (gone a day after the week-long window)
}
```
- **Per-field source**, not one source per event: `user` (their own words this turn or earlier), `assistant_confirmed` (Sonic asked "7 at Tidos, right?" and they said yes), `calendar` (step 4), `user_edit` (typed on
  the Memory page). The time can come from the calendar while the person came from chat.
- **Time is stored as they said it.** `said: "7"`. `value` ("19:00") is filled only when it is certain: they gave am/pm or a 24h time, or the words around it settle it ("tonight", "dinner", "after work" with an
  hour of 1 to 11 means PM; "morning" means AM). Otherwise `value: null, ambiguous: true` and the reply says "7, you didn't say AM or PM". Sonic never turns "7" into 19:00 by guessing.
- **Values are grounded.** Each of time, with and where must appear in the person's own words (this turn, an earlier one, or the one yes/no question Sonic asked just before), or it is dropped and the field stays
  unknown. This reuses `memory_grounding` (no new checker); an event with no fields is still a valid event.
- **Guards on the new text.** `with` at most 40 characters and `where` at most 60, no control characters or links, nothing that `is_sensitive` flags in any field (a "hospital" is never stored as a place),
  and a `where` that looks like a street address (a number followed by a street word) is refused: a venue or a neighbourhood is fine, a home address is not stored. No text of any field is ever logged.

### Capture (same single model call, same gate)
- `OPEN_LOOP_EXTRACTION_PROMPT` returns each loop as `{what, due_date, kind, time_said, with, where}`; the three new keys are empty unless the person said them in THIS message or answered a yes/no question about
  one. Names are now allowed in `with` and `where` only (the `what` stays name-free).
- Merge rule: same `due_date` and same `kind`, and `with` equal or either side unknown, is the same event. A later message then FILLS an unknown field, and a different value for a known field is a CORRECTION
  ("lmao no the date is at 7 silly"): the field is replaced, `corrected_at` set, source `user`. Nothing is ever duplicated for a correction.
- "cancelled", "fell through", "not going" on an open event sets `status: cancelled` (never followed up).

### Read side (new): what the reply is told
When an event is today or tomorrow (or the person asks "what do I have today / tonight / coming up / this week"), a block is added after the facts, at most three events, soonest first:
```
COMING UP (only what they told you; a NOT KNOWN field was never said):
- Today, Wednesday 2026-10-21: a date. With: Ashlyn (you said). Where: Tidos (you said). Time: NOT KNOWN.
  If the time comes up, say you do not know it and ask; never offer or imply one.
```
- Wording is built in code from the fields (fixed template, same style as `routines` and `self_report`), so "NOT KNOWN" cannot be paraphrased into a guess. The block also says: do not plan around a missing field; do not
  suggest a schedule that depends on one.
- The follow-up after the day ("how did the date go?") is unchanged in timing and once-only; it may now use the `with` name if one is known (open question below).
- An identity-style reply check, like the shelf's: a reply that states a time for an event whose `time` is NOT KNOWN, or an ambiguous one as definite, is rewritten once.

### Memory page (step 6, built after this)
"Coming Up" shows each event with its fields and where each came from ("You said", "You confirmed", "Your calendar", "You wrote this"); an unknown field reads "Not known" with an Add control, not blank. Edit time,
with, where; Confirm; Cancel; Delete. `PATCH /api/open-loops/{id}` takes `{time, with, where, status}`; an edit sets that field's source to `user_edit`; clearing a field returns it to NOT KNOWN.

### Not in this step
Recurring events (still excluded), calendar enrichment (step 4), alias resolution of "Ashlyn"/"Ashlynn" (step 5: until then `with` is stored as typed, and a correction of spelling is just a correction), an
event for a thing nobody said is coming (we never create one from the assistant's side).

### Evals and tests
The replay cases in "Evals" below, plus unit tests for: merge and correction, am/pm resolution, per-field grounding (an invented time drops only the time), the street-address and sensitive guards, the block's
wording for known, unknown and ambiguous fields, cancelled events, old loops without fields, and a no-logging check.

### Decisions needed from Jack
1. **Names in the follow-up.** May "how did it go?" become "how did the date with Ashlyn go?" once a name is known, or does the follow-up stay name-free as today? (Recommended: allowed; it is their own data and reads less robotic.)
2. **A person correcting vs the calendar.** If the calendar says 6 and they later say "it's at 7", who wins? (Recommended: their own words win and Sonic says the calendar differs; the calendar wins only
   against a time Sonic itself inferred, which it no longer does.)
3. **Ambiguous "7".** Keep it as "7, AM or PM not said" and ask once if it matters (recommended), or assume PM for a social event?
4. **A "Noted:" line in chat** after saving an event ("Noted: date with Ashlyn tonight at Tidos, time not known"). Recommended: yes, one short line, once, because it lets the person correct on the spot and is the
   cheapest way to build trust in what is stored.
5. **Past events on the Memory page.** Recommended: keep showing them greyed for the 7 days they are kept, then they expire (as loops do now).
6. **Home addresses.** Recommended: refuse to store them as a `where` (above). OK?

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
