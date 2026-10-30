// The pure parts of the trace panel's "facts" (docs/trace-panel-verbose-plan.md, slice 1), kept apart from React so they can be tested.
// Steps are replayed with a delay, so a step's facts can arrive before the step is drawn: facts are kept in a map keyed by step id and
// merged into the steps at render time, which makes the order they arrive in irrelevant.
import type { TraceStep } from './TracePanel';

export const MAX_FACT_LINES = 10;
export const MAX_FACT_CHARS = 200;

export type TraceFactsById = Record<string, string[]>;

/** What a read step read: the file, a capped redacted piece of its text, and whether it was cut. The live trace only; never stored. */
export interface ReadExcerpt { kind: 'read'; path: string; text: string; truncated: boolean; }
/** What a search found: the files with the matching lines (none for a search that only lists files), the counts, how many files were left out because they hold secrets, and whether it was cut. */
export interface SearchMatch { line: number; text: string; }
export interface SearchFile { path: string; matches: SearchMatch[]; }
export interface SearchExcerpt { kind: 'search'; term: string; files: SearchFile[]; total_files: number; total_matches: number; hidden: number; truncated: boolean; }
export type TraceExcerpt = ReadExcerpt | SearchExcerpt;
export type TraceExcerptsById = Record<string, TraceExcerpt>;
export const MAX_EXCERPT_CHARS = 1000;
export const MAX_EXCERPT_PATH_CHARS = 300;
export const MAX_SEARCH_FILES = 10;
export const MAX_SEARCH_MATCHES = 25;
export const MAX_SEARCH_PER_FILE = 5;
export const MAX_SEARCH_LINE_CHARS = 160;
export const MAX_SEARCH_TERM_CHARS = 120;

const count = (value: unknown): number => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : 0);

function cleanSearch(item: Record<string, unknown>): SearchExcerpt | null {
  const files: SearchFile[] = [];
  let kept = 0;
  for (const entry of Array.isArray(item.files) ? item.files : []) {
    if (!entry || typeof entry !== 'object' || files.length >= MAX_SEARCH_FILES) continue;
    const file = entry as Record<string, unknown>;
    if (typeof file.path !== 'string' || !file.path.trim()) continue;
    const matches: SearchMatch[] = [];
    for (const match of Array.isArray(file.matches) ? file.matches : []) {
      const m = match as Record<string, unknown> | null;
      if (m && typeof m.text === 'string' && count(m.line) > 0 && kept < MAX_SEARCH_MATCHES && matches.length < MAX_SEARCH_PER_FILE) {
        matches.push({ line: m.line as number, text: m.text.slice(0, MAX_SEARCH_LINE_CHARS) });
        kept += 1;
      }
    }
    files.push({ path: file.path.slice(0, MAX_EXCERPT_PATH_CHARS), matches });
  }
  if (files.length === 0) return null;
  return {
    kind: 'search', term: typeof item.term === 'string' ? item.term.slice(0, MAX_SEARCH_TERM_CHARS) : '', files,
    total_files: Math.max(count(item.total_files), files.length), total_matches: Math.max(count(item.total_matches), kept), hidden: count(item.hidden), truncated: item.truncated === true,
  };
}

/** What the server sent, kept to the same limits the server applies. Anything that is not a read excerpt with text or a search with files is dropped. */
export function cleanExcerpt(raw: unknown): TraceExcerpt | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  if (item.kind === 'search') return cleanSearch(item);
  if (item.kind !== 'read' || typeof item.text !== 'string' || !item.text.trim()) return null;
  const path = typeof item.path === 'string' ? item.path.slice(0, MAX_EXCERPT_PATH_CHARS) : '';
  return { kind: 'read', path, text: item.text.slice(0, MAX_EXCERPT_CHARS), truncated: item.truncated === true || item.text.length > MAX_EXCERPT_CHARS };
}

/** A line split around every place the term occurs (ignoring case), so the page can mark the term without ever treating the line as HTML. A blank or missing term marks nothing. */
export function splitAroundTerm(text: string, term: string): Array<{ text: string; hit: boolean }> {
  const needle = term.trim().toLowerCase();
  if (!needle) return [{ text, hit: false }];
  const lower = text.toLowerCase();
  const parts: Array<{ text: string; hit: boolean }> = [];
  let from = 0;
  for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
    if (at > from) parts.push({ text: text.slice(from, at), hit: false });
    parts.push({ text: text.slice(at, at + needle.length), hit: true });
    from = at + needle.length;
  }
  if (from < text.length) parts.push({ text: text.slice(from), hit: false });
  return parts.length ? parts : [{ text, hit: false }];
}

/** The line a search excerpt leads with: "7 matches in 3 files", or "3 files" for a search that only lists files, then what was left out. */
export function searchSummary(excerpt: SearchExcerpt): string {
  const files = `${excerpt.total_files} ${excerpt.total_files === 1 ? 'file' : 'files'}`;
  const head = excerpt.total_matches > 0 && excerpt.files.some(f => f.matches.length > 0) ? `${excerpt.total_matches} ${excerpt.total_matches === 1 ? 'match' : 'matches'} in ${files}` : files;
  const notes: string[] = [];
  if (excerpt.truncated) notes.push('showing the first ones');
  if (excerpt.hidden > 0) notes.push(excerpt.hidden === 1 ? '1 file that holds secrets left out' : `${excerpt.hidden} files that hold secrets left out`);
  return notes.length ? `${head} (${notes.join('; ')})` : head;
}

/** Records a step's excerpt. Returns the same map when there is nothing to record. */
export function recordExcerpt(byId: TraceExcerptsById, stepId: unknown, raw: unknown): TraceExcerptsById {
  if (typeof stepId !== 'string' || !stepId) return byId;
  const excerpt = cleanExcerpt(raw);
  return excerpt ? { ...byId, [stepId]: excerpt } : byId;
}

/** The steps with their excerpts attached, whether the excerpt arrived before or after the step was drawn. */
export function mergeExcerpts(steps: TraceStep[], byId: TraceExcerptsById): TraceStep[] {
  let changed = false;
  const merged = steps.map(step => {
    const excerpt = step.stepId ? byId[step.stepId] : undefined;
    if (!excerpt || step.excerpt === excerpt) return step;
    changed = true;
    return { ...step, excerpt };
  });
  return changed ? merged : steps;
}

/** What the server sent, kept to the same limits the server applies (it is shown as plain text, never as HTML). */
export function cleanFacts(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const lines: string[] = [];
  for (const item of raw) {
    if (typeof item !== 'string' || !item.trim()) continue;
    const line = item.replace(/\s+/g, ' ').trim();
    lines.push(line.length > MAX_FACT_CHARS ? `${line.slice(0, MAX_FACT_CHARS - 1)}…` : line);
    if (lines.length === MAX_FACT_LINES) break;
  }
  return lines;
}

/** Records a step's facts. Returns the same map when there is nothing to record (a missing id or no usable lines). */
export function recordFacts(byId: TraceFactsById, stepId: unknown, raw: unknown): TraceFactsById {
  if (typeof stepId !== 'string' || !stepId) return byId;
  const facts = cleanFacts(raw);
  return facts.length ? { ...byId, [stepId]: facts } : byId;
}

/** The steps with their facts attached, whether the facts arrived before or after the step was drawn. */
export function mergeFacts(steps: TraceStep[], byId: TraceFactsById): TraceStep[] {
  let changed = false;
  const merged = steps.map(step => {
    const facts = step.stepId ? byId[step.stepId] : undefined;
    if (!facts || step.facts === facts) return step;
    changed = true;
    return { ...step, facts };
  });
  return changed ? merged : steps;
}

/** Opens a closed step and closes an open one, without touching the others. */
export function toggleExpanded(open: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(open);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

const TOOL_LABELS: Record<string, string> = {
  web_search: 'Searched the web',
  read_url: 'Read a web page',
  search_code: 'Searched the code',
  read_repo_file: 'Read a file',
  find_file: 'Looked for a file',
  list_repo_tree: 'Listed the files',
  search_literal: 'Searched for exact text',
  mcp__maps__find_places: 'Looked up places',
  mcp__maps__travel_times: 'Checked travel times',
  search_library: 'Searched your documents',
  list_library_files: 'Listed your documents',
  read_library_file: 'Read one of your documents',
  run_python: 'Ran a calculation',
  search_gmail: 'Checked your email',
  read_gmail_message: 'Checked your email',
  get_gmail_attachment: 'Checked your email',
  search_outlook_mail: 'Checked your email',
  read_outlook_message: 'Checked your email',
  get_outlook_attachment: 'Checked your email',
  search_drive_files: 'Checked your Drive',
  read_drive_file: 'Checked your Drive',
  search_onedrive_files: 'Checked your Drive',
  read_onedrive_file: 'Checked your Drive',
  list_google_calendar_events: 'Checked your calendar',
  list_outlook_calendar_events: 'Checked your calendar',
};

/** A plain-words title for a tool step, or null when the step names no tool (the caller falls back to the node's label). */
export function toolStepLabel(tool: unknown, subject?: unknown): string | null {
  if (typeof tool !== 'string' || !tool) return null;
  const label = tool.startsWith('browser_') ? 'Used the browser' : (TOOL_LABELS[tool] ?? 'Used a tool');
  const about = typeof subject === 'string' ? subject.trim() : '';
  // The file name says it better than "a file": "Read main.tsx", not "Read a file: main.tsx".
  if (about && tool === 'read_repo_file') return `Read ${about}`;
  return about ? `${label}: ${about}` : label;
}

/** The steps of a turn that has ended, as they are kept on its reply: every step marked complete (the last one may still be "active" when the stream just stops). The same objects when nothing needs changing. */
export function finishedSteps(steps: TraceStep[]): TraceStep[] {
  return steps.map(step => (step.status === 'complete' ? step : { ...step, status: 'complete' as const }));
}

/** "1 step" or "7 steps": what the line under a reply says before it is opened. */
export function stepsLabel(count: number): string {
  return `${count} ${count === 1 ? 'step' : 'steps'}`;
}

/** Whether the steps under a reply are open: what the person chose if they pressed it, else open only for the newest reply (the steps of the last request stay in view until the next one starts). */
export function stepsAreOpen(chosen: boolean | null, latest: boolean): boolean {
  return chosen ?? latest;
}
