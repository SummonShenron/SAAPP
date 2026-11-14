# Pattern stories from Sonic's own counters: five templates, drafted (2026-11-13)

Status: **T1, T2 and T4 BUILT 2026-11-13 (uncommitted); T3 held and T5 dropped, as advised.** Code: `backend/utils/pattern_notes.py`; a global admin approves each template once on Sonic Health, Its record, "Pattern stories" (nothing is written for one that is not approved, and nothing is approved yet); notes are written by a background pass every few hours from FINISHED days, at most WEEKLY_CAP a week (7, Jack, 2026-11-13) and one a day, deletable and never written again once deleted, and are approved `record` notes with signal `pattern` that the existing citation machinery can offer like any other. The wording said aloud, the vent scope and the loosened identity check are NOT part of this build: they are the next layer. Dry run on the real counters (nothing written): 4 rough days, 3 good days, 0 almost-there. The original draft follows. It follows `docs/experience-shapes-draft.md`, which found that the pool of notes Sonic can cite (65) is almost all single mistakes at a task. A *pattern story* is different: code reads Sonic's daily counters (`sonic_counters`, one document a day, counts only) and, when a day matches a template, writes one note from a fixed sentence with the real numbers filled in. No model writes it, so there is nothing for a model to get wrong, and the note says only what the counters say (never a feeling).

How it would work, if approved: a person approves each **template and its thresholds once** on Sonic Health, not each generated note. Each generated note is a `record` note with a date and the counter values it was built from; a day that matches twice adds to the existing note's count instead of making another (the way captured notes already merge), and at most a few a week are made. A person can delete any generated note, and deleting is final.

What the counts below are, and are not: counts of days in the 37 days of counters on record (2026-10-... to 2026-11-12). Only 22 of those days had any coding activity at all, and most of the roughest days are Jack's own test sessions, where he was deliberately pushing on Sonic. They show what the rules would have done on the data we have, not how often it will happen to other people.

**Setbacks**, for templates 1 to 3, are counted as: edits declined + edits undone + the larger of (pushback, frustrated) + step-limit hits + stalled plans. Pushback and frustrated are the larger of the two, not the sum, because one message is often both and counting it twice made the first draft's totals run high. **Landed** is `code_edit_applied`.

## T1. A rough day

- **Fires when:** a day has at least 8 setbacks and at least as many setbacks as landed edits.
- **Note (written by code):** "On {date}, it had {setbacks} setbacks in its coding work (pushback, declined or undone edits, stalled plans, step limits) and {landed} edits that landed."
- **Key (matched, never said):** A day when a lot of what I tried ran into something, and not much got through.
- **Would have fired:** 4 of 37 days: 10-28 (22 setbacks, 22 landed), 10-30 (23, 22), 10-31 (27, 19), 11-08 (18, 7). Only 11-08 clears a stricter bar of 1.5 times as many setbacks as landed edits; at 5 setbacks it would also have fired on 10-22 (7, 0) and 11-05 (5, 3), which look like thin days, not rough ones.
- **Fits venting about:** a day where nothing goes right, a pile-up of small failures, "I can't catch a break".
- **Careful when:** the person's rough patch is a loss or a hurt, not a run of small failures: a day of setbacks at a task is a small thing next to it. Use it for annoyed or stuck, not grieving.
- **Matches shape:** a new one, "a day where setbacks piled up" (none of the 12 draft shapes is about a day).

## T2. A good day

- **Fires when:** a day has at least 5 landed edits and no more than a third as many setbacks.
- **Note:** "On {date}, {landed} of its edits landed, with {setbacks} setbacks."
- **Key:** A day where things mostly went right and each step held.
- **Would have fired:** 3 of 37 days: 10-25 (6 landed, 1 setback), 11-07 (9, 2), 11-10 (9, 1). The same three days at every floor from 4 to 6, so the rule is stable.
- **Fits:** good news, a win, relief; a person who has just had a good day. Almost never a vent.
- **Careful when:** the person is struggling: a success story lands as comparison. Gate it to light or positive moods.
- **Matches shape:** S8 (did the careful thing first, and it worked), at the scale of a day.

## T3. A rough patch that turned

- **Fires when:** a rough day (T1) is followed, within four days, by a good day (T2).
- **Note:** "On {date1} it had {setbacks1} setbacks against {landed1} landed edits; by {date2} it had {landed2} landed edits and {setbacks2} setbacks."
- **Key:** A bad stretch that was followed, within days, by a day when things worked.
- **Would have fired:** once: 11-08 (18 setbacks, 7 landed) then 11-10 (9 landed, 1 setback).
- **Fits venting about:** being in a rough patch and wondering if it will pass.
- **Careful when:** it must never promise the person's patch will turn. It is a record of what happened once, said as such, and one instance is a very thin basis for a story that comforts. Hold this one back until it has fired more than once.
- **Matches shape:** a new one, "a bad patch that turned".

## T4. Almost there

- **Fires when:** a coding turn ran out of steps while its last step was still making progress (`turn_end_ran_out_progress`).
- **Note:** "On {date}, a coding turn ran out of steps while its last step was still making progress, so the work stopped before it was done."
- **Key:** Got almost all the way there and ran out before the last step.
- **Would have fired:** once in the data so far (its counters were added on 2026-11-09, so there is only a few days of history). The rule is sound; the evidence is not there yet.
- **Fits venting about:** being close to finishing and running out of time or room; a deadline that ate the last hour.
- **Careful when:** low risk; fits ordinary overwhelm. Part of S7 already.
- **Matches shape:** S7 (ran out of room before finishing).

## T5. Waited for something that never came back

- **Intended:** a check Sonic asked to run on the person's computer that nobody answered in time, a story about waiting without knowing how something would land.
- **Data:** none. The runner's job store holds 42 finished and 5 cancelled jobs and **no expired ones**, and there is no counter for a wait that timed out. Nothing could fire this.
- **Recommendation: drop it for now.** The gap it was meant to fill (waiting) stays a gap. To build it later, count a wait that ran out (a check turn that ended "not finished", a page look that timed out) so there is something to read, and only then write the template.

## What this fills, and what it doesn't

| Gap named in the shapes draft | Filled by |
|---|---|
| a run of small failures / "can't catch a break" | T1 |
| a win after slowing down; a good stretch | T2 |
| a rough patch, then relief | T3 (one instance) |
| running out of time just before the end | T4 (one instance) |
| waiting without knowing how it will land | nothing (T5 has no data) |
| being overlooked after good work; a falling out between people; burnout, loneliness, grief; doing everything right and still hearing no | nothing, and should stay nothing: Sonic should say nothing rather than stretch a match |

## Decisions for Jack

1. **Approve the templates as drafted?** My advice: build T1 and T2 (stable, from counters that already exist), hold T3 until it has fired at least three times, keep T4 as a cheap add that fires rarely, drop T5 until there is a wait to count.
2. **Thresholds:** T1 at 8 setbacks and at least as many as landed edits is my pick: it fires on the four days that were plainly rough and skips the two thin ones.
3. **Approval:** per template and its thresholds (once), with generated notes appearing automatically and deletable? Or each generated note approved as the captured ones are? The first removes the work that keeps the pool thin; the second keeps your hand on every claim.
4. **How often:** at most how many pattern notes a week? (Suggested: 2, and none on a day that already produced one.)
5. **Wording aloud:** each note is third person ("It had..."). Said to a person it would be your loosened phrasing: "there was a day in my record when...". Say yes or edit the wording.
