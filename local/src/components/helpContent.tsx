import React from 'react';
import HelpLink from './HelpLink';
import SonicProfileCard from './SonicProfileCard';

// The words in the help panel. The topics (which there are, in what order, for whom) are in helpTopics.ts; each one has an entry here under the same id.
// The first object is for everyone and is written in plain words, with no technical terms. The second is for people who want to see how it works.
// Keep every statement true to what the app does today: if a screen changes, change its topic in the same edit.

export interface HelpContext {
  guest: boolean;
  /** Close the panel and go to a page. */
  go: (path: string) => void;
}

type Content = (ctx: HelpContext) => React.ReactNode;

export const EVERYDAY: Record<string, Content> = {
  start: ctx => ctx.guest ? (
    <>
      <p>You're in the guest sandbox: a shared demo of Sonic Assistant, a personal AI assistant built by Jack Harper.</p>
      <ul>
        <li>Ask it anything, including who Jack is, what he has built, and how this system works.</li>
        <li>Ask what it has been reading lately, or for a dry joke. It has a personality and a profile you can read under "About Sonic".</li>
        <li>Open the <strong>Trace</strong> in the "…" menu to watch the steps it takes to answer.</li>
        <li>For questions about the demo library, Sonic uses its documents first and cites the page. For anything they don't cover, it answers from general knowledge. If you'd rather it stuck to the documents, turn off <strong>Open Answers</strong> in the "…" menu.</li>
        <li>Guests share one temporary identity, so nothing here is personal and nothing is remembered about you.</li>
      </ul>
      <h4>With an account you also get</h4>
      <ul>
        <li>Your own conversations, memory and settings.</li>
        <li>Your own document library: upload PDFs once and ask about them in any chat.</li>
        <li>Email, calendar and Google Drive help, once you connect Google.</li>
        <li>Folder tools on your computer, including Tidy up.</li>
      </ul>
    </>
  ) : (
    <>
      <p>Sonic is an assistant you can talk to like a person. It answers, looks things up, reads what you give it, and, with your permission, works in your email, calendar, documents and folders. Here is the whole list.</p>

      <h4>Talk things through</h4>
      <ul>
        <li>Answer questions, explain things plainly, and help you think a decision through.</li>
        <li>Draft messages, emails, letters and summaries for you to look over.</li>
        <li>Ask you a question when yours is unclear instead of guessing, then pick up where it left off.</li>
        <li>Stop mid-answer with the stop button, or send a new message to steer it while it's still working.</li>
        <li>Offer a suggested next question after some answers, which you can tap to send.</li>
      </ul>

      <h4>Look things up</h4>
      <ul>
        <li>Search the web when a question needs current information, and say where it came from.</li>
        <li>Read a page when you paste its link, and tell you what it says.</li>
        <li>Open a page in a real browser when it needs to see how something looks, not only what it says.</li>
      </ul>

      <h4>Read what you give it</h4>
      <ul>
        <li><strong>Letters, bills and notices:</strong> what it says in plain words, what you need to do and by when, and whether it looks like a scam.</li>
        <li><strong>Photos, screenshots, PDFs and Word documents</strong>, and <strong>spreadsheets</strong>: totals across every row, how much per category, and the first rows as they are.</li>
      </ul>

      <h4>Answer from your own documents</h4>
      <ul>
        <li>Upload PDFs to your own private library once, then ask about them in any conversation. It answers from them and points to the page it used. See "Your document library".</li>
      </ul>

      <h4>Documents versus what Sonic already knows</h4>
      <ul>
        <li>When a question is about your documents, Sonic looks in your library and uses what it finds, with the page it came from. When your documents don't have the answer, it says so in its answer and uses its general knowledge.</li>
        <li>That's the <strong>Open Answers</strong> switch in the "…" menu next to the message box. <strong>On</strong> (the default): documents first, general knowledge when they don't cover it. <strong>Off</strong>: only your documents.</li>
        <li>If you want strictly what's in your documents, for example to check what a file actually says, turn <strong>Open Answers</strong> off. Then Sonic tells you when the answer isn't there instead of filling in.</li>
      </ul>

      <h4>Your email, calendar and Google</h4>
      <ul>
        <li>Search and summarize your email, and read text-based attachments.</li>
        <li>Check what's on your calendar, and create or move events.</li>
        <li>Find and read your Google Docs and Sheets.</li>
        <li>Draft and send an email, or add text to a Google Doc you've chosen.</li>
        <li>Every send, event and edit waits for your approval first.</li>
      </ul>

      <h4>Do the maths and check the code</h4>
      <ul>
        <li>Run a small Python program to work something out for certain: a loan payment, a split bill, the days between two dates, a unit conversion. Sonic checks real results instead of guessing.</li>
        <li>For developers: read code on GitHub, summarize pull requests, and draft a pull request or issue. See "For developers".</li>
      </ul>

      <h4>Work on your computer</h4>
      <ul>
        <li><strong>Tidy up a messy folder</strong> like Downloads, with a plan you approve and an undo.</li>
        <li><strong>Connect a folder</strong> so it can read your files there. It can write you a new Word document, such as a resume or letter, or make an edited copy of one. You see a preview first, nothing is written until you press Apply, and your original is never changed.</li>
      </ul>

      <h4>Remember and follow up</h4>
      <ul>
        <li>Keep short notes about what matters to you, so you don't have to say it twice. You can read, fix and remove every one.</li>
        <li>Ask how it went after something you said was coming up, like an interview or a trip.</li>
        <li>Check in on a goal or project that has gone quiet for a couple of weeks.</li>
        <li>Notice when something you say connects to an earlier conversation.</li>
      </ul>

      <h4>Be itself</h4>
      <ul>
        <li>It has a dry sense of humor, a few interests, and a profile you can read under "About Sonic".</li>
        <li>Ask what it has been reading lately, or what has changed about it.</li>
        <li>If someone is having a hard time, it starts with the people in their life before suggesting a hotline. It is not a substitute for professional help.</li>
      </ul>

      <p>Sonic can be wrong, so check anything important, like dates, amounts, and medical or legal details, with the source. Press thumbs down on an answer that looks off, and we'll see it.</p>
    </>
  ),

  try: ctx => ctx.guest ? (
    <>
      <p>A few things to ask:</p>
      <ul>
        <li>"Who is Jack Harper, and what has he built?"</li>
        <li>"How does Sonic Assistant keep one person's data separate from another's?"</li>
        <li>"What was changed in the last pull request?"</li>
        <li>"What have you been reading lately?"</li>
        <li>"Tell me a joke about vibe coding."</li>
        <li>"What's the most famous bug in computing history?"</li>
      </ul>
      <p>Open the <strong>Trace</strong> in the "…" menu to watch the steps Sonic takes to answer.</p>
    </>
  ) : (
    <>
      <p>Just type it the way you'd say it. Here are things worth trying, by what you're after.</p>

      <h4>Make sense of paperwork</h4>
      <ul>
        <li>Attach a photo of a letter or bill: "What does this say, and do I need to do anything?"</li>
        <li>"I got this text. Is it real?" Paste it or attach a screenshot.</li>
        <li>Attach a spreadsheet: "How much did I spend on groceries?" or "Which month cost the most?"</li>
      </ul>

      <h4>Make a card from a photo</h4>
      <ul>
        <li>Attach a photo: "Put 'Merry Christmas' across the bottom in gold script." Then "bigger", "try red" or "move it up" until it's right, and press Download.</li>
        <li>No photo? "Make me a Christmas card with a snowy football stadium." Sonic makes the picture (3 a day), then you add your words.</li>
      </ul>

      <h4>Ask your own documents</h4>
      <ul>
        <li>"What's in my lease about pets?"</li>
        <li>"Which of my documents mention the roof?"</li>
        <li>"Summarize the warranty on the dishwasher manual."</li>
      </ul>

      <h4>Get through your day</h4>
      <ul>
        <li>"What's on my calendar tomorrow?" or "Am I free Friday afternoon?" (after you connect Google)</li>
        <li>"Any emails I need to answer?" or "Find the email from my landlord and tell me what they asked."</li>
        <li>"Schedule lunch with Sam on Thursday at noon." It shows you the event before it adds it.</li>
        <li>"Read the CSV my accountant emailed me and add the key numbers to my notes document."</li>
      </ul>

      <h4>Look something up</h4>
      <ul>
        <li>"Look into the best way to learn Spanish and show me your sources."</li>
        <li>"Read this article and give me the three main points," with the link pasted in.</li>
        <li>"What's the weather like in Lisbon in March?"</li>
      </ul>

      <h4>Let Sonic run the numbers (it really runs Python)</h4>
      <ul>
        <li>"What's the monthly payment on a $240,000 loan at 6.5% over 30 years?"</li>
        <li>"Split this $187.40 dinner bill five ways with a 20% tip."</li>
        <li>"How many days are there between March 3 and October 17?"</li>
        <li>"Is 2 to the power 61 minus 1 a prime number?" It checks, rather than guessing.</li>
        <li>"Sort this list and tell me what's repeated," with the list pasted in.</li>
        <li>Say "run it" at the start of a question if you want it to calculate rather than estimate.</li>
      </ul>

      <h4>Think and write</h4>
      <ul>
        <li>"Help me think through whether to take this job."</li>
        <li>"Draft a polite email asking to move my appointment."</li>
        <li>"Explain compound interest like I'm twelve, then like I'm an accountant."</li>
      </ul>

      <h4>Your computer</h4>
      <ul>
        <li>"Make me a one-page resume as a Word document." (connect a folder first)</li>
        <li>"Add a skills section to my resume." It makes an edited copy and leaves the original alone.</li>
        <li>Open <strong>Tidy up</strong> and sort your Downloads folder in a minute.</li>
      </ul>

      <h4>About you and Sonic</h4>
      <ul>
        <li>"What do you remember about me?"</li>
        <li>"What have you been reading lately?"</li>
        <li>"What's new about you?"</li>
      </ul>

      <h4>Just for fun</h4>
      <ul>
        <li>"Tell me a joke about vibe coding."</li>
        <li>"What's the most absurd bug in computing history?"</li>
        <li>"Give me a dry one-liner about meetings that could have been emails."</li>
      </ul>
    </>
  ),

  attach: () => (
    <>
      <p>Press the <strong>+</strong> next to the message box to attach something. On a phone it offers to take a photo, pick one from your photos, or choose a file. You can also paste a screenshot straight into the box, and attach several things to one message.</p>
      <h4>What works</h4>
      <ul>
        <li>Photos and screenshots, PDFs, Word documents (.docx), Excel and CSV spreadsheets, and plain text files.</li>
        <li><strong>Letters, bills and notices:</strong> a clear, well-lit photo of the whole page works best. Ask "What does this say, and do I need to do anything?"</li>
        <li><strong>Spreadsheets:</strong> Sonic sees how big the sheet is, its column names, its first rows, and totals across every row, including how much per category. On a big sheet it tells you when it can't see the rest, rather than guessing.</li>
        <li><strong>Short documents</strong> are read in full. Longer ones are summarized, and Sonic looks back through the whole text when you ask about details.</li>
        <li><strong>Pictures:</strong> it can describe a photo, read the text in a screenshot, and tell you what a chart or diagram shows.</li>
      </ul>
      <h4>Put words on a photo</h4>
      <ul>
        <li>Attach a photo and say what to write: <strong>"Put 'Merry Christmas from the Harpers' across the bottom in gold script."</strong> Sonic draws the words and shows the result under its reply, with a <strong>Download</strong> button.</li>
        <li>Ask for changes until it looks right: "bigger", "move it up a bit", "try red", "a more festive font". Each change starts again from your original photo, which is never altered.</li>
        <li>It picks from a set of fonts (elegant and casual scripts, festive, bold capitals, serif and more) and colours, including Hawkeye gold. If the words would be hard to read on that part of the photo, it adds a soft shade behind them and tells you.</li>
        <li>The card is saved as a JPEG, up to about 8 inches on its long side, so it prints well. Sonic keeps your 8 most recent cards, for 30 days.</li>
        <li>Only for signed-in accounts, not the guest demo.</li>
      </ul>
      <h4>Make a picture from a description</h4>
      <ul>
        <li>No photo? Describe one: <strong>"Make me a picture of a snowy football stadium at dusk, for a Christmas card."</strong> Sonic generates it, shows it with a <strong>Download</strong> button, and you can ask for changes ("snowier", "make it a watercolor").</li>
        <li>Then add words the same way as on a photo: "Put 'Merry Christmas from the Harpers' across the bottom in gold script."</li>
        <li>You can make <strong>3 pictures a day</strong> (it starts again the next day, in your own time zone). Adding words to a picture you already have isn't limited that way.</li>
        <li>Sonic won't make pictures of real people, or sexual, graphic or deceptive pictures, and Google's own safety filters can decline others. A picture that fails or is declined doesn't count against your 3.</li>
        <li>The picture is made by a Google image model and kept like a card: your 8 most recent for 30 days. Images can contain small mistakes, so look it over before you print it.</li>
      </ul>
      <h4>What doesn't work yet</h4>
      <ul>
        <li>Old .xls, .doc and .ppt files, and Pages, Numbers and Keynote files. Save them as .xlsx, .docx or PDF and try again.</li>
        <li><strong>iPhone photos:</strong> if a photo won't attach because of its format (HEIC), attach a screenshot of it instead, or set the camera to "Most Compatible" in Settings, Camera, Formats.</li>
      </ul>
      <h4>Attaching versus your library</h4>
      <p>An attachment is for the conversation you add it to. If you want Sonic to be able to answer from a document any time, add it to your document library instead.</p>
      <p>What you attach is read by Google's Gemini so Sonic can answer. It is kept with the conversation for 30 days, then removed. Deleting the conversation removes it sooner.</p>
    </>
  ),

  tidy: ctx => (
    <>
      <p>Pick a messy folder, like Downloads, and Sonic shows you a plan for sorting its loose files into folders such as Pictures, Documents and Installers.</p>
      <ul>
        <li>You see the plan first, and can untick anything. Nothing changes until you press <strong>Tidy up</strong>.</li>
        <li><strong>It stays on your computer.</strong> Sonic only looks at file names, sizes and dates, never what's inside, and nothing is uploaded.</li>
        <li><strong>Nothing is deleted or overwritten.</strong> If a name is taken, the file gets a number, like "report (2).pdf".</li>
        <li><strong>Undo</strong> puts every file back.</li>
        <li>Files it isn't sure about, hidden files, and anything still downloading are left where they are.</li>
      </ul>
      <p>It works in Chrome or Edge on a laptop or desktop, not on a phone. If your browser won't open a folder like Downloads directly, try a folder inside it.</p>
      <p>Tidy up is different from the folder button at the left of the message box (the one that connects a local folder). Tidy up only sorts files and uploads nothing. Connecting a folder lets Sonic read your text files and Word documents and write new ones, with your approval.</p>
      <p><HelpLink go={ctx.go} path="/tidy">Open Tidy up</HelpLink></p>
    </>
  ),

  google: ctx => (
    <>
      <p>Connect your Google account and Sonic can work with your email, calendar and files when you ask. Nothing happens in the background: it only looks when you ask it to.</p>
      <h4>What it can do</h4>
      <ul>
        <li><strong>Email:</strong> search your inbox, summarize messages, read attachments that are text-based (like a CSV or a text file), and draft or send a reply.</li>
        <li><strong>Calendar:</strong> tell you what's on, check when you're free, and create or move events.</li>
        <li><strong>Drive:</strong> find and read your Google Docs and Sheets.</li>
        <li><strong>A document of yours:</strong> add text to a Google Doc you've chosen as your target document on the Integrations page, like a running notes file or weekly report.</li>
        <li><strong>Put it together:</strong> for example, read a report attached to an email, summarize it, and add the summary to your notes document.</li>
      </ul>
      <h4>What it won't do</h4>
      <ul>
        <li>It sends an email, adds an event or changes a document only after you approve that exact action on a card.</li>
        <li>It can't delete or archive your email, and it doesn't keep a copy of your mailbox. What it says about an email becomes part of that conversation.</li>
      </ul>
      <h4>Connecting</h4>
      <ul>
        <li>Google may show a notice that the app isn't verified yet. That's expected during early access: choose "Advanced", then continue.</li>
        <li>You can disconnect at any time and it stops right away.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/integrations">Open Integrations</HelpLink></p>
    </>
  ),

  library: ctx => (
    <>
      <p>Your document library is your own private, searchable collection of PDFs and data files. Add a document once, and Sonic can answer from it in any conversation, from now on. It's like giving Sonic a bookshelf of your own.</p>
      <h4>How it works</h4>
      <ul>
        <li>Open Self Service, pick your library, and add one or more PDFs. They take a little while to become searchable.</li>
        <li>Then just ask in the chat: "What's in my lease about pets?", "Which of my documents mention the roof?", "What does my insurance cover for water damage?"</li>
        <li>Sonic answers from your documents and points to the page it used, so you can check it. Pictures inside a PDF are described too, so they can be found.</li>
        <li>If your documents don't have the answer, Sonic says so and answers from what it knows. <strong>Open Answers</strong> is on by default. Turn it off in the "…" menu if you want it to stick strictly to your documents.</li>
      </ul>
      <h4>Data files from other apps</h4>
      <ul>
        <li>You can add <strong>.json</strong> files (and .jsonl, one record per line) the same way, such as an export from another app, a weekly log or a settings file. Sonic reads the file's own structure: each record in a list becomes its own searchable entry, labelled with the file name and the section it came from.</li>
        <li>Ids and timestamps are left out, and anything that looks like a password, token or key is masked before it's stored. Files up to 10 MB are accepted, and a file that can't be read as data is rejected.</li>
        <li>Uploading a file with the same name again <strong>replaces</strong> the earlier one. Search finds things in a data file, but it can't count or add things up across a whole file.</li>
      </ul>
      <h4>What it's good for</h4>
      <ul>
        <li>Leases, contracts, insurance policies and warranties.</li>
        <li>Manuals and instructions you never want to dig through again.</li>
        <li>Course notes, research papers and reports.</li>
        <li>Anything you'd otherwise search with Ctrl+F across ten files.</li>
      </ul>
      <h4>Tips</h4>
      <ul>
        <li>PDFs with real, selectable text work best. A scan is a picture of text, so Sonic may not read it word for word.</li>
        <li>Give files clear names, and keep one subject to a document, so the right page comes up.</li>
        <li>The "Scope" setting in the "…" menu chooses where Sonic looks: everything you can use, or just one library. Some accounts also have shared libraries.</li>
      </ul>
      <h4>Library versus attaching</h4>
      <p>Attaching a file adds it to one conversation, for 30 days. The library keeps a document until you delete it, and every conversation can use it.</p>
      <h4>Privacy and control</h4>
      <ul>
        <li>Your library belongs to your account. Other people's accounts can't see it.</li>
        <li>To make a document searchable, its text, and descriptions of its pictures, are processed by Google's Gemini. When you ask a question, the passages that matter are sent to it to write the answer.</li>
        <li>You can delete any document on the Self Service page, and it stops being searchable.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/self-service">Open Self Service</HelpLink></p>
    </>
  ),

  memory: ctx => (
    <>
      <p>As you talk, Sonic keeps short notes about things that matter, like your name, your plans and what you prefer, and brings the relevant ones into later conversations.</p>
      <ul>
        <li>Ask "What do you remember about me?" to hear what it has.</li>
        <li>The Memory page lists every note, with how sure Sonic is and where it came from. You can <strong>edit</strong> a note if it got something wrong, <strong>remove</strong> it, or clear them all. A note you rewrite counts as something you told Sonic directly, so it won't quietly change it back.</li>
        <li>Notes that belong together are linked ("Goes with"), like where you work and a project there, so when one comes up the other tags along. Remove a link if two notes don't belong together.</li>
        <li><strong>Coming up:</strong> if you mention something ahead, like an interview or a trip, Sonic may ask how it went once the day has passed. They're listed on the Memory page, and you can remove any.</li>
        <li>Notes you never mention again slowly fade, so old information doesn't stick forever.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/memory">Open Memory</HelpLink></p>
    </>
  ),

  profile: ctx => (
    <>
      <p>On the Account page, "About you" lets you tell Sonic about yourself on purpose. Everything is optional, and Sonic uses it quietly without reading it back to you.</p>
      <ul>
        <li><strong>Name and pronouns</strong>, and <strong>what you do</strong> (a job title, or "retired" or "student").</li>
        <li><strong>Interests</strong>, and <strong>topics you'd rather Sonic didn't bring up</strong>. If you raise one yourself, it talks about it normally.</li>
        <li><strong>How you like answers:</strong> short or detailed, direct or warm, how much humor, and how many questions Sonic asks about your life (less, balanced or more).</li>
        <li><strong>Two switches:</strong> whether Sonic may bring something up by itself at a quiet moment, and whether it may start a conversation with one question about something you told it. Both can be turned off.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/account">Open Account settings</HelpLink></p>
    </>
  ),

  'office-hours': ctx => (
    <>
      <p>Office hours is a check-in you hold with Sonic when you choose to, such as a weekly review. You press <strong>Start office hours</strong>, Sonic runs your agenda in a chat, and you press <strong>End and save notes</strong> when you are done. Sonic never starts one by itself.</p>
      <h4>How much Sonic challenges you</h4>
      <ul>
        <li>Each meeting has a setting: <strong>Supportive</strong> (Sonic asks and listens, as before), <strong>Balanced</strong> (it may raise one thing) or <strong>Challenging</strong> (up to two, each with a small next step to try).</li>
        <li>It only raises things your own records show: an action item that has carried over from meeting to meeting, a goal you haven't updated in weeks, what your tasks found, and what you logged. It doesn't guess about you, and it doesn't scold.</li>
        <li>It holds back on a day you seem low, and you can say "not today" at any point and it drops the subject for that meeting.</li>
      </ul>
      <h4>Progress notes</h4>
      <ul>
        <li>Every progress note belongs to one meeting. Between check-ins, log what happens in your own words: open a meeting's <strong>Progress notes</strong> on the Office Hours page, or in any chat write <strong>log to Weekly check-in: applied to three jobs</strong> (or <strong>log a win to Weekly check-in: ...</strong>, <strong>log a blocker to ...</strong>), using the meeting's name. Inside a running meeting, <strong>log: ...</strong> goes to that meeting, and if you have only one meeting, it goes to that one. To append to or replace an existing note, quote part of its text: "add a win to my note 'applied to three jobs': got a second interview" or "update my note 'applied to three jobs': applied to four jobs".</li>
        <li>Sonic saves exactly what you wrote. It doesn't decide for you what counts as progress, and it doesn't grade or comment on your notes.</li>
        <li>When you start a meeting, Sonic has that meeting's notes that no earlier session has covered yet in front of it. It may open with two or three of them, in your words, and ask whether anything is missing. It won't rank them, praise you beyond what you wrote, or claim to see patterns.</li>
        <li>Once a meeting's notes are saved, that meeting's progress notes logged up to then are marked as covered, so next time only the new ones come up. They stay on the page. Other meetings' notes aren't touched, and deleting a meeting deletes its progress notes.</li>
        <li>Ask "what did I log this week?" any time and Sonic reads them back. Edit or delete any note on the Office Hours page. You can log up to 25 a day, and "Forget everything" on the Memory page removes them too.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/meetings">Open Office Hours</HelpLink></p>
    </>
  ),

  'about-sonic': () => <SonicProfileCard />,

  privacy: ctx => (
    <>
      <p>The short version:</p>
      <ul>
        <li>What you type, and anything you attach, is sent to Google's Gemini so Sonic can answer.</li>
        <li>If you set <strong>Code model</strong> to Claude, the code and tool steps of a request (the question, and the code and results Sonic reads while working on it) go to Anthropic instead of Google for those steps. Sonic's reply is still written by Gemini. Set it back to Gemini any time.</li>
        <li>Your conversations are kept until you delete them. Photos and files you attach are removed after 30 days, or sooner if you delete the conversation.</li>
        <li>{ctx.guest
          ? 'Guests share one temporary identity, so nothing here is tied to you.'
          : 'Sonic keeps short notes about you. You can read, fix and remove every one on the Memory page.'}</li>
        {!ctx.guest && <li>Your document library is private to your account, and you can delete any document from it.</li>}
        <li>Sonic only looks at your email, calendar or Drive when you ask, and only changes anything after you approve it.</li>
        <li>Tidy up works entirely on your computer. Nothing from the folder is sent to us.</li>
        <li>We don't sell your data and we don't show ads.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/privacy">Read the full privacy policy</HelpLink></p>
    </>
  ),

  conversations: () => (
    <>
      <p>Your past conversations are listed on the left (on a phone, open the menu at the top). Click one to pick it up where you left off, or "New chat" to start fresh. Each one keeps its own history.</p>
      <ul>
        <li>The bin icon beside a conversation deletes it, along with the photos and files attached to it.</li>
        <li>"Export" in the "…" menu downloads the current conversation as a text file.</li>
        <li>The stop button halts Sonic mid-answer, and a new message while it's working steers it.</li>
        <li>Thumbs up and thumbs down under a reply tell us whether it was good, and why.</li>
        <li>After some answers there's a suggested next question you can tap to send.</li>
      </ul>
    </>
  ),

  menu: () => (
    <>
      <p>The "…" button next to the message box holds the less common controls:</p>
      <ul>
        <li><strong>Conversations:</strong> opens the list of your conversations.</li>
        <li><strong>Trace:</strong> shows the steps Sonic took to answer, such as searching or reading a file.</li>
        <li><strong>Export</strong> and <strong>Delete:</strong> save this conversation as a file, or delete it.</li>
        <li><strong>Scope:</strong> which library Sonic looks in: everything you can use, or just one.</li>
        <li><strong>Open Answers:</strong> on (the default), Sonic can also use its general knowledge when your documents don't have the answer. Off, it sticks to your documents.</li>
        <li><strong>Deep Thinking:</strong> slower but more thorough, for tricky questions with several parts.</li>
        <li><strong>Code model:</strong> which AI does the reasoning steps when Sonic reads code, searches or writes code for you: Gemini or Claude. Sonic's reply is still written by Gemini either way. It only appears when Claude is set up, and it isn't offered in the guest demo.</li>
        <li><strong>Message First:</strong> lets Sonic open a conversation with one question about something you told it. Off until you turn it on.</li>
        <li><strong>SAAPP:</strong> shows or hides the little robot.</li>
      </ul>
    </>
  ),

  trouble: () => (
    <>
      <ul>
        <li><strong>"Signing you in…" for a while:</strong> the server may be waking up, which can take up to a minute. If it says it couldn't reach the server, press "Try again".</li>
        <li><strong>A usage limit message:</strong> Sonic has a daily usage allowance, and each person has their own share. When it's used up, Sonic says when it resets.</li>
        <li><strong>An answer looks wrong:</strong> Sonic can be wrong, especially about numbers and dates. Check important things with the source, and press thumbs down so we can see it.</li>
        <li><strong>A photo won't attach:</strong> see "Photos, files and spreadsheets" for the formats that work.</li>
        <li><strong>Tidy up won't open a folder:</strong> some browsers protect folders like Downloads. Try a folder inside it.</li>
        <li><strong>A Word document edit says it couldn't be made:</strong> the document may have changed since Sonic read it, or part of it can't be edited safely (like tracked changes). Nothing was written. Ask again after saving your changes.</li>
        <li><strong>Sonic says it can't find something in your documents, but it's something general:</strong> check that <strong>Open Answers</strong> is on in the "…" menu. With it off, Sonic sticks to your documents.</li>
        <li><strong>Sonic says it can't find something that should be in your library:</strong> check the document finished processing, and that "Scope" in the "…" menu includes that library.</li>
      </ul>
    </>
  ),
};

// ---- For developers -------------------------------------------------------------------------------------------------------------------------
export const DEVELOPER: Record<string, Content> = {
  'dev-github': () => (
    <>
      <p>Ask Sonic to look at real code in a GitHub repository. It can chain several lookups in one turn to check an answer instead of guessing.</p>
      <ul>
        <li>List a repo's files, read specific files, compare two branches, or list recent commits.</li>
        <li>"What does agent_workflow.py do?", "What changed in the last PR?", "Find the file that handles X."</li>
        <li>It reports what it actually checked. If a lookup fails or comes back empty, it says so.</li>
        <li>Add your own GitHub token on the Integrations page so it works with your private repos.</li>
      </ul>
    </>
  ),

  'dev-prs': () => (
    <>
      <p>Sonic can draft a GitHub Pull Request or Issue, but never submits one without your approval.</p>
      <ul>
        <li>"Create a PR to merge feature/x into main" or "Open an issue about the login bug."</li>
        <li>It writes a title and description and shows you a card first.</li>
        <li>Reply "approve" to submit it, or "reject" to drop it. Nothing is written to GitHub until you say so.</li>
      </ul>
    </>
  ),

  'dev-sandbox': () => (
    <>
      <p>Sonic can write and run small, self-contained Python snippets to check its own logic before answering. They really run, in an isolated sandbox.</p>
      <ul>
        <li>Useful for calculations, checking a regex or algorithm, or trying a proposed fix on a small example.</li>
        <li>The sandbox has no files, no network, and no access to your project, and only a small set of safe standard-library modules.</li>
        <li>It can't run your actual application, only logic it can reproduce in the snippet.</li>
      </ul>
    </>
  ),

  'dev-folder': () => (
    <>
      <p>The folder button at the left of the message box (it turns orange while a folder is connected) lets Sonic answer about your real, uncommitted code on a laptop or desktop in Chrome or Edge.</p>
      <ul>
        <li>The text files in the folder are copied to Sonic's server and sent to the AI model. Secrets such as .env files and keys, and folders like node_modules and .git, are never uploaded.</li>
        <li>Access starts read-only. Sonic can propose changes, and they are written only when you press Apply. You can undo them.</li>
        <li><strong>Word documents:</strong> a .docx in the folder is turned into a numbered text outline on the server (the file itself is not kept), so Sonic can read it. It can write you a new Word document, or an edited copy next to an existing one. Every part of the copy that wasn't named in an edit is checked to be identical to the original, and the original is never written. Paragraphs with tracked changes, fields or text boxes can't be edited.</li>
        <li>CSV, log, calendar and similar plain-text files are read too.</li>
        <li>Disconnecting deletes the server copy, and it expires on its own after about 12 hours.</li>
      </ul>
    </>
  ),

  'dev-modes': () => (
    <>
      <ul>
        <li><strong>Deep Thinking</strong> uses a stronger model and more steps for questions that need real investigation. It's slower and costs more, so it's off by default.</li>
        <li><strong>Target Repo</strong> (the branch button at the left of the message box) pins a GitHub "owner/repo" so Sonic always knows which repo you mean. It persists until you change or clear it, and naming another repo in a message overrides it for that message.</li>
        <li><strong>Open Answers</strong> (on by default) lets it use general knowledge beyond your indexed documents. Turn it off to keep answers strictly to them.</li>
      </ul>
    </>
  ),

  'dev-access': () => (
    <>
      <p>Sonic keeps people's data separate.</p>
      <ul>
        <li>Each signed-in account has its own conversations, memory, settings and private document library, and nothing is shared between accounts.</li>
        <li>Shared knowledge bases (the demo ones) are open only to the accounts that have been given access to them.</li>
        <li>Searches are limited to the knowledge bases your account can use, at the database query itself, so another team's documents are never retrieved.</li>
        <li>Guests share one temporary identity with no personal data.</li>
      </ul>
    </>
  ),

  'dev-built': () => (
    <>
      <p>Sonic is a LangGraph-based multi-agent workflow behind a FastAPI service, with a React front end.</p>
      <ul>
        <li><strong>Reasoner:</strong> a small model reads each message and decides what it needs: documents, tools, memory, a write that needs approval, or just conversation.</li>
        <li><strong>Retrieval:</strong> hybrid search over your knowledge bases in MongoDB, with grading and query rewriting.</li>
        <li><strong>Tool agent:</strong> one research loop covering GitHub, the code sandbox, web search, calendar, email and Drive, able to chain several steps per turn.</li>
        <li><strong>Memory:</strong> facts are saved and recalled passively, with decay over time.</li>
        <li><strong>Voice:</strong> one reply prompt for every path, so Sonic sounds the same whatever it used.</li>
        <li><strong>Cost control:</strong> every model call is metered, and a budget guard slows optional extras before it blocks anything.</li>
      </ul>
      <p>Known limit: it can still make mistakes when it suggests code.</p>
    </>
  ),
};

export const CONTENT: Record<string, Content> = { ...EVERYDAY, ...DEVELOPER };
