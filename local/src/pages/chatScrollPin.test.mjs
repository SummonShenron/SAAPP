import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./Chat.tsx', import.meta.url), 'utf8');

test('chat tracks whether the user is near the bottom', () => {
  assert.match(src, /const nearBottomRef = useRef\(true\);/);
  assert.match(src, /addEventListener\('scroll', onScroll, \{ passive: true \}\)/);
  assert.match(src, /win\.scrollHeight - win\.scrollTop - win\.clientHeight <= 50/);
});

test('loading pin only fires when the user is near the bottom', () => {
  assert.match(src, /else if \(loading && chatWindowRef\.current && nearBottomRef\.current\) \{/);
});
