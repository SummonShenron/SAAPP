import { useState } from 'react';
import type { ApplyResult, UndoResult } from '../localEdits';
import type { EditProposal } from '../localEditsCore';
import './__styles__/LocalEditCard.css';

type Phase = 'pending' | 'applying' | 'applied' | 'undoing' | 'undone';

interface LocalEditCardProps {
  proposal: EditProposal;
  // False when no folder is connected in this browser (e.g. after a reload on another machine).
  canApply: boolean;
  onApply: (proposal: EditProposal) => Promise<ApplyResult>;
  onUndo: (proposalId: string) => Promise<UndoResult>;
}

function diffLineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'led-line-meta';
  if (line.startsWith('@@')) return 'led-line-hunk';
  if (line.startsWith('+')) return 'led-line-add';
  if (line.startsWith('-')) return 'led-line-del';
  return '';
}

/**
 * A change Sonic proposes to the user's connected local folder, shown as a diff. Nothing is
 * written until Apply is pressed, and an applied change can be undone in one click.
 */
export default function LocalEditCard({ proposal, canApply, onApply, onUndo }: LocalEditCardProps) {
  const [phase, setPhase] = useState<Phase>('pending');
  const [errors, setErrors] = useState<string[]>([]);
  const [openFiles, setOpenFiles] = useState<Set<string>>(() => new Set(proposal.files.map(f => f.path)));

  const additions = proposal.files.reduce((sum, f) => sum + f.additions, 0);
  const deletions = proposal.files.reduce((sum, f) => sum + f.deletions, 0);

  const apply = async () => {
    setPhase('applying');
    setErrors([]);
    const result = await onApply(proposal);
    if (result.ok) {
      setPhase('applied');
    } else {
      setErrors(result.errors);
      setPhase('pending');
    }
  };

  const undo = async () => {
    setPhase('undoing');
    setErrors([]);
    const result = await onUndo(proposal.proposal_id);
    if (result.ok) {
      setPhase('undone');
    } else {
      setErrors(result.errors);
      setPhase('applied');
    }
  };

  const toggleFile = (path: string) => {
    setOpenFiles(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <div className="led-card">
      <div className="led-header">
        <div className="led-title">
          <span>Proposed changes to <strong>{proposal.folder}</strong></span>
          <span className="led-stats">
            {proposal.files.length} file{proposal.files.length === 1 ? '' : 's'}{' '}
            <span className="led-add">+{additions}</span> <span className="led-del">−{deletions}</span>
          </span>
        </div>
        {proposal.summary && <div className="led-summary">{proposal.summary}</div>}
      </div>

      {proposal.files.map(file => (
        <div key={file.path} className="led-file">
          <button type="button" className="led-file-header" onClick={() => toggleFile(file.path)}>
            <span>{openFiles.has(file.path) ? '▾' : '▸'} {file.path}</span>
            <span className="led-file-kind">{file.kind === 'create' ? 'new file' : `+${file.additions} −${file.deletions}`}</span>
          </button>
          {openFiles.has(file.path) && (
            <pre className="led-diff">
              {file.diff.split('\n').map((line, index) => (
                <div key={index} className={diffLineClass(line)}>{line || ' '}</div>
              ))}
            </pre>
          )}
        </div>
      ))}

      {errors.length > 0 && (
        <div className="led-errors" role="alert">
          {errors.map((error, index) => <div key={index}>{error}</div>)}
        </div>
      )}

      <div className="led-actions">
        {(phase === 'pending' || phase === 'applying') && (
          <>
            <button type="button" className="led-btn led-btn-primary" onClick={apply} disabled={phase === 'applying' || !canApply}>
              {phase === 'applying' ? 'Applying…' : 'Apply changes'}
            </button>
            {!canApply && <span className="led-note">Connect your local folder (… menu) to apply these.</span>}
          </>
        )}
        {(phase === 'applied' || phase === 'undoing') && (
          <>
            <span className="led-note led-ok">Applied to your folder.</span>
            <button type="button" className="led-btn" onClick={undo} disabled={phase === 'undoing'}>
              {phase === 'undoing' ? 'Undoing…' : 'Undo'}
            </button>
          </>
        )}
        {phase === 'undone' && <span className="led-note">Undone — your files are back as they were.</span>}
      </div>
    </div>
  );
}
