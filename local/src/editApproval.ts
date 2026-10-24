// How much of what Sonic proposes to the connected folder is applied without a click. The choice is the person's, kept in this browser, because this browser is what writes the files.
//
//   ask      every proposal waits for Apply (the default, and how it has always worked).
//   sonic    a proposal that only touches Sonic's own folder (.sonic/: its notes and tests) is applied at once, with the diff shown and Undo; anything else still asks.
//   session  every proposal is applied at once, with the diff shown and Undo, until this tab is closed. Never remembered across a restart.
//
// Whatever the level, these always ask: a Word document (created or edited), a proposal touching more than MAX_AUTO_FILES files, and a burst (more than AUTO_BURST_LIMIT automatic
// applies in AUTO_BURST_WINDOW_MS), which is what a loop gone wrong or a hostile instruction inside a page Sonic read would look like. An automatic apply is reported to the waiting
// turn exactly like a click on Apply, and can be undone from the card or the folder menu.
import type { EditProposal } from './localEditsCore';

export type ApprovalLevel = 'ask' | 'sonic' | 'session';

export const SONIC_DIR = '.sonic/';
export const MAX_AUTO_FILES = 8;
export const AUTO_BURST_LIMIT = 6;
export const AUTO_BURST_WINDOW_MS = 60_000;
const STORAGE_KEY = 'sonic_edit_approval';

const ORDER: ApprovalLevel[] = ['ask', 'sonic', 'session'];

export function nextLevel(level: ApprovalLevel): ApprovalLevel {
  return ORDER[(ORDER.indexOf(level) + 1) % ORDER.length];
}

export function levelLabel(level: ApprovalLevel): string {
  if (level === 'sonic') return "Edit approval: automatic in .sonic/ only";
  if (level === 'session') return 'Edit approval: automatic for everything this session';
  return 'Edit approval: ask every time';
}

export function levelHint(level: ApprovalLevel): string {
  if (level === 'sonic') return "Changes inside .sonic/ (Sonic's notes and tests) are applied at once. Everything else still asks.";
  if (level === 'session') return 'Every change is applied at once until you close this tab. Each one is shown with Undo.';
  return 'Every change waits for you to press Apply.';
}

type Store = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function stores(): { local: Store | null; session: Store | null } {
  try {
    return { local: typeof localStorage === 'undefined' ? null : localStorage, session: typeof sessionStorage === 'undefined' ? null : sessionStorage };
  } catch {
    return { local: null, session: null };
  }
}

/** The saved level. "sonic" is remembered; "session" lives in sessionStorage so a closed tab forgets it. A storage problem means "ask". */
export function loadLevel(): ApprovalLevel {
  const { local, session } = stores();
  try {
    if (session?.getItem(STORAGE_KEY) === 'session') return 'session';
    if (local?.getItem(STORAGE_KEY) === 'sonic') return 'sonic';
  } catch {
    // fall through to the safe default
  }
  return 'ask';
}

export function saveLevel(level: ApprovalLevel): void {
  const { local, session } = stores();
  try {
    local?.removeItem(STORAGE_KEY);
    session?.removeItem(STORAGE_KEY);
    if (level === 'sonic') local?.setItem(STORAGE_KEY, 'sonic');
    if (level === 'session') session?.setItem(STORAGE_KEY, 'session');
  } catch {
    // not remembered; the level still applies for this page view
  }
}

const insideSonicDir = (path: string): boolean => path.startsWith(SONIC_DIR) && !path.slice(SONIC_DIR.length).split('/').includes('..');

export interface AutoDecision {
  auto: boolean;
  /** Why it will ask, when it will (for tests and for a note on the card). */
  reason?: string;
}

/** Whether this proposal is applied without a click. `recent` is the times (ms) of earlier automatic applies; the caller adds `now` to it when this one is applied. Pure. */
export function decideAutoApply(level: ApprovalLevel, proposal: EditProposal, recent: number[], now: number): AutoDecision {
  if (level === 'ask') return { auto: false };
  if (proposal.files.some(f => f.format === 'docx' || f.format === 'docx_edit') || proposal.edits.some(e => e.type === 'create_docx' || e.type === 'edit_docx')) {
    return { auto: false, reason: 'a Word document always asks' };
  }
  if (proposal.files.length > MAX_AUTO_FILES) return { auto: false, reason: `more than ${MAX_AUTO_FILES} files always asks` };
  if (recent.filter(t => now - t < AUTO_BURST_WINDOW_MS).length >= AUTO_BURST_LIMIT) return { auto: false, reason: 'too many automatic changes in a minute, so it asks' };
  if (level === 'sonic') {
    const paths = [...proposal.files.map(f => f.path), ...proposal.edits.map(e => e.path)];
    if (paths.length === 0 || !paths.every(insideSonicDir)) return { auto: false, reason: "this touches files outside .sonic/, so it asks" };
  }
  return { auto: true };
}
