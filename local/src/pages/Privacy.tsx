import React from 'react';
import './__styles__/Legal.css';

export const PrivacyPage: React.FC = () => {
  return (
    <div className="legal-container">
      <header className="legal-header">
        <h1>Privacy Policy</h1>
        <p className="legal-updated">Last updated: October 2026</p>
      </header>

      <section className="legal-body">
        <p>
          Sonic Assistant ("SAAPP," "we," "us") is a personal AI assistant built and operated by
          Jack Harper / Sonic Graph Technologies. This page explains, in plain terms, what we keep,
          who sees it, and how you can remove it, including exactly how we use Google account data
          if you connect one.
        </p>

        <h2>The short version</h2>
        <ul>
          <li>What you type, and any photos or files you attach, are sent to Google's Gemini service so Sonic can write its replies.</li>
          <li>Your conversations are kept until you delete them. The photos and files you attach are removed automatically after 30 days, or sooner if you delete the conversation.</li>
          <li>Sonic keeps short notes about you (preferences, plans, things you told it) so it can help you better. You can read and remove every one on the Memory page.</li>
          <li>Sonic only looks at your email, calendar or Drive when you ask. It sends an email, adds an event or edits a document only after you approve that specific action.</li>
          <li>We don't sell your data and we don't show ads.</li>
        </ul>

        <h2>1. Information we collect</h2>
        <ul>
          <li><strong>Account identity:</strong> when you sign in, we receive your email address and a unique account identifier from our authentication provider (Clerk).</li>
          <li><strong>Conversations:</strong> the messages you send the assistant and its replies, so we can keep your history and answer follow-up questions.</li>
          <li><strong>Photos and files you attach in a chat:</strong> the text Sonic reads out of each file, and for photos the image itself, are kept with that conversation for 30 days so you can come back to it, and then removed (the conversation itself stays). A photo of a letter or bill can contain personal details, so only attach what you're comfortable with.</li>
          <li><strong>What Sonic remembers:</strong> short notes drawn from your conversations (for example, a preference or something you're working on). They are listed on the Memory page, where you can remove any of them or all of them.</li>
          <li><strong>What you tell Sonic about yourself:</strong> the optional details on your Account page (name, pronouns, what you do, interests, how you like answers, topics to avoid).</li>
          <li><strong>Documents you add to your knowledge base:</strong> if you use Self Service, uploaded files are processed and indexed so the assistant can answer questions about them.</li>
          <li><strong>A connected folder:</strong> if you choose to connect one, its text files are copied to our server so Sonic can read them. The copy is deleted when you disconnect, and expires on its own after about 12 hours. Sonic only changes your files when you press Apply, and you can undo it. A Word document in the folder is turned into a plain-text outline on our server so Sonic can read it, and the file itself is not kept. If you ask Sonic to write or edit a Word document, the text (and, for an edit, the document) passes through our server once to make the new copy; that step does not store it, and your original is never changed.</li>
          <li><strong>Tidy up:</strong> the Tidy up page works entirely in your browser, on a folder you choose. To make its plan it looks at file names, sizes and dates; nothing from that folder is sent to us.</li>
          <li><strong>Guest sessions:</strong> a "guest" sandbox mode does not require a real account. Guests share one temporary, non-personal identity and are not tied to any individual.</li>
        </ul>

        <h2>2. Google account data</h2>
        <p>
          If you choose to connect your Google account under Integrations, we request the following
          permissions and use them only for the purpose described:
        </p>
        <ul>
          <li><strong>Calendar events (<code>calendar.events</code>):</strong> to read your calendar when you ask about your schedule, and to create or change events only when you explicitly ask and approve.</li>
          <li><strong>Gmail, read (<code>gmail.readonly</code>):</strong> to search and summarize your email when you ask. Sonic does not copy your mailbox; it reads the specific messages needed to answer your request.</li>
          <li><strong>Gmail, send (<code>gmail.send</code>):</strong> to send an email only when you ask for one and approve it before it goes out. Sonic cannot delete, archive, or change your email.</li>
          <li><strong>Google Drive (<code>drive</code>) and Google Docs (<code>documents</code>):</strong> to find and read your files when you ask, and to add to a document you've chosen only when you ask and approve.</li>
          <li><strong>Basic profile (<code>openid</code>, <code>userinfo.email</code>):</strong> to show you which Google account is connected.</li>
        </ul>
        <p>
          What Sonic says about an email, event or file, and anything it quotes from one, becomes part
          of that conversation like any other message, and is removed when you delete the conversation.
          Your Google access and refresh tokens are encrypted when stored and are never shared with
          any third party. You can disconnect Google at any time from the Integrations page; this
          revokes our access with Google and deletes the stored tokens immediately.
        </p>
        <p>
          <strong>Limited Use disclosure:</strong> SAAPP's use and transfer of information received
          from Google APIs adheres to the{' '}
          <a href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noopener noreferrer">
            Google API Services User Data Policy
          </a>, including the Limited Use requirements. We do not use Google user data for
          advertising, and we do not allow humans to read this data except as necessary to provide
          the specific feature you requested, to comply with law, or to investigate abuse.
        </p>

        <h2>3. How we store and protect your data</h2>
        <p>
          Data is stored in a MongoDB database. Sensitive credentials, including Google OAuth
          tokens and any GitHub token you save, are encrypted before being stored. We do not sell
          your data. Our logs are kept free of the content of your messages and files.
        </p>

        <h2>4. Third parties we rely on</h2>
        <p>
          We use Clerk for sign-in, MongoDB Atlas for storage, Google's Gemini API to generate
          responses and to read photos and files you attach, and standard hosting and error-monitoring
          services to keep the service running. Each provider receives only what it needs to do its
          job for us. We do not use your conversations or documents to train models ourselves.
        </p>

        <h2>5. How long we keep things, and your choices</h2>
        <ul>
          <li><strong>Conversations:</strong> kept until you delete them. Delete a conversation from the Conversations panel; the text read from its attached files and any stored photos are removed with it.</li>
          <li><strong>Photos and files you attach:</strong> removed automatically 30 days after you send them, even if the conversation stays.</li>
          <li><strong>Memory:</strong> review, edit or remove any note, or clear all of it, on the Memory page.</li>
          <li><strong>Your Account details:</strong> change or clear any of them on the Account page.</li>
          <li><strong>Google:</strong> disconnect any time from Integrations.</li>
          <li><strong>Everything:</strong> to have your account and all associated data deleted, contact us (below).</li>
        </ul>

        <h2>6. Children's privacy</h2>
        <p>SAAPP is not directed at children under 13, and we do not knowingly collect data from them.</p>

        <h2>7. Changes to this policy</h2>
        <p>
          If we change how we handle your data, especially if we add new Google permissions or new
          third-party integrations, we will update this page and the "Last updated" date above.
        </p>

        <h2>8. Contact</h2>
        <p>
          Questions about this policy or your data: <a href="mailto:jackharper0517@outlook.com">jackharper0517@outlook.com</a>
        </p>
      </section>
    </div>
  );
};

export default PrivacyPage;
