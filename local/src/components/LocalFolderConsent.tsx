import './__styles__/LocalFolderConsent.css';

interface LocalFolderConsentProps {
  onCancel: () => void;
  // Wired to a button the user clicks inside this dialog, so the browser's folder picker opens
  // from a fresh click (it refuses to open without one).
  onAccept: () => void;
}

/**
 * Shown once, before the first "Connect local folder": says plainly what leaves the browser and
 * where it goes, since a folder of source code is more sensitive than a chat message.
 */
export default function LocalFolderConsent({ onCancel, onAccept }: LocalFolderConsentProps) {
  return (
    <div className="lfc-overlay" role="dialog" aria-modal="true" aria-labelledby="lfc-title">
      <div className="lfc-dialog">
        <h3 id="lfc-title" className="lfc-title">Connect a local folder?</h3>
        <p className="lfc-lead">
          Sonic can then answer questions about your real, uncommitted work. Here is exactly what that means:
        </p>
        <ul className="lfc-list">
          <li>
            <strong>Text files are uploaded.</strong> The code, docs and config in the folder you pick are copied
            to Sonic's server when you connect and refreshed as you chat, and their contents are sent to the AI
            model (Google Gemini) that writes Sonic's replies.
          </li>
          <li>
            <strong>Secrets are skipped.</strong> Files that look like secrets (<code>.env</code>, keys,
            credentials), plus <code>node_modules</code>, <code>.git</code> and build folders, are never uploaded.
          </li>
          <li>
            <strong>Nothing changes on your computer by itself.</strong> Access starts read-only. Sonic can only
            propose changes; they are written only if you press Apply, and you can undo them.
          </li>
          <li>
            <strong>You stay in control.</strong> Disconnect any time from the “…” menu and the server copy is
            deleted. It also expires on its own after about 12 hours.
          </li>
        </ul>
        <div className="lfc-actions">
          <button type="button" className="lfc-btn" onClick={onCancel}>Cancel</button>
          <button type="button" className="lfc-btn lfc-btn-primary" onClick={onAccept}>
            I understand — choose folder
          </button>
        </div>
      </div>
    </div>
  );
}
