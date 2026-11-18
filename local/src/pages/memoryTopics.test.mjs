// Run with: node --test src/pages/memoryTopics.test.mjs   (from local/)
// The Memory page's topics (2026-11-18): a panel at the left (Coming up, All memories, one per category) and the list of the topic picked beside it. Rules in pages/memoryTopics.ts, drawn by pages/Memory.tsx.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { ALL_TOPIC, COMING_UP, activeTopicOf, topicLabel, topicList } from './memoryTopics.ts';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const page = read('./Memory.tsx');
const css = read('./__styles__/Memory.css');

test('a topic is named with a capital, and the special ones cannot be mistaken for a category', () => {
  assert.equal(topicLabel('preference'), 'Preference');
  assert.equal(topicLabel('relationship'), 'Relationship');
  assert.equal(topicLabel(''), '');
  assert.equal(topicLabel('x'), 'X');
  assert.equal(ALL_TOPIC, 'all');
  assert.notEqual(COMING_UP, ALL_TOPIC);
  assert.ok(COMING_UP.startsWith('__'), 'it cannot be the name of a category, which are plain words');
});

test('the panel lists coming up only when there are upcoming items, then everything, then the categories as given', () => {
  assert.deepEqual(topicList(['career', 'goal'], 2), [COMING_UP, 'all', 'career', 'goal']);
  assert.deepEqual(topicList(['career'], 0), ['all', 'career']);
  assert.deepEqual(topicList([], 0), ['all']);
});

test('a topic with nothing left in it falls back to everything, so the page never shows a topic that is no longer in the panel', () => {
  assert.equal(activeTopicOf('all', ['career'], 0), 'all');
  assert.equal(activeTopicOf('career', ['career', 'goal'], 0), 'career');
  assert.equal(activeTopicOf('goal', ['career'], 0), 'all', 'the last goal was forgotten');
  assert.equal(activeTopicOf(COMING_UP, ['career'], 3), COMING_UP);
  assert.equal(activeTopicOf(COMING_UP, ['career'], 0), 'all', 'the last upcoming item was removed');
  assert.equal(activeTopicOf('anything', [], 0), 'all');
  assert.equal(activeTopicOf('career', [], 5), 'all', 'no categories at all');
});

test('the page draws the panel and one list at a time, filters the saved memories by the topic, and the old category dropdown is gone', () => {
  assert.match(page, /import \{ ALL_TOPIC, COMING_UP, activeTopicOf, topicLabel \} from '\.\/memoryTopics';/);
  assert.match(page, /const \[topic, setTopic\] = useState<string>\(ALL_TOPIC\);/);
  assert.match(page, /const activeTopic = activeTopicOf\(topic, categories, loops\.length\);/);
  assert.match(page, /\.filter\(f => activeTopic === ALL_TOPIC \|\| f\.category === activeTopic\)/);
  assert.match(page, /<nav className="memory-topics" aria-label="Topics" data-help="memory\.category">/);          // the Help Mode tip for categories now sits on the panel
  assert.match(page, /\{loops\.length > 0 && <TopicButton active=\{activeTopic === COMING_UP\} label="Coming up"/);
  assert.match(page, /\{categories\.map\(cat => \(\s+<TopicButton key=\{cat\} active=\{activeTopic === cat\} label=\{topicLabel\(cat\)\}/);
  assert.match(page, /\{activeTopic === COMING_UP && \(\s+<section className="service-card memory-card" data-help="memory\.comingUp">/);
  assert.match(page, /\{activeTopic !== COMING_UP && \(\s+<section className="service-card memory-card" data-help="memory\.saved">/);
  assert.doesNotMatch(page, /categoryFilter|setCategoryFilter/);
  assert.doesNotMatch(page, /All categories/);
  // the rows, editing, forgetting and linking are the page's own code, untouched: the same handlers are still wired
  for (const name of ['handleDeleteFact', 'handleSaveEdit', 'handleSetDomain', 'handleUnlink', 'handleDifferentPeople', 'handleDeleteLoop', 'saveLoopChanges', 'handleClearAll']) assert.ok(page.includes(name), name);
  assert.match(page, /\{activeTopic === ALL_TOPIC \? `\$\{facts\.length\} SAVED` : `\$\{filteredFacts\.length\} OF \$\{facts\.length\}`\}/);
});

test('the layout is a topics panel beside the list, and on a phone the topics are a scrolling row of chips; the stacked search box keeps its natural height', () => {
  assert.match(css, /\.memory-layout \{\s+display: grid;\s+grid-template-columns: 230px minmax\(0, 1fr\);/);
  assert.match(css, /\.memory-topics \{\s+position: sticky;\s+top: 1rem;\s+display: flex;\s+flex-direction: column;/);
  assert.match(css, /\.memory-topic\.is-active \{ background: color-mix\(in srgb, var\(--accent-primary\) 14%, transparent\);/);
  assert.match(css, /\.memory-topic \.memory-topic-dot \{ background: currentColor; \}/);          // a category's dot is its own colour
  const phone = css.slice(css.indexOf('@media (max-width: 860px) {'));
  assert.match(phone, /\.memory-layout \{ grid-template-columns: minmax\(0, 1fr\); gap: 1rem; \}/);
  assert.match(phone, /\.memory-topics \{\s+position: static;\s+flex-direction: row;[\s\S]*?overflow-x: auto;/);
  assert.match(phone, /\.memory-topics-title \{ display: none; \}/);
  // the controls row is a column on a phone: the search box's 240px flex basis was its HEIGHT (a 240px-tall box) until it was told not to flex
  assert.match(css, /\.memory-controls-row \.manifest-search-input \{\s+max-width: 100%;\s+flex: 0 0 auto;/);
});
