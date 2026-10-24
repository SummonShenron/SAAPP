// The pure rules for applying an edit proposal Sonic made to the connected local folder. No browser
// APIs in here, so it can be reasoned about (and exercised) on its own; localEdits.ts does the file
// access. The matching rules mirror backend/services/local_edits.py's apply_replace, which already
// validated the proposal against the server's snapshot — but this runs against the file's CURRENT
// contents, so an edit made after the snapshot is caught instead of silently clobbered.

/** A Word document as Sonic describes it (see backend/utils/docx_writer.py). The server renders the real .docx when the user applies it. */
export interface DocxDocument {
  title: string;
  blocks: unknown[];
}

export type EditOp =
  | { type: 'replace'; path: string; old_string: string; new_string: string; replace_all?: boolean }
  | { type: 'create'; path: string; content: string }
  | { type: 'create_docx'; path: string; document: DocxDocument }
  /** An edited COPY of an existing Word document: `path` is the original (only ever read), `save_as` the new file. */
  | { type: 'edit_docx'; path: string; save_as: string; word_edits: unknown[] };

export interface EditProposalFile {
  path: string;
  kind: 'edit' | 'create';
  /** 'docx' for a new Word document, whose diff is a readable preview rather than the file's bytes. */
  format?: 'text' | 'docx' | 'docx_edit';
  /** For an edited copy of a Word document: the original it was made from. */
  source?: string;
  diff: string;
  additions: number;
  deletions: number;
}

export interface EditProposal {
  proposal_id: string;
  /** Sonic is waiting for Apply or Decline on this one and will carry on with the answer. */
  awaiting?: boolean;
  /** Set by this page, not the server: the person's edit-approval level says this one is applied without a click (src/editApproval.ts). */
  auto?: boolean;
  /** Why it asked instead, when an approval level other than "ask" is on (shown on the card). */
  autoSkipReason?: string;
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
  /** Set for a file that is not text (a new Word document): `after` is empty and the bytes come from `document`, rendered by the server at apply time. */
  binary?: 'docx';
  /** What to render. Used only while applying; never kept in the undo copy. */
  document?: DocxDocument;
  /** For an edited copy: where its bytes come from (the original, read at apply time) and the edits to make. Used only while applying. */
  docxEdit?: { source: string; word_edits: unknown[] };
  /** "size:lastModified" of the file right after it was written, so undo can tell whether it was touched since (its bytes can't be compared as text). */
  stamp?: string;
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
  replaceAll = false,
): { ok: true; content: string } | { ok: false; error: string } {
  const { text, crlf } = toLf(content);
  const oldLf = oldString.replace(/\r\n/g, '\n');
  const newLf = newString.replace(/\r\n/g, '\n');
  if (oldLf === '') return { ok: false, error: 'old_string is empty' };
  if (oldLf === newLf) return { ok: false, error: 'old_string and new_string are identical' };
  const first = text.indexOf(oldLf);
  if (first === -1) return { ok: false, error: 'the text to replace is no longer in the file' };
  const matchesSeveral = text.indexOf(oldLf, first + 1) !== -1;
  if (matchesSeveral && !replaceAll) {
    return { ok: false, error: 'the text to replace now matches more than one place in the file' };
  }
  const replaced = replaceAll
    ? text.split(oldLf).join(newLf)
    : text.slice(0, first) + newLf + text.slice(first + oldLf.length);
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
  const documents = new Map<string, DocxDocument>();   // new Word documents in this proposal, by path
  const editedCopies = new Map<string, { source: string; word_edits: unknown[] }>();   // edited copies of existing Word documents, by the copy's path

  for (const [index, op] of ops.entries()) {
    const label = `edit ${index + 1} (${op.path})`;
    const blocked = writeBlockReason(op.path);
    if (blocked) {
      errors.push(`${label}: not allowed — ${blocked}`);
      continue;
    }
    if (documents.has(op.path)) {
      errors.push(`${label}: this Word document is created by this same proposal, so nothing else can change it`);
      continue;
    }
    if (!working.has(op.path)) {
      const current = await readCurrent(op.path);
      before.set(op.path, current);
      working.set(op.path, current);
    }
    const current = working.get(op.path) ?? null;
    if (op.type === 'edit_docx') {
      // `op.path` is only ever READ; the result is written to `save_as`, which must be a new name. Checked here against the folder as it is right now.
      const target = op.save_as;
      const blockedCopy = writeBlockReason(target);
      if (blockedCopy) errors.push(`${label}: the copy ${target} is not allowed — ${blockedCopy}`);
      else if (!op.path.toLowerCase().endsWith('.docx') || !target.toLowerCase().endsWith('.docx')) errors.push(`${label}: both the document and its copy must be .docx files`);
      else if (target.toLowerCase() === op.path.toLowerCase()) errors.push(`${label}: the copy must have a new name; the original is never changed`);
      else if (current === null) errors.push(`${label}: ${op.path} is no longer in the folder`);
      else if (documents.has(target) || editedCopies.has(target) || (await readCurrent(target)) !== null) errors.push(`${label}: ${target} already exists, and Sonic never overwrites a document`);
      else editedCopies.set(target, { source: op.path, word_edits: op.word_edits });
      continue;
    }
    if (op.type === 'create_docx') {
      if (!op.path.toLowerCase().endsWith('.docx')) errors.push(`${label}: a Word document must be named something.docx`);
      else if (current !== null) errors.push(`${label}: a file with that name already exists, and Sonic never overwrites a document`);
      else {
        working.set(op.path, '');
        documents.set(op.path, op.document);
      }
      continue;
    }
    if (op.type === 'create') {
      if (current !== null) errors.push(`${label}: the file already exists`);
      else working.set(op.path, op.content);
      continue;
    }
    if (current === null) {
      errors.push(`${label}: the file no longer exists`);
      continue;
    }
    const result = applyReplace(current, op.old_string, op.new_string, op.replace_all === true);
    if (result.ok) working.set(op.path, result.content);
    else errors.push(`${label}: ${result.error}`);
  }

  if (errors.length > 0) return { ok: false, errors };
  const changes: FileChange[] = [];
  for (const [path, after] of working) {
    const document = documents.get(path);
    if (document) {
      changes.push({ path, before: null, after: '', binary: 'docx', document });
    } else if (after !== null && after !== before.get(path)) {
      changes.push({ path, before: before.get(path) ?? null, after });
    }
  }
  for (const [path, docxEdit] of editedCopies) {
    changes.push({ path, before: null, after: '', binary: 'docx', docxEdit });
  }
  return { ok: true, changes };
}

/**
 * Paths whose files are no longer as an applied change left them, so an undo would overwrite or delete the person's later work. Text files are compared
 * by content; a Word document (whose bytes were never kept) by the size and modified time recorded when it was written, so any later save of it counts.
 */
export async function changesMovedSince(
  changes: FileChange[],
  readText: (path: string) => Promise<string | null>,
  readStamp: (path: string) => Promise<string | null>,
): Promise<string[]> {
  const moved: string[] = [];
  for (const change of changes) {
    if (change.binary) {
      if (!change.stamp || (await readStamp(change.path)) !== change.stamp) moved.push(change.path);
    } else if ((await readText(change.path)) !== change.after) {
      moved.push(change.path);
    }
  }
  return moved;
}

/** The change as it is kept for undo: what is needed to put things back, without the document description. */
export function forUndo(change: FileChange): FileChange {
  const { document: _document, docxEdit: _docxEdit, ...kept } = change;
  void _document;
  void _docxEdit;
  return kept;
}
