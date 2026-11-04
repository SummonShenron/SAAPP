// Run with: node --test src/components/guestFacing.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { nameKnowledgeBases } from '../pages/sonicHealthFormat.ts';

test('a built-in knowledge base shows the name people know it by, not its internal id', () => {
  const named = nameKnowledgeBases([{ id: 'Affiliate_C', display_name: 'Affiliate C' }]);
  assert.deepEqual(named, [{ id: 'Affiliate_C', display_name: "Jack's portfolio" }]);
  assert.equal(nameKnowledgeBases([{ id: 'Affiliate_D', display_name: 'Affiliate D' }])[0].display_name, 'BTY Fitness');
});

test('a personal or unknown knowledge base keeps the name it came with, and the input is not changed', () => {
  const input = [{ id: 'kb_1', display_name: "jack's Knowledge Base" }, { id: 'Affiliate_Z', display_name: 'Affiliate Z' }];
  const copy = JSON.parse(JSON.stringify(input));
  assert.deepEqual(nameKnowledgeBases(input), copy);
  assert.deepEqual(input, copy);
  for (const bad of [null, undefined]) assert.deepEqual(nameKnowledgeBases(bad), []);
});

test('both places a person gets their knowledge bases from use it (the server answer, and the built-in guest scope)', () => {
  const api = readFileSync(new URL('../api.ts', import.meta.url), 'utf8');
  assert.match(api, /return nameKnowledgeBases\(data\.accessible_affiliates\);/);
  const hook = readFileSync(new URL('../hooks/useAffiliateScope.ts', import.meta.url), 'utf8');
  assert.match(hook, /setAllowedAffiliates\(nameKnowledgeBases\(\[\{ id: guestScope,/);
});

test('the landing page says what Sonic does, not only how it is built', () => {
  const page = readFileSync(new URL('../pages/LandingPage.tsx', import.meta.url), 'utf8');
  assert.match(page, /An AI assistant that remembers you, helps you code, and runs your checks\./);
});

test('on a phone the empty chat\'s example cards leave room for the icon strip', () => {
  const css = readFileSync(new URL('./__styles__/SessionRail.css', import.meta.url), 'utf8');
  assert.match(css, /\.chat-page:has\(\.session-rail\) \.example-cards-container \{ margin-right: 38px; \}/);
});
