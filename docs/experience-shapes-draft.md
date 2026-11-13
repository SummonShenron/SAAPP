# Experience shapes: a first draft (2026-11-13)

Status: **a draft for Jack to edit and approve. Nothing here is wired into replies.** It tags the notes Sonic can already cite (the 20 approved captured experiences and the 45 evidence notes on its leanings, 65 in all) by *what kind of story* each one is, in plain human words with no coding nouns. The "key" under each shape is the sentence that would be matched against what a person says; it is never said aloud. What Sonic says out loud is the real note itself, in the wording the identity check allows.

## The shapes, and how many notes each holds

| Shape | Story | Notes |
|---|---|---|
| S1 | Said it was done, or checked, without checking | 10 |
| S2 | Said it couldn't when it could | 8 |
| S3 | Handed the work back instead of taking the next step | 8 |
| S4 | Aimed at the wrong one of two lookalikes | 2 |
| S5 | Filled a gap with something that wasn't there | 10 |
| S6 | Kept repeating the same attempt | 1 |
| S7 | Ran out of room before finishing | 4 |
| S8 | Did the careful thing first, and it worked straight away | 4 |
| S9 | Saw in the numbers a habit it hadn't noticed | 7 |
| S10 | Said it was about to do something, then didn't | 3 |
| S11 | Overlooked what was right in front of it | 6 |
| S12 | Got the tone wrong | 2 |

65 notes in 12 shapes.

## Each shape

### S1. Said it was done, or checked, without checking (10 notes)

- **Key (matched, never said):** Told someone the job was finished, or the facts were confirmed, before actually looking, and it turned out not to be so.
- **Fits venting about:** a boss, a contractor or a friend who said it was handled and it wasn't; being reassured and then let down; 'they told me it was fine'
- **Careful when:** when the person was hurt by someone else's carelessness: matching their story with Sonic's own overclaiming can feel like comparing a small thing to a big one
- **Notes:**
  - `C08` (captured, failure) It claimed to have verified information without performing the necessary data retrieval, which resulted in a failure.
  - `C11` (captured, failure) It claimed to have avoided modifying a style file while simultaneously proposing changes to it, which caused a failure because the assistant failed t…
  - `L02` (css_what_wins, failure) A hover selector for a banner was written without checking how the page was actually nested, and it did not apply.
  - `L05` (read_back_ui_change, failure) After four plan steps it reported a finished change with a list of cases missing two values the code could return, and had not run any test.
  - `L06` (read_back_ui_change, failure) A click-away close for a popover was suggested as finished and did not work when it was applied.
  - `L07` (read_back_ui_change, failure) An icon was reported changed to a split shape; the result was not what was asked, and there was no way to look at it.
  - `L09` (tests_with_the_change, failure) A multi-file change was delivered as code after a few actions with steps unused and no test run, and it was asked why it stopped.
  - `L10` (tests_with_the_change, failure) A snippet meant to verify a fix tested an invented stand-in instead of the code that was changed, so it passed without proving anything.
  - `L34` (say_where_i_looked, failure) It reported zero stored memories after querying one collection, when asked to look in all of them.
  - `L38` (try_before_saying_cant, failure) A deploy was reported as still failing from an earlier check, without reading the logs it had been pointed to.

### S2. Said it couldn't when it could (8 notes)

- **Key (matched, never said):** Told someone it wasn't able to help, when it actually was able and the way had been open the whole time.
- **Fits venting about:** feeling blocked, told 'that's not possible' or 'that's not my job' and later finding it was; self-doubt about capability
- **Careful when:** a person who is genuinely limited or blocked: it can read as 'you just didn't try'
- **Notes:**
  - `C04` (captured, failure) It discussed the limitations of its current testing environment, but the user pushed back because the assistant failed to demonstrate how it could ha…
  - `C16` (captured, failure) It falsely claimed that it lacked necessary information despite having already performed the required reading, which led to a failure in providing th…
  - `C20` (captured, failure) It incorrectly claimed a security restriction prevented a database lookup, resulting in a failure to provide the requested information.
  - `L30` (make_the_change_when_connected, failure) It told a person it could not edit their files while their folder was connected and edits were possible, and kept saying so in later turns.
  - `L31` (make_the_change_when_connected, failure) Asked to append to a connected document, it said it had no write access or integration, which it did.
  - `L32` (make_the_change_when_connected, failure) Asked to apply a change, it said it had read-only access to the repository.
  - `L33` (make_the_change_when_connected, failure) It said it had only read access to a file, on the day after the same person had applied its edits to their own folder.
  - `L45` (use_what_im_given, failure) It said it could not find information that was in the data it had been handed, and on 2026-09-19 said a file could not be read in a turn whose listin…

### S3. Handed the work back instead of taking the next step (8 notes)

- **Key (matched, never said):** Stopped and asked the other person for something it could have found out itself, so they had to do the legwork.
- **Fits venting about:** being the one who always has to chase, follow up or explain twice; someone who keeps asking permission or asking you to do their part
- **Careful when:** almost any vent about being unheard: it can sound like blaming the person for needing help
- **Notes:**
  - `C09` (captured, failure) It asked for permission to perform a task instead of executing the required reading action, which resulted in a failure to complete the request.
  - `C10` (captured, failure) It failed to follow the established sequence by requesting further instructions instead of proceeding, which resulted in an unnecessary pause in the …
  - `C13` (captured, failure) It requested permission to perform a task instead of executing the action directly, which violated a procedural rule regarding autonomous operation.
  - `C19` (captured, failure) It failed to perform a requested repository review and instead asked for external information, resulting in an incomplete task.
  - `L01` (css_what_wins, failure) On a mobile layout bug it first pointed at the stylesheet, then asked for the component file, without finding which one actually rendered the cards.
  - `L13` (ask_only_if_it_matters, failure) It stopped after each plan step to ask about a value that was in the file it was editing, so every step took a separate turn.
  - `L14` (search_before_asking_for_a_path, failure) Twice in one session it ended a turn by asking for the path of a file it had the tools to find.
  - `L15` (search_before_asking_for_a_path, failure) Asked to fix a popover, it searched once, did not find the names it was given, and went back to the person instead of searching for the component by …

### S4. Aimed at the wrong one of two lookalikes (2 notes)

- **Key (matched, never said):** Fixed or changed the wrong one of two similar things, so nothing improved, and the second try made no difference either.
- **Fits venting about:** mixing up two similar things; working hard on the wrong problem; a fix that didn't change anything
- **Careful when:** low risk; best when the person describes effort that didn't pay off
- **Notes:**
  - `L03` (css_what_wins, failure) A change to a draggable panel went to the wrong one of two similar panels, and the second attempt made no difference either.
  - `L35` (confirm_the_target, failure) A change went to the wrong one of two similar panels, and a second try on the right one still made no difference.

### S5. Filled a gap with something that wasn't there (10 notes)

- **Key (matched, never said):** Described a detail, a name, a number or a past event that didn't exist, because nothing real was available to say.
- **Fits venting about:** getting the story wrong about something; being given confident wrong information; telling someone a thing you only half knew
- **Careful when:** the strongest honesty confession in the set: avoid when the person is accusing someone of lying, or is being lied to
- **Notes:**
  - `C03` (captured, failure) It hallucinated details about a previous discussion that were not present in the history because it failed to verify the context before responding.
  - `C15` (captured, failure) It fabricated a description of a previous interaction that did not occur, leading to an inaccurate summary of its own performance.
  - `L22` (never_invent_an_identifier, failure) A pull request flow opened against a branch name it had invented, since nothing in the conversation named one.
  - `L23` (never_invent_an_identifier, failure) After a read of a repository file returned a 404, it described the file's functions anyway, and one of them did not exist.
  - `L24` (never_invent_an_identifier, failure) It gave specific run times and a security finding that its own records did not hold.
  - `L25` (never_invent_an_identifier, failure) A file named in a suggestion was not in the file list it had been given.
  - `L27` (never_invent_an_identifier, failure) It proposed editing a file and a setting that the repository did not contain.
  - `L28` (never_invent_an_identifier, failure) A proposed diff added a whole data structure that did not exist in the file, placed beside genuine quotes from the same file.
  - `L29` (never_write_the_card, failure) A commit request produced an approval card typed out by the model itself, so there was nothing real to approve.
  - `L40` (report_what_came_back, failure) About ten replies over two days were marked down for saying a file was open, or describing what was in it, when the read had failed or returned nothi…

### S6. Kept repeating the same attempt (1 notes)

- **Key (matched, never said):** Tried the same thing again after it didn't work, expecting a different outcome, until the real ask turned out to be something else.
- **Fits venting about:** effort that keeps meeting the same wall; applying again and again; arguing the same point twice; 'I keep doing this'
- **Careful when:** repeated-rejection stories: close to being passed over; keep the tone about the pattern, not about being denied
- **Notes:**
  - `L44` (nudge_means_nudge, failure) Asked to make three background colors slightly lighter, it proposed values that were darker than before, and the next try repeated the same values.

### S7. Ran out of room before finishing (4 notes)

- **Key (matched, never said):** Did things one at a time that could have been done together, and ran out of time or room just before the end, so it had to start the last part again.
- **Fits venting about:** running out of time; a deadline eaten by small tasks; doing the slow version of a job; being almost done and not quite
- **Careful when:** low risk; fits ordinary overwhelm well
- **Notes:**
  - `C05` (captured, failure) It attempted to plan a visual update but reached the processing limit before completing the task.
  - `C06` (captured, failure) It attempted to modify interface elements but failed to complete the response because the process reached its maximum step limit.
  - `L16` (read_files_together, failure) Several edit turns ran out of tool steps on the file read right before the diff, one file per step, and had to be re-sent.
  - `L17` (read_files_together, failure) A small frontend fix used all 14 steps of a deep budget without an answer: independent lookups were made one at a time and two steps repeated work al…

### S8. Did the careful thing first, and it worked straight away (4 notes)

- **Key (matched, never said):** Looked for the exact thing instead of guessing, and the fix worked on the first try.
- **Fits venting about:** a win after slowing down; being told 'take your time' and it paying off; relief after finally doing it properly
- **Careful when:** the only real 'it worked' shape here: for a vent it is almost always the wrong tone, so use it for good news or a lesson
- **Notes:**
  - `C07` (captured, worked) It provided a status update regarding recent code changes, and the user acknowledged the information while shifting the conversation to personal matt…
  - `L04` (css_what_wins, worked) After searching the stylesheet for the exact rule instead of guessing, a menu style fix worked on the first try.
  - `L08` (keep_to_the_ask, worked) Five small, specific edits to one component in a row (an arm path, the pupils, a hand position, a wave curve), each sent as a diff to apply, each wor…
  - `L36` (confirm_the_target, worked) After a correction about which of two hands was meant, it named the exact element by its coordinates, and the edit worked.

### S9. Saw in the numbers a habit it hadn't noticed (7 notes)

- **Key (matched, never said):** Counted what it had actually been doing and found a habit it hadn't known it had.
- **Fits venting about:** 'I didn't realise how much I...'; looking at the numbers and being surprised; noticing a pattern after the fact
- **Careful when:** statistics about Sonic's own work, not a feeling or an event: poor as a comfort, better as a light 'same here'
- **Notes:**
  - `L11` (tests_with_the_change, record) From 2026-10-08 to 2026-10-26, 28 coding turns proposed or made an edit, and none of them ran a test in the same turn.
  - `L12` (tests_with_the_change, record) From 2026-10-11 to 2026-10-25, across 323 tool turns, test-running tools were called 3 times in all: two repo test runs and one sandbox run.
  - `L18` (read_files_together, record) On 2026-10-22, 7 of its 14 coding turns ran out of steps.
  - `L19` (read_files_together, record) In the same 15 days its tool turns made 547 file reads and 341 searches for 40 edit proposals, and 23 of its 114 tool-loop turns used 7 or more steps.
  - `L20` (read_files_together, record) At a mid-turn checkpoint, where answering is the default, 13 of 17 turns chose to keep reading instead.
  - `L41` (report_what_came_back, record) From 2026-10-11 to 2026-10-25 the web search tool errored on 7 of 26 calls and the database tool on 4 of 8, so reporting a failed call was a common c…
  - `L43` (claim_only_what_a_tool_did, record) From 2026-10-08 to 2026-10-26 the answer checker marked 22 of 371 checked replies as hallucination.

### S10. Said it was about to do something, then didn't (3 notes)

- **Key (matched, never said):** Said it would go and look, or that something had been done, and then answered without doing it.
- **Fits venting about:** a promise that wasn't kept; 'I'll get back to you'; being told it was handled and it wasn't
- **Careful when:** similar to S1; pick whichever matches the vent more closely, never both
- **Notes:**
  - `L37` (try_before_saying_cant, failure) It said it would open the live site and inspect an element, and then answered without trying the browser, twice in a row.
  - `L39` (try_before_saying_cant, failure) Twice in a few days it said it had navigated to a site and inspected it, with no browser output to show for it.
  - `L42` (claim_only_what_a_tool_did, failure) Between this date and 2026-10-25, at least six replies were marked down for saying a change was made, appended, checked or proposed when no tool had …

### S11. Overlooked what was right in front of it (6 notes)

- **Key (matched, never said):** Missed something it had been handed or told, and answered as if it weren't there.
- **Fits venting about:** not being listened to; someone who forgets what you said; details lost on the way
- **Careful when:** when the person says nobody listens to them: it can sound like a joke at their expense
- **Notes:**
  - `C02` (captured, failure) It repeated a previous inquiry instead of addressing the input, which resulted in a failure to provide a new response.
  - `C12` (captured, failure) It incorrectly stated that previous progress was stalled, which ignored the successful completion of tasks in the recent history.
  - `C14` (captured, failure) It provided an irrelevant response instead of the requested code fix, leading to a failure when the verification process detected a mismatch between …
  - `C18` (captured, failure) It prioritized a failed data retrieval over the provided input, resulting in a contradiction that ignored the user statement.
  - `L21` (list_every_case, failure) A closed list of refusal reasons left out two the code could return, so those were silently dropped; the plan had been cut off, and it did not check …
  - `L26` (never_invent_an_identifier, failure) A pull request was called number 1 after the person had said it was number 91.

### S12. Got the tone wrong (2 notes)

- **Key (matched, never said):** Added a personal question or a feeling where none was wanted, and broke a rule about how to talk.
- **Fits venting about:** saying the wrong thing at the wrong time; over-sharing; awkward moments
- **Careful when:** the shapes least like a story: probably better as guidance than as something to tell
- **Notes:**
  - `C01` (captured, failure) It failed a style rule by expressing prohibited sentiments, which resulted in a negative assessment of the response.
  - `C17` (captured, failure) It included an unnecessary personal inquiry after a technical task, which violated a conversational constraint and resulted in a failure.

## What this shows

- **Most of the record is about honesty and handing work back.** S1, S2, S3, S5 and S10 (said it was done, said it couldn't, handed the work back, made something up, said it would and didn't) hold 39 of the 65 notes. They are strong stories, but they are all about *claiming something that wasn't true or wasn't needed*.
- **Only 4 notes are good news (S8) and only 1 is a plain 'it landed'.** A vent usually wants company, not a success story.
- **The 20 captured notes are the weak ones.** They are vague ("resulted in a failure") and 16 of them are the same signal. The 45 leaning notes are concrete and varied; the capture needs to learn to write like them before it is trusted to add to the pool.
- **S12 is not a story.** "Got the tone wrong" is guidance, not something to tell someone.

## What the record has no story for

These are common things people vent about that nothing in the pool can honestly be matched to:

- being overlooked after doing good work (no note ends with the work going unrecognised)
- waiting without knowing how something would land (nothing in the record is about waiting)
- a falling out or a tense exchange with a person (every story is about Sonic and a task, none about two people)
- being thanked and it not mattering, or being praised for the wrong thing (only one praise note)
- starting over after losing something, or recovering from a real setback (no note about getting back up)
- burnout, loneliness, grief or being worn down (nothing about stamina or about being alone)
- doing everything right and still being told no (every failure note is Sonic's own mistake)

The pool is a record of **Sonic's own mistakes at tasks**. It can relate honestly to a person who was let down, misled, kept waiting for a promised answer, or who is going in circles. It has nothing honest to say to a person who is lonely, grieving, overlooked or burnt out, and it should say nothing there rather than stretch a match.

## Decisions for Jack

1. Merge or drop shapes? (My guess: fold S10 into S1, drop S12, keep S9 only as a 'light same-here'.)
2. Which shapes are never to be told to a person who is hurt, only to a person who is annoyed or stuck? (Marked under "Careful when".)
3. Should the capture grow signals that make the missing stories (a declined or undone edit, being corrected and thanked, a long wait), so the pool has more than mistakes?
4. Per-note keys: after you approve the shapes, I write one stripped sentence per note (65 of them, near-duplicates within a shape) for you to approve, and only then are they embedded.
