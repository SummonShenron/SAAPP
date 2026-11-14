import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// The sources may use CRLF line endings; the patterns below never depend on which.
const read = (...parts) => fs.readFileSync(path.join(here, ...parts), 'utf8').replace(/\r\n/g, '\n');
const api = read('..', 'api.ts');
const hook = read('..', 'hooks', 'useConversationList.ts');
const rail = read('Rail.tsx');
const layout = read('Layout.tsx');
const blade = read('ConversationsBlade.tsx');
const tips = read('helpTips.ts');
const icons = read('icons.tsx');

// The page calls `api.renameConversation`, which only exists when the function is listed in the object api.ts exports at its end: an exported function that is not listed there does not type-check and fails when clicked.
const apiObject = api.slice(api.lastIndexOf('export const api = {'));
const listed = name => new RegExp(String.raw`^[ \t]+${name},$`, 'm').test(apiObject);

test('the rename call exists, sends a PATCH with the title, and is listed on the api object the pages use', () => {
  assert.match(api, /export async function renameConversation\(sessionId: string, title: string\)/);
  assert.match(api, /method: "PATCH"[\s\S]{0,200}JSON\.stringify\(\{ title \}\)/);
  assert.ok(apiObject.startsWith('export const api = {'), 'found the api object');
  assert.ok(listed('renameConversation'));
});

test('every api.rename... the rename code calls is on the api object', () => {
  let calls = 0;
  for (const [file, source] of [['useConversationList.ts', hook], ['ConversationsBlade.tsx', blade]]) {
    for (const [, name] of source.matchAll(/api\.(rename\w+)\(/g)) {
      calls += 1;
      assert.ok(listed(name), `${file} calls api.${name}`);
    }
  }
  assert.equal(calls, 2);
});

test('the list hook renames on the server and changes only that title in the list', () => {
  assert.match(hook, /api\.renameConversation\(sessionId, title\)/);
  assert.match(hook, /c\.session_id === sessionId \? \{ \.\.\.c, title: saved\?\.title \|\| title \} : c/);
  assert.match(hook, /return \{ items, loading, remove, rename \}/);
});

test('the sidebar has a rename button for each conversation, wired through the layout to the hook', () => {
  assert.match(rail, /onRename: \(sessionId: string, title: string\) => void;/);
  assert.match(rail, /className="rail-convo-rename"[\s\S]{0,260}onClick=\{\(\) => conversations\.onRename\(c\.session_id, c\.title\)\}/);
  assert.match(layout, /onRename: renameConversation,/);
  assert.match(layout, /conversationList\.rename\(sessionId, next\.slice\(0, 200\)\)/);
});

test('a cancelled, empty or unchanged name saves nothing (sidebar and blade)', () => {
  assert.match(layout, /if \(!next \|\| next === title\) return;/);
  assert.match(blade, /if \(!next \|\| next === title\) return;/);
});

test('the blade shows rename as an icon button beside delete, not as text inside the title', () => {
  assert.match(blade, /className="conversation-rename-btn"[\s\S]{0,300}onClick=\{\(e\) => handleRename\(e, c\.session_id, c\.title\)\}/);
  assert.ok(blade.indexOf('conversation-rename-btn') < blade.indexOf('conversation-delete-btn'));
  assert.doesNotMatch(blade, /^[ \t]+Rename[ \t]*$/m);
});

test('the new button has a help tip and an icon, and every help key the rail uses exists', () => {
  assert.match(tips, /'rail\.convoRename': \{ title: 'Rename a conversation'/);
  assert.match(icons, /^[ \t]+pencil: \(/m);
  const keys = [...rail.matchAll(/data-help="(rail\.[A-Za-z]+)"/g)].map(match => match[1]);
  assert.ok(keys.includes('rail.convoRename'));
  for (const key of keys) assert.ok(tips.includes(`'${key}'`), `help tip for ${key}`);
});
