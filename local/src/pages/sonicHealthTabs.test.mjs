// Run with: node --test src/pages/sonicHealthTabs.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { GROUPS, TABS, groupBadge, groupOf, isTab, labelOf, nextEntry, nextTop } from './sonicHealthTabs.ts';

test('every section is in exactly one group, and no group lists a section that does not exist', () => {
  const listed = GROUPS.flatMap(group => [...group.tabs]);
  assert.equal(listed.length, new Set(listed).size);
  assert.deepEqual([...listed].sort(), TABS.map(t => t.id).sort());
  for (const group of GROUPS) assert.ok(group.tabs.length >= 1 && group.label);
  assert.equal(new Set(GROUPS.map(g => g.id)).size, GROUPS.length);
});

test('a group id never collides with a section id of another group (their element ids are sh-tab-<id>)', () => {
  const single = new Set(GROUPS.filter(g => g.tabs.length === 1).map(g => g.tabs[0]));
  for (const group of GROUPS.filter(g => g.tabs.length > 1)) {
    assert.ok(!TABS.some(t => t.id === group.id), `${group.id} is also a section`);
    assert.ok(!single.has(group.id));
  }
});

test('the menu shrinks fifteen flat tabs to six items, with Home and Safety opening directly', () => {
  assert.equal(TABS.length, 15);
  assert.equal(GROUPS.length, 6);
  assert.deepEqual(GROUPS.filter(g => g.tabs.length === 1).map(g => g.id), ['home', 'safety']);
  assert.equal(GROUPS[0].id, 'home');
});

test('a section finds its group and its label, and only real sections are accepted', () => {
  assert.equal(groupOf('budget').id, 'spend');
  assert.equal(groupOf('goals').id, 'quality');
  assert.equal(groupOf('selfmodel').id, 'sonic');
  assert.equal(groupOf('safety').id, 'safety');
  assert.equal(labelOf('knowledge'), 'Knowledge graph');
  assert.equal(labelOf('experiences'), 'Its record');
  assert.equal(isTab('home'), true);
  for (const bad of ['', 'spend', 'Home', null, undefined, 'usage ']) assert.equal(isTab(bad), false);
});

test('a closed group shows a bang when anything in it needs attention, a dot for news, and a group of one passes its own badge through', () => {
  const quality = GROUPS.find(g => g.id === 'quality');
  assert.equal(groupBadge(quality, {}), null);
  assert.deepEqual(groupBadge(quality, { observations: { text: '3', tone: 'info' } }), { text: '•', tone: 'info' });
  assert.deepEqual(groupBadge(quality, { observations: { text: '3', tone: 'info' }, feedback: { text: '2 down', tone: 'info' } }), { text: '•', tone: 'info' });
  assert.deepEqual(groupBadge(quality, { observations: { text: '3', tone: 'info' }, behavior: { text: '!', tone: 'warn' } }), { text: '!', tone: 'warn' });
  const safety = GROUPS.find(g => g.id === 'safety');
  assert.deepEqual(groupBadge(safety, { safety: { text: '!', tone: 'warn' } }), { text: '!', tone: 'warn' });
  const spend = GROUPS.find(g => g.id === 'spend');
  assert.deepEqual(groupBadge(spend, { budget: { text: 'PAUSED', tone: 'warn' } }), { text: '!', tone: 'warn' });
  assert.equal(groupBadge(spend, { safety: { text: '!', tone: 'warn' } }), null);                  // a badge on a section in another group is not this group's
});

test('every section that can carry a badge still can, in the group it lives in', () => {
  const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');
  for (const id of [...page.matchAll(/badges\.(\w+) = /g)].map(m => m[1])) assert.ok(isTab(id), id);
});

test('the arrow keys move across the groups and wrap, and Home and End go to the ends', () => {
  assert.equal(nextTop(0, 'ArrowRight', 6), 1);
  assert.equal(nextTop(5, 'ArrowRight', 6), 0);
  assert.equal(nextTop(0, 'ArrowLeft', 6), 5);
  assert.equal(nextTop(3, 'Home', 6), 0);
  assert.equal(nextTop(3, 'End', 6), 5);
  for (const key of ['ArrowDown', 'ArrowUp', 'Enter', ' ', 'a', 'Tab']) assert.equal(nextTop(2, key, 6), null, key);
  assert.equal(nextTop(0, 'ArrowRight', 0), null);
});

test('inside an open list up and down move and wrap, and Home and End go to the ends', () => {
  assert.equal(nextEntry(0, 'ArrowDown', 3), 1);
  assert.equal(nextEntry(2, 'ArrowDown', 3), 0);
  assert.equal(nextEntry(0, 'ArrowUp', 3), 2);
  assert.equal(nextEntry(1, 'Home', 3), 0);
  assert.equal(nextEntry(1, 'End', 3), 2);
  for (const key of ['ArrowLeft', 'ArrowRight', 'Enter', 'Escape', 'Tab']) assert.equal(nextEntry(1, key, 3), null, key);
});

test('what the page and the old links relied on is unchanged: the section ids, the remembered key and Home opening a section by id', () => {
  const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');
  assert.match(page, /const LAST_TAB_KEY = 'sonic-health-tab';/);
  assert.match(page, /navigate\(`\/sonic-health\/\$\{next\}`\)/);
  assert.match(page, /onOpen=\{selectTab\}/);
  assert.match(page, /<SonicHealthMenu tab=\{tab\} onSelect=\{selectTab\} badges=\{badges\} \/>/);
  assert.doesNotMatch(page, /role="tablist"/);
});

test('the menu is a disclosure navigation: expanded state on the group, current page on the section, Escape and arrows handled, a click outside closes it', () => {
  const menu = readFileSync(new URL('./SonicHealthMenu.tsx', import.meta.url), 'utf8');
  assert.match(menu, /aria-expanded=\{single \? undefined : expanded\}/);
  assert.match(menu, /aria-current=\{tab === entry \? 'page' : undefined\}/);
  assert.match(menu, /aria-haspopup=\{single \? undefined : 'true'\}/);
  assert.match(menu, /event\.key === 'Escape'/);
  assert.match(menu, /document\.addEventListener\('mousedown'/);
  assert.match(menu, /tabIndex=\{showing \? 0 : -1\}/);                                              // one stop in the bar for Tab, arrows do the rest
});
