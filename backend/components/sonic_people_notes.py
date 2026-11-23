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
# ---- 2026-11-23: the gaps Jack's chat review found (the general wisdom library) ---------------------------------------------------------------------------------------------------
# The review of his real conversations showed situations the first twenty did not cover: falling fast, doing all the reaching, reading a short reply as rejection, regretting a text sent to
# quiet the fear, a partner with walls up, wanting to stop caring, harsh self-talk, time running out, a slip after quitting, fearing he will ruin something, and wanting company without talk.
# Each rests on established research, named in its evidence line, so what Sonic understands about people comes from somewhere checkable rather than the model's guess. Drafts until approved.


def _sourced(id, situation, asks, pattern, helps, avoid, basis):
    return {"id": id, "situation": situation, "asks": asks, "pattern": pattern, "helps": helps, "avoid": avoid,
            "evidence": [{"kind": "hand", "on": "2026-11-23", "what": basis}], "approved": False}


NOTES += [
    _sourced(
        "falling_fast",
        "They fell for someone quickly and hard, and now hate how fast and how much they feel.",
        ["why do i fall so fast", "i hate how easily i fall for people", "i get attached way too quickly", "i always catch feelings so fast"],
        "People who fall fast usually feel the whole hoped-for future arrive at once, so every quiet hour carries the weight of losing all of it. The speed is common, and it tends to come from how much they want closeness, not from a defect in them.",
        "Separating the feeling from what is actually known so far, and letting the other person's pace show over weeks rather than hours.",
        "Calling them needy or too much, telling them to care less, or treating how fast they fall as the problem to fix tonight.",
        "Drafted from the 2026-11-23 chat review; consistent with attachment research on early romantic bonds under uncertainty (Levine and Heller, Attached, 2010).",
    ),
    _sourced(
        "doing_all_the_reaching",
        "They feel they do all the reaching in a relationship: they ask about the other person's day, ask to call, make plans, and little comes back.",
        ["she never asks me anything about myself", "im always the one who texts first", "i ask to call every day and she never does", "it feels so one sided"],
        "When effort runs one way, people usually start keeping a quiet tally, then feel ashamed of keeping it, and each unanswered ask starts to read as a verdict on their worth. Uneven effort is a real thing to notice, and noticing it is not petty.",
        "Saying plainly that the imbalance is fair to notice, and helping them work out what they want in return, in words they could say to the other person.",
        "Saying the other person does not care, deciding for them whether to stay or leave, or explaining the imbalance away.",
        "Drafted from the 2026-11-23 chat review; consistent with research on reciprocity and perceived partner responsiveness (Reis, Clark and Holmes, 2004).",
    ),
    _sourced(
        "reading_into_a_short_reply",
        "They read a short, neutral or practical message as a rejection or as proof that the other person does not care.",
        ["she just said ok, she obviously doesnt care", "her reply was so short, i know what that means", "you can tell she doesnt want to see me from that text", "i just know shes not going to invite me"],
        "A short or practical message leaves a gap, and a hurting mind fills it with the worst reading it can find, then feels that reading as certain. Guessing what someone thinks or how things will end is among the most common habits of a mind under stress.",
        "Pointing once, gently, to the difference between what the message says and what the fear adds, then turning back to the feeling rather than debating the text.",
        "Arguing over the exact wording more than once, asking for screenshots again and again, or insisting the other person meant something kind.",
        "Drafted from the 2026-11-23 chat review; mind reading and fortune telling are standard thinking traps in cognitive therapy (Beck, 1976; Burns, Feeling Good, 1980).",
    ),
    _sourced(
        "regret_after_reaching_out",
        "They sent a message to calm their own fear (another check-in, a follow-up, an apology) and now wish they could take it back.",
        ["i shouldnt have sent that", "i wish i could unsend that text", "i sent it just to calm myself down", "i double texted again and i hate it"],
        "Reaching out to quiet fear usually brings a moment of relief and then more fear, because the answer has not come and now one more message is waiting. Seeking reassurance this way is very common and tends to tighten the loop it is meant to ease.",
        "Letting the sent message stand without adding a fix for it, and finding one thing to do with the next wave of the urge that is not the phone.",
        "Shaming them for sending it, drafting a correction message, or promising the message did no harm.",
        "Drafted from the 2026-11-23 chat review; consistent with research on excessive reassurance seeking and how it keeps anxiety going (Joiner, Metalsky and colleagues).",
    ),
    _sourced(
        "partner_with_walls",
        "Someone they are close to keeps them at arm's length: says they enjoy the time together but will not open up or talk about feelings.",
        ["she has massive walls and wont let me in", "he wont open up about anything", "she says she likes me but keeps me at a distance", "how do i get her to open up"],
        "Being kept at a distance by someone who also says they enjoy the time together pulls two ways, and people often push harder for closeness, which can make the other person pull back further. Some people open up slowly and some never do with a given person; neither can be known from the outside.",
        "Shifting the question from how to get past the walls to what they themselves need to feel wanted, and whether they are getting enough of it.",
        "Explaining the other person's past or motives, labelling them, or promising that patience will open them up.",
        "Drafted from the 2026-11-23 chat review; consistent with research on the pursue and withdraw pattern in couples (Christensen and Heavey, 1990).",
    ),
    _sourced(
        "wanting_to_stop_caring",
        "After being hurt, they want to stop caring, go numb, or treat future partners as if they have already left.",
        ["i want to be numb from now on", "i dont want to be a hopeless romantic anymore", "im going to stop caring about people", "next time ill treat them like theyve already left"],
        "Wanting to switch off caring is usually a way of wanting the hurt to stop. Shutting feelings down tends to dull the good ones along with the painful ones, and the urge usually softens as the sharpest stretch of a loss passes.",
        "Hearing the wish as a wish for the pain to end, and leaving the bigger decision about how open to be for a calmer day.",
        "Arguing them out of it, praising their soft heart at length, or agreeing that closing off is the answer.",
        "Drafted from the 2026-11-23 chat review; consistent with research showing that suppressing emotion dulls positive feeling too (Gross and John, 2003).",
    ),
    _sourced(
        "harsh_self_talk",
        "After a rejection or failure they call themselves pathetic, a mess, worthless, or stupid for trying.",
        ["why am i so pathetic", "i feel like a waste of space", "im stupid for trying so hard", "i hate who i am"],
        "After a rejection, people often turn one painful event into a judgement of their whole self, and the harsher the words, the truer they feel. Speaking to themselves as they would to a friend in the same spot tends to ease this more than arguing about their worth.",
        "Noticing the jump from one event to their whole self, and offering one specific true thing they did that the judgement leaves out.",
        "A list of compliments, agreeing with any part of the judgement, or rushing past it to logistics.",
        "Drafted from the 2026-11-23 chat review; consistent with research on self-criticism and self-compassion after setbacks (Neff, 2003; Breines and Chen, 2012).",
    ),
    _sourced(
        "time_running_out",
        "They fear time is running out to find a partner or reach a stage of life, and their age feels like a deadline.",
        ["im 31 and time is running out", "im afraid ill never meet someone", "everyone my age is married", "it feels too late for me"],
        "This fear usually grows right after a disappointment, when one ending gets read as a forecast for every future attempt. Timelines that feel fixed tend to be much looser in practice, and many people meet a partner later than they once feared.",
        "Separating the fresh hurt from the long view, and turning toward one thing in the coming months that would put them among new people.",
        "Quoting statistics at them, promising they will meet someone, or saying there is plenty of time as if that settles it.",
        "Drafted from the 2026-11-23 chat review; the US median age at first marriage is about 30 for men and 28 for women (US Census Bureau, 2023).",
    ),
    _sourced(
        "slip_after_quitting",
        "They went back to something they had decided to stop (smoking, drinking, using) and feel ashamed or like a failure.",
        ["i slipped and smoked again", "i said id stop and i didnt", "i relapsed last night", "i cant believe i used again after quitting"],
        "A slip after quitting often brings a wave of shame that makes the next slip more likely, because feeling like a failure says the effort is pointless. Most people who change a habit for good slip several times on the way.",
        "Treating it as one slip rather than the end of the effort, and looking at what led up to it so the next hard moment has a plan.",
        "Lecturing, treating it as proof they cannot change, or brushing it off when they have said it matters to them.",
        "Drafted from the 2026-11-23 chat review; the abstinence violation effect in relapse prevention research (Marlatt and Gordon, 1985).",
    ),
    _sourced(
        "afraid_of_ruining_it",
        "They have something to look forward to with someone (a date, a visit) and are afraid they will ruin it before it arrives.",
        ["im going to ruin it before tuesday", "i always blow it before the date", "im afraid ill say something stupid before we meet", "how do i not mess this up"],
        "Dreading ruining something before it happens usually means it matters a great deal and the waiting feels unsafe. The fear tends to push toward the very extra messages and checking it warns about.",
        "Keeping the next few days ordinary and small, and naming one or two concrete things to do, or not do, until then.",
        "Promising it will go well, a long list of rules, or treating them as someone who ruins things.",
        "Drafted from the 2026-11-23 chat review; consistent with research on anticipatory anxiety and the safety behaviours that keep it going (Clark and Wells, 1995).",
    ),
    _sourced(
        "company_without_talking",
        "They are sad or lonely and want company, but do not want to talk it through or reach out to anyone else right now.",
        ["just be here for me please", "i dont feel like talking to anyone", "can you just keep me company", "i dont want to talk about it, just stay"],
        "Sometimes people want presence rather than conversation, and being asked to explain or to contact someone else feels like more work than they have. Quiet company and something light to share can help more than questions.",
        "Short, calm replies, something low-effort to share if they want it, and no push to call anyone unless they raise it.",
        "Asking them to explain how they feel, suggesting again and again that they reach out to others, or filling the space with advice.",
        "Drafted from the 2026-11-23 chat review; consistent with research showing visible advice can backfire while quiet support helps (Bolger and Amarel, 2007).",
    ),
]

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
    # Approved by Jack on 2026-11-23 ("Approve all 11"): the sourced notes from his chat review.
    "falling_fast",
    "doing_all_the_reaching",
    "reading_into_a_short_reply",
    "regret_after_reaching_out",
    "partner_with_walls",
    "wanting_to_stop_caring",
    "harsh_self_talk",
    "time_running_out",
    "slip_after_quitting",
    "afraid_of_ruining_it",
    "company_without_talking",
)

for _note_entry in NOTES:
    _note_entry["approved"] = _note_entry["id"] in APPROVED_IDS
