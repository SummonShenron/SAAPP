import React, { useEffect, useMemo, useState } from 'react';
import { keyValueStore } from '../idb';
import {
  buildPlan, describeStays, formatBytes, MAX_FILES, planMoves,
  type TidyGroup, type TidyMove, type TidyPlan,
} from '../tidy/tidyCore';
import { applyMoves, scanFolder, undoMoves, type MoveFailure, type TidyDirLike } from '../tidy/tidyFs';
import './__styles__/Tidy.css';

// The browser's folder picker and permission calls, which are not in every TypeScript library yet.
interface PickerHandle extends TidyDirLike {
  queryPermission?(descriptor: { mode: 'readwrite' }): Promise<PermissionState>;
  requestPermission?(descriptor: { mode: 'readwrite' }): Promise<PermissionState>;
}
type PickerWindow = Window & {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite'; id?: string; startIn?: string }) => Promise<PickerHandle>;
};

interface LastTidy {
  handle: PickerHandle;
  folderName: string;
  done: TidyMove[];
  createdFolders: string[];
  at: number;
}

const lastStore = keyValueStore('saapp-tidy', 'state');
const SHOWN_NAMES = 5;

function pickerAvailable(): boolean {
  return typeof window !== 'undefined' && typeof (window as PickerWindow).showDirectoryPicker === 'function';
}

async function canWrite(handle: PickerHandle): Promise<boolean> {
  try {
    if (!handle.queryPermission) return true;
    if ((await handle.queryPermission({ mode: 'readwrite' })) === 'granted') return true;
    if (handle.requestPermission) return (await handle.requestPermission({ mode: 'readwrite' })) === 'granted';
  } catch {
    // treated as "not allowed"
  }
  return false;
}

function plural(n: number, one: string, many: string): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}

const GroupRow: React.FC<{ group: TidyGroup; checked: boolean; onToggle: () => void }> = ({ group, checked, onToggle }) => {
  const [open, setOpen] = useState(false);
  const names = open ? group.files.slice(0, 200) : group.files.slice(0, SHOWN_NAMES);
  return (
    <li className="tidy-group">
      <label className="tidy-group-head">
        <input type="checkbox" checked={checked} onChange={onToggle} aria-label={`Sort ${group.label}`} />
        <span className="tidy-group-title">{group.label}</span>
        <span className="tidy-group-meta">{plural(group.files.length, 'file', 'files')} · {formatBytes(group.bytes)}</span>
      </label>
      <div className="tidy-group-dest">Goes into a folder called <strong>{group.folder}</strong></div>
      <ul className="tidy-names">
        {names.map(f => <li key={f.name}>{f.name}</li>)}
      </ul>
      {group.files.length > SHOWN_NAMES && (
        <button type="button" className="tidy-link" onClick={() => setOpen(o => !o)}>
          {open ? 'Show fewer' : `Show all ${group.files.length.toLocaleString()}`}
        </button>
      )}
    </li>
  );
};

type Stage = 'start' | 'scanning' | 'plan' | 'empty' | 'toobig' | 'working' | 'done' | 'undone';

export const TidyPage: React.FC = () => {
  const supported = pickerAvailable();
  const [stage, setStage] = useState<Stage>('start');
  const [folder, setFolder] = useState<PickerHandle | null>(null);
  const [plan, setPlan] = useState<TidyPlan | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [failed, setFailed] = useState<MoveFailure[]>([]);
  const [moved, setMoved] = useState(0);
  const [last, setLast] = useState<LastTidy | null>(null);
  const [notice, setNotice] = useState('');

  useEffect(() => {
    lastStore.get<LastTidy>('last').then(saved => { if (saved?.handle && saved.done?.length) setLast(saved); });
  }, []);

  const scan = async (handle: PickerHandle) => {
    setStage('scanning');
    setNotice('');
    try {
      const { files } = await scanFolder(handle);
      if (files.length > MAX_FILES) { setStage('toobig'); return; }
      const next = buildPlan(files, Date.now(), files.map(f => f.name));
      setPlan(next);
      setSelected(new Set(next.groups.map(g => g.id)));
      setStage(next.groups.length ? 'plan' : 'empty');
    } catch {
      setNotice("Sonic couldn't read that folder. Try choosing it again.");
      setStage('start');
    }
  };

  const choose = async () => {
    setNotice('');
    let handle: PickerHandle;
    try {
      handle = await (window as PickerWindow).showDirectoryPicker!({ id: 'saapp-tidy', mode: 'readwrite', startIn: 'downloads' });
    } catch {
      // Cancelling also lands here. Some browsers refuse to open a few protected folders (Downloads, Documents, Desktop) as a whole, and say nothing about it.
      setNotice("No folder was opened. If you chose one and nothing happened, your browser may protect that folder. Try a folder inside it instead.");
      return;
    }
    setFolder(handle);
    await scan(handle);
  };

  const toggle = (id: string) => setSelected(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  const moves = useMemo(() => (plan ? planMoves(plan, selected) : []), [plan, selected]);

  const tidy = async () => {
    if (!folder || !moves.length) return;
    if (!(await canWrite(folder))) {
      setNotice('Sonic needs your permission to change this folder. Press Tidy up again and choose Allow.');
      return;
    }
    setStage('working');
    setProgress({ done: 0, total: moves.length });
    const result = await applyMoves(folder, moves, (done, total) => setProgress({ done, total }));
    setFailed(result.failed);
    setMoved(result.done.length);
    if (result.done.length) {
      const saved: LastTidy = { handle: folder, folderName: folder.name, done: result.done, createdFolders: result.createdFolders, at: Date.now() };
      setLast(saved);
      await lastStore.put('last', saved);
    }
    setStage('done');
  };

  const undo = async (target: LastTidy) => {
    if (!(await canWrite(target.handle))) {
      setNotice('Sonic needs your permission to put the files back. Press Undo again and choose Allow.');
      return;
    }
    setStage('working');
    setProgress({ done: 0, total: target.done.length });
    const result = await undoMoves(target.handle, target.done, target.createdFolders, (done, total) => setProgress({ done, total }));
    setFailed(result.failed);
    setMoved(result.done.length);
    setLast(null);
    await lastStore.remove('last');
    setStage('undone');
  };

  const startOver = () => { setPlan(null); setFolder(null); setFailed([]); setNotice(''); setStage('start'); };

  if (!supported) {
    return (
      <div className="tidy-page">
        <h2 className="tidy-heading">Tidy up a folder</h2>
        <section className="tidy-card">
          <p>Tidying a folder works in Chrome or Edge on a laptop or desktop computer. It isn't available on a phone, or in Safari or Firefox.</p>
        </section>
      </div>
    );
  }

  return (
    <div className="tidy-page">
      <h2 className="tidy-heading">Tidy up a folder</h2>

      {notice && <div className="tidy-notice" role="status">{notice}</div>}

      {stage === 'start' && (
        <>
          <section className="tidy-card">
            <p className="tidy-lead">
              Pick a messy folder, like Downloads, and Sonic will show you a plan for sorting its loose files into folders such as Pictures,
              Documents and Installers. You see the plan first, and nothing changes until you press <strong>Tidy up</strong>.
            </p>
            <ul className="tidy-promises">
              <li><strong>It stays on your computer.</strong> Sonic only looks at file names, sizes and dates, never what's inside, and nothing is uploaded.</li>
              <li><strong>Nothing is deleted or overwritten.</strong> If a name is already taken, the file gets a number, like "report (2).pdf".</li>
              <li><strong>You can undo it.</strong> One button puts every file back.</li>
            </ul>
            <button type="button" className="tidy-btn" onClick={choose}>Choose a folder</button>
          </section>
          {last && (
            <section className="tidy-card">
              <div className="tidy-last">
                <div>
                  <div className="tidy-label">Last time</div>
                  <div className="tidy-hint">
                    You tidied “{last.folderName}” on {new Date(last.at).toLocaleDateString()}: {plural(last.done.length, 'file', 'files')} moved.
                  </div>
                </div>
                <button type="button" className="tidy-btn tidy-btn-quiet" onClick={() => undo(last)}>Undo it</button>
              </div>
            </section>
          )}
        </>
      )}

      {stage === 'scanning' && <section className="tidy-card"><p>Looking at the files…</p></section>}

      {stage === 'empty' && (
        <section className="tidy-card">
          <p>There's nothing here that Sonic would move. {plan && plan.stays.length ? `${plural(plan.stays.length, 'file', 'files')} stay where they are (${describeStays(plan.stays)}).` : 'The folder has no loose files.'}</p>
          <button type="button" className="tidy-btn tidy-btn-quiet" onClick={startOver}>Choose a different folder</button>
        </section>
      )}

      {stage === 'toobig' && (
        <section className="tidy-card">
          <p>That folder has more than {MAX_FILES.toLocaleString()} loose files, which is too many to sort at once. Try a smaller folder, like Downloads.</p>
          <button type="button" className="tidy-btn tidy-btn-quiet" onClick={startOver}>Choose a different folder</button>
        </section>
      )}

      {stage === 'plan' && plan && (
        <>
          <section className="tidy-card">
            <p className="tidy-lead">
              In “{folder?.name}”, {plural(plan.movableFiles, 'file', 'files')} of {plan.totalFiles.toLocaleString()} can be sorted into {plural(plan.groups.length, 'folder', 'folders')}.
              Untick anything you'd rather leave where it is.
            </p>
            {plan.stays.length > 0 && (
              <p className="tidy-hint">{plural(plan.stays.length, 'file', 'files')} will stay where they {plan.stays.length === 1 ? 'is' : 'are'}: {describeStays(plan.stays)}.</p>
            )}
          </section>
          <ul className="tidy-groups">
            {plan.groups.map(g => <GroupRow key={g.id} group={g} checked={selected.has(g.id)} onToggle={() => toggle(g.id)} />)}
          </ul>
          <div className="tidy-actions">
            <button type="button" className="tidy-btn" onClick={tidy} disabled={!moves.length}>
              {moves.length ? `Tidy up ${plural(moves.length, 'file', 'files')}` : 'Nothing selected'}
            </button>
            <button type="button" className="tidy-btn tidy-btn-quiet" onClick={startOver}>Choose a different folder</button>
          </div>
        </>
      )}

      {stage === 'working' && (
        <section className="tidy-card" role="status" aria-live="polite">
          <p>Working… {progress ? `${progress.done.toLocaleString()} of ${progress.total.toLocaleString()}` : ''}</p>
          <progress className="tidy-progress" value={progress?.done ?? 0} max={progress?.total ?? 1} />
          <p className="tidy-hint">Please keep this page open until it's finished.</p>
        </section>
      )}

      {(stage === 'done' || stage === 'undone') && (
        <section className="tidy-card">
          <p className="tidy-lead">
            {stage === 'done'
              ? `Done. ${plural(moved, 'file', 'files')} sorted into folders.`
              : `Put back. ${plural(moved, 'file', 'files')} returned to where they were.`}
          </p>
          {failed.length > 0 && (
            <>
              <p className="tidy-hint">{plural(failed.length, 'file', 'files')} couldn't be {stage === 'done' ? 'moved' : 'put back'} and {failed.length === 1 ? 'was' : 'were'} left alone:</p>
              <ul className="tidy-names">
                {failed.slice(0, 8).map(f => <li key={f.name}>{f.name}: {f.reason}</li>)}
                {failed.length > 8 && <li>and {failed.length - 8} more</li>}
              </ul>
            </>
          )}
          <div className="tidy-actions">
            {stage === 'done' && last && <button type="button" className="tidy-btn tidy-btn-quiet" onClick={() => undo(last)}>Undo</button>}
            <button type="button" className="tidy-btn" onClick={startOver}>Tidy another folder</button>
          </div>
        </section>
      )}
    </div>
  );
};

export default TidyPage;
