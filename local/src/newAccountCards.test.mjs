// Run with: node --test src/newAccountCards.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The new-account welcome cards (backend/utils/example_questions.py) as the page handles them: what pressing each one does, that it is counted by name, and the demo project's diff card for a signed-in person.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const chat = read('./pages/Chat.tsx');
const api = read('./api.ts');
const card = read('./components/LocalEditCard.tsx');
const cards = read('./welcomeCards.ts');

test('a card can leave words in the message box, open a page, or send its question: the three things a new-account card does', () => {
  assert.match(cards, /action\?: 'attach' \| 'open' \| 'signup' \| 'prefill';/);
  assert.match(api, /action\?: 'attach' \| 'open' \| 'prefill'; prompt\?: string; path\?: string; demo\?: boolean; kind\?: string/);
  const chooser = chat.slice(chat.indexOf('const chooseWelcomeCard = (card: WelcomeCard) => {'), chat.indexOf('const handleFileUpload'));
  assert.match(chooser, /if \(card\.action === 'prefill'\) \{[\s\S]*?setInput\(card\.prompt \?\? card\.text\);[\s\S]*?inputRef\.current\?\.focus\(\)[\s\S]*?return;\s*\}/);        // the words go in the box, the cursor goes there, nothing is sent
  assert.match(chooser, /if \(card\.action === 'open' && card\.path\) \{\s*routeNavigate\(card\.path\);/);                                                  // the runner and knowledge-base cards open their pages
  assert.match(chooser, /demoProjectRef\.current = card\.demo === true;\s*handleSendMessage\(card\.text/);                                                    // the demo card is sent as a question, on the demo project
});

test('a press of a new-account card is counted by its kind before anything else happens, and a card with no kind counts nothing', () => {
  const chooser = chat.slice(chat.indexOf('const chooseWelcomeCard = (card: WelcomeCard) => {'), chat.indexOf('const handleFileUpload'));
  assert.ok(chooser.indexOf('if (card.kind) api.trackWelcomeCard(card.kind);') > 0 && chooser.indexOf('if (card.kind) api.trackWelcomeCard(card.kind);') < chooser.indexOf("if (card.action === 'prefill')"));
  const start = api.indexOf('export function trackWelcomeCard(');
  const body = api.slice(start, api.indexOf('\n}', start));
  assert.match(body, /\/api\/welcome-click/);
  assert.match(body, /keepalive: true/);                                                                                                                      // survives the page going to /account or /self-service
  assert.match(body, /catch \{[\s\S]*?not counted/);                                                                                                          // a card works whether or not the count does
  assert.match(api, /trackWelcomeCard,\r?\n/);                                                                                                                  // and it is on the api object the page calls
});

test('the demo project\'s diff card offers a signed-in person a way to connect their own project, and a phone says why it cannot', () => {
  assert.match(card, /demoLabel\?: string;\s*demoDisabled\?: boolean;/);
  assert.match(card, /disabled=\{demoDisabled\}>\{demoLabel \?\? 'Sign up to apply this to your own project'\}/);                                           // a guest still sees the sign-up words
  assert.match(chat, /connect: \(\) => Promise<void>;\s*canConnect: boolean;/);
  assert.equal((chat.match(/connect: workspace\.connect, canConnect: workspace\.supported/g) ?? []).length, 2, 'the ref starts with it and is refreshed every render');
  assert.match(chat, /'Connect your own project to apply this' : 'Connecting a project needs Chrome or Edge on a computer'/);
  assert.match(chat, /demoDisabled=\{!isGuestPrincipal\(getEffectivePrincipal\(\)\) && !localEditActionsRef\.current\.canConnect\}/);                        // nothing to press where there is no folder picker
});
