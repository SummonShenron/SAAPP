"""People notes: what people commonly go through in a situation, and what tends to help, written as understanding Sonic can draw on when someone is going through it.

THE IDEA (Jack, 2026-11-08; docs/people-understanding-plan.md). Sonic's follow-up questions to someone who was hurting turned into an interview, because when it did not know what to say it asked. A friend who understands says something
instead: that people in this spot replay the last exchange, that going quiet after something vulnerable is usually the other person being overwhelmed. A note puts that understanding in front of the reply at the moment it matters.

WHAT A NOTE IS. A short entry about PEOPLE, never about Sonic's feelings and never with a person's words in it: no quote, no name, no date of anyone's event. It is said as a read ("people often...", "it is common to..."), never as Sonic's own
experience ("I know how that feels" and "I've seen this" are blocked by backend/utils/identity_checks.py). It describes what people commonly go through, never what this person is: no label, no diagnosis, and nothing stated as fact about what
another person feels or means.

NOTHING IS USED UNTIL IT IS APPROVED. `approved: True` marks a note a person has agreed to, and every other note is a draft that is never put in a prompt (the same rule as the leanings, backend/components/sonic_leanings.py). The twenty below are
drafts written by Claude Code on 2026-11-08 from the situations Sonic actually met; Jack approves a note by adding its id to APPROVED_IDS at the bottom of this file (one place, one line each); he approved all twenty on 2026-11-08. A test holds every note, approved or not, to the checks in backend/utils/people_notes.py check_registry.

FIELDS: `id` (also its counter's name); `situation` (what someone is in, in plain words); `asks` (3 or 4 short example messages in the words people use: a request is scored against the best of them and the situation, which is much sharper than the
situation alone); `pattern` (what people commonly go through); `helps` (what tends to help, concrete); `avoid` (what not to ask or say); `evidence` (where it comes from: `kind` "hand" for a note written by a person or by Claude Code for a person's approval,
"reading" for one from an approved article reflection, "record" for an aggregate from conversations, with the date and one plain sentence); `approved`.
"""

MAX_SITUATION_CHARS = 220
MAX_PATTERN_CHARS = 360
MAX_HELPS_CHARS = 260
MAX_AVOID_CHARS = 240
MAX_ASK_CHARS = 120
MAX_EVIDENCE_CHARS = 200

_DRAFT = [{"kind": "hand", "on": "2026-11-08", "what": "Drafted by Claude Code from situations Sonic met, as general understanding; for Jack to approve."}]


def _note(id, situation, asks, pattern, helps, avoid):
    return {"id": id, "situation": situation, "asks": asks, "pattern": pattern, "helps": helps, "avoid": avoid, "evidence": list(_DRAFT), "approved": False}


NOTES = [
    _note(
        "went_quiet_after_something_vulnerable",
        "Someone they care about has gone quiet after the person said something vulnerable (that they like them, that they are struggling, that they need something).",
        ["she stopped replying after i told her how i feel", "he hasn't texted since i said i liked him", "my friend went silent after i opened up", "ever since i told them i haven't heard anything"],
        "People in this spot usually replay the exchange hunting for the mistake, check for any sign of life, and swing between hurt and anger. Going quiet after something vulnerable is more often the other person feeling overwhelmed or unsure what to say than a verdict on the person who spoke, though nobody outside can know which.",
        "Naming the replaying as the mind trying to fix something it cannot, and leaving them a way out of the loop that is theirs: a walk, someone they trust, work that absorbs them.",
        "Asking what was said, what they wish had been said, or why; stating what the other person feels or means as fact; promising they will reply.",
    ),
    _note(
        "blocked_or_cut_off",
        "They have found signs that someone has blocked or muted them: calls going to voicemail, a profile that vanished, messages not delivered.",
        ["i think she blocked me, i can't see her profile", "my calls go straight to voicemail now", "i can't find him on whatsapp anymore", "those signs mean i'm blocked right"],
        "Being cut off without a word lands as a door closing, and people tend to treat each sign as proof and keep checking for more. Checking again usually feeds the hurt rather than settling it: the signs are most of what there is to know, and the rest is guesswork.",
        "Saying plainly that being cut off without an explanation hurts, and that stopping the checking is a kind thing to do for themselves.",
        "Confirming or denying that they are blocked; guessing at the other person's reasons; asking them to retell what led up to it.",
    ),
    _note(
        "waiting_for_a_reply",
        "They are waiting for a message, call or answer that matters, and the waiting is taking over the day.",
        ["still no reply", "i keep checking my phone", "it's been three days and nothing", "i'm just waiting to hear back"],
        "Waiting for an answer that matters eats attention: people check the phone far more than they mean to, write and rewrite replies they do not send, and read meaning into every hour of delay. The not knowing is often harder than the answer would be.",
        "Something that fills their hands and part of their attention for a set stretch, with the phone out of reach, and a rough time after which they let themselves check again.",
        "Predicting when or whether the reply will come; telling them not to think about it.",
    ),
    _note(
        "replaying_a_conversation",
        "They keep going over something they said or did in a conversation that went badly.",
        ["i keep replaying what i said", "i can't stop going over that conversation", "i wish i could take it back", "i keep thinking about what i should have said"],
        "Replaying a conversation is the mind searching for a version where it went differently. It rarely finds anything new, because everything it has is already in the replay, so it loops and tires the person out without settling anything.",
        "Saying that the loop is the mind trying to fix something thinking cannot fix, and giving the energy one concrete place to go: write down the one thing they would say if they could, then put it away.",
        "Asking them to retell it; judging whether what they said was right or wrong.",
    ),
    _note(
        "anger_after_being_hurt",
        "They are angry at someone who hurt them or went quiet, and call them a coward, cold or cruel.",
        ["she's a coward, i deserve a response", "i'm so angry at him right now", "how could they just ignore me", "i deserve better than this"],
        "Anger after being hurt is common and often arrives first because it feels steadier than not knowing. It tends to come in waves, with the hurt underneath showing up when the anger tires.",
        "Letting the anger be said without agreeing or arguing, and noting that wanting an answer is reasonable even when the other person is not going to give one tonight.",
        "Defending the other person; agreeing that they are a bad person; telling them to calm down; suggesting they send the angry message.",
    ),
    _note(
        "loneliness_at_night",
        "It is late, they are alone, and the quiet is making the loneliness louder: going to bed, an empty apartment, a call that just ended.",
        ["i feel so lonely tonight", "going to bed and it's so quiet", "i'm alone with my thoughts", "the nights are the worst"],
        "Loneliness tends to be loudest at night, when the day's tasks stop and nothing competes for attention, and thoughts that were manageable at noon feel larger. It usually eases a little with morning light and ordinary activity.",
        "Something low-effort that gives the mind a gentle thread to follow (a familiar show, music, a short call), and plain permission to sleep badly tonight and solve nothing.",
        "Asking them to describe how lonely they feel; running through wellness tips (food, water, sleep habits) as a checklist.",
    ),
    _note(
        "same_story_again",
        "They say this always happens: it ends the same way, they always end up alone, nothing ever works out.",
        ["it always ends up like this", "every single time i end up alone", "i thought it would be different this time", "why does this keep happening to me"],
        "After a repeated hurt, people tend to read the newest loss as proof of an old story, so one ending is felt as the whole pattern. Being tired of it happening again is real; whether it is the same story is hard to see from inside the newest hurt.",
        "Acknowledging how tiring it is for it to happen again, and leaving the question of what it means for later, when the hurt is less fresh.",
        "Arguing that it is not a pattern; analysing their past relationships; promising it will be different.",
    ),
    _note(
        "loss_of_something_built",
        "Something they built or maintained at work was taken away, archived or cancelled.",
        ["they archived my repo", "my project got cancelled", "all that work for nothing", "they shut down what i was building"],
        "Losing work people put themselves into tends to hurt out of proportion to the object, because it carried effort, identity and a sense of progress. Anger at the decision and grief for the work often come together, and the practical question of what can be saved usually waits until the first sting passes.",
        "Naming the loss as real before turning to what can be recovered, then looking at the smallest salvageable piece.",
        "Jumping straight to solutions; shrinking it to just a repo or just a project.",
    ),
    _note(
        "waiting_on_a_decision",
        "They finished an interview, application or audition and are waiting to hear back.",
        ["i had the interview and haven't heard back", "waiting to hear about the job", "i think it went well but no word yet", "should i follow up"],
        "The wait after something that mattered is a limbo: people replay the interview, rehearse both outcomes, and find it hard to plan around either. A few days of silence is common and usually reflects the other side's process, not the quality of the performance.",
        "Choosing something else to move forward in the meantime so the whole week is not riding on one answer, and a polite follow-up after a stated window if they want the sense of acting.",
        "Predicting the result; asking them to re-evaluate how it went.",
    ),
    _note(
        "bombed_after_preparing",
        "They prepared hard for an exam, review or presentation and it went badly.",
        ["i bombed my midterm", "i studied all week and it still went badly", "the presentation was a disaster", "i feel like an idiot after that review"],
        "A bad result after real preparation feels like unfair proof of inability. People tend to remember the worst few minutes and forget the rest, and the sting is usually sharpest in the first day.",
        "Separating the result from the effort, naming what they did prepare, and holding off post-mortems for a day.",
        "Telling them it was not that bad; asking them to go through the questions they missed tonight.",
    ),
    _note(
        "family_silence_after_big_news",
        "They told a parent or relative something big (a change of plan, a decision) and the response has been silence or deflection.",
        ["my dad hasn't called since i told him", "my mom is being distant after my news", "he just says he's busy", "my family went quiet after i told them"],
        "A parent's silence after big news is often the parent absorbing it, worried, or unsure how to respond, and being busy is a common way to postpone a hard conversation. To the person waiting it feels like disapproval or withdrawn support.",
        "Saying that the silence hurts because the person's approval matters, and that they can reach out once with something small and warm that asks for nothing.",
        "Telling them what the parent feels; asking them to retell what they said.",
    ),
    _note(
        "repeated_cancellations",
        "A friend or family member keeps cancelling plans or letting them down.",
        ["she cancelled on me again", "that's the third time this year", "i expected it but it still stings", "he never follows through"],
        "Repeated cancellations wear on people differently from a single one: each is small, and the sting is the pattern. Many people stop expecting anything and then feel foolish for still being hurt.",
        "Saying that expecting it and still being hurt can both be true, and that it is fair to decide what they will keep offering.",
        "Explaining away the other person's reasons; telling them to confront them.",
    ),
    _note(
        "feeling_numb",
        "They describe feeling flat, numb or disconnected, without a clear cause.",
        ["i just feel numb lately", "nothing really feels like anything", "i'm just flat", "i don't feel much of anything"],
        "Numbness often shows up after a stretch of too much hurt or stress, as if the volume were turned down to cope. It is common and usually temporary, and can feel stranger than sadness because there is nothing to point at.",
        "Saying that numbness can be what too much feels like from the inside, and keeping the next step small and gentle.",
        "Asking them to name what they feel; urging them to go and do something big.",
    ),
    _note(
        "urge_to_reach_out",
        "They badly want to text or call someone who has pulled away.",
        ["i want to call her again", "i really want to text him", "i can't hold off from reaching out", "should i just send it"],
        "The urge to reach out after being left is strong and comes in waves, with relief only for a moment after sending and often a harder wait afterward. People often send in the worst hour (late, lonely) and wish they had waited for morning.",
        "Writing the message without sending it, and deciding to look at it again after a night's sleep or once the wave has passed.",
        "Telling them what to do; predicting how the other person will respond.",
    ),
    _note(
        "what_did_i_do_wrong",
        "They blame themselves and cannot see what they did wrong.",
        ["i don't understand what i did wrong", "it must be something i said", "it's my fault isn't it", "what's wrong with me"],
        "When someone pulls away without explanation, people tend to turn the question on themselves, because an answer about themselves feels more fixable than not knowing. Often there was no single thing, and the reason belongs to the other person's situation.",
        "Saying that searching for the mistake is the mind trying to regain control, and that not having the answer is the hard part, not a verdict on them.",
        "Listing possible mistakes; asking what they said or did.",
    ),
    _note(
        "after_a_big_push",
        "A hard push or deadline has ended and they feel flat, tired or oddly low.",
        ["we shipped and i feel empty", "i'm drained after that deadline", "now that it's over i feel nothing", "crashed after the release"],
        "After a sustained push a drop in energy and mood is common: the adrenaline and focus that carried the person leave all at once, and the quiet can feel like loss.",
        "Treating the crash as expected, and keeping the next day deliberately light.",
        "Planning the next goal straight away.",
    ),
    _note(
        "comparison_and_falling_behind",
        "They are watching others move ahead (jobs, projects, relationships) and feel left behind.",
        ["everyone else seems to be moving ahead", "i feel so behind", "they got the job and i didn't", "my friends are all doing better than me"],
        "Comparison bites hardest when the person is already low: they set their own worst unseen moments against other people's visible highlights, and the gap feels like a verdict. Feeling behind depends on which timeline is being used.",
        "Naming the comparison as part of the low mood, and anchoring on one thing they actually did this week.",
        "Listing the other person's flaws; telling them not to compare.",
    ),
    _note(
        "dread_of_the_workday",
        "They dread the day's work or a task they keep avoiding.",
        ["i'm dreading today", "i really don't feel like working", "i keep avoiding it", "i'm not looking forward to this at all"],
        "Dread of a task is usually bigger than the task: avoiding it keeps it looming, and starting is the hardest part. A small first step tends to shrink it faster than a plan.",
        "Agreeing the dread is real and naming the smallest possible first step (ten minutes, one file).",
        "Asking how they feel about it; a long productivity plan.",
    ),
    _note(
        "after_saying_something_awkward",
        "They said something awkward or exposed in a social moment and keep cringing over it.",
        ["i said something so weird", "i feel so embarrassed about what i said", "i can't stop cringing", "i made it awkward"],
        "People overestimate how much others noticed or remember, and remember their own awkward moments in sharp detail. The cringe usually fades faster than expected.",
        "Saying that other people rarely hold on to it, and offering a light way to let it go.",
        "Asking them to repeat what they said; insisting it was not awkward.",
    ),
    _note(
        "end_of_a_relationship_first_weeks",
        "A relationship, or a hoped-for one, has ended and they are in the first days or weeks.",
        ["i miss her so much", "it's really over and i can't believe it", "i can't stop thinking about him", "i'm in so much pain over this breakup"],
        "The first weeks after a relationship ends are often the hardest: grief, longing and bargaining arrive in waves, sleep and appetite wobble, and the mind keeps reaching for the person. It rarely moves in a straight line, and a good day can be followed by a hard one.",
        "Letting the waves be expected, and keeping routines small and steady (meals, a walk, a regular bedtime) without making them a task.",
        "A timeline for getting over it; advice about the other person; asking them to dwell on details.",
    ),
]


# A note is approved by adding its id here (Jack approved all twenty on 2026-11-08, unread, to try them: he can remove any id, or reword a note, at any time). Everything not listed stays a draft and is never put in a prompt.
# The changelog line for them is `people_notes` in backend/components/sonic_changelog.py.
APPROVED_IDS = (
    "went_quiet_after_something_vulnerable",
    "blocked_or_cut_off",
    "waiting_for_a_reply",
    "replaying_a_conversation",
    "anger_after_being_hurt",
    "loneliness_at_night",
    "same_story_again",
    "loss_of_something_built",
    "waiting_on_a_decision",
    "bombed_after_preparing",
    "family_silence_after_big_news",
    "repeated_cancellations",
    "feeling_numb",
    "urge_to_reach_out",
    "what_did_i_do_wrong",
    "after_a_big_push",
    "comparison_and_falling_behind",
    "dread_of_the_workday",
    "after_saying_something_awkward",
    "end_of_a_relationship_first_weeks",
)

for _note_entry in NOTES:
    _note_entry["approved"] = _note_entry["id"] in APPROVED_IDS
