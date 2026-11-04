// The words of Help Mode (components/HelpMode.tsx), one entry per `data-help` id. Stages 1 to 3 of docs/help-system-plan.md: the shell, the chat page, and every other page. Each tip says what the thing does, in plain words, and
// only what the code really does; where something is worth knowing it will NOT do, `wont` says so. The test (helpMode.test.mjs) keeps this and the page in step: every id used in the source has a tip here,
// and every tip here is used.

import type { HelpTip } from './helpModeLogic';

export const HELP_TIPS: Record<string, HelpTip> = {
  // ---- the top bar ----
  'topbar.menu': { title: 'Menu', what: 'Opens the navigation: your chats, and the pages (Memory, Activities, Integrations and the rest).', when: 'You are on a phone, where the navigation is hidden until you ask for it.' },
  'topbar.title': { title: 'Page title', what: 'The page you are on. In Chat it is the name of the open conversation.' },
  'topbar.tagline': { title: 'Sonic', what: 'The name and one line about what this is.' },

  // ---- the navigation ----
  'rail.brand': { title: 'Sonic', what: 'Takes you to the chat.' },
  'rail.collapse': { title: 'Collapse or expand the sidebar', what: 'Shrinks the navigation to icons, or opens it out again.', when: 'You want more room for the page. Your choice is remembered.' },
  'rail.closeMenu': { title: 'Close the menu', what: 'Hides the navigation again.' },
  'rail.new': { title: 'New chat', what: 'Starts a fresh conversation. The one you were in is kept in your list.', when: 'You are moving on to a different subject.', wont: 'Delete the conversation you leave.' },
  'rail.chat': { title: 'Chat', what: 'Talk to Sonic: ask questions, work on code, get things done.' },
  'rail.selfService': { title: 'Self Service', what: 'Your own library of documents. Upload files and Sonic can answer questions from them.', when: 'You want answers from your own material.' },
  'rail.tidy': { title: 'Tidy up', what: 'Helps you organise a folder on your computer. It makes its plan in your browser from file names, sizes and dates.', wont: 'Send what is inside your files anywhere.' },
  'rail.memory': { title: 'Memory', what: 'The short notes Sonic keeps about you so it can help better. You can read, edit and remove every one.', when: 'You want to see or change what Sonic remembers.' },
  'rail.reading': { title: 'Reading', what: 'Things Sonic has read that it thinks you might like.' },
  'rail.activities': { title: 'Activities', what: 'Tasks you have set up for Sonic to do on a schedule, and what they found.' },
  'rail.officeHours': { title: 'Office Hours', what: 'A set time to sit down with Sonic, with notes afterwards.' },
  'rail.integrations': { title: 'Integrations', what: 'The accounts you can connect, such as Google (calendar, email, Drive) and GitHub.', when: 'You want Sonic to look at your calendar, email or code.', wont: 'Do anything with a connected account until you ask, and it asks you before it sends or changes anything.' },
  'rail.sonicHealth': { title: 'Sonic Health', what: 'Counts and charts about how Sonic itself is behaving. For admins only.' },
  'rail.recent': { title: 'Recent chats', what: 'Your conversations, newest first.', when: 'You want to go back to an earlier chat.' },
  'rail.convo': { title: 'A conversation', what: 'Opens this conversation, so you can carry on where you left off.' },
  'rail.convoDelete': { title: 'Delete a conversation', what: 'Removes this conversation, with anything attached to it, after you confirm.', wont: 'Delete anything else.' },
  'user.chip': { title: 'You', what: 'Opens a menu with your Account settings, a light or dark theme, the full Help guide and Disconnect.', when: 'You want the settings, or the long Help guide.' },

  // ---- the chat page ----
  'chat.trace': { title: 'Trace', what: 'Shows the steps Sonic took on your last request: what it read, searched and decided.', when: 'You want to see how an answer was reached.' },
  'chat.terminal': { title: 'Terminal', what: 'Shows the output of checks and commands Sonic ran on your own computer through the runner.', when: 'You have the runner set up and want to see what it printed.' },
  'chat.terminalPanel': { title: 'Terminal', what: 'The output of checks and commands run on your computer through the runner, newest first.' },
  'chat.sessionColumn': { title: 'Session column', what: 'Shows or hides the column down the right side: your computer and folder, the repository, how full the memory is, the plan, and your chat settings.' },
  'chat.loadEarlier': { title: 'Load earlier messages', what: 'Brings in the older part of a long conversation.' },
  'chat.budget': { title: 'Usage', what: 'Says where you are against the usage limit. When a limit is reached Sonic pauses and says when it resets.' },

  // ---- a message ----
  'msg.like': { title: 'Helpful', what: 'Tells us this answer helped.', when: 'An answer was good and you want more like it.' },
  'msg.dislike': { title: 'Not helpful', what: 'Tells us this answer missed, and lets you say why.', when: 'An answer was wrong or not what you asked for. It is how we see what to fix.' },
  'msg.followUp': { title: 'Suggested follow-up', what: 'A next question Sonic thinks you might want. Pressing it sends it as your next message.' },
  'msg.retry': { title: 'Try again', what: 'Sends your message again after something went wrong.' },
  'msg.copyCode': { title: 'Copy code', what: 'Copies this block of code to your clipboard.' },
  'msg.steps': { title: 'Steps', what: 'The steps Sonic took to reach this answer. Press to open or close the list.' },

  // ---- the message box ----
  'composer.input': { title: 'Message box', what: 'Type to Sonic. Enter sends. While Sonic is working, a message here steers it.', when: 'Always: this is how you ask for things.' },
  'composer.send': { title: 'Send', what: 'Sends your message. While Sonic is working it becomes Steer (with text in the box) or Stop (with the box empty).' },
  'composer.attach': { title: 'Attach a file', what: 'Adds a photo or a file to your message. Sonic reads its text, and looks at photos, for this conversation.', when: 'You want Sonic to work from a file you have.', wont: 'Keep it forever: attachments are removed after 30 days.' },

  // ---- the settings under the message box ----
  'chip.model': { title: 'Models', what: 'Which AI does which job: the Base model writes the replies, the Tool model does the working steps (including reading code, email and search), and the Execute model carries out an approved plan. Presets set the last two together.', when: 'You want a cheaper or stronger model for a job.', wont: 'Send your words to Anthropic or OpenAI unless you choose a model from them.' },
  'chip.mode': { title: 'Plan or Agent', what: 'Plan shows you a plan to approve before Sonic changes your files. Agent goes straight to the work.', when: 'Plan for a change that touches several files.', wont: 'Change your files without your Apply in either mode.' },
  'chip.thinking': { title: 'Swift or Deep', what: 'Swift is standard effort. Deep takes more steps and retries on a hard question, and costs more.', when: 'Deep for something hard; Swift for the rest.' },
  'chip.answers': { title: 'Grounded or Open', what: 'Grounded answers from your own library. Open can also answer beyond it.', when: 'Grounded when you want answers only from your documents.' },
  'chip.export': { title: 'Export', what: 'Saves this conversation as a file.' },
  'chip.help': { title: 'Settings tips', what: 'A few tips about these settings.' },

  // ---- the session column ----
  'session.connection': { title: 'Connection', what: 'Your computer and your folder: whether the runner is connected, and whether Sonic can see the folder you chose.', when: 'You want Sonic to run your checks or edit your files.', wont: 'Run anything on your computer without you approving it.' },
  'session.github': { title: 'GitHub', what: 'The repository Sonic reads and works on when you ask about code on GitHub.' },
  'session.gauges': { title: 'Gauges', what: 'How full Sonic\'s memory work is, and how much of your usage limit you have used.' },
  'session.plan': { title: 'Plan', what: 'The plan you approved, and which steps are done.' },
  'session.conversation': { title: 'This conversation', what: 'What Sonic is carrying from this conversation, such as the short summary of a long one.' },
  'session.controls': { title: 'Chat settings', what: 'The same settings as under the message box, kept here too.' },

  // ---- cards in the conversation ----
  'card.apply': { title: 'Apply', what: 'Writes the proposed changes into your connected folder. This is the moment your files change, and you can Undo it from the same card.', when: 'You have read the changes and want them.', wont: 'Touch any file that is not shown on the card.' },
  'card.decline': { title: 'Decline', what: 'Says no to the proposed changes. You can add why, and Sonic adjusts.', wont: 'Change anything.' },
  'card.undo': { title: 'Undo', what: 'Puts the files back as they were before you applied the change.' },
  'card.diffFile': { title: 'A changed file', what: 'Opens or closes the lines that would change in this file: red is removed, green is added.' },
  'card.plan': { title: 'Plan', what: 'The plan Sonic proposed before changing anything: the files and the ordered steps. You can edit or remove a step, approve it, or cancel it.', wont: 'Edit your files until you approve it, and then each change still shows for your Apply.' },
  'card.planApprove': { title: 'Approve and start', what: 'Tells Sonic to carry out the plan, step by step.', wont: 'Skip your Apply on each change.' },
  'card.runnerCheck': { title: 'Run a check', what: 'Sonic is asking to run one of your project\'s own checks on your computer, through the runner. The result goes back to Sonic.', wont: 'Run until you press Run.' },
  'card.steps': { title: 'Steps to run', what: 'A short list of steps Sonic suggests, each with its own Run button. A step that changes things asks you to confirm first.', wont: 'Run a step until you press it.' },

  // ---- Memory ----
  'memory.comingUp': { title: 'Coming up', what: 'Things you mentioned that are on the way, with what Sonic knows about each and where it came from. A detail you never gave shows as not known.', when: 'You want to check or correct what Sonic has noted about an event.' },
  'memory.looksRight': { title: 'Looks right', what: 'Marks this upcoming item as checked by you, so Sonic treats its details as right.' },
  'memory.editLoop': { title: 'Edit an upcoming item', what: 'Lets you change its day, time, who it is with and where.', when: 'A detail is wrong or you can now fill in one that was not known.' },
  'memory.cancelLoop': { title: 'Cancel it / Restore', what: 'Says it fell through, so Sonic will not ask about it. Restore brings it back.', wont: 'Delete it: it stays listed as cancelled.' },
  'memory.forgetLoop': { title: 'Forget this', what: 'Removes this upcoming item from Sonic\'s memory.' },
  'memory.saved': { title: 'Saved memories', what: 'The notes Sonic keeps about you, each with how sure it is and where it came from. You can search, edit or forget any of them. Each is filed under a part of your life (work, your own projects, personal, or general) so Sonic does not mix them up.', when: 'You want to see what Sonic remembers, or fix something.' },
  'memory.search': { title: 'Search memories', what: 'Narrows the list to memories that contain what you type.' },
  'memory.category': { title: 'Filter by category', what: 'Shows only one category of memory, such as identity, preference, career, project, goal or relationship.' },
  'memory.domain': { title: 'Filter by part of your life', what: 'Shows only the memories filed under work, your own projects, personal or general.' },
  'memory.clearAll': { title: 'Clear all', what: 'Removes every memory, and also the short notes Sonic keeps from past conversations and the older copies it kept when it tidied up. This cannot be undone.', when: 'You want Sonic to start again knowing nothing about you.' },
  'memory.rowDomain': { title: 'Part of your life', what: 'Which part of your life this memory is about: work, your own projects, personal or general. If it is filed wrong, change it here.' },
  'memory.confidence': { title: 'How confident', what: 'How sure Sonic is that this is still true. It can fall as a memory gets older.' },
  'memory.source': { title: 'Where it came from', what: 'Whether you told Sonic this, wrote it yourself, or Sonic inferred it or noticed a pattern.' },
  'memory.editFact': { title: 'Edit this memory', what: 'Lets you change the wording. Your wording replaces the old one and counts as something you told Sonic directly.' },
  'memory.forgetFact': { title: 'Forget this memory', what: 'Removes this memory, after you confirm.' },
  'memory.differentPeople': { title: 'They are different people', what: 'Sonic thought two spellings were one person. This tells it they are two.' },
  'memory.unlink': { title: 'These do not belong together', what: 'Removes the link between two memories that Sonic thought went together.', wont: 'Delete either memory.' },

  // ---- Account ----
  'account.profile': { title: 'Profile', what: 'Who you are signed in as, and whether the account is registered or a guest.' },
  'account.about': { title: 'About you', what: 'Optional details that shape how Sonic talks to you: your name, pronouns, what you do, your interests and how you like answers. Nothing here is required, and Save keeps your changes.', when: 'You want Sonic to know you a little, or to talk to you differently.' },
  'account.style': { title: 'How you like answers', what: 'How long, how warm, how funny, and how many questions about you. When you are having a hard time Sonic still softens and slows down, whatever you pick.' },
  'account.avoid': { title: 'Please do not bring up', what: 'Things Sonic will not raise on its own. If you bring one up yourself, it talks about it normally.' },
  'account.reach': { title: 'How much Sonic reaches out', what: 'How often Sonic may bring something up at a quiet moment: ask what else is going on, mention something it leans toward, or something it has read. Less is rare and gentle; more is a little more often; Not at all turns it off.' },
  'account.messageFirst': { title: 'Let Sonic start conversations', what: 'Lets Sonic open a chat with one question about something you told it, like how a date went. How often follows the reach setting above, and it is unavailable while that is set to Not at all.' },
  'account.save': { title: 'Save', what: 'Saves the changes in this card.' },
  'account.mascot': { title: 'The little robot', what: 'Shows or hides SAAPP, the robot that sits on the message box and reacts while Sonic works. Off gives a plain logo.' },
  'account.digest': { title: 'Incident digest', what: 'For admins: when you open a conversation, Sonic tells you about new production incidents, once each and at most every 12 hours.' },
  'account.theme': { title: 'Appearance', what: 'Switches between the light and the dark theme.' },
  'account.data': { title: 'Your data', what: 'How long things are kept (conversations until you delete them; attachments 30 days), with ways in to Memory, Integrations and the Privacy page.' },

  // ---- The runner (Account) ----
  'runner.card': { title: 'Run checks on your machine', what: 'An optional small program on your own computer that runs your project\'s tests, type checks or builds when Sonic asks and you press Run on a card. This is where you set it up.', when: 'You want Sonic to check its own work with your project\'s real tests.', wont: 'Run anything on your computer without you pressing Run.' },
  'runner.add': { title: 'Add a runner', what: 'Makes a one-time code and a starter script for the computer you want to use. Name the computer first if you like.' },
  'runner.code': { title: 'One-time code', what: 'The code the starter script uses to connect that computer to your account, once.' },
  'runner.download': { title: 'Download setup', what: 'Downloads the starter script for your kind of computer. Running it installs the runner there.' },
  'runner.remove': { title: 'Remove a runner', what: 'Disconnects this computer: its key stops working.', wont: 'Uninstall anything from the computer.' },
  'runner.project': { title: 'A project', what: 'A folder on that computer the runner may run checks in, with the checks it found there.' },
  'runner.check': { title: 'A check', what: 'One of your project\'s own checks, with the command it runs. Switched on, Sonic may ask to run it (and you still press Run).' },
  'runner.fakeKey': { title: 'Fake AI key', what: 'Keeps a test from spending real money by giving it made-up AI keys while the project\'s checks run.' },
  'runner.readOnly': { title: 'Commands that only look', what: 'Lets commands that only look around (git status, git diff, ls, cat and the like) run without the Run button, so Sonic can read your project without a click each time.', wont: 'Skip your Run button for anything that changes things.' },
  'runner.autoUpdate': { title: 'Update automatically', what: 'Installs a newer version of the runner by itself, only while no check is running, then restarts it. Off until you turn it on.' },
  'runner.addProject': { title: 'Add a project', what: 'Asks for a project to be added. Only the folder\'s name comes to Sonic; the runner then opens a window on that computer where you choose the folder.' },

  // ---- Paid use (Account, only where billing is on) ----
  'billing.card': { title: 'Paid use', what: 'Your credit balance and today\'s paid use. Credit is used at what the models cost, one dollar for one dollar.' },
  'billing.add': { title: 'Add credit', what: 'Buys credit through the payment provider\'s own page, so you never type a card here. The processing fee is shown as its own line before you pay.' },
  'billing.limit': { title: 'Your daily limit', what: 'The most paid use in one day. You can set it lower than the one you are given, or clear it.', wont: 'Charge you when it is reached: Sonic just pauses until tomorrow and your credit is untouched.' },
  'billing.fine': { title: 'The fine print', what: 'The processing fee is not refundable. Unused credit can be refunded on request by email; credit you have used is not.' },

  // ---- Integrations ----
  'integrations.tabs': { title: 'Sections', what: 'Switches between Google, Outlook, GitHub and settings.' },
  'integrations.google': { title: 'Google', what: 'Connects your Google account (Calendar, Gmail, Drive and Docs) so Sonic can look at them when you ask. It sends an email or changes an event or document only after you approve that exact action. You can disconnect any time.', wont: 'Do anything with your Google account until you ask.' },
  'integrations.microsoft': { title: 'Microsoft', what: 'Connects a personal Microsoft account (Outlook.com, Hotmail, Live) so Sonic can search and read your mail, check your calendar and find files in OneDrive. It only sends an email or changes your calendar after you approve that exact action.' },
  'integrations.targetDoc': { title: 'Target document', what: 'A Google Doc Sonic can add to when you ask, for example after summarising an email or a file. Paste its address or ID.' },
  'integrations.github': { title: 'GitHub token', what: 'Your own personal access token, so Sonic can read, review and open pull requests and issues on your repositories as you. It is checked with GitHub, stored encrypted and never shown again after you save it. Without one Sonic uses a shared token.' },

  // ---- Activities ----
  'tasks.new': { title: 'New task', what: 'Starts a short set of steps for something you want Sonic to look into on a schedule.' },
  'tasks.results': { title: 'Results', what: 'Shows or hides what this task has found so far.' },
  'tasks.runNow': { title: 'Run now', what: 'Runs this task straight away instead of waiting for its time. Not available while it is paused.' },
  'tasks.pause': { title: 'Pause or resume', what: 'Pauses the task so it stops running on its schedule, or starts it again.' },
  'tasks.edit': { title: 'Edit a task', what: 'Lets you change what it looks for and when.' },
  'tasks.delete': { title: 'Delete a task', what: 'Removes the task and stops it running.' },
  'tasks.wizard': { title: 'Setting up a task', what: 'Two short steps: what kind of task, then what to look for and how often. Nothing starts until you create it.' },
  'tasks.kind': { title: 'What kind of task', what: 'Look something up (searches the web and job boards and keeps what is new), review your pull requests (what a careful reviewer would flag), or audit your code (security problems and risky code).' },
  'tasks.cadence': { title: 'How often', what: 'Every day or every week, at roughly the time you choose.' },
  'tasks.window': { title: 'When exactly', what: 'At that time, or sometime in the two or three hours after, at a quiet moment so it does not interrupt you.' },
  'tasks.location': { title: 'Where should the jobs be', what: 'Optional: narrows a job search to a place.' },

  // ---- Office Hours ----
  'meetings.start': { title: 'Start office hours', what: 'Opens a chat where Sonic runs the agenda. You end it, and the notes are kept here.', when: 'It is time for your regular check-in, or you want to hold it now.' },
  'meetings.notes': { title: 'Notes', what: 'The summary and action items from past meetings, which you can edit or delete.' },
  'meetings.progress': { title: 'Progress notes', what: 'Things you log to this meeting between sessions, as a note, a win or a blocker. Sonic lists the ones the meeting has not covered when you start it, in your own words.' },
  'meetings.edit': { title: 'Edit a meeting', what: 'Lets you change its name, timing, purpose, agenda and how much Sonic challenges you.' },
  'meetings.delete': { title: 'Delete a meeting', what: 'Removes the meeting and its notes, after you confirm.' },
  'meetings.new': { title: 'New meeting', what: 'Sets up a standing meeting with Sonic, like a weekly check-in.' },
  'meetings.cadence': { title: 'When you mean to hold it', what: 'Optional. It only marks the meeting as due that day, in your timezone. You can still start it early, late, or skip it.' },
  'meetings.match': { title: 'Calendar title', what: 'Optional: the title of the matching event on your Google Calendar, if you keep one there.' },
  'meetings.agenda': { title: 'Agenda', what: 'What Sonic goes through: one item per line, up to eight.' },
  'meetings.coaching': { title: 'How much to challenge you', what: 'Supportive: Sonic asks and listens, and challenges nothing. Balanced: it may raise one thing the records show, like an item that keeps carrying over. Challenging: up to two things a meeting, each with a small next step. Never harsh, and only from what is recorded.' },

  // ---- Reading ----
  'reading.shelf': { title: 'Sonic\'s reading shelf', what: 'Pieces Sonic found and read on its own, about the things it is curious about, with a short note on each in its own words.' },
  'reading.topics': { title: 'Filter by topic', what: 'Shows only the pieces on one topic. All shows everything.' },

  // ---- Self Service ----
  'selfservice.kb': { title: 'Knowledge base', what: 'Picks which library of documents you are adding to and managing on this page.' },
  'selfservice.upload': { title: 'Add documents', what: 'Adds files to the selected library, where Sonic reads and indexes them so it can answer from them.', when: 'You want answers from your own material.' },
  'selfservice.manifest': { title: 'Your documents', what: 'The documents in the selected library. Search them, or delete one so Sonic stops using it.' },

  // ---- Tidy up ----
  'tidy.choose': { title: 'Choose a folder', what: 'Picks the folder to tidy, like Downloads. Sonic looks only at file names, sizes and dates, and nothing leaves your computer.', wont: 'Change anything yet: you see a plan first.' },
  'tidy.group': { title: 'A group of files', what: 'Tick to sort these files into their folder, untick to leave them where they are.' },
  'tidy.go': { title: 'Tidy up', what: 'Moves the ticked files into folders as the plan showed. Nothing is deleted or overwritten: a name that is taken gets a number.' },
  'tidy.undo': { title: 'Undo', what: 'Moves the files back where they were.' },
  'tidy.startOver': { title: 'Choose a different folder', what: 'Goes back to choosing a folder, without changing anything.' },

  // ---- Sonic Health (admins) ----
  'health.tab': { title: 'Health section', what: 'A group of Sonic\'s own numbers. A group with several pages opens a list of them.' },
  'health.entry': { title: 'Health page', what: 'Opens this page of charts and tables about how Sonic is behaving.' },
};
