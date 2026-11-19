// Run with: node --experimental-test-module-mocks --test src/components/stepKinds.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// Which little icon a running step's dot is (components/stepKinds.ts rules, components/StepIcon.tsx drawings, __styles__/StepIcons.css motion), and how the page hands the kind to the step.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { KIND_NAME, STEP_KINDS, cleanKind, kindSummary, stepKindOf } from './stepKinds.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const icon = read('./StepIcon.tsx');
const iconCss = read('./__styles__/StepIcons.css');
const traceSteps = read('./TraceSteps.tsx');
const traceCss = read('./__styles__/TracePanel.css');
const chat = read('../pages/Chat.tsx');
const facts = read('./traceFacts.ts');
const workflow = read('../../../backend/services/agent_workflow.py');

test('the examples the person asked for: code is </>, a web search the globe, a database search the database, a read the book, conversation the chat bubble, and anything else the general icon', () => {
  assert.equal(stepKindOf('run_python', 'tool_agent_node'), 'code');
  assert.equal(stepKindOf('propose_local_edits', 'tool_agent_node'), 'code');
  assert.equal(stepKindOf('web_search', 'tool_agent_node'), 'web');
  assert.equal(stepKindOf('read_url', 'tool_agent_node'), 'web');
  assert.equal(stepKindOf('run_mongo_query', 'tool_agent_node'), 'database');
  assert.equal(stepKindOf('search_memory', 'tool_agent_node'), 'database');
  assert.equal(stepKindOf('read_repo_file', 'tool_agent_node'), 'read');
  assert.equal(stepKindOf(undefined, 'conversational_node'), 'chat');
  assert.equal(stepKindOf(undefined, 'formatter_node'), 'chat');
  assert.equal(stepKindOf('search_code', 'tool_agent_node'), 'search');
  assert.equal(stepKindOf('search_gmail', 'tool_agent_node'), 'mail');
  assert.equal(stepKindOf('list_google_calendar_events', 'tool_agent_node'), 'calendar');
  assert.equal(stepKindOf('mcp__maps__find_places', 'tool_agent_node'), 'map');
  assert.equal(stepKindOf('browser_click', 'tool_agent_node'), 'browser');
  assert.equal(stepKindOf('look_at_local_page', 'tool_agent_node'), 'browser');
  assert.equal(stepKindOf(undefined, 'reasoner_node'), 'think');
  // the steps the server sends from app.py with no tool: what is injected into the prompt is a syringe, what is added from an attachment a plus
  assert.equal(stepKindOf(undefined, 'user_memory'), 'inject', 'Injected saved preferences/facts into context prompt.');
  assert.equal(stepKindOf(undefined, 'earlier_attachments'), 'add', 'Added what an earlier attachment in this conversation showed.');
  assert.equal(stepKindOf(undefined, 'attachment_context'), 'add');
  assert.equal(stepKindOf(undefined, 'self_correction_guardrail'), 'inject');
  assert.equal(stepKindOf(undefined, 'user_memory_recall'), 'database');
  assert.equal(stepKindOf(undefined, 'goal_nudge_checkin'), 'chat');
});

test('a tool decides the kind over the node, and a step with nothing the page knows gets the general icon, whatever it is sent', () => {
  assert.equal(stepKindOf('web_search', 'conversational_node'), 'web');
  assert.equal(stepKindOf('some_new_tool', 'reasoner_node'), 'generic', 'a tool it has never heard of is generic, not the node\'s kind');
  assert.equal(stepKindOf(undefined, 'tool_agent_node'), 'generic');
  assert.equal(stepKindOf(undefined, 'a_node_nobody_named'), 'generic');
  assert.equal(stepKindOf(undefined, undefined), 'generic');
  assert.equal(stepKindOf('', ''), 'generic');
  assert.equal(stepKindOf(42, {}), 'generic');
  assert.equal(stepKindOf(null, null), 'generic');
  for (const word of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) assert.equal(stepKindOf(word, word), 'generic', `${word} is not an own entry`);
});

test('cleanKind keeps the kinds the page knows and drops everything else', () => {
  for (const kind of STEP_KINDS) assert.equal(cleanKind(kind), kind);
  for (const bad of ['', 'Code', 'rocket', null, undefined, 7, {}, ['code']]) assert.equal(cleanKind(bad), undefined, JSON.stringify(bad));
  assert.equal(STEP_KINDS.at(-1), 'generic');
  assert.equal(new Set(STEP_KINDS).size, STEP_KINDS.length);
});

// ---- the tools and nodes that really exist: the same names the server sends --------------------------------------------------------------------------------------------------------------------------

test('every tool in the agent\'s own menu has an icon of its own: a new tool added without one fails here', () => {
  const tools = [...workflow.matchAll(/"- ([a-z_]+) — /g)].map(m => m[1]);
  assert.ok(tools.length > 50, `found the menu's tools (${tools.length})`);
  const none = new Set([]);          // tools that may be drawn with the general icon on purpose: name them here
  const generic = [...new Set(tools)].filter(tool => stepKindOf(tool, 'tool_agent_node') === 'generic' && !none.has(tool));
  assert.deepEqual(generic, [], `these tools have no icon: ${generic.join(', ')}`);
});

test('every tool the page words a step for (traceFacts TOOL_LABELS) has an icon too', () => {
  const block = facts.slice(facts.indexOf('const TOOL_LABELS'), facts.indexOf('};', facts.indexOf('const TOOL_LABELS')));
  const names = [...block.matchAll(/^\s+([a-z_]+): '/gm)].map(m => m[1]);
  assert.ok(names.length >= 20, `found the labelled tools (${names.length})`);
  for (const tool of names) assert.notEqual(stepKindOf(tool, 'tool_agent_node'), 'generic', `${tool} has an icon`);
  assert.equal(stepKindOf('mcp__maps__travel_times', 'tool_agent_node'), 'map');
});

test('every workflow node the page words a step for (Chat.tsx getNodeLabel) has an icon, and so do the nodes the server names in its events', () => {
  const body = chat.slice(chat.indexOf('const getNodeLabel'), chat.indexOf('export const ChatPage'));
  const nodes = [...body.matchAll(/case '([a-z_]+)':/g)].map(m => m[1]);
  assert.ok(nodes.length >= 15, `found the labelled nodes (${nodes.length})`);
  for (const node of nodes) assert.notEqual(stepKindOf(undefined, node), 'generic', `${node} has an icon`);
  const emitted = new Set([
    ...read('../../../backend/services/agent_workflow.py').matchAll(/"node": "([a-z_]+)"/g),
    ...read('../../../app.py').matchAll(/'node': '([a-z_]+)'/g),          // app.py sends its own steps (saved context, attachments, the guardrail ...), in single quotes
  ].map(m => m[1]));
  assert.ok(emitted.has('user_memory') && emitted.has('earlier_attachments'), 'app.py\'s nodes are read too');
  const general = new Set(['tool_agent_node', 'image_gen_node', 'image_card_node']);          // these three are drawn with the general icon on purpose: they are not one kind of work
  for (const node of emitted) {
    if (general.has(node)) assert.equal(stepKindOf(undefined, node), 'generic', node);
    else assert.notEqual(stepKindOf(undefined, node), 'generic', `${node} (sent by the server) has an icon`);
  }
});

// ---- the drawing and the motion ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------

test('every kind has its own drawing, in the accent colour, hidden from assistive technology, and an unknown kind falls back to the general one', () => {
  for (const kind of STEP_KINDS) assert.match(icon, new RegExp(`^  ${kind}: \\(\\) =>`, 'm'), `${kind} is drawn`);
  assert.equal((icon.match(/^  [a-z]+: \(\) =>/gm) ?? []).length, STEP_KINDS.length, 'and nothing is drawn that has no kind');
  assert.match(icon, /aria-hidden="true" focusable="false"/);
  assert.match(icon, /width="14" height="14" viewBox="0 0 16 16"/);          // the dot's own 14px
  assert.match(icon, /const Shape = ICONS\[kind\] \?\? ICONS\.generic;/);
  assert.match(icon, /className=\{`si si-\$\{kind in ICONS \? kind : 'generic'\}\$\{still \? ' si-still' : ''\}`\}/);
  assert.match(iconCss, /\.si \{[^}]*color: var\(--accent-primary\);/);
});

test('every drawing moves, each in its own way, and holds still for reduced motion', () => {
  const moving = ['si-lt', 'si-gt', 'si-slash', 'si-meridian', 'si-layer', 'si-dot', 'si-line', 'si-sweep', 'si-flap', 'si-day', 'si-pin', 'si-twinkle', 'si-cursor', 'si-node', 'si-plunger', 'si-plus'];
  for (const name of moving) {
    assert.ok(icon.includes(`"${name}`) || icon.includes(` ${name}`), `${name} is on a drawing`);
    assert.match(iconCss, new RegExp(`\\.${name}[ ,{-][^{]*\\{[^}]*animation:`), `${name} has an animation`);
  }
  // each of the twelve drawings has at least one moving part
  for (const kind of STEP_KINDS) {
    const start = icon.indexOf(`  ${kind}: () =>`);
    const end = icon.indexOf('\n  [a-z]+: () =>', start + 5);
    const drawing = icon.slice(start, end > 0 ? end : icon.indexOf('};', start));
    assert.ok(moving.some(name => drawing.includes(name)), `${kind} has a moving part`);
  }
  const reduced = iconCss.slice(iconCss.lastIndexOf('@media (prefers-reduced-motion: reduce) {'));
  assert.match(reduced, /\.si \*, \.si \{ animation: none !important; \}/);
  assert.match(reduced, /\.si-flap \{ stroke-dashoffset: 0; \}/);          // the mail flap stands drawn, not half-erased
  assert.doesNotMatch(iconCss, /infinite[^;]*;[^}]*[^s]\{/, 'no stray rule');
});

// ---- how the page uses it -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

test('a step\'s dot is its icon: accent and moving while it runs, the same icon in gray and standing once it is done, a cross only when its tool failed, and no check or pulsing core any more', () => {
  assert.match(traceSteps, /<span className=\{`trace-node-dot\$\{failed \? '' : ' trace-node-dot-icon'\}`\} aria-hidden="true">/);          // every step but a failed one
  assert.match(traceSteps, /\{failed \? <Icon name="close" size=\{12\} \/> : <StepIcon kind=\{step\.kind\} still=\{done\} \/>\}/);          // done = the same icon, held still
  assert.doesNotMatch(traceSteps, /name="check"/, 'the checkmark is gone');
  assert.match(traceSteps, /kind\?: StepKind;/);
  assert.doesNotMatch(traceSteps, /trace-node-core/);
  assert.doesNotMatch(traceCss, /trace-node-core/);
  // a 20px dot centred where the old 14px one was (left -3px, top 0: the centre stays at 7px across and 10px down), so the line, the stub and the loader graph do not move
  const dot = traceCss.slice(traceCss.indexOf('.trace-node-dot {'), traceCss.indexOf('}', traceCss.indexOf('.trace-node-dot {')));
  assert.match(dot, /left: -3px;\s+top: 0;\s+width: 20px;\s+height: 20px;/);
  assert.equal(-3 + 20 / 2, 7, 'the centre across is the trunk, 7px');
  assert.equal(0 + 20 / 2, 10, 'and 10px down, where the loader graph\'s last node lands');
  assert.match(dot, /z-index: 3;/);          // above the graph (z-index 2): the end of a graph's last edge tucks under the dot
  assert.match(traceCss, /\.trace-node::before \{[^}]*top: 20px;/);          // the line and the stub start at the dot's lower edge
  assert.match(traceCss, /\.trace-node-running::after \{[^}]*top: 20px;/);
  // the icon dot has no ring and no pulsing ring, keeps the page's colour in both themes, is gray once done, and is the dot's whole size
  assert.match(traceCss, /\.trace-node-dot\.trace-node-dot-icon, \.trace-node-active \.trace-node-dot\.trace-node-dot-icon \{ border-width: 0; background: var\(--bg-main\); \}/);
  assert.match(traceCss, /\.theme-shadow \.trace-node-dot\.trace-node-dot-icon, \.theme-shadow \.trace-node-done \.trace-node-dot\.trace-node-dot-icon, \.theme-shadow \.trace-node-active \.trace-node-dot\.trace-node-dot-icon \{ border-width: 0; background: var\(--bg-main\); \}/);
  assert.match(traceCss, /\.trace-node-active \.trace-node-dot\.trace-node-dot-icon::after \{ display: none; \}/);
  assert.match(traceCss, /\.trace-node-dot \.si \{ width: 20px; height: 20px; \}/);
  assert.match(traceCss, /\.trace-node-done \.trace-node-dot \.si \{ color: var\(--text-muted\); \}/);
  // the colour eases from the accent to gray when the step finishes, and the old pop-in of the check is gone
  assert.match(iconCss, /\.si \{[^}]*transition: color 0\.3s ease;/);
  assert.doesNotMatch(iconCss + traceCss, /si-check/);
  assert.doesNotMatch(traceCss.slice(traceCss.lastIndexOf('@media (prefers-reduced-motion: reduce) {')), /trace-node-done \.trace-node-dot > svg/);
  assert.match(icon, /className=\{`si si-\$\{kind in ICONS \? kind : 'generic'\}\$\{still \? ' si-still' : ''\}`\}/);
});

test('the page works the kind out from the tool and node the server named, when a step is added, and keeps nothing else about them', () => {
  assert.match(chat, /import \{ cleanKind, stepKindOf \} from '\.\.\/components\/stepKinds';/);
  assert.match(chat, /kind: cleanKind\(payload\?\.kind\) \?\? stepKindOf\(payload\?\.tool, payload\?\.node\),/);
  const queued = chat.slice(chat.indexOf('const processNodeQueue'), chat.indexOf('const handleSendMessage'));
  assert.match(queued, /stepId: item\.stepId,\s+tool: item\.tool,\s+node: item\.node,/);          // the queue item already carries both
  assert.match(chat, /tool: payload\.tool, subject: payload\.subject,/);          // and it gets them from the server's event
});

test('every kind has a plain name for its tooltip, and the names say what kind of step it was, not what it found', () => {
  assert.deepEqual(Object.keys(KIND_NAME).sort(), [...STEP_KINDS].sort());
  for (const kind of STEP_KINDS) {
    assert.ok(KIND_NAME[kind].length > 3 && KIND_NAME[kind].length <= 40, kind);
    assert.doesNotMatch(KIND_NAME[kind], /found|learned|discovered|\d/, `${kind}: a name is a kind of step, never a result`);
  }
  assert.equal(new Set(Object.values(KIND_NAME)).size, STEP_KINDS.length, 'no two kinds share a name');
  assert.equal(KIND_NAME.inject, 'Added saved context to the prompt');
  assert.equal(KIND_NAME.add, 'Added an attachment\'s text');
});

// ---- the summary beside a finished reply's steps ----------------------------------------------------------------------------------------------------------------------------------------------------

test('the line above a finished reply shows the kind of each step that did something, in order, at most eight, and how many more', () => {
  const step = (kind, extra = {}) => ({ kind, ...extra });
  assert.deepEqual(kindSummary([step('think'), step('web'), step('code')]), { kinds: ['think', 'web', 'code'], more: 0 });
  assert.deepEqual(kindSummary([]), { kinds: [], more: 0 });
  assert.deepEqual(kindSummary([step('read'), step(undefined), step('rocket')]).kinds, ['read', 'generic', 'generic'], 'no kind, or one the page does not know, is the general icon');
  assert.deepEqual(kindSummary([step('read'), step('chat', { turnEnd: 'answered' })]).kinds, ['read'], 'the closing summary step is not something that was done');
  const twelve = Array.from({ length: 12 }, (_, i) => step(STEP_KINDS[i % 11]));
  const shown = kindSummary(twelve);
  assert.equal(shown.kinds.length, 8);
  assert.equal(shown.more, 4);
  assert.deepEqual(shown.kinds, twelve.slice(0, 8).map(s => s.kind));
  assert.deepEqual(kindSummary(twelve, 3), { kinds: twelve.slice(0, 3).map(s => s.kind), more: 9 });
});

test('the summary is drawn standing, small and muted, beside the count, and is decoration only', () => {
  const disclosure = read('./StepsDisclosure.tsx');
  assert.match(disclosure, /const kinds = kindSummary\(steps\);/);
  assert.match(disclosure, /<span className="steps-toggle-label">\{stepsLabel\(steps\.length\)\}<\/span>\s+\{\/\*[^*]*\*\/\}\s+\{kinds\.kinds\.length > 0 && \(\s+<span className=\{`steps-kinds\$\{live \? ' steps-kinds-live' : ''\}`\} aria-hidden="true">/);
  // a finished reply's icons all stand; while a turn runs the one for the step that is still working moves
  assert.match(disclosure, /\{kinds\.kinds\.map\(\(kind, i\) => <span key=\{i\} className="steps-kind" title=\{KIND_NAME\[kind\]\}><StepIcon kind=\{kind\} still=\{!\(live && shown\[i\]\?\.status === 'active'\)\} \/><\/span>\)\}/);
  assert.match(disclosure, /\{kinds\.more > 0 && <span className="steps-kinds-more">\+\{kinds\.more\}<\/span>\}/);
  assert.match(iconCss, /\.si-still, \.si-still \* \{ animation: none !important; \}/);
  assert.match(traceCss, /\.steps-kinds \.si \{ width: 14px; height: 14px; color: var\(--text-muted\); \}/);
  assert.match(icon, /still\?: boolean/);
});
