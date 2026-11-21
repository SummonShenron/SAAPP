import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('./Chat.tsx', import.meta.url), 'utf8').replace(/\r\n/g, '\n');

test('chat tracks whether the user is near the bottom', () => {
  assert.match(src, /const nearBottomRef = useRef\(true\);/);
  assert.match(src, /addEventListener\('scroll', onScroll, \{ passive: true \}\)/);
  assert.match(src, /win\.scrollHeight - win\.scrollTop - win\.clientHeight <= 50/);
});

test('a running turn is no longer pinned to the bottom instantly on every token: the stream follower eases down and lets go when the person scrolls up (pages/streamFollow.ts)', () => {
  assert.doesNotMatch(src, /else if \(loading && chatWindowRef\.current && nearBottomRef\.current\) \{/, 'the per-token instant pin is gone');
  assert.match(src, /import \{ StreamFollower \} from '\.\/streamFollow';/);
  assert.match(src, /const followingTurn = loading \|\| messages\.some\(m => m\.streaming\);/);       // for the turn and for as long as its last words are still being shown
  assert.match(src, /new StreamFollower\(\(\) => chatWindowRef\.current\)/);
  assert.match(src, /followerRef\.current\.start\(\);/);
  assert.match(src, /followerRef\.current\?\.release\(\);/);           // the end: finish the last sweep, then stop
  assert.match(src, /useEffect\(\(\) => \(\) => followerRef\.current\?\.stop\(\), \[\]\);/);
  // a message that starts a turn is followed by the follower, not by a one-off smooth scroll its first frame would cancel
  assert.match(src, /if \(!loading\) messagesEndRef\.current\?\.scrollIntoView\(\{ behavior: 'smooth', block: 'end' \}\);/);
  // the pin when a turn ends only runs when the follower is not still finishing the reply
  assert.match(src, /\} else if \(!followingTurn && \(justFinishedLoading \|\| isInitialLoad\) && chatWindowRef\.current\) \{/);
});

test('a thread that fills from empty with no turn running is pinned to the bottom until it settles or the person steers (pages/initialPin.ts), and sending a message ends the pin', () => {
  assert.match(src, /import \{ startInitialPin \} from '\.\/initialPin';/);
  assert.match(src, /if \(isInitialLoad && !loading\) \{\s+cancelInitialPinRef\.current\?\.\(\);\s+cancelInitialPinRef\.current = startInitialPin\(\(\) => chatWindowRef\.current\);\s+\} else if \(isNewMessage && messages\[messages\.length - 1\]\?\.sender === 'user'\) \{\s+cancelInitialPinRef\.current\?\.\(\);/);
  assert.match(src, /useEffect\(\(\) => \(\) => cancelInitialPinRef\.current\?\.\(\), \[\]\);/);          // and it is stopped when the page goes
  assert.doesNotMatch(src, /initialPinRef|requestAnimationFrame\(tick\)/, 'the one-shot version that gave up when the window was not there yet is gone');
});
