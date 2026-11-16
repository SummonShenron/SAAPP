# Shape stories: what the blind tests found (2026-11-14)

A shape story is one real thing from Sonic's own record, said once as a record ("There was a time in my record when ...") followed by a bridge sentence, when what a person says has the same shape. Code: `backend/components/sonic_shapes.py` (the stories, approvals), `backend/utils/shape_stories.py` (gate, matching, placing), `backend/evals/shape_story_eval.py` (the tests below). Every message in every test was invented; nothing is from a real conversation.

## Method
Claude Haiku (the reply voice, built from the app's real prompt for a fresh mood) writes a reply to each invented message. Claude Sonnet judges two replies blind, in BOTH orders (a pair the two orders disagree on is a split, not a win). Spend about $1.20 in all, counted as eval spend.
Caveats: small sets (10 to 18 messages), one author for both the stories' example messages and the test messages (so matching is flattered), the judge is a model and not a person, and the first rubric penalised a story for being about Sonic (fixed from round three). The real arbiter is a person reading the replies.

## Round 1: ask the model (prompt wording)
Told in 0 of 20 replies with the old "only if it genuinely helps" wording and 1 of 20 with "say it unless an exception applies". The Haiku voice ends its prompt with a long reminder (the Claude voice module's "voice" tail) that says to give the person something "never as your own experience", and the last instruction wins. Lesson: do not ask Haiku to do something its closing reminder forbids; if a behaviour must happen, place it in code.

## Round 2: place the bare sentence in code
Told 20 of 20 (`insert_story`, word for word, after the reply's first paragraph). No story won 19 of 20 and the story was called forced in all 20. Part of that was the rubric. Read by a person: accurate but a non sequitur, with no bridge to what the person said. Jack: for a person upset with ANOTHER party, a plain "I told someone it was done before I checked" reads as defending that party; it has to be an admission that sides with them.

## Round 3: a stance and a bridge, with a fairer rubric
Each shape got a `stance` (self or other) and a one-sentence bridge ("So I know how easy it is to put real work into the wrong one."). The bridge beat the bare story 19 of 20. Against no story: wins 5, loses 8, split 7. For shapes about the person's own mistake: about even (5 wins, 6 losses). For the two messages aimed at someone else (movers, a leak): 0 wins, even with the admission wording, because the story is Sonic doing the exact thing the person is angry about.

## Round 4: all eight shapes, story with bridge against no story (36 pairs)
Even overall (14 to 14, 8 split). By story:

| story | story wins / no story wins | forced |
|---|---|---|
| fixed_the_wrong_lookalike | 5 / 0 of 6 | 0 |
| rough_day_in_the_record | 4 / 0 of 4 | 0 |
| careful_first_worked (good moments) | 4 / 0 of 6 | 2 |
| ran_out_just_before_the_end | 1 / 2 of 4 | 3 |
| counted_and_found_a_habit | 0 / 2 of 4 (2 split) | 4 |
| kept_repeating_the_attempt | 0 / 3 of 4 | 4 |
| good_day_in_the_record | 0 / 4 of 4 | 4 |
| said_done_unchecked | 0 / 3 of 4 | 3 |

The pattern: a story works when it MIRRORS the person's exact mistake or day, and fails when it is a loose analogy (the judge: "vague", "only loosely analogous", "tacked on" to a happy message).

## Matching
On 31 invented messages with real embeddings, a threshold of 0.70 forced nothing (right matches 0.68 to 0.77 for the first five shapes, near-misses and no-story messages 0.53 to 0.69), finding 16 of 18 right matches. "I got the job!" scored 0.690 against the broad good-day story, so that shape has its own floor of 0.78 (`min_score`). Real vents will score lower than invented ones, so expect fewer matches, not more.

## Where it stands
Approved (live on the next backend restart): fixed_the_wrong_lookalike, kept_repeating_the_attempt, ran_out_just_before_the_end, counted_and_found_a_habit, rough_day_in_the_record, careful_first_worked. Drafts: said_done_unchecked, good_day_in_the_record. To refine first: the three loose ones that are approved (kept_repeating, ran_out, counted) and good_day.
After real turns exist, read: counters `shape_stories_offered`, `shape_stories_cited`, `shape_story_used_<id>`; and the Sonic Health Feedback page tables by "record" (story used or not) and by "stakes". A shape that is offered but never rated up, or rated down, is the next to rewrite.

## Also measured the same day (read-only, counts)
Record cases were offered 85 times in three weeks and cited 0 times; 31 stamped thumbs ever; an average reply call is about 11,300 input tokens with 0 cached; the stable opening of a reply prompt is about 2,400 tokens, so caching it saves at most about 19% of reply input cost.
