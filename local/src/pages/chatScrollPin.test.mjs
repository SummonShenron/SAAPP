import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./Chat.tsx', import.meta.url), 'utf8').replace(/\r\n/g, '\n');

test('chat tracks whether the user is near the bottom', () => {
  assert.match(src, /const nearBottomRef = useRef\(true\);/);
  assert.match(src, /addEventListener\('scroll', onScroll, \{ passive: true \}\)/);
  assert.match(src, /win\.scrollHeight - win\.scrollTop - win\.clientHeight <= 50/);
});

test('loading pin only fires when the user is near the bottom', () => {
  assert.match(src, /else if \(loading && chatWindowRef\.current && nearBottomRef\.current\) \{/);
});

test('a thread that fills from empty with no turn running is pinned to the bottom until it settles or the person steers (pages/initialPin.ts), and sending a message ends the pin', () => {
  assert.match(src, /import \{ startInitialPin \} from '\.\/initialPin';/);
  assert.match(src, /if \(isInitialLoad && !loading\) \{\s+cancelInitialPinRef\.current\?\.\(\);\s+cancelInitialPinRef\.current = startInitialPin\(\(\) => chatWindowRef\.current\);\s+\} else if \(isNewMessage && messages\[messages\.length - 1\]\?\.sender === 'user'\) \{\s+cancelInitialPinRef\.current\?\.\(\);/);
  assert.match(src, /useEffect\(\(\) => \(\) => cancelInitialPinRef\.current\?\.\(\), \[\]\);/);          // and it is stopped when the page goes
  assert.doesNotMatch(src, /initialPinRef|requestAnimationFrame\(tick\)/, 'the one-shot version that gave up when the window was not there yet is gone');
});
