# Draft situation facts written by Sonnet (2026-11-13) for review

Status: DRAFTS ONLY. They are in `backend/components/sonic_situation_facts.py` but `APPROVED_IDS` is empty, so none is ever put in a prompt. To approve one, add its id to `APPROVED_IDS` there.

What these are: one-sentence facts about how a process works (interview loops, hiring queues, reviews, admissions), shown to Sonic only when someone who is hurting is facing that situation, so it can say something true instead of guessing. Each passes `check_registry` (one sentence, no statistic, no "often" or "usually", no list). Claude Code edited every fact after Sonnet's draft to remove frequency words and to state the condition each is true under. The `Verify` line is Sonnet's note on what could differ by country, company or field: please read it before approving.

How to review: a fact should be something you would be comfortable having Sonic say to a hurting person as how the thing works, never as a promise about their case. Approve what you are sure of, reword or delete the rest.

## `weak_round_still_invited`

- Situation: They think they did badly in one step of a multi-step interview process and have been invited to the next round, or are afraid they will not be.
- Matched by messages like: "i bombed the first interview but they invited me to round two, why" / "that screening call went terribly and now i'm spiraling" / "got a second round invite even though i stumbled on a question"
- **Fact:** At companies that run several rounds, a weak answer in one round is weighed with the notes from the others unless that round is a stated pass-or-fail gate.
- Verify: Some companies do use a single weak signal as an automatic cut, and the scoring method differs by company and field. The fact is scoped to companies with multiple rounds.

## `live_coding_went_badly`

- Situation: A live coding exercise in a technical interview went badly and they believe the whole hiring process is now over for them.
- Matched by messages like: "froze during the live coding and i'm sure i'm out" / "couldn't finish the coding problem in the interview, is it over" / "my live coding round was a disaster and i feel sick"
- **Fact:** At companies that run several interview rounds, the hiring decision combines the feedback from every round, so the coding exercise is one input and not the whole verdict.
- **Fact:** Interviewers at many companies write down how a candidate reasons and communicates during a live coding exercise, not only whether the solution is finished.
- Verify: Weight given to coding performance varies a lot by company, role and seniority, and some firms do reject on a failed coding round. Confirm the wording on what interviewers record applies broadly enough.

## `waiting_after_interview`

- Situation: Days have passed since an interview and they have heard nothing, and the silence is making them assume the worst.
- Matched by messages like: "it's been a week since my interview and no word, i think i'm rejected" / "why is it so quiet after the interview" / "still waiting to hear back and i'm losing it"
- **Fact:** At companies where several interviewers give feedback on each candidate, the decision waits until all of that feedback is collected, which leaves a candidate without news for days.
- Verify: Some employers do treat no contact as a quiet rejection after a long enough time, so the second fact is scoped to the period before contact. Check that it reads as accurate.

## `months_no_replies`

- Situation: They have sent applications for months and received almost no responses, and they feel invisible or unqualified.
- Matched by messages like: "applied to so many jobs and nobody replies" / "months of applying and just silence, what is wrong with me" / "i keep getting ghosted by every application"
- **Fact:** At employers that receive many applications for one opening, the first screen is done by software or a quick skim, so an application may be filtered out before a person weighs the candidate's ability.
- **Fact:** A posting can close, freeze or be filled before every applicant is answered, so a missing reply is not a review of the candidate's worth.
- Verify: Screening software use varies by employer and country, and small employers often read every application by hand. The first fact is limited to high-volume employers. Posting freezes are real but vary in how they occur.

## `raise_request_unanswered`

- Situation: They asked their manager for a raise or promotion and have not received an answer, and they feel ignored or fear a quiet no.
- Matched by messages like: "asked my boss about a raise two weeks ago and nothing" / "put in for a promotion and heard no answer, am i being brushed off" / "my manager hasn't replied about my raise request"
- **Fact:** At organizations where a raise or promotion needs approval above the manager or a budget cycle, a reply waits on steps the employee cannot see.
- Verify: Approval structure differs by company size and sector, and some managers decide on their own. The fact is scoped to organizations where approval above the manager is required.

## `passed_over_for_junior`

- Situation: A promotion went to someone with less experience, and they feel overlooked and doubt their standing.
- Matched by messages like: "they promoted someone more junior than me and i'm gutted" / "got passed over and the promotion went to the new guy" / "how did a junior get the role i wanted"
- **Fact:** At organizations that fill a specific open role, the decision compares candidates against what that role requires, not against length of service.
- Verify: Some organizations do promote mainly by tenure, especially in unionized or public settings, and criteria differ by field. The fact is scoped with the word many.

## `rejected_from_program`

- Situation: Rejected from a program, school or scholarship that admitted many other applicants, and reading the rejection as a verdict on their worth or ability.
- Matched by messages like: "everyone else got into the program except me" / "i got rejected and idk what's wrong with me" / "they took so many people and still said no to me"
- **Fact:** Where a program has a fixed number of places, the decision compares applicants against each other and the needs of that group, so a rejection reflects that comparison and not a measurement of a person's worth.
- **Fact:** Where places are capped, applicants who meet every stated criterion are still declined when there are more of them than places.
- Verify: Varies by institution: a few programs admit everyone who meets a threshold, so the second fact applies only where places are capped.

## `failed_certification_exam`

- Situation: Failed a certification or licensing exam and is taking it as proof they are not good enough for the profession.
- Matched by messages like: "i failed the exam so i guess im not cut out for this" / "failed my boards today, feel like a fraud" / "i studied for months and still failed, im so dumb"
- **Fact:** A certification exam measures performance on one set of questions on one day, so it samples what a person knows and does not measure their whole competence.
- **Fact:** Many certification bodies let a candidate who did not pass sit the exam again after a set wait.
- Verify: Retake rules differ by body, country and field, including waiting periods and attempt limits, so check the second fact against the specific exam.

## `laid_off`

- Situation: Laid off from a job and wondering whether it means they were a poor performer or did something wrong.
- Matched by messages like: "i got laid off today, what did i do wrong" / "they cut me and kept others, feels so personal" / "laid off after 3 years, i feel worthless"
- **Fact:** A layoff removes positions to cut costs or change structure, and the roles chosen are decided by budget, team structure or business priorities as well as by individual performance.
- Verify: Selection criteria differ by employer and country, and some layoffs do weigh performance, so the first fact is about how layoffs are framed in general, not every case. Check the wording 'often'-free version fits your policy.

## `bad_performance_review`

- Situation: Received a poor performance review and feels it proves they are failing at their job and will be fired.
- Matched by messages like: "my review was terrible, im probably getting fired" / "got a bad review and i cant stop thinking about it" / "my manager rated me low, i feel like a failure"
- **Fact:** A performance review records one manager's assessment over a set period against that company's criteria, so it describes a time window and a standard, not a permanent verdict on ability.
- **Fact:** At companies with a formal improvement process, a low review is followed by stated expectations and a review period before any employment decision.
- Verify: Improvement processes vary widely by company, country and employment law, and at-will employment can skip them, so confirm the second fact fits the audience.

## `heavily_criticised_work`

- Situation: Their code or work received heavy, extensive criticism in a review and they feel it shows they are incompetent.
- Matched by messages like: "my pr got like 60 comments, im so embarrassed" / "they tore my work apart in review" / "so much red ink on my draft, maybe i cant do this"
- **Fact:** A reviewer is asked to list everything they would change, so the number of comments reflects the reviewer's thoroughness and the size of the work as well as its quality.
- **Fact:** Review comments are about the work as it stands at one moment, and the same work may be revised in response.
- Verify: Review culture differs by team and field, and some reviews are gatekeeping and not revisable, so check the second fact against context.

## `client_went_silent`

- Situation: A freelance client stopped replying after receiving a proposal or invoice, and they worry they did something wrong or were rejected.
- Matched by messages like: "client hasnt replied since i sent the invoice" / "sent a proposal a week ago and nothing, they hate it" / "my client ghosted me after the quote"
- **Fact:** At clients with budget approval or accounts-payable steps, a proposal or invoice passes through other people before anyone replies.
- **Fact:** A client who has not answered has not given a reason, so the silence alone does not show whether they disliked the work.
- Verify: Approval chains differ by client size and country; the first fact assumes a client with internal approval steps, and 'usually' should be changed to 'at clients with budget approval or accounts payable' if your rules forbid it.
