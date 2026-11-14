"""Jokes: a few curated lines Sonic may say in one specific, objectively detected situation (the machinery is backend/utils/jokes.py).

WHY (Jack, 2026-11-14). Sonic has a little humor already (voice examples, the light moments), but humor that lands depends on exact words and timing, which a model asked to "be funny" does not give. So a joke here is: a SITUATION that code can detect
without guessing, a few LINES a person wrote and approved, and a rule that says when it may fire. The line is placed in the reply by code, word for word, the way the shape stories are (a model is never asked to produce it, because the Haiku voice's closing reminder
wins over a later instruction). NOTHING IS USED UNTIL IT IS APPROVED: Jack approves a joke in the Sonic Health "Its voice" tab (or by adding its id to APPROVED_IDS at the bottom); every other entry is a draft. Each joke has to land, so this starts with one.

THE FIRST JOKE: "the simulations" (the clone trooper meme: "just like the simulations", said right before it is not). Sonic really has been tested against a sandbox repo, so the line is true: it only turns the testing into the punchline. When a change that was proposed is
a big one (three files or more) the line is the reality check ("this is not like the simulations"); when it is a small one (one file, a few lines) it is the straight meme ("just like the simulations"). It fires only after a change was PROPOSED, so it only ever speaks to a person asking for
a code change, and it works for anyone who knows a model is tested before the real thing, whether or not they know the meme (Jack, 2026-11-14: for everyone).

A JOKE ENTRY. `trigger` is a named situation the code can detect (jokes.TRIGGERS: "big_change", "small_change"); `situation` says it in words for the reviewer; `lines` are the approved words (each one or two short sentences, at most MAX_LINE_CHARS, no feeling claimed, no name,
no em dash). One line is picked at random each time, never the one used last. Nothing in it is from a conversation: written by Claude Code on 2026-11-14; Jack approves.
"""

MAX_LINE_CHARS = 120
MAX_LINES = 5


def _joke(id, trigger, situation, lines):
    return {"id": id, "trigger": trigger, "situation": situation, "lines": lines, "approved": False}


JOKES = [
    _joke(
        "simulations_big_change",
        "big_change",
        "A change touching three or more files was just proposed: harder than the tests it was trained against.",
        [
            "Several files, all of them real. This is not like the simulations.",
            "The simulations never had this many moving parts. Not like the simulations.",
            "Well. They did not simulate this.",
        ],
    ),
    _joke(
        "simulations_small_change",
        "small_change",
        "A small change to one file was just proposed: the size of the tests it was trained against.",
        [
            "One file, a small fix. Just like the simulations.",
            "Small and clean. Just like the simulations.",
            "A fix this size? Just like the simulations.",
        ],
    ),
]

# A joke is approved by adding its id here. 2026-11-14, Jack: both "simulations" jokes, all their lines.
APPROVED_IDS = (
    "simulations_big_change",
    "simulations_small_change",
)

for _entry in JOKES:
    _entry["approved"] = _entry["id"] in APPROVED_IDS
