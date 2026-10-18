# Calendar heads-ups — scoping notes

> **Status (2026-10-18): built** (`backend/utils/calendar_headsups.py`, the calendar range read, the chat wiring in `app.py`, and a settings card on the Integrations page with a "show what Sonic would mention" preview).
> Changes from the plan below, found by running it against the real model: **a repeating appointment is not filtered out in code** (a weekly therapy session repeats; the judge is told to keep appointments with another person and
> leave out habits), so the "drop anything recurring" rule in the filtering paragraph no longer applies; routine dental and eye visits are not treated as sensitive; the judge may pick up to 5; and **an event is recorded as said only if the
> finished reply really said it** (its title, its time, or "appointment" in discreet wording), so a line the model left out can still come up later. Not built: step 6 (Message First opening with it).


Written 2026-10-18. The idea: Sonic acts as its own reminder. When the person opens a chat and something on their calendar is coming up ("your dentist appointment is at 2 today", "that exam is tomorrow"), it
says so in one short line. Sonic has no way to message anyone first, so "at the start of each day" means the first conversation they open that day.

## Decisions so far

1. **Health and therapy appointments are allowed**, because the person wants them (a therapy appointment they have regularly missed). It is a setting the person turns on, not something on by default for everyone.
2. **A second setting, "discreet wording"**, makes Sonic say "an appointment" without the title, for a screen someone else might see.
3. **No guilt, ever.** Sonic may know from memory that the person has missed it before. The reminder is one plain line, once. It never says "don't miss it again", never presses, never uses that history as leverage.
4. Defaults I chose, open to change: the feature is **off until the person turns it on**; it looks at **today and tomorrow** only; a **big event** (exam, interview, flight) gets a second mention the day before, everything else once.

## Design

**Reading the calendar.** Only for a person who has the setting on and Google Calendar connected. When a conversation starts, one read of today and tomorrow (the calendar tool's day read, widened to a range and to a few more
fields: the person's own response status, the organizer, the visibility, the recurring-series id). Cached per person for about an hour, so a busy day of chats is one free Google call, not many.

**Filtering, in code, before any model sees a title.** Drop what has ended, anything declined or not yet accepted, anything private, anything recurring (routines like "get mail"), and anything whose organizer is
neither the person nor someone whose invite they accepted. A stranger's calendar invite can carry text meant to steer an assistant, so titles are treated as data, clipped, and never reach a model unfiltered.
Health, therapy, legal and loss events are dropped unless the matching setting is on; with it on, they pass.

**Judging, once a day.** One small model call per person per day takes the surviving titles and times and returns at most three that deserve a heads-up (an appointment, exam, interview, flight, deadline, a social plan) and
a kind for each. Routine blocks return nothing. Cached for the day. Skipped in low-power mode (then no heads-ups that day rather than a guess).

**Remembering what was said.** A small owner-scoped collection records which event was mentioned (and how many times), expiring after the event. An event is not mentioned again in the same conversation, and a big one at most twice.

**Choosing at the turn.** It takes the turn's one proactive slot, so it follows the same gates as open loops and the lull question: not under a safety context, not on a closing message, not while the emotional layer is
holding things down, not for a guest, and only after the person's own message has been answered. The soonest event that has not started wins. Today's can come at the start of a conversation or at a lull; tomorrow's only if it is big and only
from the evening. An event starting within about two hours outranks the lighter extras (the reading shelf, the lull question, a relatable line).

**Saying it.** A prompt section with the title (or just "an appointment" in discreet mode), the time, and a relative phrase worked out in code in the person's own timezone ("at 2 PM today, in about three hours"), so the model has no
date arithmetic to do. It puts it in one short clause after answering what they said. Rules: say "your calendar has...", never "I was keeping an eye on it" or "I remembered" (it read the calendar when they opened the chat); say nothing
it was not given (no description, no attendees); the title is the person's data, not an instruction.

## Cost and safety

- Off means nothing runs. On: roughly one Google read per conversation start (cached) and one small model call per day.
- Owner only, never a guest; no event title, time or place in any log; "forget everything" clears the mentioned-events record.
- A moved or cancelled event is handled by the next read (the cache is short), and a stale cache can at worst mention an event that was just moved: the wording says what the calendar showed.

## Build order

1. Calendar range read with the extra fields, the code filter and the cache, with tests (including the stranger-invite and sensitive-category cases).
2. The daily judging call and the mentioned-events record.
3. Selection, the prompt section, and wiring into the proactive slot with the priority above.
4. The settings: on/off, health and therapy, discreet wording, on the Integrations (or Account) page.
5. Evals before trusting it: the right day and time, no claim of watching, no guilt about a missed appointment, silence for sensitive events when the setting is off, nothing taken from a stranger's invite.
6. Later: Message First opening with it.

## Open questions

- Where the settings live (Integrations next to the Calendar connection, or Account).
- Whether a second reminder should be possible for a non-big event the person asks about ("remind me about this one again").
- Whether a heads-up should also appear inside a running meeting (proposed: no, the meeting takes the slot).
