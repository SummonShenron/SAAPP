// Run with: node --test src/railCollapsed.test.mjs   (from local/)
// The app rail collapsed to icons on a desktop (2026-11-18): no gray column; the logo sits in a gray box and the icons float. The expanded rail and the phone's drawer are untouched.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const css = readFileSync(new URL('./components/__styles__/AppShell.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const P = '.app-shell .app-rail.app-rail-compact:not(.app-rail-drawer)';
const escaped = P.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

test('the collapsed rail has no fill and no edge, so the icons float on the page', () => {
  assert.match(css, new RegExp(`${escaped} \\{ --rail-box: var\\(--bg-alt\\); background: transparent; border-right-color: transparent; \\}`));
  assert.match(css, /\.app-shell\.theme-shadow \.app-rail\.app-rail-compact:not\(\.app-rail-drawer\) \{ --rail-box: var\(--panel-bg\); \}/);          // the logo's box is gray in the dark theme too
});

test('the logo sits in a gray 40px box, the new-chat button floats, and the current page keeps a soft box', () => {
  assert.match(css, new RegExp(`${escaped} \\.rail-brand \\{ width: 40px; height: 40px; box-sizing: border-box; justify-content: center; padding: 0; border-radius: 12px; background: var\\(--rail-box\\); \\}`));
  assert.match(css, new RegExp(`${escaped} \\.rail-new \\{ background: transparent; border-color: transparent; \\}`));
  assert.match(css, new RegExp(`${escaped} \\.rail-item\\.active \\{ background: var\\(--rail-box\\); border-color: transparent; \\}`));
  assert.match(css, new RegExp(`${escaped} \\.rail-new:hover \\{ background: var\\(--bg-surface-hover\\); \\}`));
});

test('it comes after the quiet shell\'s rail rules (which also style the edge and the buttons) and its selectors are stronger than theirs', () => {
  assert.ok(css.indexOf(`${P} {`) > css.indexOf('.app-shell:not(.theme-shadow) .app-rail .rail-new {'), 'later in the file, so it wins a tie');
  assert.ok(css.indexOf(`${P} {`) > css.indexOf('.app-shell:not(.theme-shadow) .app-rail { --rail-bg: var(--quiet-bg);'));
  // .app-shell + .app-rail + .app-rail-compact + :not(.app-rail-drawer) + .rail-item + .active = six classes, one more than the quiet rule's five
  assert.equal((`${P} .rail-item.active`.match(/\./g) ?? []).length, 6);
  assert.equal(('.app-shell:not(.theme-shadow) .app-rail .rail-item.active'.match(/\./g) ?? []).length, 5);
});

test('the expanded rail and the phone drawer keep the gray panel and the edge, and it fades when the rail is opened or closed', () => {
  const base = css.slice(css.indexOf('.app-rail {'), css.indexOf('}', css.indexOf('.app-rail {')));
  assert.match(base, /background: var\(--rail-bg\);/);
  assert.match(base, /border-right: 1px solid var\(--border-color\);/);
  assert.match(base, /transition: width 0\.18s ease, background-color 0\.18s ease, border-color 0\.18s ease;/);
  const mine = css.slice(css.indexOf('/* Collapsed to icons on a desktop'), css.indexOf('/* The feedback hearts'));
  const rules = mine.split('\n').filter(line => line.startsWith('.app-shell'));
  assert.equal(rules.length, 8);
  assert.ok(rules.every(rule => rule.includes(':not(.app-rail-drawer)')), 'every new rule leaves the drawer out');
  assert.doesNotMatch(css.slice(css.indexOf(`${P} {`)), /\.app-rail-drawer \.rail-brand|\.app-rail:not\(\.app-rail-compact\) \.rail-brand/);
});
