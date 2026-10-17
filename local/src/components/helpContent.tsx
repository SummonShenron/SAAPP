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
        <li>Guests share one temporary identity, so nothing here is personal and nothing is remembered about you.</li>
        <li>Sign in to keep your own conversations, memory and settings, and to connect your own email and calendar.</li>
      </ul>
    </>
  ) : (
    <>
      <p>Sonic is an assistant you can talk to like a person. It can:</p>
      <ul>
        <li><strong>Answer questions</strong> and look things up on the web, saying where the information came from.</li>
        <li><strong>Read what you attach:</strong> a photo of a letter or bill, a PDF, a Word document or a spreadsheet, and tell you what it says in plain words.</li>
        <li><strong>Help with your email and calendar</strong> once you connect Google. It asks before it sends or changes anything.</li>
        <li><strong>Remember what matters to you</strong>, so you don't have to say it twice.</li>
        <li><strong>Think things through with you</strong> or draft something for you to look over.</li>
        <li><strong>Tidy up a messy folder</strong> on your computer, with your approval.</li>
      </ul>
      <p>If a question is unclear, Sonic asks you instead of guessing. And it can be wrong, so check anything important, like dates, amounts, and medical or legal details, with the source.</p>
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
      </ul>
      <p>Open the <strong>Trace</strong> in the "…" menu to watch the steps Sonic takes to answer.</p>
    </>
  ) : (
    <>
      <p>Just type it the way you'd say it. For example:</p>
      <ul>
        <li><strong>A letter or bill:</strong> attach a photo and ask "What does this say, and do I need to do anything?"</li>
        <li><strong>Is it a scam?</strong> "I got this text. Is it real?" Paste it or attach a screenshot.</li>
        <li><strong>A spreadsheet:</strong> attach it and ask "How much did I spend on groceries?"</li>
        <li><strong>Your day:</strong> "What's on my calendar tomorrow?" or "Any emails I need to answer?" (after you connect Google)</li>
        <li><strong>Looking something up:</strong> "Look into the best way to learn Spanish and show me your sources."</li>
        <li><strong>A decision:</strong> "Help me think through whether to take this job."</li>
        <li><strong>Writing:</strong> "Draft a polite email asking to move my appointment."</li>
        <li><strong>About Sonic:</strong> "What do you remember about me?" or "What have you been reading lately?"</li>
      </ul>
    </>
  ),

  attach: () => (
    <>
      <p>Press the <strong>+</strong> next to the message box to attach something. On a phone it offers to take a photo, pick one from your photos, or choose a file. You can also paste a screenshot straight into the box.</p>
      <ul>
        <li><strong>Works:</strong> photos and screenshots, PDFs, Word documents (.docx), Excel and CSV spreadsheets, and plain text files.</li>
        <li><strong>Spreadsheets:</strong> Sonic sees how big the sheet is, its column names, its first rows, and totals across every row, including how much per category. On a big sheet it tells you when it can't see the rest, rather than guessing.</li>
        <li><strong>Doesn't work yet:</strong> old .xls, .doc and .ppt files, and Pages, Numbers and Keynote files. Save them as .xlsx, .docx or PDF and try again.</li>
        <li><strong>iPhone photos:</strong> if a photo won't attach because of its format (HEIC), attach a screenshot of it instead, or set the camera to "Most Compatible" in Settings, Camera, Formats.</li>
        <li><strong>For a letter:</strong> a clear, well-lit photo of the whole page works best.</li>
      </ul>
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
      <p><HelpLink go={ctx.go} path="/tidy">Open Tidy up</HelpLink></p>
    </>
  ),

  google: ctx => (
    <>
      <p>Connect your Google account and Sonic can look at your calendar, search and summarize your email, and find your files in Drive when you ask.</p>
      <ul>
        <li>It only sends an email, adds an event or changes a document after you approve that exact action.</li>
        <li>It doesn't keep a copy of your mailbox. What it says about an email becomes part of that conversation.</li>
        <li>Google may show a notice that the app isn't verified yet. That's expected during early access: choose "Advanced", then continue.</li>
        <li>You can disconnect at any time and it stops right away.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/integrations">Open Integrations</HelpLink></p>
    </>
  ),

  library: ctx => (
    <>
      <p>Your document library is a private collection of PDFs that Sonic can answer from in any conversation. It's separate from attaching a file to one chat, which only lasts for that conversation.</p>
      <ul>
        <li>Add PDFs on the Self Service page. They can take a little while to become searchable.</li>
        <li>Then just ask, for example "What's in my lease?" or "Which of my documents mention the roof?"</li>
        <li>The "Scope" setting in the "…" menu chooses which library Sonic looks in.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/self-service">Open Self Service</HelpLink></p>
    </>
  ),

  memory: ctx => (
    <>
      <p>As you talk, Sonic keeps short notes about things that matter, like your name, your plans and what you prefer, and brings the relevant ones into later conversations.</p>
      <ul>
        <li>Ask "What do you remember about me?" to hear what it has.</li>
        <li>The Memory page lists every note. You can remove any of them, or clear them all.</li>
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
        <li><strong>How you like answers:</strong> short or detailed, direct or warm, and how much humor.</li>
        <li><strong>Two switches:</strong> whether Sonic may bring something up by itself at a quiet moment, and whether it may start a conversation with one question about something you told it. Both can be turned off.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/account">Open Account settings</HelpLink></p>
    </>
  ),

  'about-sonic': () => <SonicProfileCard />,

  privacy: ctx => (
    <>
      <p>The short version:</p>
      <ul>
        <li>What you type, and anything you attach, is sent to Google's Gemini so Sonic can answer.</li>
        <li>Your conversations are kept until you delete them. Photos and files you attach are removed after 30 days, or sooner if you delete the conversation.</li>
        <li>{ctx.guest
          ? 'Guests share one temporary identity, so nothing here is tied to you.'
          : 'Sonic keeps short notes about you. You can read and remove every one on the Memory page.'}</li>
        <li>Sonic only looks at your email, calendar or Drive when you ask, and only changes anything after you approve it.</li>
        <li>Tidy up works entirely on your computer. Nothing from the folder is sent to us.</li>
        <li>We don't sell your data and we don't show ads.</li>
      </ul>
      <p><HelpLink go={ctx.go} path="/privacy">Read the full privacy policy</HelpLink></p>
    </>
  ),

  conversations: () => (
    <>
      <p>Your past conversations are listed on the left (on a phone, open the menu at the top). Click one to pick it up where you left off, or "New chat" to start fresh.</p>
      <ul>
        <li>The bin icon beside a conversation deletes it, along with the photos and files attached to it.</li>
        <li>"Export" in the "…" menu downloads the current conversation as a text file.</li>
        <li>Thumbs up and thumbs down under a reply tell us whether it was good, and why.</li>
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
        <li><strong>Open Answers:</strong> on, Sonic can also use its general knowledge when your documents don't have the answer. Off, it sticks to your documents.</li>
        <li><strong>Deep Thinking:</strong> slower but more thorough, for tricky questions with several parts.</li>
        <li><strong>Message First:</strong> lets Sonic open a conversation with one question about something you told it. Off until you turn it on.</li>
        <li><strong>SAAPP:</strong> shows or hides the little robot.</li>
      </ul>
    </>
  ),

  trouble: () => (
    <>
      <ul>
        <li><strong>"Signing you in…" for a while:</strong> the server may be waking up, which can take up to a minute. If it says it couldn't reach the server, press "Try again".</li>
        <li><strong>A usage limit message:</strong> Sonic has a usage allowance. When it's used up, Sonic says when it resets.</li>
        <li><strong>An answer looks wrong:</strong> Sonic can be wrong, especially about numbers and dates. Check important things with the source, and press thumbs down so we can see it.</li>
        <li><strong>A photo won't attach:</strong> see "Photos, files and spreadsheets" for the formats that work.</li>
        <li><strong>Tidy up won't open a folder:</strong> some browsers protect folders like Downloads. Try a folder inside it.</li>
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
      <p>"Connect local folder" in the "…" menu lets Sonic answer about your real, uncommitted code on a laptop or desktop in Chrome or Edge.</p>
      <ul>
        <li>The text files in the folder are copied to Sonic's server and sent to the AI model. Secrets such as .env files and keys, and folders like node_modules and .git, are never uploaded.</li>
        <li>Access starts read-only. Sonic can propose changes, and they are written only when you press Apply. You can undo them.</li>
        <li>Disconnecting deletes the server copy, and it expires on its own after about 12 hours.</li>
      </ul>
    </>
  ),

  'dev-modes': () => (
    <>
      <ul>
        <li><strong>Deep Thinking</strong> uses a stronger model and more steps for questions that need real investigation. It's slower and costs more, so it's off by default.</li>
        <li><strong>Target Repo</strong> pins a GitHub "owner/repo" so Sonic always knows which repo you mean. It persists until you change or clear it, and naming another repo in a message overrides it for that message.</li>
        <li><strong>Open Answers</strong> lets it use general knowledge beyond your indexed documents.</li>
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
