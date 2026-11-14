// Run with: node --experimental-test-module-mocks --test src/pages/initialPin.test.mjs   (from local/)
// Landing on the newest message when a thread first fills (pages/initialPin.ts), with a fake chat window and a fake frame clock.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PIN_FRAMES, PIN_STOP_EVENTS, startInitialPin } from './initialPin.ts';

function clock() {
  const queue = [];
  let next = 1;
  return {
    raf: callback => { const id = next++; queue.push([id, callback]); return id; },
    caf: id => { const at = queue.findIndex(([queued]) => queued === id); if (at >= 0) queue.splice(at, 1); },
    frame: () => { const [, callback] = queue.shift() ?? []; callback?.(); },
    pending: () => queue.length,
  };
}
function fakeWindow() {
  const listeners = new Map();
  const win = {
    scrollHeight: 1000, scrolls: [],
    scrollTo(options) { win.scrolls.push(options); },
    addEventListener(type, listener) { listeners.set(type, [...(listeners.get(type) ?? []), listener]); },
    removeEventListener(type, listener) { listeners.set(type, (listeners.get(type) ?? []).filter(l => l !== listener)); },
    fire(type) { [...(listeners.get(type) ?? [])].forEach(listener => listener()); },
    count: () => [...listeners.values()].reduce((n, list) => n + list.length, 0),
  };
  return win;
}

test('it pins to the bottom on every frame, so the layout that settles late is followed down', () => {
  const win = fakeWindow(); const c = clock();
  startInitialPin(() => win, c.raf, c.caf);
  c.frame();
  win.scrollHeight = 1800;          // the markdown and images laid out after the first pin
  c.frame();
  win.scrollHeight = 2600;
  c.frame();
  assert.deepEqual(win.scrolls.map(s => s.top), [1000, 1800, 2600]);
  assert.ok(win.scrolls.every(s => s.behavior === 'instant'), 'instant: the window has smooth scrolling, which would crawl');
});

test('with no chat window yet it keeps trying instead of giving up, which is what left the thread at the top', () => {
  let win = null; const c = clock();
  startInitialPin(() => win, c.raf, c.caf);
  c.frame(); c.frame(); c.frame();
  assert.equal(c.pending(), 1, 'still going');
  win = fakeWindow();
  c.frame();
  assert.equal(win.scrolls.length, 1);
  assert.equal(win.scrolls[0].top, 1000);
});

test('it lets go the moment the person scrolls, touches, clicks or presses a key, and then touches nothing', () => {
  assert.deepEqual([...PIN_STOP_EVENTS].sort(), ['keydown', 'pointerdown', 'touchstart', 'wheel']);
  for (const type of PIN_STOP_EVENTS) {
    const win = fakeWindow(); const c = clock();
    startInitialPin(() => win, c.raf, c.caf);
    c.frame();
    assert.equal(win.scrolls.length, 1);
    win.fire(type);
    assert.equal(c.pending(), 0, `${type}: no more frames`);
    c.frame();
    assert.equal(win.scrolls.length, 1, `${type}: no more scrolling`);
    assert.equal(win.count(), 0, `${type}: listeners removed`);
  }
});

test('it stops by itself after its frames, removes its listeners, and can be stopped early from outside, more than once', () => {
  const win = fakeWindow(); const c = clock();
  startInitialPin(() => win, c.raf, c.caf);
  for (let i = 0; i < PIN_FRAMES + 5; i++) c.frame();
  assert.equal(win.scrolls.length, PIN_FRAMES);
  assert.equal(c.pending(), 0);
  assert.equal(win.count(), 0);
  const win2 = fakeWindow(); const c2 = clock();
  const stop = startInitialPin(() => win2, c2.raf, c2.caf);
  c2.frame();
  stop(); stop();
  assert.equal(c2.pending(), 0);
  assert.equal(win2.count(), 0);
  c2.frame();
  assert.equal(win2.scrolls.length, 1);
});

test('the listeners are attached once, when the window first exists, and not before', () => {
  let win = null; const c = clock();
  startInitialPin(() => win, c.raf, c.caf);
  c.frame();
  win = fakeWindow();
  assert.equal(win.count(), 0);
  c.frame(); c.frame();
  assert.equal(win.count(), PIN_STOP_EVENTS.length);
});
