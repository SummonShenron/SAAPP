// Run with: node --test src/stepsAlwaysVisible.test.mjs   (from local/)
// 2026-11-18: the microscope button is gone from the top bar. Every reply always shows its steps: nodes while it runs, and after it a line with the number of steps, an icon for each kind and a little arrow that
// opens them (components/StepsDisclosure.tsx). There is no switch for it any more in the app's own page (the embedded widget keeps its own Trace menu entry).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const chat = read('./pages/Chat.tsx');
const disclosure = read('./components/StepsDisclosure.tsx');
const strip = text => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');

test('the top bar has no microscope button, no trace toggle and no dot beside it, and the icon itself is gone', () => {
  const code = strip(chat);
  assert.doesNotMatch(code, /microscope|Toggle execution trace|topbar-btn-dot|data-help="chat\.trace"/);
  assert.doesNotMatch(read('./components/icons.tsx'), /microscope/);
  assert.doesNotMatch(read('./components/__styles__/AppShell.css'), /topbar-btn-dot/);
  assert.doesNotMatch(read('./components/helpTips.ts'), /'chat\.trace'/, 'its help tip went with it');
  // the other buttons of the bar are still there
  assert.match(code, /aria-label="Toggle the terminal"/);
});

test('the steps are not a setting any more: the message list takes no showSteps, and every reply of the app\'s own page draws them', () => {
  const code = strip(chat);
  assert.doesNotMatch(code, /showSteps/);
  assert.match(code, /const liveMessageId = loading && !isEmbedded && lastMessage\?\.sender === 'ai' \? lastMessage\.id : null;/);
  assert.match(code, /\{msg\.sender === 'ai' && !isEmbedded && \(msg\.id === liveMessageId\s+\? <StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>\s+: replySteps && replySteps\.length > 0 && <StepsDisclosure steps=\{replySteps\} running=\{false\} latest=\{isNewestReply\} idPrefix=\{`reply-\$\{msg\.id\}`\} \/>\)\}/);
  assert.match(code, /liveMessageId === null && <StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>/, 'and a turn with no reply bubble yet draws its own');
  assert.doesNotMatch(code, /sonic-loader-container|sonic-ring-loader/, 'no second, quieter loader for when the steps were hidden');
});

test('after the reply there is the steps line: the number, an icon for each kind of step and the little arrow, always there and not behind a setting', () => {
  const line = disclosure.slice(disclosure.indexOf('const StepsLine'), disclosure.indexOf('const StepsDisclosure: React.FC'));
  assert.match(line, /<span className="steps-toggle-label">\{stepsLabel\(steps\.length\)\}<\/span>/, 'the count');
  assert.match(line, /kinds\.kinds\.map\(\(kind, i\) => <span key=\{i\} className="steps-kind" title=\{KIND_NAME\[kind\]\}><StepIcon /, 'an icon for each kind');
  assert.match(line, /<span className="steps-toggle-chevron" aria-hidden="true">▸<\/span>/, 'and the arrow');
  assert.match(line, /onClick=\{onToggle\}/, 'which opens and closes the steps');
  // the same line, from the same component, after the reply and while the turn runs
  assert.equal((disclosure.match(/<StepsLine /g) ?? []).length, 2);
  assert.match(disclosure, /\{!waiting && <StepsLine steps=\{steps\} open=\{open\} controls=\{bodyId\} onToggle=\{toggle\} live \/>\}/);
  assert.match(disclosure, /<StepsLine steps=\{steps\} open=\{open\} controls=\{bodyId\} onToggle=\{toggle\} \/>/);
});

test('the embedded widget keeps what it had: its own Trace menu entry and drawer', () => {
  const code = strip(chat);
  assert.match(code, /case 'Trace':\s+toggleTracePanel\(\);/);
  assert.match(code, /\{isEmbedded && showTracePanel && \(/);
  assert.match(code, /e\.item\.label !== 'Trace'/, 'and only the embedded widget keeps the menu entry');
});

test('the little arrow alone decides what a turn shows, while it runs and after: open draws the nodes under the line, closed leaves just the line, and the choice carries on when the turn ends', () => {
  // open unless the person pressed it: a running turn counts as the newest reply, so it starts open
  assert.match(disclosure, /const open = stepsAreOpen\(chosen, defaultStepsOpen\(running \|\| latest, preference\)\);/);
  assert.match(disclosure, /const body = waiting \|\| open;/);
  const running = disclosure.slice(disclosure.indexOf('  if (running) {'), disclosure.indexOf('  return (\n    <div className="steps-inline">'));
  assert.match(running, /\{body && \(\s+<div className=\{`gl-startwrap/, 'the nodes (and before the first step, the logo) are drawn only when open or waiting');
  assert.ok(running.indexOf('<TraceSteps') > running.indexOf('{body && ('), 'the live nodes are only inside it');
  assert.equal((running.match(/<TraceSteps/g) ?? []).length, 1);
  assert.match(running, /\{!waiting && open && hidden > 0 && \(/, 'the "earlier steps" button is part of the open view');
  // nothing else sets the choice: one setter, called only from the arrow
  assert.equal((disclosure.match(/setChosen\(/g) ?? []).length, 1, 'only the arrow sets it, and both lines use the same handler');
  // the one instance carries the choice from the running turn to the finished reply: Chat draws both from the same place in the same list
  assert.match(strip(chat), /\? <StepsDisclosure steps=\{liveSteps\} running idPrefix="live" \/>\s+: replySteps && replySteps\.length > 0 && <StepsDisclosure /);
});

test('the icons show up as the steps are taken: each new one pops in, the one for the step still working keeps moving, and the finished ones stand', () => {
  assert.match(disclosure, /still=\{!\(live && shown\[i\]\?\.status === 'active'\)\}/);
  const css = read('./components/__styles__/TracePanel.css');
  assert.match(css, /\.steps-kinds-live \.steps-kind \{ animation: gl-arrive \.3s ease backwards; \}/);
  assert.match(css, /@keyframes gl-arrive \{ from \{ opacity: 0; transform: scale\(\.7\); \} to \{ opacity: 1; transform: none; \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{[^]*\.steps-kinds-live \.steps-kind[^]*animation: none;/, 'still, for reduced motion');
  // an icon is keyed by its place in the list, so only a new step's icon is a new element (and animates)
  assert.match(disclosure, /<span key=\{i\} className="steps-kind"/);
});

test('with the steps closed the logo animation goes the whole time the turn runs, under the line, with the step Sonic is on beside it; open, the nodes are shown instead (the logo only before the first step)', () => {
  const running = disclosure.slice(disclosure.indexOf('  if (running) {'), disclosure.indexOf('  return (\n    <div className="steps-inline">'));
  assert.match(running, /const current = \[\.\.\.steps\]\.reverse\(\)\.find\(step => step\.status === 'active'\) \?\? steps\[steps\.length - 1\];/, 'the step that is working, else the newest');
  assert.match(running, /\{!waiting && !open && \(\s+<div className="gl-startwrap gl-start-wait" role="status">\s+<ThinkingLogo size=\{THINKING_LOGO_PX\} reducedMotion=\{reduced\} \/>\s+<span className="agent-status-text"><span className="steps-first-title">\{current\?\.title \|\| WAITING_TEXT\}<\/span><\/span>/);
  // after the line, before the nodes' own wrapper, and the two never show together
  assert.ok(running.indexOf('{!waiting && !open && (') > running.indexOf('<StepsLine'));
  assert.ok(running.indexOf('{!waiting && !open && (') < running.indexOf('{body && ('));
  assert.match(running, /const body = waiting \|\| open;/);
  // it is only while the turn runs: the finished reply's closed line has no logo
  const finished = disclosure.slice(disclosure.indexOf('  return (\n    <div className="steps-inline">'));
  assert.doesNotMatch(finished, /ThinkingLogo/);
  // it sits under the line the way the nodes do
  assert.match(read('./components/__styles__/TracePanel.css'), /\.steps-inline-live > \.steps-toggle \+ \.gl-startwrap \{ margin-top: 10px; \}/);
});
