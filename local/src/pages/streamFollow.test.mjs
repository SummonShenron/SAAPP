// Run with: node --experimental-test-module-mocks --test src/pages/streamFollow.test.mjs   (from local/)
// Following a streaming reply down the page (pages/streamFollow.ts), with a fake chat window and a fake frame clock.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FOLLOW, StreamFollower, easedTop, movedUpByPerson } from './streamFollow.ts';

function world() {
  let now = 0;
  let nextId = 1;
  const queue = new Map();
  const listeners = new Map();
  const win = {
    scrollTop: 0, scrollHeight: 1000, clientHeight: 500, moves: [],
    scrollTo({ top, behavior }) {
      const max = Math.max(0, win.scrollHeight - win.clientHeight);
      win.scrollTop = Math.min(Math.max(top, 0), max);
      win.moves.push({ top: win.scrollTop, behavior });
      win.fire('scroll', {});
    },
    addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) ?? []), listener]); },
    removeEventListener(type, listener) { listeners.set(type, (listeners.get(type) ?? []).filter(l => l !== listener)); },
    fire(type, event = {}) { [...(listeners.get(type) ?? [])].forEach(listener => listener(event)); },
    listenerCount: () => [...listeners.values()].reduce((n, list) => n + list.length, 0),
  };
  const raf = callback => { const id = nextId++; queue.set(id, callback); return id; };
  const caf = id => { queue.delete(id); };
  const frame = (ms = 16) => { now += ms; const due = [...queue]; queue.clear(); due.forEach(([, callback]) => callback()); };
  const follower = new StreamFollower(() => win, raf, caf, () => now, () => false);
  return { win, follower, frame, pending: () => queue.size, time: () => now };
}
const bottom = w => w.win.scrollHeight - w.win.clientHeight;

// ---- the pure parts ---------------------------------------------------------------------------------------------------------------------------

test('a frame moves a fraction of the way, lands on the target when close, and never overshoots', () => {
  const next = easedTop(0, 100, 16.7);
  assert.ok(next > 0 && next < 100, `moved part of the way (${next})`);
  assert.equal(easedTop(99.5, 100, 16.7), 100, 'within a pixel it lands');
  assert.equal(easedTop(100, 100, 16), 100);
  let top = 0;
  for (let i = 0; i < 200; i += 1) { const was = top; top = easedTop(top, 100, 16); assert.ok(top >= was && top <= 100, 'monotone, never past the target'); }
  assert.equal(top, 100);
});

test('it is quicker when far behind than when close, and a longer frame covers more, but a very long one is counted as the cap', () => {
  const farShare = (easedTop(0, 1200, 16.7) - 0) / 1200;
  const nearShare = (easedTop(0, 40, 16.7) - 0) / 40;
  assert.ok(farShare > nearShare, 'a gap of a whole screen is closed faster, proportionally');
  assert.ok(easedTop(0, 100, 33) > easedTop(0, 100, 16), 'twice the time, more distance');
  assert.equal(easedTop(0, 100, 5000), easedTop(0, 100, FOLLOW.maxFrameMs), 'a frame is never counted as longer than the cap');
  assert.ok(easedTop(0, 100, 5000) < 100, 'and even then it does not jump the whole way');
});

test('the page moving UP is the person, but not a page that got shorter and was clamped, and not rounding', () => {
  assert.equal(movedUpByPerson(400, 700, 900), true, 'higher than we left it, and not at the bottom');
  assert.equal(movedUpByPerson(698.5, 700, 900), false, 'rounding');
  assert.equal(movedUpByPerson(600, 700, 600), false, 'the page shrank and the browser lowered scrollTop to the new bottom: not the person');
  assert.equal(movedUpByPerson(900, 700, 900), false, 'moving down is never "up"');
});

// ---- following ---------------------------------------------------------------------------------------------------------------------------------

test('while the reply grows it follows the bottom a step at a time, instantly in browser terms but eased in distance, ending on the bottom', () => {
  const w = world();
  w.follower.start();
  w.win.scrollHeight = 1600;
  const tops = [];
  for (let i = 0; i < 80 && w.win.scrollTop < bottom(w); i += 1) { w.frame(); tops.push(w.win.scrollTop); }
  assert.equal(w.win.scrollTop, bottom(w));
  const steps = tops.map((t, i) => t - (i ? tops[i - 1] : 0));
  assert.ok(steps.length > 6, `it took ${steps.length} frames, a sweep and not a jump`);
  assert.ok(steps.every(s => s >= 0), 'only ever downward');
  assert.ok(steps.slice(1, 4).every((s, i) => s <= steps[i] + 1e-9) || steps[0] >= steps[steps.length - 2], 'the steps get smaller as it nears the bottom (an ease-out)');
  assert.ok(w.win.moves.every(m => m.behavior === 'instant'), 'each frame is a direct move (the page itself must not add a second animation)');
});

test('growth every frame is followed without falling further and further behind', () => {
  const w = world();
  w.follower.start();
  for (let i = 0; i < 120; i += 1) { w.win.scrollHeight += 18; w.frame(); }
  assert.ok(bottom(w) - w.win.scrollTop < 120, `still within a few lines of the bottom (${(bottom(w) - w.win.scrollTop).toFixed(0)} px behind) while growing 18 px a frame`);
});

test('with nothing to scroll to it moves nothing and keeps no work going except the frame loop', () => {
  const w = world();
  w.win.scrollTop = bottom(w);
  w.follower.start();
  w.frame(); w.frame();
  assert.equal(w.win.moves.length, 0);
});

// ---- letting go --------------------------------------------------------------------------------------------------------------------------------

test('the wheel up lets go, and it stays let go while the reply keeps growing', () => {
  const w = world();
  w.win.scrollTop = bottom(w);
  w.follower.start();
  w.win.scrollHeight = 1400; w.frame(); w.frame();
  w.win.fire('wheel', { deltaY: -120 });
  assert.equal(w.follower.isFollowing(), false);
  const where = w.win.scrollTop;
  for (let i = 0; i < 20; i += 1) { w.win.scrollHeight += 40; w.frame(); }
  assert.equal(w.win.scrollTop, where, 'the page does not move while they read up the thread');
});

test('the wheel down does not let go', () => {
  const w = world();
  w.follower.start();
  w.win.fire('wheel', { deltaY: 120 });
  assert.equal(w.follower.isFollowing(), true);
});

test('a finger dragging down (the page moving up) lets go, a finger dragging up does not', () => {
  const w = world();
  w.follower.start(); w.frame();
  w.win.fire('touchstart', { touches: [{ clientY: 300 }] });
  w.win.fire('touchmove', { touches: [{ clientY: 250 }] });
  assert.equal(w.follower.isFollowing(), true, 'the finger moved up: the page moves down, toward the newest text');
  w.win.fire('touchmove', { touches: [{ clientY: 330 }] });
  assert.equal(w.follower.isFollowing(), false);
});

test('arrow up, page up and home let go; other keys do not', () => {
  for (const key of ['ArrowUp', 'PageUp', 'Home']) {
    const w = world(); w.follower.start(); w.frame();
    w.win.fire('keydown', { key });
    assert.equal(w.follower.isFollowing(), false, key);
  }
  const w = world(); w.follower.start(); w.frame();
  for (const key of ['a', 'Enter', 'ArrowDown', 'Shift']) w.win.fire('keydown', { key });
  assert.equal(w.follower.isFollowing(), true);
});

test('dragging the scrollbar up lets go, and scrolling back to the bottom takes it up again', () => {
  const w = world();
  w.win.scrollTop = bottom(w);
  w.follower.start();
  w.win.scrollHeight = 1300; w.frame();
  w.win.scrollTop = 200; w.win.fire('scroll');             // not our move: higher than where we left the page
  assert.equal(w.follower.isFollowing(), false);
  w.win.scrollTop = bottom(w) - 10; w.win.fire('scroll');  // back within reach of the bottom
  assert.equal(w.follower.isFollowing(), true);
});

test('a page that shrinks under it is not taken for the person scrolling up', () => {
  const w = world();
  w.win.scrollTop = bottom(w);
  w.follower.start(); w.frame();
  w.win.scrollHeight = 700;                                 // a card closed: the browser lowers scrollTop to the new bottom
  w.win.scrollTop = bottom(w); w.win.fire('scroll');
  assert.equal(w.follower.isFollowing(), true);
});

test('starting again (a new message) follows again even after the person had scrolled away', () => {
  const w = world();
  w.follower.start();
  w.win.fire('wheel', { deltaY: -50 });
  assert.equal(w.follower.isFollowing(), false);
  w.follower.stop();
  w.follower.start();
  assert.equal(w.follower.isFollowing(), true);
});

// ---- ending ------------------------------------------------------------------------------------------------------------------------------------

test('on release it finishes the last sweep to the bottom and then stops by itself', () => {
  const w = world();
  w.follower.start();
  w.win.scrollHeight = 2000;
  w.frame(); w.frame();
  assert.ok(w.win.scrollTop < bottom(w));
  w.follower.release();
  for (let i = 0; i < 60 && w.pending() > 0; i += 1) w.frame();
  assert.equal(w.win.scrollTop, bottom(w), 'the page is not left a few lines short');
  assert.equal(w.pending(), 0, 'and the loop is over');
  assert.equal(w.win.listenerCount(), 0, 'with nothing left listening');
});

test('on release it does not chase a person who has scrolled away, it just stops', () => {
  const w = world();
  w.follower.start(); w.frame();
  w.win.fire('wheel', { deltaY: -100 });
  w.win.scrollHeight = 3000;
  w.follower.release();
  w.frame();
  assert.equal(w.pending(), 0);
});

test('a release that cannot settle is cut off after the settle time', () => {
  const w = world();
  w.follower.start();
  w.follower.release();
  for (let i = 0; i < 80 && w.pending() > 0; i += 1) { w.win.scrollHeight += 5000; w.frame(); }   // the page grows faster than it can follow
  assert.equal(w.pending(), 0);
  assert.ok(w.time() <= FOLLOW.settleMs + 200);
});

test('stop removes every listener and the pending frame, and is safe to call twice or before start', () => {
  const w = world();
  w.follower.stop();
  w.follower.start(); w.frame();
  assert.ok(w.win.listenerCount() > 0);
  w.follower.stop(); w.follower.stop();
  assert.equal(w.win.listenerCount(), 0);
  assert.equal(w.pending(), 0);
});

test('with reduced motion it goes straight to the bottom instead of easing', () => {
  const w = world();
  const instant = new StreamFollower(() => w.win, callback => { w.frame.__cb = callback; return 1; }, () => {}, () => 0, () => true);
  instant.start();
  w.win.scrollHeight = 5000;
  w.frame.__cb();
  assert.equal(w.win.scrollTop, bottom(w));
});

test('a missing window (not mounted yet) is waited for, not an error', () => {
  let win = null;
  let id = 0;
  const callbacks = [];
  const follower = new StreamFollower(() => win, callback => { callbacks.push(callback); return ++id; }, () => {}, () => 0, () => false);
  follower.start();
  assert.doesNotThrow(() => callbacks.shift()());
  assert.equal(callbacks.length, 1, 'it asks for another frame');
});
