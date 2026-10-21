"""Sonic's goals, as approved by a person: the one place a goal is defined (backend/utils/goals.py reads it).

A goal here is NOT a wish. It is a control loop, and every part of it is something the server can check:

- a SIGNAL that already exists (counters or per-person counts, never text),
- a BEHAVIOUR it changes (a bounded, expiring lever, or nothing at all while it is only being measured),
- an EFFECT CHECK (does the signal move once the lever is applied),
- a GUARDRAIL (a second number that must not get worse: a lever that improves one number by silencing Sonic is switched off),
- a HUMAN APPROVAL. A lever only acts while an admin has approved this exact revision of the goal; changing a bound here bumps `revision`, which withdraws the approval
  until it is given again. Measuring needs no approval; changing behaviour does.

What Sonic may say about a goal is limited to what the loop does ("I track how often... and I bring things up less when..."): no wanting, hoping or caring, because nothing
here is a state of anyone's. The words below are what every user can be shown and what Sonic says them from, so they pass the identity check like any other self-description.
"""

# A goal with "lever": None is only measured: it has nothing to approve and nothing it can change. A goal with a lever also needs "guardrail", "guardrail_share" and "guardrail_min_events".
GOALS = [
    {
        "id": "welcome",
        "revision": 1,
        "title": "Bring things up only when people pick them up",
        # Said to everyone, and shown on the status page. First person, about what Sonic does.
        "statement": "I keep track of how often the things I bring up on my own get picked up, and I bring things up less often for people who mostly pass on them.",
        "signal": "Of the things Sonic brought up unprompted (an open question at a lull, a reading-shelf item, a callback, a leaning of its own), the share whose next message picked it up.",
        "lever": "The wait before the next unprompted offer is lengthened for a person whose recent offers were mostly passed on: 2x, then 4x the usual 2 hours at most. It never goes silent, "
                 "never makes Sonic pushier, and fades after 21 days without evidence. The person's own 'no offers' switch always wins.",
        "guardrail": "If the lever has held back more than 60% of the offers that were otherwise due this week, it switches itself off until that falls.",
        "target_rate": 0.4,        # picked up at least this often is on track
        "drop_alarm": 0.15,        # a fall of this many points against the week before is drift whatever the level
        "min_sample": 20,          # offers answered this week before any rate is said
        "guardrail_share": 0.6,
        "guardrail_min_events": 20,
        # How the headline number is said and shown.
        "rate_label": "picked up",
        "sample_label": "answered offers",
        "rate_phrase": "of the things I brought up on my own got picked up",
        "people_gated": True,      # a number about other people: only said with enough people behind it
    },
    {
        "id": "coding",
        "revision": 1,
        "title": "Coding work that holds up",
        "statement": "I keep track of how often my coding work gets sent back as not working, how often I run out of steps partway through, and how often I test an edit before offering it. "
                     "For now I only measure these.",
        "signal": "Of the messages that follow my coding work (reading or changing the repo or the connected folder), the share that do not say it failed or did not work. Also: coding turns that "
                  "ran out of steps, and proposed edits that were also tested in the same turn.",
        # MEASURE ONLY: no lever and no guardrail, so there is nothing to approve. A lever is chosen after a few weeks of numbers (docs/goals-plan.md).
        "lever": None,
        "guardrail": None,
        "target_rate": 0.8,        # a guess to be tuned from real numbers: at least this share of follow-ups not saying it failed is on track
        "drop_alarm": 0.1,
        "min_sample": 20,          # follow-up messages this week before any rate is said
        "rate_label": "did not come back as failed",
        "sample_label": "follow-up messages",
        "rate_phrase": "of the messages that followed my coding work did not say it had failed",
        "people_gated": False,     # the counters hold no person, so the sample floor is the only gate; the shares are rounded
    },
]


def goal(goal_id: str):
    return next((g for g in GOALS if g["id"] == goal_id), None)
