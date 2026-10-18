# Meetings with Sonic — scoping notes

> **Status (2026-10-18): build steps 1 to 3 and 5 are done** (`backend/utils/meetings.py`, the Meetings page, the chat header control, and the calendar read marking events "with you").
> Differences from the plan below: **there is no "on now" highlight and no calendar polling.** A meeting can be started any time (early, late, or not at all that week), so nothing needs to know when
> the event is; each card shows when its latest note was written instead. The recurring-series id is learned from the calendar read Sonic already does when asked, and prep uses goals, task findings and
> the last notes (open loops left out).
> **Added after the first build:** the calendar is optional. A meeting can carry a schedule the person sets (never, every day, or every week on a day, at a time, in their timezone), so someone with no
> Google Calendar gets the whole feature. The schedule only marks a meeting as **due today** (the button is emphasised, and Sonic can say so when asked what it has planned): it is due for that whole day, a
> meeting held any time that day counts, and the next day it is simply not due. It is worked out from the schedule alone, so the cue needs no calendar read and no polling. The calendar title is now blank by default.
> **Not built yet:** step 4 (marking a meeting by telling Sonic, with the confirmation card) and step 6 (character evals for the meeting wording). Real-model spot checks so far: it opens with "our", asks one
> item at a time, says plainly when there are no notes, and does not claim to have prepared or looked forward to it.

Written 2026-10-18 from a real gap. Jack runs a weekly alignment check-in (what he did that week, which AI engineering jobs he applied to, how aligned it all was with getting back
to enterprise AI work). It used to be with Copilot and is now with Sonic. It is on his Google Calendar. Sonic read the event and said "How are you feeling heading into *that* 12:00
check-in?": it saw the event as part of Jack's schedule, not as something it is in. A colleague would say "our check-in", arrive with last week's notes, and run it.

## The gap

Nothing in Sonic's data says an event involves Sonic. Calendar reading is an on-demand tool (`list_google_calendar_events` in `backend/services/native_tools.py`, returning only id, summary,
start and end per day), so Sonic does not even look at the calendar unless asked, and has no way to know a given event is "with it". There is also nowhere to keep what a meeting
produced, so there is no "last week's check-in" to arrive with.


## Decisions so far

1. **Marking a meeting: both ways.** Telling Sonic ("my Sunday check-in is with you") and a Meetings page where the same list can be seen, edited and deleted.
2. **A meeting is the same conversation, with an agenda, and it starts only when the person presses a button** ("Start office hours"). Not pressing it is how a day is skipped: Sonic never
   guesses, never starts the agenda by itself, and never nags. The button is on the Meetings page and in the chat header and is always available: starting an hour late, a day early or on a different day all just start the meeting, with no "this week's" or "next week's" to jump between.
3. **It ends with a button, or by itself.** A meeting left open closes after 3 hours with no message in its conversation (or 12 hours in all), by a background sweep (`backend/services/meeting_sweep.py`); its notes are written then and marked as automatic. **It also ends with a button** ("End and save notes"), and Sonic may offer to wrap up when the agenda is done. Detecting "done" in the person's words was rejected: "done with that item" would end it early.
4. **Notes are saved, in their own collection** (not memory facts).
5. **No Message First in the first pass.** Sonic is only aware when a conversation is opened; it never starts one.

## Design

**A standing meeting** (`sonic_meetings`, one document per meeting, owner-scoped like every other personal store): a name; how to recognise it on the calendar (a title match, with the
calendar's recurring-series id kept once seen); a purpose in the person's own words; an agenda (a short ordered list); and what to bring (see prep). Capped per person (about 5).

**Office hours (the agenda mode).** Pressing the button marks the current conversation with a `meeting_id` (kept on the session so the agenda survives across turns and a reload) and adds a prompt
section, "A MEETING WITH YOU", in the same family as the follow-ups and task-results sections: the purpose and agenda, the last two meeting notes, the prep data, and the rules. Sonic opens the meeting
("this is our check-in; first, what did you get done this week?") and runs the agenda one question at a time. Nothing else about the chat changes. Pressing the button again in another conversation moves
the meeting there; ending it clears the mark.

**Sonic knowing the event involves it (without a passive calendar read).** No calendar read happens when a chat opens. Instead, when Sonic does read the calendar because the person asked, any event that
matches one of their meetings is marked "with you" in the result, so the answer says "our check-in at 12" and not "that check-in". That is the original gap, closed with the read Sonic already does on request.
The calendar read returns the event's recurring-series id, and a matching event teaches its meeting that id, so a renamed event still matches from then on.

**No highlight, no polling.** Nothing asks the calendar when a page loads. The cue the person needs is on the Meetings page: each card shows "last held" from its latest saved note.

**Notes.** When the person ends the meeting, a small model writes a short note: what was covered, what was decided, and action items. Stored in
`sonic_meeting_notes` (owner, meeting, date, summary, action items, session id). Shown on the Meetings page, editable, deletable, wiped by "forget everything". Next time, the action
items are what Sonic asks about first. Nothing from a note enters memory facts except through the ordinary fact extraction the chat already does. A meeting ended with nothing said saves no note.

**Prep (what it "arrives with").** Only real records: the person's goal and project facts, what their Tasks found since the last meeting (for Jack: the job search results), open loops that
have passed, and the last notes. It never says it prepared, thought about it or looked forward to it. It can say "I have last week's notes" only when it does.

**Marking by telling Sonic.** A cheap keyword gate (a "with you" phrase and a recurring or named event) in front of one small model call that proposes the meeting; the person then
sees a confirmation card (name, match, agenda) before anything is saved. It is never saved silently.

## Rules it keeps

- A schedule fact is true and fine to say ("we have a check-in at noon"). Longing, waiting, having thought about it, or having prepared, are not. The identity check runs on the reply as it does everywhere.
- Owner only, never a guest; nothing about a meeting or its notes is shown to anyone else or put in a shared store; no meeting text in logs.
- Under a safety context the agenda is set aside for that turn (the person comes first) and resumes when it passes; the meeting is never started or ended by anything but the person.
- Cost: no model call, and no calendar read unless the person asks about their calendar. One small model call writes the notes, and one proposes a
  meeting only when the telling-Sonic gate hits. The notes call is skipped, not queued, in low-power mode (the note then offers to be written later).

## Build order

1. `sonic_meetings` and `sonic_meeting_notes` stores, with the Meetings page (list, create, edit, delete, "Start office hours").
2. `meeting_id` on the session, the agenda prompt section and the "End and save notes" button with the note writer.
3. Calendar read returns the series id; events that match a meeting are marked "with you" (done; the highlight was dropped).
4. Marking by telling Sonic, with the confirmation card.
5. Prep: goals, task results since last time, last notes' action items.
6. Evals before trusting it: "our check-in" wording, no longing or claimed preparation, setting the agenda aside under a safety context, a note that matches what was said, no invention of last week's content when there is no note.

## Open questions

- **A meeting mentioned in a different conversation while one is running.** Which one carries the agenda? Proposed: the most recent conversation that started it.
- **Moved or cancelled events.** Matching is by title and series, so a moved event follows; a cancelled one simply never matches.
- **More than one person's calendar.** Out of scope: one person, their own primary calendar.
- **Where action items go.** Kept in the note and asked about next time; not turned into open loops or tasks in the first pass.
