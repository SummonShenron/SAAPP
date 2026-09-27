import React from 'react';
import './__styles__/Legal.css';

export const TermsPage: React.FC = () => {
  return (
    <div className="legal-container">
      <header className="legal-header">
        <h1>Terms of Service</h1>
        <p className="legal-updated">Last updated: September 2026</p>
      </header>

      <section className="legal-body">
        <p>
          These Terms govern your use of Sonic Assistant ("SAAPP"), operated by Jack Harper /
          Sonic Graph Technologies. By signing in or using the guest sandbox, you agree to these
          Terms.
        </p>

        <h2>1. The service</h2>
        <p>
          SAAPP is a personal AI assistant that can answer questions, search connected knowledge
          bases, and — when you explicitly ask and grant access — take actions on your behalf,
          such as reading or scheduling events on a Google Calendar you've connected. SAAPP is
          provided on a best-effort basis and may be unavailable at times, including during
          third-party outages (e.g. the language model or Google APIs it depends on).
        </p>

        <h2>2. Your account and connected services</h2>
        <p>
          You're responsible for keeping your sign-in credentials secure. If you connect a Google
          account, you're granting SAAPP permission to act on that account only as described in our{' '}
          <a href="/#/privacy">Privacy Policy</a> — you can revoke this at any time from Integrations
          or directly from your Google Account settings.
        </p>

        <h2>3. Acceptable use</h2>
        <p>You agree not to:</p>
        <ul>
          <li>Use SAAPP to violate any law, or to access data or accounts you're not authorized to access.</li>
          <li>Attempt to circumvent rate limits, security controls, or the isolation between different users' data.</li>
          <li>Use SAAPP's connected-service actions (e.g. calendar writes) to send spam, harass others, or cause harm.</li>
          <li>Reverse-engineer or resell access to the service without permission.</li>
        </ul>

        <h2>4. Google API Services compliance</h2>
        <p>
          SAAPP's use of Google user data, for any Google service it connects to, adheres to the{' '}
          <a href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noopener noreferrer">
            Google API Services User Data Policy
          </a>, including the Limited Use requirements described in our Privacy Policy.
        </p>

        <h2>5. No warranty</h2>
        <p>
          SAAPP is provided "as is," without warranties of any kind. Responses from the assistant
          may occasionally be inaccurate — you're responsible for verifying anything important
          before relying on it, especially actions with real-world effects like scheduling an event.
        </p>

        <h2>6. Limitation of liability</h2>
        <p>
          To the fullest extent permitted by law, Jack Harper / Sonic Graph Technologies is not
          liable for indirect, incidental, or consequential damages arising from your use of SAAPP.
        </p>

        <h2>7. Termination</h2>
        <p>
          We may suspend or terminate access for violation of these Terms. You may stop using
          SAAPP, and disconnect any connected accounts, at any time.
        </p>

        <h2>8. Changes to these Terms</h2>
        <p>We may update these Terms as the service evolves; continued use after an update means you accept the revised Terms.</p>

        <h2>9. Contact</h2>
        <p>Questions about these Terms: <a href="mailto:jackharper0517@outlook.com">jackharper0517@outlook.com</a></p>
      </section>
    </div>
  );
};

export default TermsPage;
