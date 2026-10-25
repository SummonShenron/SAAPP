import { useEffect, useRef, useState } from 'react';
import type { ApplyResult, UndoResult } from '../localEdits';
import type { EditProposal } from '../localEditsCore';
import './__styles__/LocalEditCard.css';

type Phase = 'pending' | 'applying' | 'applied' | 'undoing' | 'undone' | 'declined';

interface LocalEditCardProps {
  proposal: EditProposal;
  // False when no folder is connected in this browser (e.g. after a reload on another machine).
  canApply: boolean;
  onApply: (proposal: EditProposal) => Promise<ApplyResult>;
  onUndo: (proposalId: string) => Promise<UndoResult>;
  /** Reports Apply (after the files were written) or Decline to the turn that is waiting on this proposal. */
  onDecision?: (proposalId: string, outcome: 'applied' | 'declined', reason: string) => void;
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
export default function LocalEditCard({ proposal, canApply, onApply, onUndo, onDecision }: LocalEditCardProps) {
  const [phase, setPhase] = useState<Phase>('pending');
  const [declining, setDeclining] = useState(false);
  const [reason, setReason] = useState('');
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
      onDecision?.(proposal.proposal_id, 'applied', '');      // always: the server notes the files as work waiting to be committed, whether or not a turn is waiting
    } else {
      setErrors(result.errors);
      setPhase('pending');
    }
  };

  const decline = () => {
    setPhase('declined');
    setDeclining(false);
    if (proposal.awaiting) onDecision?.(proposal.proposal_id, 'declined', reason.trim());
  };

  // An approval level other than "ask" (src/editApproval.ts) applies this one as soon as the card appears, exactly as if the button had been pressed: the files are written,
  // the waiting turn is told, and Undo stays. Once only, and only when there is a folder to write to.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (proposal.auto && canApply && !autoStarted.current) {
      autoStarted.current = true;
      void apply();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
            <span className="led-file-kind">
              {file.format === 'docx_edit'
                ? `+${file.additions} −${file.deletions} · edited copy`
                : file.format === 'docx' ? 'new Word document' : file.kind === 'create' ? 'new file' : `+${file.additions} −${file.deletions}`}
            </span>
          </button>
          {openFiles.has(file.path) && file.format === 'docx_edit' && (
            <div className="led-note led-preview-note">
              A new copy of <strong>{file.source}</strong> with these changes. Your original is never changed, and everything else in the copy is checked to be identical to it.
            </div>
          )}
          {openFiles.has(file.path) && file.format === 'docx' && (
            <div className="led-note led-preview-note">A preview of the text. The Word file itself is made when you press Apply, and your original files are never touched.</div>
          )}
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
            {proposal.awaiting && phase === 'pending' && !declining && (
              <button type="button" className="led-btn" onClick={() => setDeclining(true)}>Decline</button>
            )}
            {!canApply && <span className="led-note">Connect your local folder (… menu) to apply these.</span>}
            {proposal.autoSkipReason && <span className="led-note">Asking because {proposal.autoSkipReason}.</span>}
          </>
        )}
        {phase === 'pending' && declining && (
          <>
            <input className="led-reason" type="text" maxLength={300} autoFocus value={reason} aria-label="Why decline? (optional)"
              placeholder="Why? (optional) Sonic will adjust" onChange={e => setReason(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') decline(); }} />
            <button type="button" className="led-btn" onClick={decline}>Decline</button>
            <button type="button" className="led-btn" onClick={() => setDeclining(false)}>Back</button>
          </>
        )}
        {phase === 'declined' && <span className="led-note">Declined. Nothing was changed{proposal.awaiting ? '; Sonic will carry on from that.' : '.'}</span>}
        {(phase === 'applied' || phase === 'undoing') && (
          <>
            <span className="led-note led-ok">{proposal.auto ? 'Applied to your folder automatically (your edit-approval setting).' : 'Applied to your folder.'}</span>
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
