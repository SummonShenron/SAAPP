// What kind of step a trace step is, so its dot can be a little icon of what is being done (components/StepIcon.tsx) while it runs: </> for code, a globe for the web, a cylinder for a database, a chat bubble for
// conversation, a book for reading, and one general icon for any step that has no icon of its own. No React in here, so it can be tested (stepKinds.test.mjs).
//
// WHERE THE KIND COMES FROM: the server already sends the tool's name with a tool step and the workflow node's name with every step (backend/services/agent_workflow.py, native_loop.py), and the page uses them to
// word the step (traceFacts.ts toolStepLabel, Chat.tsx getNodeLabel). The kind is worked out from those two and nothing else, never from the step's words, so a reworded title cannot change an icon. A tool or node
// with no entry here is 'generic' (stepKinds.test.mjs fails when a tool in the agent's menu has no entry, so a new tool is given an icon, or is named there as having none, when it is added).

export type StepKind = 'code' | 'web' | 'database' | 'chat' | 'read' | 'search' | 'mail' | 'calendar' | 'map' | 'think' | 'browser' | 'inject' | 'add' | 'generic';

export const STEP_KINDS: readonly StepKind[] = ['code', 'web', 'database', 'chat', 'read', 'search', 'mail', 'calendar', 'map', 'think', 'browser', 'inject', 'add', 'generic'];

/** Tool name -> kind. Names are the agent's own (the "- name — ..." lines of its menu). */
const TOOL_KIND: Readonly<Record<string, StepKind>> = {
  // writing, running and changing code (and the git side of it)
  run_python: 'code', run_snippet: 'code', run_local_command: 'code', run_local_check: 'code', run_repo_tests: 'code', run_sandbox_tests: 'code',
  propose_local_edits: 'code', propose_commit: 'code', propose_git_commit: 'code', propose_merge: 'code', propose_append_target_doc: 'code',
  start_local_server: 'code', stop_local_server: 'code', diff_branches: 'code', list_commits: 'code', list_pull_requests: 'code', check_deploy_status: 'code',
  // the web
  web_search: 'web', read_url: 'web',
  // a database: the person's memory, knowledge base and documents, and the app's own records
  run_mongo_query: 'database', search_memory: 'database', search_my_knowledge: 'database', search_library: 'database', propose_kb_entry: 'database',
  // reading one thing
  read_repo_file: 'read', read_library_file: 'read', read_drive_file: 'read', read_onedrive_file: 'read', read_app_incident: 'read', read_app_logs: 'read',
  // looking for something
  search_code: 'search', search_literal: 'search', find_file: 'search', list_repo_tree: 'search', trace_symbol: 'search', list_library_files: 'search',
  search_drive_files: 'search', search_onedrive_files: 'search', list_app_incidents: 'search',
  // mail and calendar
  search_gmail: 'mail', read_gmail_message: 'mail', get_gmail_attachment: 'mail', search_outlook_mail: 'mail', read_outlook_message: 'mail', get_outlook_attachment: 'mail', propose_send_email: 'mail',
  list_google_calendar_events: 'calendar', list_outlook_calendar_events: 'calendar',
  // looking at a page in a browser
  look_at_local_page: 'browser',
  // working out a plan, and talking to the person
  propose_steps: 'think', submit_plan: 'think',
  ask_choice: 'chat',
};

/** Workflow node name -> kind, for a step that names no tool. */
const NODE_KIND: Readonly<Record<string, StepKind>> = {
  conversational_node: 'chat', formatter_node: 'chat', generate_node: 'chat', summarizer_node: 'chat', progress_note_node: 'chat',
  coordinator_node: 'think', reasoner_node: 'think', rewrite_query_node: 'think', reward_evaluator: 'think', reply_fit_check: 'think',
  memory_node: 'database', memory_recall_node: 'database', memory_save_node: 'database', retrieve_node: 'database', grading_node: 'database', grade_documents_node: 'database',
  web_search_node: 'web',
  code_interpreter_node: 'code', pr_summary: 'code', pr_summarizer_node: 'code', draft_pr_node: 'code', execute_pr_node: 'code', propose_write_node: 'code', execute_write_node: 'code',
  github_search: 'search',
  // what is put into the prompt before the answer is written (apps/app.py's own steps): saved context and lessons are injected, an attachment's text is added
  user_memory: 'inject', self_correction_guardrail: 'inject', earlier_attachments: 'add', attachment_context: 'add',
  user_memory_recall: 'database', goal_nudge_checkin: 'chat',
};

/** A table's own entry for a name, never something every object has ("toString", "constructor"): those are not kinds. */
function own(table: Readonly<Record<string, StepKind>>, name: string): StepKind | undefined {
  return Object.prototype.hasOwnProperty.call(table, name) ? table[name] : undefined;
}

/** The kind of a step from the tool it used and, with no tool, the node it ran in. Anything not named is 'generic'. Pure. */
export function stepKindOf(tool: unknown, node: unknown): StepKind {
  if (typeof tool === 'string' && tool) {
    if (tool.startsWith('browser_')) return 'browser';
    if (tool.startsWith('mcp__maps__')) return 'map';
    return own(TOOL_KIND, tool) ?? 'generic';
  }
  if (typeof node === 'string' && node) return own(NODE_KIND, node) ?? 'generic';
  return 'generic';
}

/** What a kind of step is called when its small icon is pointed at (the summary beside a finished reply's "7 steps"). It says what KIND of step it was, never what it found. */
export const KIND_NAME: Readonly<Record<StepKind, string>> = {
  code: 'Ran or changed code', web: 'Used the web', database: 'Looked in memory or a database', chat: 'Talked or wrote the answer', read: 'Read something', search: 'Searched',
  mail: 'Checked mail', calendar: 'Checked the calendar', map: 'Looked up places', think: 'Worked something out', browser: 'Used a browser',
  inject: 'Added saved context to the prompt', add: 'Added an attachment\'s text', generic: 'Took a step',
};

/** What the "7 steps" line above a finished reply shows beside its count: the kind of each step that did something (not the closing summary step), in order, at most `max`, and how many more there were. Pure. */
export function kindSummary(steps: ReadonlyArray<{ kind?: StepKind; turnEnd?: unknown }>, max = 8): { kinds: StepKind[]; more: number } {
  const all = steps.filter(step => !step.turnEnd).map(step => cleanKind(step.kind) ?? 'generic');
  return { kinds: all.slice(0, max), more: Math.max(0, all.length - max) };
}

/** A kind the page knows, or undefined (anything else is dropped, and the step is drawn with the general icon). Pure. */
export function cleanKind(value: unknown): StepKind | undefined {
  return typeof value === 'string' && (STEP_KINDS as readonly string[]).includes(value) ? (value as StepKind) : undefined;
}
