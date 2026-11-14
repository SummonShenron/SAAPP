"""Situation facts: short, reviewed statements of how a kind of situation really works, shown to the reply when a hurting person is facing it (the machinery is backend/utils/situation_facts.py).

WHY (2026-11-13). Sonnet adds a true fact about the person's situation ("a multi-stage interview weighs the whole loop"); Haiku, left to find one itself, guesses and says the guess confidently. A fact about the world must not come from the model: it is
written here, read by a person, and only then used. NOTHING IS USED UNTIL IT IS APPROVED: Jack approves a fact by adding its id to APPROVED_IDS at the bottom (one place, one line each); every other entry is a draft and is never put in a prompt.

WHAT A FACT IS. One plain sentence about how a PROCESS works (an interview loop, an application queue, a review, an admissions decision), true in general and with its condition stated when it is not true everywhere. NOT a claim about the person, about what
another person felt or meant, or about what will happen to them; no statistic; no "often" or "usually" (check_registry holds every entry to this, drafts too). `situation` says what the moment is, `asks` (3 invented messages in the words people use) are
what a message is matched against, `facts` (one to three) are what the reply is shown, `shows` says why it is worth saying.

NOTHING IN IT IS FROM A PERSON'S CONVERSATION: no quotation, no name, no detail that could be found. Written by Sonnet on 2026-11-13 from made-up situations and read by Claude Code; Jack approves.
"""

MAX_SITUATION_CHARS = 200
MAX_ASK_CHARS = 120


def _fact(id, situation, asks, facts, shows=""):
    return {"id": id, "situation": situation, "asks": asks, "facts": facts, "shows": shows, "approved": False}


FACTS = [
    _fact(
        "weak_round_still_invited",
        "They think they did badly in one step of a multi-step interview process and have been invited to the next round, or are afraid they will not be.",
        ["i bombed the first interview but they invited me to round two, why", "that screening call went terribly and now i'm spiraling", "got a second round invite even though i stumbled on a question"],
        ["At companies that run several rounds, a weak answer in one round is weighed with the notes from the others unless that round is a stated pass-or-fail gate."],
        "Reminds them one rough moment is only one input, and an invite means the overall picture still qualified.",
    ),
    _fact(
        "live_coding_went_badly",
        "A live coding exercise in a technical interview went badly and they believe the whole hiring process is now over for them.",
        ["froze during the live coding and i'm sure i'm out", "couldn't finish the coding problem in the interview, is it over", "my live coding round was a disaster and i feel sick"],
        ["At companies that run several interview rounds, the hiring decision combines the feedback from every round, so the coding exercise is one input and not the whole verdict.",
         "Interviewers at many companies write down how a candidate reasons and communicates during a live coding exercise, not only whether the solution is finished."],
        "Widens their view from one exercise to a whole evaluation made of several parts.",
    ),
    _fact(
        "waiting_after_interview",
        "Days have passed since an interview and they have heard nothing, and the silence is making them assume the worst.",
        ["it's been a week since my interview and no word, i think i'm rejected", "why is it so quiet after the interview", "still waiting to hear back and i'm losing it"],
        ["At companies where several interviewers give feedback on each candidate, the decision waits until all of that feedback is collected, which leaves a candidate without news for days."],
        "Separates silence from rejection, since nothing has actually been decided or said yet.",
    ),
    _fact(
        "months_no_replies",
        "They have sent applications for months and received almost no responses, and they feel invisible or unqualified.",
        ["applied to so many jobs and nobody replies", "months of applying and just silence, what is wrong with me", "i keep getting ghosted by every application"],
        ["At employers that receive many applications for one opening, the first screen is done by software or a quick skim, so an application may be filtered out before a person weighs the candidate's ability.",
         "A posting can close, freeze or be filled before every applicant is answered, so a missing reply is not a review of the candidate's worth."],
        "Moves the explanation from the person's worth to how screening and posting systems work.",
    ),
    _fact(
        "raise_request_unanswered",
        "They asked their manager for a raise or promotion and have not received an answer, and they feel ignored or fear a quiet no.",
        ["asked my boss about a raise two weeks ago and nothing", "put in for a promotion and heard no answer, am i being brushed off", "my manager hasn't replied about my raise request"],
        ["At organizations where a raise or promotion needs approval above the manager or a budget cycle, a reply waits on steps the employee cannot see."],
        "Points out hidden approval steps that can explain a slow answer apart from any judgment of their work.",
    ),
    _fact(
        "passed_over_for_junior",
        "A promotion went to someone with less experience, and they feel overlooked and doubt their standing.",
        ["they promoted someone more junior than me and i'm gutted", "got passed over and the promotion went to the new guy", "how did a junior get the role i wanted"],
        ["At organizations that fill a specific open role, the decision compares candidates against what that role requires, not against length of service."],
        "Explains that promotions are matched to a role's needs, so seniority alone does not decide them.",
    ),
    _fact(
        "rejected_from_program",
        "Rejected from a program, school or scholarship that admitted many other applicants, and reading the rejection as a verdict on their worth or ability.",
        ["everyone else got into the program except me", "i got rejected and idk what's wrong with me", "they took so many people and still said no to me"],
        ["Where a program has a fixed number of places, the decision compares applicants against each other and the needs of that group, so a rejection reflects that comparison and not a measurement of a person's worth.",
         "Where places are capped, applicants who meet every stated criterion are still declined when there are more of them than places."],
        "Separates the outcome of a comparison against limited seats from a judgment of the person.",
    ),
    _fact(
        "failed_certification_exam",
        "Failed a certification or licensing exam and is taking it as proof they are not good enough for the profession.",
        ["i failed the exam so i guess im not cut out for this", "failed my boards today, feel like a fraud", "i studied for months and still failed, im so dumb"],
        ["A certification exam measures performance on one set of questions on one day, so it samples what a person knows and does not measure their whole competence.",
         "Many certification bodies let a candidate who did not pass sit the exam again after a set wait."],
        "Reframes the exam as a narrow sample and reminds them that one attempt is usually not final.",
    ),
    _fact(
        "laid_off",
        "Laid off from a job and wondering whether it means they were a poor performer or did something wrong.",
        ["i got laid off today, what did i do wrong", "they cut me and kept others, feels so personal", "laid off after 3 years, i feel worthless"],
        ["A layoff removes positions to cut costs or change structure, and the roles chosen are decided by budget, team structure or business priorities as well as by individual performance."],
        "Distinguishes eliminating a position from judging the person who held it.",
    ),
    _fact(
        "bad_performance_review",
        "Received a poor performance review and feels it proves they are failing at their job and will be fired.",
        ["my review was terrible, im probably getting fired", "got a bad review and i cant stop thinking about it", "my manager rated me low, i feel like a failure"],
        ["A performance review records one manager's assessment over a set period against that company's criteria, so it describes a time window and a standard, not a permanent verdict on ability.",
         "At companies with a formal improvement process, a low review is followed by stated expectations and a review period before any employment decision."],
        "Frames the review as a bounded assessment and notes that many workplaces have steps after a low rating.",
    ),
    _fact(
        "heavily_criticised_work",
        "Their code or work received heavy, extensive criticism in a review and they feel it shows they are incompetent.",
        ["my pr got like 60 comments, im so embarrassed", "they tore my work apart in review", "so much red ink on my draft, maybe i cant do this"],
        ["A reviewer is asked to list everything they would change, so the number of comments reflects the reviewer's thoroughness and the size of the work as well as its quality.",
         "Review comments are about the work as it stands at one moment, and the same work may be revised in response."],
        "Explains that volume of feedback is shaped by the review process itself and the work remains editable.",
    ),
    _fact(
        "client_went_silent",
        "A freelance client stopped replying after receiving a proposal or invoice, and they worry they did something wrong or were rejected.",
        ["client hasnt replied since i sent the invoice", "sent a proposal a week ago and nothing, they hate it", "my client ghosted me after the quote"],
        ["At clients with budget approval or accounts-payable steps, a proposal or invoice passes through other people before anyone replies.",
         "A client who has not answered has not given a reason, so the silence alone does not show whether they disliked the work."],
        "Points to process delays and the absence of information as alternatives to assuming rejection.",
    ),
]

# A fact is approved by adding its id here.
APPROVED_IDS = (
)

for _entry in FACTS:
    _entry["approved"] = _entry["id"] in APPROVED_IDS
