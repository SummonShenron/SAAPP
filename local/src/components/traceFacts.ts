// The pure parts of the trace panel's "facts" (docs/trace-panel-verbose-plan.md, slice 1), kept apart from React so they can be tested.
// Steps are replayed with a delay, so a step's facts can arrive before the step is drawn: facts are kept in a map keyed by step id and
// merged into the steps at render time, which makes the order they arrive in irrelevant.
import type { TraceStep } from './TracePanel';

export const MAX_FACT_LINES = 10;
export const MAX_FACT_CHARS = 200;

export type TraceFactsById = Record<string, string[]>;

/** What a read step read: the file, a capped redacted piece of its text, and whether it was cut. The live trace only; never stored. */
export interface TraceExcerpt { kind: 'read'; path: string; text: string; truncated: boolean; }
export type TraceExcerptsById = Record<string, TraceExcerpt>;
export const MAX_EXCERPT_CHARS = 1000;
export const MAX_EXCERPT_PATH_CHARS = 300;

/** What the server sent, kept to the same limits the server applies. Anything that is not a read excerpt with text is dropped. */
export function cleanExcerpt(raw: unknown): TraceExcerpt | null {
  if (!raw || typeof raw !== 'object') return null;
  const item = raw as Record<string, unknown>;
  if (item.kind !== 'read' || typeof item.text !== 'string' || !item.text.trim()) return null;
  const path = typeof item.path === 'string' ? item.path.slice(0, MAX_EXCERPT_PATH_CHARS) : '';
  return { kind: 'read', path, text: item.text.slice(0, MAX_EXCERPT_CHARS), truncated: item.truncated === true || item.text.length > MAX_EXCERPT_CHARS };
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
