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
            "app.py::/api/chat/opening",
        ],
        "month": "2026-10",
        "title": "Starting a conversation",
        "line": (
            "If you turn on Message First, I may open a conversation with one question about something you told me, like "
            "how a date went. It's off until you turn it on."
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
            "I keep a short list of what has changed about me, so when you ask what's new I can answer from that instead of "
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
            "You can connect a folder from your computer so I can read it, and when I propose changes to its files you see "
            "a diff with an Apply button and can undo it."
        ),
        "keywords": ["local folder", "folder", "local files", "edit files", "apply", "undo", "my computer"],
        "volunteer": ["my local files", "my folder", "on my machine"],
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
        "id": "example_questions",
        "requires": [
            "backend/utils/example_questions.py::def build_example_questions",
        ],
        "month": "2026-10",
        "title": "Example questions that fit you",
        "line": "The example questions on the home screen come from your own documents and connections, not a fixed list.",
        "keywords": ["example questions", "suggestions", "starter questions"],
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
