import React from 'react';
import './__styles__/Legal.css';

export const PrivacyPage: React.FC = () => {
  return (
    <div className="legal-container">
      <header className="legal-header">
        <h1>Privacy Policy</h1>
        <p className="legal-updated">Last updated: September 2026</p>
      </header>

      <section className="legal-body">
        <p>
          Sonic Assistant ("SAAPP," "we," "us") is a personal AI assistant built and operated by
          Jack Harper / Sonic Graph Technologies. This page explains what information we collect,
          how we use it, and the choices you have — including specifically how we use Google
          account data if you connect one.
        </p>

        <h2>1. Information we collect</h2>
        <ul>
          <li><strong>Account identity:</strong> when you sign in, we receive your email address and a unique account identifier from our authentication provider (Clerk).</li>
          <li><strong>Conversation data:</strong> the messages you send the assistant, and the assistant's responses, so we can maintain conversation history and answer follow-up questions.</li>
          <li><strong>Documents you upload:</strong> if you use the Self Service knowledge-base feature, uploaded files are processed and indexed so the assistant can answer questions about them.</li>
          <li><strong>Guest sessions:</strong> a "guest" sandbox mode is available that does not require a real account; guest sessions share a temporary, non-personal identity and are not tied to any individual.</li>
        </ul>

        <h2>2. Google account data (Calendar, and services we may add later)</h2>
        <p>
          If you choose to connect your Google account under Integrations, we request the following
          scopes and use them only for the stated purpose:
        </p>
        <ul>
          <li><strong>Calendar events (<code>calendar.events</code>):</strong> to read your calendar when you ask the assistant about your schedule, and to create or update events on your calendar when you explicitly ask the assistant to schedule or change something. We never read or modify your calendar without you asking.</li>
          <li><strong>Basic profile (<code>openid</code>, <code>userinfo.email</code>):</strong> to show you which Google account is connected, so you always know exactly what's linked.</li>
        </ul>
        <p>
          Your Google access and refresh tokens are encrypted at rest and are never shared with any
          third party. You can disconnect your Google account at any time from the Integrations
          page — this revokes our access with Google and deletes the stored tokens immediately.
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
        <p>
          If we add Gmail or Drive features in the future, this policy will be updated first to
          describe exactly what new scopes are requested and why, before any such feature is
          enabled for your account.
        </p>

        <h2>3. How we store and protect your data</h2>
        <p>
          Data is stored in a MongoDB database. Sensitive credentials, including Google OAuth
          tokens, are encrypted before being stored. We do not sell your data to anyone, and we do
          not use your conversation content or documents to train third-party models beyond what's
          necessary to generate a response to you.
        </p>

        <h2>4. Third parties we rely on</h2>
        <p>
          We use Clerk for authentication, MongoDB Atlas for storage, one or more large-language-model
          providers to generate responses, and standard hosting/error-monitoring infrastructure to
          keep the service running. Each of these providers only receives the minimum data needed to
          perform their function for us.
        </p>

        <h2>5. Your choices</h2>
        <ul>
          <li>Disconnect any connected Google account at any time from Integrations.</li>
          <li>Delete individual conversations from the Conversations panel.</li>
          <li>Contact us (below) to request deletion of your account and associated data.</li>
        </ul>

        <h2>6. Children's privacy</h2>
        <p>SAAPP is not directed at children under 13, and we do not knowingly collect data from them.</p>

        <h2>7. Changes to this policy</h2>
        <p>
          If we change how we handle your data — especially if we add new Google scopes or new
          third-party integrations — we will update this page and the "Last updated" date above.
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
