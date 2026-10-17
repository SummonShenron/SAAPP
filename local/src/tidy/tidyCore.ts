// Tidy up a folder: deciding what goes where. Plain functions with no browser or React in them, so they can be tested on their own
// (tidyCore.test.mjs, run from local/ with `node --test src/tidy/tidyCore.test.mjs`).
//
// The rules are fixed and visible, not guessed: a file goes to a folder by what KIND of file it is (its ending), and anything that is not clearly one
// of the kinds stays where it is. Only a file's name, size and date are ever looked at; its contents are never read.

export interface TidyFile {
  name: string;
  size: number;
  /** Milliseconds since 1970, as a File reports it. */
  modified: number;
}

export interface TidyGroup {
  id: string;
  /** What the person sees: "Pictures". */
  label: string;
  /** Where the files go, inside the chosen folder. May have a sub-folder: "Pictures/Screenshots". */
  folder: string;
  files: TidyFile[];
  bytes: number;
}

export interface TidyStay {
  file: TidyFile;
  reason: 'in_progress' | 'recent' | 'system' | 'unsure';
}

export interface TidyPlan {
  groups: TidyGroup[];
  stays: TidyStay[];
  totalFiles: number;
  movableFiles: number;
}

/** One planned move, and, once done, where the file really went (a name that was taken gets "(2)"). */
export interface TidyMove {
  name: string;
  folder: string;
  to?: string;
}

interface Kind {
  id: string;
  label: string;
  folder: string;
  extensions: string[];
}

// Order matters only for screenshots, which are looked for first (by name) and so never end up in the plain Pictures group.
const KINDS: Kind[] = [
  { id: 'pictures', label: 'Pictures', folder: 'Pictures', extensions: ['jpg', 'jpeg', 'png', 'gif', 'webp', 'heic', 'heif', 'bmp', 'tif', 'tiff', 'svg', 'raw', 'cr2', 'nef'] },
  { id: 'documents', label: 'Documents', folder: 'Documents', extensions: ['pdf', 'doc', 'docx', 'rtf', 'odt', 'txt', 'md', 'pages', 'epub'] },
  { id: 'spreadsheets', label: 'Spreadsheets', folder: 'Spreadsheets', extensions: ['xls', 'xlsx', 'xlsm', 'csv', 'tsv', 'ods', 'numbers'] },
  { id: 'presentations', label: 'Presentations', folder: 'Presentations', extensions: ['ppt', 'pptx', 'key', 'odp'] },
  { id: 'music', label: 'Music', folder: 'Music', extensions: ['mp3', 'wav', 'm4a', 'flac', 'aac', 'ogg', 'wma'] },
  { id: 'videos', label: 'Videos', folder: 'Videos', extensions: ['mp4', 'mov', 'mkv', 'avi', 'webm', 'wmv', 'm4v'] },
  { id: 'archives', label: 'Zip files and archives', folder: 'Archives', extensions: ['zip', 'rar', '7z', 'tar', 'gz', 'tgz', 'bz2'] },
  { id: 'installers', label: 'Installers and programs', folder: 'Installers', extensions: ['exe', 'msi', 'msix', 'appx', 'dmg', 'pkg', 'apk', 'iso'] },
];
const SCREENSHOTS: Kind = { id: 'screenshots', label: 'Screenshots', folder: 'Pictures/Screenshots', extensions: [] };
const SCREENSHOT_NAME = /^(screenshot|screen[ _-]?shot|screen[ _-]?capture)/i;   // "Screenshot 2026-10-01...", "Screenshot_2026...", "Screen Shot ..."
const PICTURE_EXTENSIONS = new Set(KINDS[0].extensions);

const IN_PROGRESS_EXTENSIONS = new Set(['crdownload', 'part', 'partial', 'tmp', 'download', 'opdownload']);
const SYSTEM_NAMES = new Set(['desktop.ini', 'thumbs.db', '.ds_store']);
/** A file changed this recently may still be being written (a download, a save), so it is left alone. */
export const RECENT_MS = 10 * 60 * 1000;
/** Most files moved in one go: a plan this long is almost certainly the wrong folder (a whole drive, a project). */
export const MAX_FILES = 2000;

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot > 0 && dot < name.length - 1 ? name.slice(dot + 1).toLowerCase() : '';
}

/** Why a file is left alone whatever its kind, or null when it may be moved. */
export function skipReason(file: TidyFile, now: number): TidyStay['reason'] | null {
  const lower = file.name.toLowerCase();
  if (lower.startsWith('.') || lower.startsWith('~$') || SYSTEM_NAMES.has(lower)) return 'system';
  if (IN_PROGRESS_EXTENSIONS.has(extensionOf(file.name))) return 'in_progress';
  if (now - file.modified < RECENT_MS) return 'recent';
  return null;
}

export function kindOf(name: string): Kind | null {
  const ext = extensionOf(name);
  if (!ext) return null;
  if (PICTURE_EXTENSIONS.has(ext) && SCREENSHOT_NAME.test(name)) return SCREENSHOTS;
  return KINDS.find(kind => kind.extensions.includes(ext)) ?? null;
}

/**
 * The plan for the loose files in a folder (files already inside sub-folders are never part of it). `existingFileNames` are the names of the
 * folder's own files, used so a folder is never given a name that a file already has.
 */
export function buildPlan(files: TidyFile[], now: number, existingFileNames: Iterable<string> = []): TidyPlan {
  const taken = new Set([...existingFileNames].map(n => n.toLowerCase()));
  const byKind = new Map<string, TidyGroup>();
  const stays: TidyStay[] = [];

  for (const file of files) {
    const why = skipReason(file, now);
    if (why) { stays.push({ file, reason: why }); continue; }
    const kind = kindOf(file.name);
    if (!kind) { stays.push({ file, reason: 'unsure' }); continue; }
    let group = byKind.get(kind.id);
    if (!group) {
      group = { id: kind.id, label: kind.label, folder: folderNameFor(kind.folder, taken), files: [], bytes: 0 };
      byKind.set(kind.id, group);
    }
    group.files.push(file);
    group.bytes += file.size;
  }

  const groups = [...byKind.values()].sort((a, b) => b.files.length - a.files.length || a.label.localeCompare(b.label));
  for (const group of groups) group.files.sort((a, b) => a.name.localeCompare(b.name));
  return { groups, stays, totalFiles: files.length, movableFiles: groups.reduce((n, g) => n + g.files.length, 0) };
}

/** "Pictures" unless a FILE with that name is already in the folder, then "Pictures (sorted)". Only the first part of a path can clash. */
function folderNameFor(folder: string, takenFileNames: Set<string>): string {
  const [first, ...rest] = folder.split('/');
  const safe = takenFileNames.has(first.toLowerCase()) ? `${first} (sorted)` : first;
  return [safe, ...rest].join('/');
}

/** The moves for the groups the person left ticked. */
export function planMoves(plan: TidyPlan, selectedGroupIds: Iterable<string>): TidyMove[] {
  const chosen = new Set(selectedGroupIds);
  const moves: TidyMove[] = [];
  for (const group of plan.groups) {
    if (!chosen.has(group.id)) continue;
    for (const file of group.files) moves.push({ name: file.name, folder: group.folder });
  }
  return moves;
}

/** `name` made different from every name in `taken` (compared without regard to case, as Windows does): "report.pdf" becomes "report (2).pdf". */
export function uniqueName(name: string, taken: Set<string>): string {
  if (!taken.has(name.toLowerCase())) return name;
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  for (let n = 2; ; n++) {
    const candidate = `${stem} (${n})${ext}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}

/** The moves that put everything back, last move first. Only moves that really happened (they carry `to`) can be undone. */
export function reverseMoves(done: TidyMove[]): TidyMove[] {
  return done.filter(m => m.to).map(m => ({ name: m.to as string, folder: m.folder, to: m.name })).reverse();
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
  return `${value >= 100 || value % 1 === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

const STAY_TEXT: Record<TidyStay['reason'], string> = {
  in_progress: 'still downloading',
  recent: 'changed in the last few minutes',
  system: 'hidden or system files',
  unsure: "not sure where they belong",
};

/** A short, plain account of what stays where it is and why, e.g. "12 not sure where they belong, 2 still downloading". */
export function describeStays(stays: TidyStay[]): string {
  const counts = new Map<TidyStay['reason'], number>();
  for (const s of stays) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  return (['unsure', 'recent', 'in_progress', 'system'] as const)
    .filter(reason => counts.has(reason))
    .map(reason => `${counts.get(reason)} ${STAY_TEXT[reason]}`)
    .join(', ');
}
