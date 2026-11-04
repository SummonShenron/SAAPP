// Run with: node --test src/components/selfServiceAccess.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { canUseSelfService } from './selfServiceAccess.ts';

test('an admin sees Self Service, whatever knowledge bases they have', () => {
  assert.equal(canUseSelfService(['Global_Admins'], []), true);
  assert.equal(canUseSelfService(['Staff', 'Global_Admins'], ['Affiliate_A']), true);
});

test('someone who may add documents to a knowledge base they can use sees it', () => {
  assert.equal(canUseSelfService(['Affiliate_A Ingesters'], ['Affiliate_A', 'Affiliate_B']), true);
  assert.equal(canUseSelfService(['Affiliate_B Ingesters', 'Affiliate_A Readers'], ['Affiliate_A', 'Affiliate_B']), true);
});

test('a guest, or anyone who can only read, does not', () => {
  assert.equal(canUseSelfService([], ['Affiliate_C']), false);                                   // a guest: a knowledge base to read, no ingester role
  assert.equal(canUseSelfService(['Affiliate_C Readers'], ['Affiliate_C']), false);
  assert.equal(canUseSelfService(['Affiliate_A Ingesters'], ['Affiliate_C']), false);            // the role is for a knowledge base they cannot use
  assert.equal(canUseSelfService(['Affiliate_C Ingesters'], []), false);                          // a role without a knowledge base to use it on
  assert.equal(canUseSelfService(['Ingesters'], ['']), false);                                    // an empty id never matches
});

test('anything that is not a list reads as no access, never as a crash', () => {
  for (const groups of [null, undefined]) assert.equal(canUseSelfService(groups, ['Affiliate_A']), false);
  assert.equal(canUseSelfService(['Affiliate_A Ingesters'], null), false);
  assert.equal(canUseSelfService(['Affiliate_A Ingesters'], undefined), false);
  assert.equal(canUseSelfService('Global_Admins', ['x']), false);
});

test('the link follows the rule: the navigation asks the hook, the hook never asks for a guest, and the page itself uses the same rule', () => {
  const rail = readFileSync(new URL('./Rail.tsx', import.meta.url), 'utf8');
  assert.match(rail, /\{ path: '\/self-service', label: 'Self Service', icon: 'folder', show: canSelfService \}/);
  const layout = readFileSync(new URL('./Layout.tsx', import.meta.url), 'utf8');
  assert.match(layout, /const canSelfService = useSelfServiceAccess\(authReady\);/);
  assert.match(layout, /canSelfService=\{canSelfService\}/);
  const hook = readFileSync(new URL('../hooks/useSelfServiceAccess.ts', import.meta.url), 'utf8');
  assert.match(hook, /if \(!principal \|\| isGuestPrincipal\(principal\)\) \{ setAllowed\(false\); return undefined; \}/);
  assert.match(hook, /canUseSelfService\(groups, \(knowledgeBases \?\? \[\]\)\.map\(base => base\.id\)\)/);
  const page = readFileSync(new URL('../pages/SelfService.tsx', import.meta.url), 'utf8');
  assert.match(page, /isGlobalAdmin \|\| \(selectedAffiliate && userGroups\.includes\(`\$\{selectedAffiliate\} Ingesters`\)\)/);     // the rule the link copies
});
