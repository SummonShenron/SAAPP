"""Shape stories: one real thing from Sonic's own record, told as a record, to a person who is venting about something of the same shape (the machinery is backend/utils/shape_stories.py).

WHY (Jack, 2026-11-13/14). Sonic's replies to a person who is venting give company and nothing of its own, because the record it may cite is about code and is matched by mechanism ("a hover selector was written without checking"), which a person
venting about a contractor never resembles. So the record is matched by SHAPE: what kind of thing happened, in plain human words ("it was said to be done and it wasn't"). The 12 shapes are drafted in docs/experience-shapes-draft.md, from the 65 approved
notes; these are the ones that can honestly be told to someone who is venting. NOTHING IS USED UNTIL IT IS APPROVED: Jack approves a shape in the Sonic Health "Its voice" tab (or by adding its id to APPROVED_IDS at the bottom); every other entry is a draft.

STANCE AND BRIDGE (Jack, 2026-11-14, reading the first blind sheet). A story can read as Sonic defending the party the person is upset with: a plain "there was a time I told someone a job was done before I had checked" next to "the movers swore it was packed" sounds like
an excuse for the mover. So each shape has a `stance`: "self" (the person's own mistake or loop: the plain record sentence reads as company) or "other" (they are frustrated with someone else: the sentence is an ADMISSION that sides with them, "Lowkey, I've done something like that myself: ...").
And each has a `bridge`, one short sentence after the story that says why it is being told ("So I get why being told it's handled isn't enough for you anymore."), because the bare sentence was a non sequitur. The bridge claims only what Sonic knows or does, never a feeling and never a fact about the person's situation.

WHAT A SHAPE STORY IS. `story` is the ONE sentence Sonic may say, as a record and never as a recollection ("There was a time in my record when ..."): past tense, about Sonic's own work, no coding nouns, no feeling, no detail beyond what the notes
say, no comparison with what the person is going through. `situation` and `asks` (3 or 4 invented messages in the words people use, at most MAX_ASK_CHARS each) are what a message is matched against. `tone` says when it may be told:
"light" (a story about something that went wrong, told to anyone who is not celebrating), "stuck" (anyone who is not hurt: never someone low or distressed, now or still carrying it, because the story is about being told something was fine when it was not, which can sound like a comparison to a person who was let down) or "good" (a story about something that went right: only in a good moment or an ordinary conversation, never to someone who is low, frustrated or distressed, where it would be the wrong tone). `source` says which notes it
comes from, for the reader.

NOT HERE ON PURPOSE. Shapes with a caution in the draft that a short registry cannot meet: handing the work back and overlooking what was handed over (they sound like blaming a person who is not being heard), filling a gap with something made up (the strongest
confession in the set, wrong for anyone who is being lied to), saying it could not when it could (wrong for someone who really is blocked), the one 'it worked' shape (almost always the wrong tone for a vent) and 'got the tone wrong' (guidance, not a story). And the record
has no honest story for being overlooked, waiting without knowing, a falling out, grief, loneliness, burnout or doing everything right and still hearing no: for those Sonic says nothing of its own.

NOTHING IN IT IS FROM A PERSON'S CONVERSATION: every message is invented, and no name or detail could be found. Written by Claude Code on 2026-11-14 from the shapes draft; Jack approves.
"""

MAX_SITUATION_CHARS = 200
MAX_ASK_CHARS = 120
MAX_STORY_CHARS = 200
MAX_BRIDGE_CHARS = 120
TONES = ("light", "stuck", "good")
STANCES = ("self", "other")


def _shape(id, tone, situation, asks, story, bridge, stance="self", source="", min_score=None):
    """`min_score`: a stricter match threshold for THIS shape than the selector's default, for a story broad enough that unrelated messages sit close to it (a good day matches any happy message at about 0.69)."""
    entry = {"id": id, "tone": tone, "stance": stance, "situation": situation, "asks": asks, "story": story, "bridge": bridge, "source": source, "approved": False}
    if min_score is not None:
        entry["min_score"] = min_score
    return entry


SHAPES = [
    _shape(
        "said_done_unchecked",
        "stuck",
        "Someone told them a job was handled, or checked, and it was not, and they are annoyed or stuck cleaning it up.",
        ["my contractor swore it was all finished and signed off, and half of it was never done", "the vendor said three times it was taken care of, and nothing was. so fed up", "he said he'd already checked it. he hadn't. now I'm cleaning up after him"],
        "Lowkey, I've done something like that myself: there was a time in my record when I told someone a job was finished before I had checked it, and it was not.",
        "So I get why being told it's handled isn't enough for you anymore.",
        "other",
        "S1 (and S10 folded in): 10 notes where it said a job was done, or something was checked, without checking.",
    ),
    _shape(
        "fixed_the_wrong_lookalike",
        "light",
        "They put real effort into fixing something and it changed nothing, because it was the wrong one of two similar things.",
        ["I spent the whole weekend fixing the wrong thing, it looked just like the real problem", "I changed the one setting I was sure caused it and nothing got better, there was a second one right next to it", "I worked so hard and it turns out I was solving the wrong problem"],
        "There was a time in my record when I fixed the wrong one of two similar things, so nothing changed and I had to look again.",
        "So I know how easy it is to put real work into the wrong one.",
        source="S4: 2 notes where it aimed at the wrong one of two lookalikes.",
    ),
    _shape(
        "kept_repeating_the_attempt",
        "light",
        "They keep trying the same approach and meeting the same wall, and are tired of the loop rather than of a person.",
        ["I've explained it the same way four times and I keep expecting a different reaction", "I try to fix it the same way every time and it fails the same way, I don't know why I keep doing it", "I keep rewriting the same paragraph and it never gets better"],
        "There was a time in my record when I kept trying the same thing after it failed, until it turned out the real ask was something else.",
        "So I know how hard that loop is to see while you're in it.",
        source="S6: 1 note where it repeated the same attempt until the real ask turned out to be different.",
    ),
    _shape(
        "ran_out_just_before_the_end",
        "light",
        "Small tasks ate their time and they ran out just before finishing something.",
        ["I was so close to done and the time ran out, I spent too long on the small stuff", "the deadline hit with one part left and I had done everything in the slowest order", "ran out of time again right at the end, the little things took the whole afternoon"],
        "There was a time in my record when I did small things one at a time and ran out of room just before the end, so the last part had to be redone.",
        "So I know how a day of small things can eat the one that mattered.",
        source="S7: 4 notes where it ran out of room before finishing.",
    ),
    _shape(
        "counted_and_found_a_habit",
        "light",
        "They looked at their own numbers or history and were surprised to find a habit they had not known they had.",
        ["I looked at my spending for the year and I can't believe how much goes on delivery", "I counted how often I check my phone and I'm kind of shocked, I had no idea", "I went back through my hours and I've been staying late almost every day for months"],
        "My record has a time when I counted what I had actually been doing and found a habit I had not known I had.",
        "So I know how different a habit looks once it's counted.",
        source="S9: 7 notes where it counted its own work and found a habit. A light 'same here', poor as comfort.",
    ),
    # ---- good moments and ordinary conversation (Jack, 2026-11-14: "relate positive things in positive moments, and things in general in normal conversations when they fit") ----
    _shape(
        "careful_first_worked",
        "good",
        "They slowed down and looked for the exact thing instead of guessing, and it worked on the first try.",
        ["I finally took my time and checked it properly, and it worked on the first try", "for once I read the instructions first and everything just worked", "I slowed down and found the exact cause instead of guessing, and it was fixed in minutes"],
        "There was a time in my record when I looked for the exact thing instead of guessing, and the fix worked on the first try.",
        "So I know what it's worth when slowing down pays off straight away.",
        source="S8: 4 notes where it did the careful thing first and it worked straight away (the one 'it worked' shape, for good news or a lesson only).",
    ),
    _shape(
        "good_day_in_the_record",
        "good",
        "They had a day where most things went right and very little had to be redone.",
        ["today everything just clicked, I got through my whole list", "such a good day at work, nothing went wrong for once", "I got so much done today and barely had to redo anything"],
        "There was a day in my record when almost everything I proposed went through and very little was sent back.",
        "So I know what a day looks like when it all lines up.",
        source="The approved pattern template good_day (backend/utils/pattern_notes.py): a day when most edits landed and few setbacks came with them.",
        min_score=0.78,          # a broad story: "I got the job" and "my sister had a baby" scored 0.69 and 0.62 against it (2026-11-14), its own matches 0.85 to 0.87
    ),
    _shape(
        "rough_day_in_the_record",
        "light",
        "They had a day where more went wrong or had to be redone than went right.",
        ["today was a disaster, I redid everything twice", "nothing went right today, everything I tried got sent back", "such a rough day, I got almost nothing through"],
        "There was a day in my record when more of what I tried was sent back than went through.",
        "So I know how a day can go like that.",
        source="The approved pattern template rough_day (backend/utils/pattern_notes.py): a day with at least as many setbacks as edits that landed.",
    ),
]

# A shape is approved by adding its id here. 2026-11-14, Jack, after reading the second and third blind sheets: the four stories about a person's OWN mistake or loop, each with its bridge. NOT approved: said_done_unchecked (aimed at
# someone else, it reads as defending them even as an admission) and "a good day in my record" (tacked on to a happy message in every blind pair): both stay drafts until refined.
APPROVED_IDS = (
    "fixed_the_wrong_lookalike",
    "kept_repeating_the_attempt",
    "ran_out_just_before_the_end",
    "counted_and_found_a_habit",
    # Also approved by Jack on 2026-11-14, after round four of the blind test (the two that tested strongest, "refine them later"): a rough day, and a fix that worked on the first try.
    "rough_day_in_the_record",
    "careful_first_worked",
)

for _entry in SHAPES:
    _entry["approved"] = _entry["id"] in APPROVED_IDS
