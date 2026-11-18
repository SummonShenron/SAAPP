// Run with: node --test src/components/patchyLook.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { TURN_SENT_STATUS, budgetFromLevel, lookForNode, lookForStep, resolveMood } from './patchyLook.ts';
import { STEP_KINDS, stepKindOf } from './stepKinds.ts';

const calm = { laughing: false, nudged: false, faulted: false, celebrating: false, watching: false, drowsy: false, look: 'idle', budget: 'ok' };

test('a turn that has just been sent, and the planning steps, look like thinking', () => {
  for (const node of ['', null, undefined, TURN_SENT_STATUS, 'coordinator_node', 'reasoner_node', 'memory_node']) {
    assert.equal(lookForNode(node), 'thinking', String(node));
  }
});

test('the searching steps get the magnifying glass', () => {
  for (const node of ['retrieve_node', 'grade_documents_node', 'web_search_node', 'github_search', 'pr_summary']) {
    assert.equal(lookForNode(node), 'searching', node);
  }
});

// The tool agent (the only loop) reports every step as the node 'tool_agent_node' and names the tool apart, so the node alone never told a search from typing: the magnifying glass never came out (2026-11-18).
test('a tool step looks like what the tool does: looking things up is the magnifying glass, planning is thinking, the rest is typing', () => {
  for (const tool of ['web_search', 'read_url', 'search_code', 'search_literal', 'find_file', 'search_memory', 'search_library', 'run_mongo_query', 'read_repo_file', 'read_library_file', 'search_gmail', 'list_google_calendar_events', 'look_at_local_page']) {
    assert.equal(lookForStep('tool_agent_node', tool), 'searching', tool);
  }
  for (const tool of ['propose_steps', 'submit_plan']) assert.equal(lookForStep('tool_agent_node', tool), 'thinking', tool);
  for (const tool of ['run_python', 'run_repo_tests', 'propose_local_edits', 'propose_commit', 'ask_choice', 'a_tool_nobody_has_heard_of']) assert.equal(lookForStep('tool_agent_node', tool), 'writing', tool);
});

test('a step with no tool is looked at by its node, exactly as before', () => {
  for (const node of ['', null, undefined, TURN_SENT_STATUS, 'coordinator_node', 'retrieve_node', 'web_search_node', 'rewrite_query_node', 'generate_node', 'tool_agent_node']) {
    assert.equal(lookForStep(node, ''), lookForNode(node), String(node));
    assert.equal(lookForStep(node), lookForNode(node), String(node));
    assert.equal(lookForStep(node, null), lookForNode(node), String(node));
  }
  assert.equal(lookForStep('tool_agent_node', ''), 'writing');
});

test('the glass follows the step icon: a kind that looks something up gets it, and no kind is left out by accident', () => {
  const glass = STEP_KINDS.filter(kind => ['web', 'search', 'database', 'read', 'mail', 'calendar', 'map', 'browser'].includes(kind));
  assert.equal(glass.length, 8);
  for (const kind of STEP_KINDS) {
    // a tool of that kind, found by asking the step-kind rules for any tool name that gives it
    const tool = ['web_search', 'search_code', 'search_memory', 'read_repo_file', 'search_gmail', 'list_google_calendar_events', 'look_at_local_page', 'run_python', 'propose_steps', 'ask_choice'].find(name => stepKindOf(name, 'tool_agent_node') === kind);
    if (!tool) continue;
    const look = lookForStep('tool_agent_node', tool);
    assert.equal(look === 'searching', glass.includes(kind), `${kind} via ${tool} is ${look}`);
  }
});

test('the page keeps the tool of the newest step and gives Patchy the look for it, clearing it whenever the status is cleared', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  assert.match(chat, /import \{ TURN_SENT_STATUS, budgetFromLevel, inToolLoop, lookForStep \} from '\.\.\/components\/patchyLook';/);
  assert.match(chat, /const \[agentTool, setAgentTool\] = useState<string>\(''\);/);
  assert.match(chat, /setAgentStatus\(item\.node\);\n\s+setAgentTool\(item\.tool \?\? ''\);/);
  assert.match(chat, /look=\{loading \? lookForStep\(agentStatus, agentTool\) : 'idle'\}/);
  assert.doesNotMatch(chat, /lookForNode/, 'the node alone cannot tell a search from typing');
  assert.equal((chat.match(/setAgentStatus\(''\); setAgentTool\(''\);/g) ?? []).length, 3, 'every place the status is cleared clears the tool too');
  assert.match(chat, /setAgentStatus\(TURN_SENT_STATUS\);\n\s+setAgentTool\(''\);/);
});

test('the retry steps look puzzled', () => {
  assert.equal(lookForNode('rewrite_query_node'), 'puzzled');
  assert.equal(lookForNode('reward_evaluator'), 'puzzled');
});

test('the steps that write the answer, run code or draft a PR are typing, and so is any step we have never heard of', () => {
  for (const node of ['conversational_node', 'generate_node', 'formatter_node', 'summarizer_node', 'code_interpreter_node', 'draft_pr_node', 'execute_pr_node', 'some_new_node']) {
    assert.equal(lookForNode(node), 'writing', node);
  }
});

test('the budget level maps to what Patchy shows, and anything unexpected counts as fine', () => {
  assert.equal(budgetFromLevel('notice'), 'notice');
  assert.equal(budgetFromLevel('throttled'), 'throttled');
  assert.equal(budgetFromLevel('blocked'), 'blocked');
  for (const level of ['ok', 'off', '', null, undefined, 'weird']) assert.equal(budgetFromLevel(level), 'ok', String(level));
});

test('nothing going on is idle', () => {
  assert.equal(resolveMood(calm), 'idle');
});

test('a poke is answered over everything, including a failure and a running turn', () => {
  assert.equal(resolveMood({ ...calm, laughing: true, faulted: true, look: 'writing' }), 'laugh');
  assert.equal(resolveMood({ ...calm, nudged: true, faulted: true }), 'soft');
});

test('a failure outranks a running turn, a celebration and a pause', () => {
  assert.equal(resolveMood({ ...calm, faulted: true, look: 'writing', celebrating: true, budget: 'blocked' }), 'error');
});

test('a running turn shows its look and is not interrupted by watching the box or sleepiness', () => {
  assert.equal(resolveMood({ ...calm, look: 'searching', watching: true, drowsy: true }), 'active');
});

test('a finished turn celebrates, even over a blocked budget, but not over a failure', () => {
  assert.equal(resolveMood({ ...calm, celebrating: true, budget: 'blocked' }), 'done');
});

test('a blocked budget puts him to sleep, over watching and drowsiness', () => {
  assert.equal(resolveMood({ ...calm, budget: 'blocked', watching: true, drowsy: true }), 'asleep');
});

test('engaging with the box makes him watch, and a long quiet makes him drowsy, in that order', () => {
  assert.equal(resolveMood({ ...calm, watching: true, drowsy: true }), 'watching');
  assert.equal(resolveMood({ ...calm, drowsy: true }), 'drowsy');
});

test('a throttled or notice-level budget does not change the mood by itself', () => {
  assert.equal(resolveMood({ ...calm, budget: 'throttled' }), 'idle');
  assert.equal(resolveMood({ ...calm, budget: 'notice' }), 'idle');
});

test('a fist pump shows praise, and the sad face shows sad, over a quiet or finished turn', () => {
  assert.equal(resolveMood({ ...calm, praised: true }), 'praise');
  assert.equal(resolveMood({ ...calm, praised: true, celebrating: true }), 'praise');
  assert.equal(resolveMood({ ...calm, sad: true, drowsy: true }), 'sad');
});

test('praise and sad still yield to a poke, a failure and a running turn', () => {
  assert.equal(resolveMood({ ...calm, praised: true, laughing: true }), 'laugh');
  assert.equal(resolveMood({ ...calm, sad: true, faulted: true }), 'error');
  assert.equal(resolveMood({ ...calm, praised: true, look: 'writing' }), 'active');
});
