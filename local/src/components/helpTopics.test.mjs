// Run with: node --test src/components/helpTopics.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { DEVELOPER_GROUP, GROUPS, TOPICS, groupedTopics, visibleTopics } from './helpTopics.ts';

const content = readFileSync(new URL('./helpContent.tsx', import.meta.url), 'utf8');
const [everyday, developer] = content.split('// ---- For developers');

test('every topic has a unique id, a short title, and a known group', () => {
  assert.equal(new Set(TOPICS.map(t => t.id)).size, TOPICS.length);
  for (const topic of TOPICS) {
    assert.ok(topic.title.length > 0 && topic.title.length <= 40, topic.title);
    assert.ok(GROUPS.includes(topic.group), topic.id);
  }
});

test('every topic has words, in the right section: developer topics in the developer part, the rest in the everyday part', () => {
  for (const topic of TOPICS) {
    const key = new RegExp(`^\\s*(?:'${topic.id}'|${topic.id}):`, 'm');
    const section = topic.group === DEVELOPER_GROUP ? developer : everyday;
    assert.match(section, key, `${topic.id} is missing from its section of helpContent.tsx`);
  }
});

test('a guest sees only what applies to a guest, and the account topics are for signed-in people', () => {
  const guestIds = visibleTopics(true).map(t => t.id);
  for (const needsAccount of ['tidy', 'google', 'library', 'memory', 'profile']) {
    assert.ok(!guestIds.includes(needsAccount), needsAccount);
    assert.ok(visibleTopics(false).some(t => t.id === needsAccount), needsAccount);
  }
  for (const forEveryone of ['start', 'try', 'attach', 'privacy', 'about-sonic', 'dev-built']) assert.ok(guestIds.includes(forEveryone), forEveryone);
});

test('the groups come in order, the developer group is last, and an empty group is left out', () => {
  const names = groupedTopics(false).map(g => g.group);
  assert.deepEqual(names, [...GROUPS]);
  assert.equal(names.at(-1), DEVELOPER_GROUP);
  assert.ok(groupedTopics(true).every(g => g.topics.length > 0));
  assert.equal(groupedTopics(true)[0].topics[0].id, 'start');           // the panel opens on "What Sonic can do"
});

test('the first thing a signed-in person reads is about them, with no technical terms anywhere in the everyday topics', () => {
  const banned = /x-user-id|directory\.json|\bprincipal\b|LangGraph|\bRAG\b|\baffiliate|\bembedding|\bvector\b|(?<![.\w])JSON\b|\bAPI\b/i;   // JSON is jargon in prose, but a file ending (.json) is something people need to know
  const found = everyday.match(banned);
  assert.equal(found, null, `jargon in the everyday help: ${found && found[0]}`);
});

test('what the help says about how long attachments are kept matches the privacy page and the account page', () => {
  assert.match(everyday, /30 days/);
  assert.match(readFileSync(new URL('../pages/Privacy.tsx', import.meta.url), 'utf8'), /30 days/);
  assert.match(readFileSync(new URL('../pages/Account.tsx', import.meta.url), 'utf8'), /30 days/);
});

test('the pages the help sends people to exist as routes', () => {
  const app = readFileSync(new URL('../App.tsx', import.meta.url), 'utf8');
  const paths = [...everyday.matchAll(/path="(\/[a-z-]+)"/g)].map(m => m[1]);
  assert.ok(paths.length >= 5);
  for (const path of new Set(paths)) assert.ok(app.includes(`path="${path}"`), `no route for ${path}`);
});
