// The pure rules for applying an edit proposal Sonic made to the connected local folder. No browser
// APIs in here, so it can be reasoned about (and exercised) on its own; localEdits.ts does the file
// access. The matching rules mirror backend/services/local_edits.py's apply_replace, which already
// validated the proposal against the server's snapshot — but this runs against the file's CURRENT
// contents, so an edit made after the snapshot is caught instead of silently clobbered.

export type EditOp =
  | { type: 'replace'; path: string; old_string: string; new_string: string }
  | { type: 'create'; path: string; content: string };

export interface EditProposalFile {
  path: string;
  kind: 'edit' | 'create';
  diff: string;
  additions: number;
  deletions: number;
}

export interface EditProposal {
  proposal_id: string;
  summary: string;
  folder: string;
  files: EditProposalFile[];
  edits: EditOp[];
}

/** One file's change as it will be written: `before` is null for a file that doesn't exist yet. */
export interface FileChange {
  path: string;
  before: string | null;
  after: string;
}

const NEVER_WRITE_DIRS = new Set([
  'node_modules', '.git', 'dist', 'build', '__pycache__', '.venv', 'venv', '.next', 'coverage',
]);
const SECRET_FILENAMES = new Set([
  'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519', '.npmrc', '.pypirc', '.netrc', 'credentials.json',
  'secrets.json', 'secrets.yaml', 'secrets.yml', 'secrets.toml',
]);
const SECRET_SUFFIXES = ['.pem', '.key', '.p12', '.pfx', '.keystore', '.env'];
const ENV_TEMPLATE_SUFFIXES = ['.example', '.sample', '.template'];

/** A reason this path must never be written to, or null if it's fine. Applied again here even
 *  though the server already filtered the proposal — this tab is the last line before the disk. */
export function writeBlockReason(path: string): string | null {
  if (!path || path.length > 500 || path.includes('\0') || path.includes('\\') || path.includes(':') || path.startsWith('/')) {
    return 'unsafe path';
  }
  const parts = path.split('/');
  if (parts.some(part => part === '' || part === '.' || part === '..')) return 'unsafe path';
  if (parts.slice(0, -1).some(part => NEVER_WRITE_DIRS.has(part))) return 'in a protected directory';
  const name = parts[parts.length - 1].toLowerCase();
  if (ENV_TEMPLATE_SUFFIXES.some(s => name.endsWith(s))) return null;
  if (name === '.env' || name.startsWith('.env.') || SECRET_FILENAMES.has(name) || SECRET_SUFFIXES.some(s => name.endsWith(s))) {
    return 'looks like a secret/credential file';
  }
  return null;
}

export function toLf(text: string): { text: string; crlf: boolean } {
  const crlf = text.includes('\r\n');
  return { text: crlf ? text.replace(/\r\n/g, '\n') : text, crlf };
}

export function applyReplace(
  content: string,
  oldString: string,
  newString: string,
): { ok: true; content: string } | { ok: false; error: string } {
  const { text, crlf } = toLf(content);
  const oldLf = oldString.replace(/\r\n/g, '\n');
  const newLf = newString.replace(/\r\n/g, '\n');
  if (oldLf === '') return { ok: false, error: 'old_string is empty' };
  if (oldLf === newLf) return { ok: false, error: 'old_string and new_string are identical' };
  const first = text.indexOf(oldLf);
  if (first === -1) return { ok: false, error: 'the text to replace is no longer in the file' };
  if (text.indexOf(oldLf, first + 1) !== -1) {
    return { ok: false, error: 'the text to replace now matches more than one place in the file' };
  }
  const replaced = text.slice(0, first) + newLf + text.slice(first + oldLf.length);
  return { ok: true, content: crlf ? replaced.replace(/\n/g, '\r\n') : replaced };
}

/**
 * Dry-runs every edit, in order, against the files' current contents (`readCurrent` returns a
 * file's text, or null if it doesn't exist). Either every edit applies cleanly — returning one
 * FileChange per touched file — or nothing does, with a list of what's wrong. Nothing is written.
 */
export async function planEdits(
  ops: EditOp[],
  readCurrent: (path: string) => Promise<string | null>,
): Promise<{ ok: true; changes: FileChange[] } | { ok: false; errors: string[] }> {
  const errors: string[] = [];
  const before = new Map<string, string | null>();
  const working = new Map<string, string | null>();

  for (const [index, op] of ops.entries()) {
    const label = `edit ${index + 1} (${op.path})`;
    const blocked = writeBlockReason(op.path);
    if (blocked) {
      errors.push(`${label}: not allowed — ${blocked}`);
      continue;
    }
    if (!working.has(op.path)) {
      const current = await readCurrent(op.path);
      before.set(op.path, current);
      working.set(op.path, current);
    }
    const current = working.get(op.path) ?? null;
    if (op.type === 'create') {
      if (current !== null) errors.push(`${label}: the file already exists`);
      else working.set(op.path, op.content);
      continue;
    }
    if (current === null) {
      errors.push(`${label}: the file no longer exists`);
      continue;
    }
    const result = applyReplace(current, op.old_string, op.new_string);
    if (result.ok) working.set(op.path, result.content);
    else errors.push(`${label}: ${result.error}`);
  }

  if (errors.length > 0) return { ok: false, errors };
  const changes: FileChange[] = [];
  for (const [path, after] of working) {
    if (after !== null && after !== before.get(path)) {
      changes.push({ path, before: before.get(path) ?? null, after });
    }
  }
  return { ok: true, changes };
}
