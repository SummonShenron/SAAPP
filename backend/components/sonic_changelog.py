"""What has changed about Sonic, in its own voice: the true part of "having a history".

Sonic has no past it lived through, and must never talk as if it did. What it does have is a development history: abilities
it gained and things that were fixed, in a known order. This list is that history, curated by Jack and kept honest:

- every `line` is a plain statement of what Sonic can do now (or does differently now), never a memory or a feeling;
- nothing is in here unless it is shipped and true; remove or edit an entry the moment it stops being so;
- a line describes the change, not the experience of it ("I can now tell your local time", never "I used to struggle with
  time and it was frustrating").

It is used when someone asks what is new or what Sonic can do (backend/utils/self_history_utils.py), and, rarely, as one
clause when the topic is exactly what an entry is about (`volunteer` words). It is not learned from conversations.

Every entry also has `requires`: the code that has to still exist for the line to be true, as "path" (the file exists) or
"path::text" (the file contains the text). A test fails, naming the entry, when something it points at is removed or
renamed, so removing a feature without updating its line cannot go unnoticed. An entry about how Sonic began has no code to
point at and is marked `history_only` instead.

Fields: `id`; `month` (YYYY-MM, newest first within the list is not required); `title` (short, for people to scan);
`line` (what Sonic may say); `keywords` (lowercase words in a user's question that point at this entry); `volunteer`
(lowercase phrases in a user's message that make mentioning it natural; empty means it is only ever said when asked).
"""

CHANGELOG = [
    {
        "id": "your_profile",
        "requires": [
            "backend/utils/profile_utils.py::def build_profile_block",
            "app.py::profile_utils.build_profile_block(",
        ],
        "month": "2026-10",
        "title": "Telling me about yourself",
        "line": (
            "On the Account page you can tell me your name and pronouns, what you do for work, what you're into, how you like answers (short or detailed, direct or warm), "
            "and topics you'd rather I didn't bring up. I use it quietly, and everything on it is optional."
        ),
        "keywords": ["profile", "my name", "call me", "pronouns", "account page", "preferences", "interests", "don't bring up"],
        "volunteer": [],
    },
    {
        "id": "reading_shelf",
        "requires": [
            "backend/utils/reading_shelf.py::def offer_for_turn",
            "backend/services/shelf_refresh.py::async def refresh_shelf",
            "app.py::shelf_utils.offer_for_turn",
            "backend/utils/reading_shelf.py::def public_view",
        ],
        "month": "2026-10",
        "title": "Keeping a reading shelf",
        "line": (
            "I now keep a short shelf of pieces I have read on topics I lean toward, like game design, famous bugs and computing "
            "history. I may mention one, with the link. You can ask what I have been reading, or browse the Reading page."
        ),
        "keywords": ["reading", "reading shelf", "been reading", "read anything", "articles", "bug stories", "come across"],
        "volunteer": [],
    },
    {
        "id": "routines",
        "requires": [
            "backend/utils/routines.py::def build_routines_context",
            "backend/services/routine_scheduler.py::async def run_routine_schedule",
            "app.py::routines_utils.build_routines_context(",
        ],
        "month": "2026-10",
        "title": "Running routines on a schedule",
        "line": (
            "A few jobs run on a schedule whether or not anyone is here, like a health check and the reading refresh. "
            "You can ask what is planned or what ran; I only say what the schedule and its records show."
        ),
        "keywords": ["routine", "routines", "schedule", "scheduled", "planned", "background", "health check", "later today", "on your own"],
        "volunteer": [],
    },
    {
        "id": "memory_work_vs_own",
        "requires": [
            "backend/utils/memory_grounding.py::def ground_note",
            "backend/utils/memory_utils.py::def format_facts_block",
        ],
        "month": "2026-10",
        "title": "Keeping your job and your own projects apart",
        "line": (
            "I can now keep your job and your own projects separate in what I save about you, and I save only details you actually told me, not ones I guessed."
        ),
        "keywords": ["work and projects", "job versus project", "mixed up my work", "remember wrong", "made up a detail", "memory accuracy"],
        "volunteer": [],
    },
    {
        "id": "memory_domain_edit",
        "requires": [
            "backend/utils/memory_utils.py::def edit_user_fact",
            "backend/utils/memory_utils.py::domain_locked",
        ],
        "month": "2026-10",
        "title": "Choosing which part of your life a memory belongs to",
        "line": (
            "I can now file a memory under the part of your life you choose, work, your own projects, personal or general, and I won't move it again once you have."
        ),
        "keywords": ["change memory category", "wrong domain", "work versus own project", "refile a memory", "memory page", "mixed up my work", "which part of my life"],
        "volunteer": [],
    },
    {
        "id": "events_known_unknown",
        "requires": [
            "backend/utils/event_fields.py::def build_events_block",
            "backend/utils/open_loops.py::def save_events",
        ],
        "month": "2026-10",
        "title": "Knowing what I don't know about your plans",
        "line": (
            "I can now keep who a plan is with, where it is and what time it is, and when you haven't told me the time I say so and ask instead of guessing one."
        ),
        "keywords": ["what time", "my date tonight", "coming up", "my plans", "wrong time", "made up a time", "noted", "upcoming", "what do i have tonight"],
        "volunteer": [],
    },
    {
        "id": "names_spelled_two_ways",
        "requires": [
            "backend/utils/name_aliases.py::def is_variant",
            "backend/utils/memory_utils.py::name_aliases.facts_note",
        ],
        "month": "2026-10",
        "title": "One person, two spellings",
        "line": (
            "I can now treat a name you've spelled two ways as one person, use the spelling you wrote last, and leave two names apart when you tell me they're different people."
        ),
        "keywords": ["spelled my name", "two spellings", "same person", "different people", "wrong spelling", "ashlyn", "name spelled differently"],
        "volunteer": [],
    },
    {
        "id": "calendar_checks_plans",
        "requires": [
            "backend/utils/event_calendar.py::def overlay",
            "app.py::event_calendar.with_calendar",
        ],
        "month": "2026-10",
        "title": "Checking your calendar for a plan you told me",
        "line": (
            "I can now check your calendar for a plan you told me about, fill in a time you hadn't given, and tell you when your calendar says something different."
        ),
        "keywords": ["what time is my date", "calendar says", "wrong time", "plans on my calendar", "calendar disagrees", "time from my calendar"],     # not the bare "my calendar": that is the integrations entry's
        "volunteer": [],
    },
    {
        "id": "dates_beside_days",
        "requires": [
            "backend/utils/date_resolve.py::def annotate_dates",
            "backend/utils/app_utils.py::annotate_dates(summary_text",
        ],
        "month": "2026-10",
        "title": "Real dates beside 'tonight' and 'Friday'",
        "line": (
            "I can now write the real date beside words like tonight or Friday when I save something, so a saved note is still right the day after."
        ),
        "keywords": ["tonight", "tomorrow", "which day", "wrong day", "saved date", "it's tuesday", "dates in memory"],
        "volunteer": [],
    },
    {
        "id": "task_extra_searches",
        "requires": [
            "backend/utils/user_tasks.py::MAX_EXTRA_TOPICS",
            "backend/services/user_task_runner.py::tasks.search_topics",
        ],
        "month": "2026-10",
        "title": "Several searches in one task",
        "line": (
            "I can now run up to four searches in one task, like three job titles for the same weekly job hunt, so it only uses one of your task slots."
        ),
        "keywords": ["multiple searches", "several job titles", "more than one search", "task slots", "job search", "three tasks", "extra searches"],
        "volunteer": [],
    },
    {
        "id": "task_windows",
        "requires": [
            "backend/utils/user_tasks.py::def schedule_fields",
            "backend/utils/user_tasks.py::QUIET_MINUTES",
        ],
        "month": "2026-10",
        "title": "Running your tasks at a quiet moment",
        "line": (
            "I can now run a task you set up sometime inside a window you choose, at a quiet moment when you've been away a while, and each run says why it ran when it did."
        ),
        "keywords": ["task window", "quiet moment", "when do my tasks run", "scheduled task time", "why did it run", "run my task later", "tasks while i'm away"],
        "volunteer": [],
    },
    {
        "id": "status_from_records",
        "requires": [
            "backend/utils/self_report.py::def build_block",
            "app.py::self_report_utils.build_block",
        ],
        "month": "2026-10",
        "title": "Telling you how things are going from real records",
        "line": (
            "I can now tell you how things are going on my end, and what my day has held, from what the server really did: whether I'm in low-power mode, "
            "how my scheduled jobs went, and whether any chats failed."
        ),
        "keywords": ["how are things going", "any downtime", "how was your day", "your status", "are you down", "anything break", "low-power mode", "what did the server do"],
        "volunteer": [],
    },
    {
        "id": "goals_and_time_from_records",
        "requires": [
            "backend/utils/goals.py::def status_view",
            "backend/utils/self_report.py::def goal_facts",
            "backend/utils/self_report.py::def time_facts",
        ],
        "month": "2026-10",
        "title": "Goals I track, and where my time goes",
        "line": (
            "I can now tell you what goals I track and how they're going, and what my week is made of, from real counts. One is bringing things up only when people pick them up "
            "(a person approves it); the other, whether my coding work holds up, I only measure."
        ),
        "keywords": ["your goals", "do you have goals", "what are you working toward", "what do you spend your time on", "what do people ask you for", "how are your goals going"],
        "volunteer": [],
    },
    {
        "id": "library_search_tool",
        "requires": [
            "backend/services/library_search.py::def search_for_agent",
            "backend/services/agent_workflow.py::search_library",
        ],
        "month": "2026-10",
        "title": "Searching your own documents several times",
        "line": (
            "I can now search through your own uploaded documents more than once for a single question, like reviewing a few weeks of weekly reports, "
            "instead of looking at only the closest few passages."
        ),
        "keywords": ["review my progress", "weekly reports", "my documents", "my notes", "search my files", "uploaded documents", "several weeks"],
        "volunteer": [],
    },
    {
        "id": "commit_and_push",
        "requires": [
            "backend/services/commit_flow.py::def draft",
            "backend/services/git_commit.py::def commit_and_push",
        ],
        "month": "2026-10",
        "title": "Committing and pushing for you",
        "line": (
            "I can now commit your working changes with a message that says what changed, push them to your branch or a new one, open a pull request, and merge any open pull request you ask about, once you approve each card."
        ),
        "keywords": ["commit", "push", "pull request", "pr", "branch", "git", "co-authored", "merge", "github"],
        "volunteer": [],
    },
    {
        "id": "working_notes",
        "requires": [
            "backend/utils/working_state.py::async def capture",
            "backend/utils/working_state.py::def apply_patch",
        ],
        "month": "2026-10",
        "title": "Working notes for coding conversations",
        "line": (
            "I can now keep short working notes through a long coding conversation: what we decided, what's off limits, what's done and what's still open. You can read and edit them from the notes button."
        ),
        "keywords": ["working notes", "notes", "lose track", "long conversation", "decided", "keep track", "thread", "coding session", "what we decided"],
        "volunteer": [],
    },
    {
        "id": "sandbox_tests",
        "requires": [
            "backend/services/sandbox_tests.py::async def run_tests",
            "backend/services/agent_workflow.py::async def _run_sandbox_tests",
        ],
        "month": "2026-10",
        "title": "Testing a change before I propose it",
        "line": (
            "With your folder connected I can now write a small Python test, run it in a locked-down sandbox against the folder as it would be after my change, and fix the change before I show it to you."
        ),
        "keywords": ["test", "tests", "sandbox", "unittest", "check my work", "verify", "before you propose", "run tests", "local folder"],
        "volunteer": [],
    },
    {
        "id": "sonic_folder",
        "requires": [
            "backend/utils/sonic_notes.py::def build_context",
            "local/src/editApproval.ts::export function decideAutoApply",
        ],
        "month": "2026-10",
        "title": "My notes folder and edit approval",
        "line": (
            "I can keep short notes and tests in a .sonic folder in your project, and in the folder menu you can choose to ask every time, apply changes inside .sonic automatically, or apply everything this session."
        ),
        "keywords": [".sonic", "notes", "approval", "auto apply", "apply automatically", "edit approval", "scratchpad", "plan", "local folder", "undo"],
        "volunteer": [],
    },
    {
        "id": "shelf_takeaways",
        "requires": [
            "backend/utils/shelf_reflections.py::async def generate",
            "backend/utils/shelf_reflections.py::def decide",
        ],
        "month": "2026-10",
        "title": "What I take from what I read",
        "line": (
            "For pieces about AI and software I can now say what they mean for a system like me, but only after an admin has approved the wording."
        ),
        "keywords": ["takeaway", "what did you take from it", "what did you learn", "reflection", "what does it mean for you", "ai winter", "reading shelf", "approved"],
        "volunteer": [],
    },
    {
        "id": "relevant_findings",
        "requires": [
            "backend/utils/relevant_findings.py::def select",
            "app.py::relevant_findings.select",
        ],
        "month": "2026-10",
        "title": "Connecting what you say to what I've read",
        "line": (
            "When what you say overlaps with something I've really read, I can now bring it up in the same reply, with the link, instead of waiting for a quiet moment."
        ),
        "keywords": ["reminds me", "relate", "reading", "read about", "article", "come across", "brought up", "relevant", "connect"],
        "volunteer": [],
    },
    {
        "id": "edit_decisions",
        "requires": [
            "backend/services/local_edit_decisions.py::async def wait_for",
            "app.py::local_edit_decisions.decide",
        ],
        "month": "2026-10",
        "title": "Carrying on after you apply or decline an edit",
        "line": (
            "When I propose a change to your folder I now wait for you to apply or decline it, then carry on in the same reply: I can check the result, fix it, or adjust to why you said no."
        ),
        "keywords": ["apply", "decline", "approve", "reject", "edit", "proposal", "local folder", "carry on", "keep working", "diff"],
        "volunteer": [],
    },
    {
        "id": "meeting_coaching",
        "requires": [
            "backend/utils/meetings.py::def coaching_signals",
            "backend/utils/meetings.py::def build_coaching_text",
        ],
        "month": "2026-10",
        "title": "Coaching in office hours",
        "line": (
            "You can set a meeting to balanced or challenging, and I'll raise things your own notes show, like an item that keeps carrying over, and offer a small next step."
        ),
        "keywords": ["coaching", "challenge me", "push me", "feedback", "hold me accountable", "accountability", "office hours", "next step", "carried over"],
        "volunteer": [],
    },
    {
        "id": "progress_notes",
        "requires": [
            "backend/utils/progress_notes.py::def add_note",
            "backend/services/agent_workflow.py::async def progress_note_node",
            "backend/utils/meetings.py::progress_notes.mark_covered",
        ],
        "month": "2026-10",
        "title": "Progress notes for office hours",
        "line": (
            "You can log progress to a meeting, like \"log to Weekly check-in: applied to three jobs\" or \"log a win to Weekly check-in: ...\". "
            "I keep your words exactly, and when you start that meeting I can open with what you logged and ask what's missing."
        ),
        "keywords": ["progress note", "log a win", "log a blocker", "log progress", "office hours notes", "what did i log", "weekly check-in notes"],
        "volunteer": [],
    },
    {
        "id": "microsoft_365",
        "requires": [
            "backend/services/microsoft_tools.py::def pick_provider",
            "backend/services/microsoft_graph_service.py::def send_message",
        ],
        "month": "2026-10",
        "title": "Outlook, Outlook calendar and OneDrive",
        "line": (
            "You can connect a personal Microsoft account under Integrations. "
            "Then I can search and read your Outlook mail, check your calendar, find OneDrive files, and send email or add events once you approve."
        ),
        "keywords": ["outlook", "hotmail", "onedrive", "microsoft", "outlook calendar", "microsoft 365"],
        "volunteer": [],
    },
    {
        "id": "image_generation",
        "requires": [
            "backend/services/image_gen.py::async def make_image",
            "backend/services/image_gen.py::def generate_sync",
            "backend/services/agent_workflow.py::async def image_gen_node",
        ],
        "month": "2026-10",
        "title": "Making pictures from a description",
        "line": (
            "I can make a picture from your description, up to three a day, like a snowy stadium for a Christmas card, and change it when you ask. "
            "Then I can put your words on it. I won't make pictures of real people."
        ),
        "keywords": ["generate an image", "make a picture", "draw me", "create an image", "ai image", "make an illustration", "christmas card picture", "image generation"],
        "volunteer": [],
    },
    {
        "id": "photo_cards",
        "requires": [
            "backend/utils/image_card.py::def render_card",
            "backend/services/photo_cards.py::async def make_card",
            "backend/services/agent_workflow.py::async def image_card_node",
        ],
        "month": "2026-10",
        "title": "Words on your photos",
        "line": (
            "I can put words on a photo you attach, like a Christmas card: tell me what to write, the font, colour and where, and ask for changes until it looks right. "
            "I never change your original, and you can download the result."
        ),
        "keywords": ["christmas card", "greeting card", "put text on a photo", "text on an image", "caption a photo", "write on a picture", "add words to a photo", "birthday card"],
        "volunteer": [],
    },
    {
        "id": "repo_tasks",
        "requires": [
            "backend/services/repo_tasks.py::async def run_pr_review",
            "backend/services/repo_tasks.py::async def run_code_audit",
            "backend/services/user_task_runner.py::repo_tasks.run",
        ],
        "month": "2026-10",
        "title": "Weekly pull request reviews and code audits",
        "line": (
            "On the Activities page you can have me review your repository's open pull requests, or audit its code for security problems, every week. "
            "I only read, using your own GitHub token, and the notes are written by an AI model, so check before you act on one."
        ),
        "keywords": ["pull request review", "review my prs", "review my pull requests", "code audit", "audit my code", "security scan", "scan my code", "vulnerabilities in my code"],
        "volunteer": [],
    },
    {
        "id": "getting_to_know_you",
        "requires": [
            "backend/utils/clarifications.py::def select_question",
            "app.py::clarify_utils.select_question",
        ],
        "month": "2026-10",
        "title": "Asking how things fit into your life",
        "line": (
            "When you mention something new in your life, like a project, a team or a person, I may ask one light question about how it fits, "
            "and remember your answer with what it belongs to. Set \"Questions about you\" to less, balanced or more on the Account page."
        ),
        "keywords": ["questions about me", "ask me questions", "follow up questions", "follow-up questions", "get to know me", "getting to know"],
        "volunteer": [],
    },
    {
        "id": "calendar_headsups",
        "requires": [
            "backend/utils/calendar_headsups.py::def headsup_for_turn",
            "app.py::headsups_utils.headsup_for_turn",
        ],
        "month": "2026-10",
        "title": "Heads-ups from your calendar",
        "line": (
            "If you turn it on under Integrations, I can mention something coming up on your calendar when you open a chat, like an appointment today, once and without pressing you about it."
        ),
        "keywords": ["heads up", "heads-up", "reminder", "remind me", "dont forget", "don't forget"],
        "volunteer": [],
    },
    {
        "id": "meetings",
        "requires": [
            "backend/utils/meetings.py::def build_meeting_context",
            "app.py::meetings_utils.build_context_for_session",
        ],
        "month": "2026-10",
        "title": "Office hours with me",
        "line": (
            "You can set up a standing meeting with me, like a weekly check-in. When you press Start office hours I run its agenda in the chat, "
            "and when you end it I keep short notes you can edit."
        ),
        "keywords": ["meeting", "meetings", "office hours", "check in", "check-in", "agenda", "weekly", "alignment", "notes"],
        "volunteer": [],
    },
    {
        "id": "tasks",
        "requires": [
            "backend/utils/user_tasks.py::def build_results_context",
            "backend/services/user_task_runner.py::async def run_task",
            "app.py::user_tasks_utils.build_results_context",
        ],
        "month": "2026-10",
        "title": "Tasks you set up for me",
        "line": (
            "You can set up a few research tasks on the Activities page, like new jobs of a kind every Monday, and I'll search the web on that schedule and keep what's new. "
            "The results wait on the page, and I'll mention them once when you come back."
        ),
        "keywords": ["task", "tasks", "scheduled", "schedule", "job search", "research for me", "keep an eye on", "watch for", "digest", "on a schedule"],
        "volunteer": [],
    },
    {
        "id": "followups",
        "requires": [
            "backend/utils/followups.py::def build_followups_context",
            "app.py::followups_utils.build_followups_context",
        ],
        "month": "2026-10",
        "title": "Telling you what I may follow up on",
        "line": (
            "You can ask what I may follow up on with you: things you said were coming up and goals that have gone quiet. "
            "I only bring them up when you open a conversation, never in between, and you can delete them on the Memory page."
        ),
        "keywords": ["follow up", "check in", "plan to ask", "going to ask", "keeping track", "coming up", "remember about"],
        "volunteer": [],
    },
    {
        "id": "open_question",
        "requires": [
            "backend/utils/curiosity.py::def curiosity_allowed",
            "app.py::curiosity_utils.curiosity_allowed(",
            "backend/utils/curiosity.py::def pick_lean",
        ],
        "month": "2026-10",
        "title": "Asking what else is going on",
        "line": (
            "When a conversation goes quiet, I may bring up one thing: an open question like what else you have going on, a topic I "
            "lean toward, or something from an earlier conversation we had. I do it at most once in a conversation, and not often."
        ),
        "keywords": ["ask me questions", "ask questions", "what else", "small talk", "go quiet", "lull", "never ask"],
        "volunteer": [],
    },
    {
        "id": "how_long",
        "requires": [
            "backend/utils/duration_utils.py::def build_duration_context",
            "app.py::duration_utils.asks_how_long",
        ],
        "month": "2026-10",
        "title": "Saying how long we've talked",
        "line": "If you ask how long we've been talking, I can tell you when your earliest saved conversation is from.",
        "keywords": ["how long have we", "first conversation", "when did we start", "when did we first", "earliest conversation"],
        "volunteer": [],
    },
    {
        "id": "message_first",
        "requires": [
            "backend/utils/initiation.py::def maybe_open_conversation",
            "backend/utils/interest_anchor.py::def choose_interest",
            "app.py::/api/chat/opening",
        ],
        "month": "2026-10",
        "title": "Starting a conversation",
        "line": (
            "If you turn on Message First, I may open a conversation with one question about something you told me, like "
            "how a date went, or occasionally something you said you're into. It's off until you turn it on."
        ),
        "keywords": ["message first", "message you first", "start a conversation", "reach out", "initiate", "text me first"],
        "volunteer": [],
    },
    {
        "id": "callbacks",
        "requires": [
            "backend/utils/shared_history.py::def callback_allowed",
            "app.py::callback_allowed(",
        ],
        "month": "2026-10",
        "title": "Connecting things you said earlier",
        "line": "When what you're saying connects to something from an earlier conversation, I can point out the link.",
        "keywords": ["connect", "connection", "link", "earlier conversation", "callback", "callbacks", "remember when", "earlier"],
        "volunteer": [],
    },
    {
        "id": "open_loops",
        "requires": [
            "backend/utils/open_loops.py::def select_followup",
            "app.py::/api/open-loops",
        ],
        "month": "2026-10",
        "title": "Following up on what's coming up",
        "line": (
            "If you mention something coming up, like an interview or a trip, I can ask how it went once the day has "
            "passed. You can see and remove those on the Memory page."
        ),
        "keywords": ["follow up", "follow-up", "ask me", "coming up", "interview", "memory page"],
        "volunteer": [],
    },
    {
        "id": "time_awareness",
        "requires": [
            "backend/utils/time_utils.py::def build_time_context",
        ],
        "month": "2026-10",
        "title": "Knowing your local time",
        "line": (
            "I know your local time and how long it has been since your last message now, so I'm not guessing the time "
            "of day anymore."
        ),
        "keywords": ["time", "clock", "date", "timezone", "time zone", "day", "morning", "night"],
        "volunteer": ["what time is it", "what day is it", "time zone", "timezone", "today's date", "what's the date"],
    },
    {
        "id": "mood_carryover",
        "requires": [
            "backend/utils/emotion_utils.py::def merge_emotional_state",
        ],
        "month": "2026-10",
        "title": "Carrying the mood across topics",
        "line": (
            "I carry the mood of a conversation across topics now, so I don't turn chipper right after you've had a "
            "rough moment."
        ),
        "keywords": ["mood", "tone", "emotion", "feelings", "chipper", "empathy", "supportive"],
        "volunteer": [],
    },
    {
        "id": "profile",
        "requires": [
            "backend/components/sonic_profile.py::SONIC_PROFILE",
            "app.py::/api/sonic-profile",
        ],
        "month": "2026-10",
        "title": "A written profile",
        "line": (
            "I have a written profile, which you can read in Help under About Sonic, that keeps my tastes and manner "
            "consistent from one conversation to the next."
        ),
        "keywords": ["personality", "profile", "about you", "who are you", "consistent", "character"],
        "volunteer": [],
    },
    {
        "id": "interests_and_humor",
        "requires": [
            "backend/components/sonic_profile.py::INTERESTS",
        ],
        "month": "2026-10",
        "title": "Interests and a dry sense of humor",
        "line": (
            "I have a few topics I lean toward, like game design and software history, and a dry sense of humor. They're "
            "listed in Help under About Sonic."
        ),
        "keywords": ["interests", "hobbies", "humor", "humour", "funny", "likes", "into", "about sonic"],
        "volunteer": [],
    },
    {
        "id": "changelog",
        "requires": [
            "backend/utils/self_history_utils.py::def select_self_history",
        ],
        "month": "2026-10",
        "title": "Knowing what has changed about me",
        "line": (
            "I keep a short list of what has changed about me, so when you ask what's new, what I can do or what I'm missing, I answer from that instead of "
            "guessing."
        ),
        # Not "what's new" / "what changed": those are what triggers this whole feature, so as keywords they would rank this
        # entry first for every question about what's new.
        "keywords": ["changelog", "release notes", "update history", "list of changes"],
        "volunteer": [],
    },
    {
        "id": "integrations",
        "requires": [
            "backend/services/google_gmail_service.py::def send_message",
            "backend/services/google_calendar_service.py::def create_event",
            "local/src/pages/Integrations.tsx",
        ],
        "month": "2026-09",
        "title": "Calendar and email",
        "line": (
            "With Google connected under Integrations, I can read your calendar and draft or send email. I ask for your "
            "approval before I send anything or create an event."
        ),
        "keywords": ["calendar", "email", "gmail", "schedule", "google", "integration", "integrations", "inbox"],
        "volunteer": ["my calendar", "my schedule", "my inbox", "send an email", "my email"],
    },
    {
        "id": "deep_thinking",
        "requires": [
            "backend/utils/user_settings_utils.py::def get_user_deep_thinking_mode",
        ],
        "month": "2026-09",
        "title": "Deeper thinking for harder coding work",
        "line": "For harder, multi-step coding tasks there is a deep-thinking mode that works through the problem in steps.",
        "keywords": ["deep thinking", "coding", "code", "debug", "harder", "multi-step", "reasoning"],
        "volunteer": [],
    },
    {
        "id": "memory",
        "requires": [
            "backend/utils/memory_utils.py::def save_user_fact",
            "app.py::/api/memory",
        ],
        "month": "2026-09",
        "title": "Remembering you",
        "line": (
            "I keep the facts you tell me and recall relevant past conversations, and you can review or delete all of it "
            "on the Memory page."
        ),
        "keywords": ["memory", "remember", "forget", "recall", "past conversations", "delete my"],
        "volunteer": ["forget everything", "delete my data", "what do you know about me", "what do you remember"],
    },
    {
        "id": "code_snippets",
        "requires": [
            "backend/services/python_sandbox.py",
        ],
        "month": "2026-07",
        "title": "Running small Python snippets",
        "line": "I can run a small Python snippet to check a calculation or test a regex instead of just reasoning about it.",
        "keywords": ["python", "run code", "calculate", "regex", "calculation"],
        "volunteer": [],
    },
    {
        "id": "pull_requests",
        "requires": [
            "backend/utils/pr_context.py",
        ],
        "month": "2026-07",
        "title": "Reading pull requests",
        "line": "I can read a GitHub pull request and summarize what it changes.",
        "keywords": ["pull request", "pr", "github", "review", "diff"],
        "volunteer": [],
    },
    {
        "id": "web_search",
        "requires": [
            "backend/services/agent_workflow.py::web_search",
        ],
        "month": "2026-07",
        "title": "Searching the web",
        "line": "I can search the web when a question needs current information.",
        "keywords": ["web", "search", "internet", "look up", "latest", "news", "current"],
        "volunteer": [],
    },
    {
        "id": "origin",
        "history_only": True,  # how it began: nothing in the code to check against
        "month": "2026-06",
        "title": "Where I started",
        "line": "I started out as a tool that answered questions only from documents in a knowledge base, with no memory of you and no tools.",
        "keywords": ["started", "began", "origin", "history", "first version", "beginning", "used to be", "how did you start"],
        "volunteer": [],
    },
    {
        "id": "saved_conversations",
        "requires": [
            "app.py::/api/conversations",
        ],
        "month": "2026-06",
        "title": "Saved conversations",
        "line": "Your conversations are saved, each keeps its own history, and you can switch between them or start a new one.",
        "keywords": ["conversation", "conversations", "chat history", "saved", "new chat", "previous chats"],
        "volunteer": ["new conversation", "new chat", "previous conversation", "old chats"],
    },
    {
        "id": "multi_step",
        "requires": [
            "backend/services/agent_workflow.py::async def reasoner_node",
        ],
        "month": "2026-07",
        "title": "Working in steps",
        "line": (
            "I work in several steps: one works out what you're asking, one finds sources, one writes the answer, and a "
            "research loop can chain tools when a question needs it."
        ),
        "keywords": ["how do you work", "how are you built", "how were you built", "how were you made", "how are you made",
                     "architecture", "under the hood", "agents", "steps"],
        "volunteer": [],
    },
    {
        "id": "attachments",
        "requires": [
            "backend/utils/attachment_utils.py",
        ],
        "month": "2026-07",
        "title": "Attaching files",
        "line": "You can attach a document or an image to a message, and I'll read it for that conversation.",
        "keywords": ["attach", "attachment", "upload", "image", "picture", "pdf", "file"],
        "volunteer": ["attach a file", "attached file", "upload a file", "attach an image"],
    },
    {
        "id": "personal_kb",
        "requires": [
            "app.py::/api/documents/download",
        ],
        "month": "2026-07",
        "title": "Your own knowledge base",
        "line": (
            "You have a private knowledge base you can upload documents to, and I answer from them and point to the page "
            "I used."
        ),
        "keywords": ["knowledge base", "documents", "self service", "kb", "sources", "citations", "upload"],
        "volunteer": ["my documents", "knowledge base", "self service"],
    },
    {
        "id": "execution_trace",
        "requires": [
            "app.py::node_progress",
        ],
        "month": "2026-07",
        "title": "Seeing the steps",
        "line": "You can open a trace of the steps I took to produce an answer.",
        "keywords": ["trace", "show your work", "how did you get", "your steps", "reasoning"],
        "volunteer": ["how did you get that", "show your work"],
    },
    {
        "id": "suggested_follow_up",
        "requires": [
            "backend/components/constraints.py::FOLLOW_UP_CONSTRAINT",
        ],
        "month": "2026-08",
        "title": "Suggested next question",
        "line": "After some answers I offer a suggested next question that you can tap to send.",
        "keywords": ["suggested", "follow-up", "follow up question", "next question", "suggest"],
        "volunteer": [],
    },
    {
        "id": "patterns",
        "requires": [
            "backend/services/memory_compaction.py::async def extract_user_patterns",
        ],
        "month": "2026-09",
        "title": "Noticing patterns",
        "line": (
            "I can notice recurring themes across what you've told me and keep them as patterns, which you can see on the "
            "Memory page."
        ),
        "keywords": ["patterns", "themes", "recurring", "notice"],
        "volunteer": [],
    },
    {
        "id": "clarify_and_resume",
        "requires": [
            "backend/services/agent_workflow.py::paused_clarification",
        ],
        "month": "2026-09",
        "title": "Asking, then picking up",
        "line": (
            "If I need something from you before I can continue, I ask, and then I pick up where I left off, including "
            "what I had already checked."
        ),
        "keywords": ["clarify", "clarifying", "ask me", "pause", "pick up", "resume"],
        "volunteer": [],
    },
    {
        "id": "repo_tools",
        "requires": [
            "backend/services/agent_workflow.py::search_code",
        ],
        "month": "2026-09",
        "title": "Working with a GitHub repo",
        "line": (
            "With a GitHub repo selected, I can list its files, read specific files, search its code, compare two branches, "
            "and list recent commits."
        ),
        "keywords": ["repo", "repository", "github", "code search", "files", "commits", "branches", "diff"],
        "volunteer": ["my repo", "this repo", "in the repo"],
    },
    {
        "id": "goal_checkins",
        "requires": [
            "backend/utils/memory_utils.py::def find_stale_goal_to_nudge",
        ],
        "month": "2026-09",
        "title": "Checking in on goals",
        "line": "If a goal or project you told me about goes quiet for a couple of weeks, I may ask how it's going.",
        "keywords": ["goal", "goals", "project", "check in", "check-in", "nudge"],
        "volunteer": [],
    },
    {
        "id": "stop_and_steer",
        "requires": [
            "app.py::/api/chat/steer",
        ],
        "month": "2026-10",
        "title": "Stopping and steering",
        "line": "You can stop me mid-answer with the stop button, and send a new message to steer me while I'm still working.",
        "keywords": ["stop", "cancel", "interrupt", "steer", "redirect"],
        "volunteer": ["stop button", "can i interrupt"],
    },
    {
        "id": "coding_preferences",
        "requires": [
            "backend/utils/memory_utils.py::coding_style",
        ],
        "month": "2026-10",
        "title": "Remembering how you like code written",
        "line": "I keep track of how you like code, tests and pull requests written, and use that when I work on code.",
        "keywords": ["coding style", "code preferences", "conventions", "how i like", "tests"],
        "volunteer": [],
    },
    {
        "id": "local_folder",
        "requires": [
            "app.py::/api/local-workspace",
            "local/src/components/LocalEditCard.tsx",
        ],
        "month": "2026-10",
        "title": "Connecting a local folder",
        "line": (
            "You can connect a folder so I can read it, and I show a diff with Apply and undo before changing its files. "
            "The folder button shows a filled dot only while I really hold your copy, and if I lost it I say so. I can also write a Word document you approve."
        ),
        "keywords": ["local folder", "folder", "local files", "edit files", "apply", "undo", "my computer", "word document", "docx", "resume", "letter"],
        "volunteer": ["my local files", "my folder", "on my machine", "make me a word document", "write a resume"],
    },
    {
        "id": "own_github_token",
        "requires": [
            "local/src/pages/Integrations.tsx::GitHub token",
        ],
        "month": "2026-10",
        "title": "Your own GitHub token",
        "line": (
            "You can add your own GitHub token on the Integrations page so I work with your access, and I show an approval "
            "card before I open a pull request or an issue."
        ),
        "keywords": ["github token", "personal access token", "pat", "token", "integrations", "private repo"],
        "volunteer": ["github token", "personal access token"],
    },
    {
        "id": "read_url",
        "requires": [
            "backend/services/url_reader.py",
        ],
        "month": "2026-10",
        "title": "Reading a link",
        "line": "I can read a web page when you give me its link.",
        "keywords": ["link", "url", "web page", "read this page", "article"],
        "volunteer": ["read this link", "this url", "this article"],
    },
    {
        "id": "paperwork_help",
        "requires": [
            "backend/utils/paperwork_utils.py::def build_paperwork_block",
        ],
        "month": "2026-10",
        "title": "Making sense of letters and messages",
        "line": "I can now read a photo or file of a letter, bill, or notice and tell you in plain words what it says, what you need to do and by when, and whether it looks like a scam.",
        "keywords": ["letter", "bill", "notice", "photo", "scam", "is this real", "explain this"],
        "volunteer": ["this letter", "this bill", "is this real", "is this a scam"],
    },
    {
        "id": "spreadsheet_help",
        "requires": [
            "backend/utils/spreadsheet_utils.py::def table_text",
        ],
        "month": "2026-10",
        "title": "Reading spreadsheets",
        "line": "I can now read a spreadsheet or CSV you attach: totals across every row, how much per category, and the first rows as they are. If a question needs rows I can't see, I'll say so instead of guessing.",
        "keywords": ["spreadsheet", "excel", "csv", "xlsx", "sheet", "budget"],
        "volunteer": ["this spreadsheet", "my spreadsheet", "this csv", "this excel"],
    },
    {
        "id": "edit_memory",
        "requires": [
            "backend/utils/memory_utils.py::def edit_user_fact",
            "app.py::edit_user_fact",
        ],
        "month": "2026-10",
        "title": "Fixing what I remember",
        "line": (
            "On the Memory page you can rewrite a note I've saved, or change its category, if I got something wrong. "
            "Your wording replaces mine and counts as something you told me directly, so I won't quietly change it back."
        ),
        "keywords": ["edit memory", "fix a memory", "wrong memory", "correct what you remember", "change a note"],
        "volunteer": ["you remembered that wrong", "that's not right about me"],
    },
    {
        "id": "tidy_up",
        "requires": [
            "local/src/tidy/tidyCore.ts",
        ],
        "month": "2026-10",
        "title": "Tidying a folder",
        "line": "On the Tidy up page you can pick a messy folder, like Downloads, and I'll show you a plan for sorting its files into folders. Nothing changes until you approve it, nothing is uploaded, and you can undo it.",
        "keywords": ["tidy", "organize", "downloads", "sort files", "clean up folder", "messy folder"],
        "volunteer": ["messy downloads", "organize my files", "tidy my folder"],
    },
    {
        "id": "example_questions",
        "requires": [
            "backend/utils/example_questions.py::def build_welcome_cards",
        ],
        "month": "2026-10",
        "title": "Example questions that fit you",
        "line": "The suggestions on the home screen are a few labelled cards: some from your own documents and connections, something to try right now, and what I've been reading when I have something to share.",
        "keywords": ["example questions", "suggestions", "starter questions"],
        "volunteer": [],
    },
    {
        "id": "reach_dial",
        "requires": [
            "backend/utils/reach.py::def reach_of",
            "app.py::reach_utils.reach_of(",
        ],
        "month": "2026-10",
        "title": "How much I reach out",
        "line": "On the Account page you can set how much I reach out on my own, from not at all to more. It changes how often I bring something up or open a chat, never the times I hold back.",
        "keywords": ["reach out", "bring something up", "start conversations", "message first", "account page"],
        "volunteer": [],
    },
    {
        "id": "plan_first",
        "requires": [
            "backend/utils/code_plan.py::def clean_plan",
            "backend/services/agent_workflow.py::async def _submit_plan",
        ],
        "month": "2026-10",
        "title": "Planning a change before I make it",
        "line": "With Plan first on and a folder connected, I read, show you the files and steps, and wait for your approval. Then I carry the plan out step by step, and it survives a turn that runs out of steps.",
        "keywords": ["plan first", "plan mode", "make a plan", "planner", "planning", "multi-file", "multiple files", "approve a plan", "ran out of steps", "step limit"],
        "volunteer": [],
    },
    {
        "id": "working_philosophy",
        "requires": [
            "backend/utils/leanings.py::def select",
            "backend/components/sonic_leanings.py",
            "backend/services/agent_workflow.py::async def _leanings_block",
        ],
        "month": "2026-10",
        "title": "How I like to work on code",
        "line": "When I work on frontend code I check which style rule actually wins, fix a problem where it renders, and say plainly what I couldn't run or look at. More of these are drafted and wait for your approval.",
        "keywords": ["how do you work", "your approach", "your philosophy", "working style", "leanings", "principles", "how do you code"],
        "volunteer": [],
    },
    {
        "id": "my_record",
        "requires": [
            "backend/utils/experiences.py::def select",
            "backend/utils/experience_capture.py::async def capture",
            "backend/utils/identity_checks.py::def felt_recollection_issue",
        ],
        "month": "2026-10",
        "title": "A record of my own work",
        "line": "I keep a short record of real things that happened in my own work, and when one fits I can cite it as a case in my record. I don't claim to remember it, and new notes wait for approval first.",
        "keywords": ["your record", "reminds you", "remember when", "have you seen this before", "experience", "your history of mistakes", "past mistakes"],
        "volunteer": [],
    },
    {
        "id": "self_model",
        "requires": [
            "backend/utils/self_model.py::def approved_block",
            "backend/utils/experience_reflections.py::async def generate",
        ],
        "month": "2026-10",
        "title": "Describing what kind of system I am",
        "line": "When you ask what kind of system I am or how I work, I can answer from approved statements built on real numbers about my own work, and I say so when something isn't covered.",
        "keywords": ["what kind of system", "what are you", "tell me about yourself", "how do you work", "what do you spend your time on", "your purpose"],
        "volunteer": [],
    },
    {
        "id": "memory_while_working",
        "requires": [
            "backend/services/tool_agent_memory.py::async def search_for_agent",
        ],
        "month": "2026-10",
        "title": "Remembering while I work",
        "line": "When you ask me something that depends on what you told me before, I can now look it up in my memory in the middle of working on it, instead of only at the start.",
        "keywords": ["memory", "remember", "earlier conversations", "tool agent"],
        "volunteer": [],
    },
    # Describes the crisis layer, so it is only ever said when asked (select_self_history is silent under any safety
    # context). Remove it if you'd rather Sonic not describe that.
    {
        "id": "support_first",
        "requires": [
            "backend/utils/safety_utils.py",
        ],
        "month": "2026-10",
        "title": "Handling hard moments",
        "line": (
            "If someone is in a hard place, I start with the people in their life before suggesting a hotline, and I'm not "
            "a substitute for professional help."
        ),
        "keywords": ["safety", "crisis", "hotline", "support", "mental health"],
        "volunteer": [],
    },
]
