// Run with: node --experimental-test-module-mocks --test src/components/streamPacer.test.mjs   (from local/)
// The pacer that shows a streamed reply a word at a time (components/streamPacer.ts), with a fake clock so every frame and pause is exact.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { PACER_DEFAULTS, StreamPacer, planWords, takeUnits, unitsOf } from './streamPacer.ts';

function fakeClock() {
  let now = 0;
  let nextId = 1;
  const frames = new Map();
  const timers = new Map();
  const clock = {
    now: () => now,
    frame: callback => { const id = nextId++; frames.set(id, callback); return id; },
    cancelFrame: id => { frames.delete(id); },
    timeout: (callback, ms) => { const id = nextId++; timers.set(id, { callback, at: now + ms }); return id; },
    clearTimeout: id => { timers.delete(id); },
  };
  /** Runs one frame `ms` later (as the browser would), plus any timer that came due. */
  const advance = (ms = 16) => {
    now += ms;
    for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.callback(); }
    const due = [...frames];
    frames.clear();
    due.forEach(([, callback]) => callback());
  };
  return { clock, advance, frames: () => frames.size, timers: () => timers.size, time: () => now };
}

function rig(options) {
  const world = fakeClock();
  const shown = [];
  const pacer = new StreamPacer(text => shown.push(text), world.clock, options);
  return { ...world, pacer, shown, text: () => shown.join('') };
}
/** Runs frames until nothing more is shown or `limit` frames have passed. */
function run(world, limit = 1000) {
  for (let i = 0; i < limit && world.frames() > 0; i += 1) world.advance(16);
}

// ---- the pure parts --------------------------------------------------------------------------------------------------------------------------------

test('a buffer is split into words with their whitespace, and the last word is incomplete until whitespace follows it', () => {
  assert.deepEqual(unitsOf('Hello big world'), [{ text: 'Hello ', complete: true }, { text: 'big ', complete: true }, { text: 'world', complete: false }]);
  assert.deepEqual(unitsOf('  leading space '), [{ text: '  leading ', complete: true }, { text: 'space ', complete: true }]);
  assert.deepEqual(unitsOf('para one.\n\nPara two'), [{ text: 'para ', complete: true }, { text: 'one.\n\n', complete: true }, { text: 'Para ', complete: true }, { text: 'two', complete: false }]);
  assert.deepEqual(unitsOf(''), []);
  assert.deepEqual(unitsOf('   \n '), [], 'whitespace alone has no word to show');
});

test('taking units never splits a word, takes at most the count, and keeps the unfinished word unless it is allowed', () => {
  assert.deepEqual(takeUnits('one two three', 2, false), { out: 'one two ', rest: 'three' });
  assert.deepEqual(takeUnits('one two three', 5, false), { out: 'one two ', rest: 'three' }, 'the unfinished word waits');
  assert.deepEqual(takeUnits('one two three', 5, true), { out: 'one two three', rest: '' });
  assert.deepEqual(takeUnits('one two ', 0, true), { out: '', rest: 'one two ' });
  assert.deepEqual(takeUnits('Hel', 3, false), { out: '', rest: 'Hel' }, 'a half word is never shown while more may come');
  const text = 'a bb ccc dddd';
  const { out, rest } = takeUnits(text, 3, false);
  assert.equal(out + rest, text, 'nothing is lost or reordered');
});

test('the pace is the steady minimum for a small backlog and speeds up so a large one is worked off in about the catch-up time', () => {
  const small = planWords(3, 1000, 0, 32, 1400, 100);
  assert.equal(small.count, 3, 'never more than is waiting');
  const steady = planWords(10, 100, 0, 32, 1400, 100);
  assert.equal(steady.count, 3, '32 words a second for 100 ms is 3.2 words');
  assert.ok(Math.abs(steady.carry - 0.2) < 1e-9, 'the fraction is carried to the next frame');
  const big = planWords(280, 100, 0, 32, 1400, 100);
  assert.equal(big.count, 20, '280 words in 1.4 s is 200 a second: 20 in 100 ms');
  assert.equal(planWords(280, 100, 0, 32, 1400, 12).count, 12, 'and never more than the per-frame cap');
  assert.equal(planWords(60, 1000, 0, 32, 600, 500).count, 60, 'a backlog is never overdrawn');
  assert.deepEqual(planWords(0, 100, 0.7, 32, 1400, 12), { count: 0, carry: 0 });
});

// ---- the pacer ---------------------------------------------------------------------------------------------------------------------------------------

test('only whole words are ever shown, in order, and everything arrives', () => {
  const w = rig();
  const reply = 'The quick brown fox jumps over the lazy dog and then keeps running for a good while longer.';
  for (let i = 0; i < reply.length; i += 3) {
    w.pacer.push(reply.slice(i, i + 3));
    w.advance(16);
    for (const piece of w.shown) assert.match(piece, /\s$|^$/, `a piece ends on whitespace, never inside a word: ${JSON.stringify(piece)}`);
  }
  w.pacer.finish(() => {});
  run(w);
  assert.equal(w.text(), reply);
});

test('a burst is spread over frames instead of landing at once, at no more than the per-frame cap', () => {
  const w = rig();
  w.pacer.push(Array.from({ length: 60 }, (_, i) => `w${i}`).join(' ') + ' ');
  for (let i = 0; i < 3; i += 1) w.advance(16);
  assert.ok(w.shown.length >= 1 && w.shown.every(piece => piece.trim().split(/\s+/).length <= PACER_DEFAULTS.maxWordsPerFrame), 'a few words a frame, never the burst');
  assert.ok(w.text().trim().split(/\s+/).length < 60, 'the burst is not shown at once');
  run(w);
  assert.ok(w.shown.length > 5, 'the rest follows over many frames');
  assert.equal(w.text().trim().split(/\s+/).length, 60);
});

test('a small steady stream is shown at about the minimum pace, not instantly and not slower than it arrives', () => {
  const w = rig();
  w.pacer.push('one two three four five six seven eight nine ten ');
  let frames = 0;
  while (w.text().trim().split(/\s+/).filter(Boolean).length < 10 && frames < 200) { w.advance(16); frames += 1; }
  const seconds = (frames * 16) / 1000;
  assert.ok(seconds > 0.18 && seconds < 0.5, `ten words took ${seconds.toFixed(2)} s at 32 a second`);
});

test('a big backlog speeds up: it is worked off in about the catch-up time, not at the minimum pace', () => {
  const w = rig();
  w.pacer.push(Array.from({ length: 300 }, (_, i) => `w${i}`).join(' ') + ' ');
  while (w.pacer.pending().length > 0 && w.time() < 10000) w.advance(16);
  assert.ok(w.time() < 3000, `300 words took ${w.time()} ms (at the 32 a second minimum it would take 9.4 s)`);
});

test('a word that is still arriving is held until the next token shows where it ends', () => {
  const w = rig();
  w.pacer.push('Hello wor');
  for (let i = 0; i < 6; i += 1) w.advance(16);
  assert.equal(w.text(), 'Hello ', 'the whole word before it is shown, the half word is not');
  w.pacer.push('ld and');
  for (let i = 0; i < 6; i += 1) w.advance(16);
  assert.equal(w.text(), 'Hello world ');
});

test('a half word is shown once the stream has been quiet for a moment, so a pause mid-word does not hide it for ever', () => {
  const w = rig();
  w.pacer.push('Hello wor');
  for (let i = 0; i < 40; i += 1) w.advance(16);       // 640 ms of silence
  assert.equal(w.text(), 'Hello wor');
  w.pacer.push('ld ');
  for (let i = 0; i < 6; i += 1) w.advance(16);
  assert.equal(w.text(), 'Hello world ', 'the rest of the word then follows');
});

test('finishing shows what is waiting within the finish time and then calls done exactly once', () => {
  const w = rig();
  let done = 0;
  w.pacer.push(Array.from({ length: 200 }, (_, i) => `w${i}`).join(' ') + ' tail');
  w.pacer.finish(() => { done += 1; });
  while (done === 0 && w.time() < 5000) w.advance(16);
  assert.equal(done, 1);
  assert.ok(w.time() <= 450, `done after ${w.time()} ms: within about the finish time`);
  assert.ok(w.text().endsWith('tail'), 'the unfinished last word is shown at the end');
  run(w);
  assert.equal(done, 1, 'never called twice');
});

test('finishing with nothing waiting calls done at once, and the end of a reply with no tokens is not held up', () => {
  const w = rig();
  let done = 0;
  w.pacer.finish(() => { done += 1; });
  assert.equal(done, 1);
  const again = rig();
  again.pacer.push('x ');
  again.advance(16);
  again.advance(16);
  let second = 0;
  again.pacer.finish(() => { second += 1; });
  run(again);
  assert.equal(second, 1);
});

test('if no frame ever comes (a hidden tab) the safety timer shows everything and calls done', () => {
  const w = rig();
  let done = 0;
  w.pacer.push('a b c d e f ');
  w.pacer.finish(() => { done += 1; });
  assert.equal(w.timers(), 1);
  w.advance(PACER_DEFAULTS.finishMs * 4 + 10);          // the timer fires first; the frame that is due is irrelevant
  assert.equal(done, 1);
  assert.equal(w.text(), 'a b c d e f ');
});

test('flush shows everything now, finishes a finish that was waiting, and leaves nothing behind', () => {
  const w = rig();
  let done = 0;
  w.pacer.push('one two three four');
  w.pacer.finish(() => { done += 1; });
  w.pacer.flush();
  assert.equal(w.text(), 'one two three four');
  assert.equal(done, 1);
  assert.equal(w.pacer.pending(), '');
  assert.equal(w.frames(), 0);
  assert.equal(w.timers(), 0);
});

test('reset drops what is waiting and a pending finish, and the next text starts clean', () => {
  const w = rig();
  let done = 0;
  w.pacer.push('old text that was never shown ');
  w.pacer.finish(() => { done += 1; });
  w.pacer.reset();
  run(w);
  assert.equal(w.text(), '');
  assert.equal(done, 0, 'a reset reply does not finish the old one');
  w.pacer.push('new ');
  run(w);
  assert.equal(w.text(), 'new ');
});

test('a disposed pacer shows nothing more and does not call done from the dead; finishing it just calls done', () => {
  const w = rig();
  w.pacer.push('some words here ');
  w.pacer.dispose();
  w.advance(16);
  w.pacer.push('more ');
  run(w);
  assert.equal(w.text(), '');
  let done = 0;
  w.pacer.finish(() => { done += 1; });
  assert.equal(done, 1, 'a finish on a dead pacer still calls done, so nothing waits on it');
});

test('a long frame is counted as no longer than the cap, so coming back to a hidden tab does not dump the backlog in one frame', () => {
  const w = rig();
  w.pacer.push(Array.from({ length: 400 }, (_, i) => `w${i}`).join(' ') + ' ');
  w.advance(30000);                                      // thirty seconds later, one frame
  assert.ok(w.text().trim().split(/\s+/).length <= PACER_DEFAULTS.maxWordsPerFrame);
});

test('whitespace on its own (a paragraph break between tokens) is shown at once and does not hold up the loop', () => {
  const w = rig();
  w.pacer.push('first ');
  run(w);
  w.pacer.push('\n\n');
  w.advance(16);
  assert.equal(w.text(), 'first \n\n');
  assert.equal(w.frames(), 0, 'no frames are being spent on nothing');
});

test('code and markdown marks are kept intact: a word is whatever lies between spaces', () => {
  const w = rig();
  const reply = '```python\nprint("hi")\n```\n\n**bold** and `code` here ';
  w.pacer.push(reply);
  run(w);
  assert.equal(w.text(), reply);
});

// ---- a reply that arrives whole: drain -------------------------------------------------------------------------------------------------------------

const words = n => Array.from({ length: n }, (_, i) => `w${i}`).join(' ');

test('a reply that arrives whole is revealed a few words at a time, not all at once, and done is called when the last word is out', () => {
  const w = rig();
  let done = 0;
  const reply = words(150);                               // no whitespace after the last word
  w.pacer.push(reply);
  w.pacer.drain(() => { done += 1; });
  for (let i = 0; i < 3; i += 1) w.advance(16);
  assert.ok(w.shown.length >= 1 && w.shown.every(piece => piece.trim().split(/\s+/).length <= PACER_DEFAULTS.maxWordsPerFrame), 'a few words a frame, never the lot');
  assert.equal(done, 0);
  while (done === 0 && w.time() < 10000) w.advance(16);
  assert.equal(done, 1);
  assert.equal(w.text(), reply, 'the whole reply, including its last word, which has no whitespace after it');
  assert.ok(w.time() > 900 && w.time() < 3500, `150 words took ${w.time()} ms: a flow of a second or two, not a flash and not a crawl`);
  run(w);
  assert.equal(done, 1, 'once');
});

test('the reveal is gentler than the end-of-stream catch-up: the same backlog takes longer to show', () => {
  const quick = rig();
  quick.pacer.push(words(150) + ' ');
  let q = 0;
  quick.pacer.finish(() => { q += 1; });
  while (q === 0 && quick.time() < 10000) quick.advance(16);
  const gentle = rig();
  let g = 0;
  gentle.pacer.push(words(150) + ' ');
  gentle.pacer.drain(() => { g += 1; });
  while (g === 0 && gentle.time() < 10000) gentle.advance(16);
  assert.ok(gentle.time() > quick.time() * 2, `reveal ${gentle.time()} ms against finish ${quick.time()} ms`);
});

test('a finish that arrives while a whole reply is being revealed (the turn ending) waits for it and does not speed it up', () => {
  const w = rig();
  const order = [];
  w.pacer.push(words(150));
  w.pacer.drain(() => order.push('reveal'));
  for (let i = 0; i < 5; i += 1) w.advance(16);
  w.pacer.finish(() => order.push('end of turn'));
  while (order.length < 2 && w.time() < 10000) w.advance(16);
  assert.deepEqual(order, ['reveal', 'end of turn'], 'both are called, in the order asked, once each');
  assert.ok(w.time() > 900, `it kept its own pace (${w.time()} ms), the finish did not squeeze it into ${PACER_DEFAULTS.finishMs} ms`);
  assert.equal(w.text(), words(150));
});

test('every finish that was asked for is called once, in order: a second one does not replace the first', () => {
  const w = rig();
  const order = [];
  w.pacer.push(words(60) + ' ');
  w.pacer.finish(() => order.push('final text'));
  w.pacer.finish(() => order.push('turn over'));
  while (order.length < 2 && w.time() < 5000) w.advance(16);
  assert.deepEqual(order, ['final text', 'turn over']);
  run(w);
  assert.deepEqual(order, ['final text', 'turn over'], 'and never again');
});

test('draining with nothing waiting calls done at once', () => {
  const w = rig();
  let done = 0;
  w.pacer.drain(() => { done += 1; });
  assert.equal(done, 1);
});

test('a flush during a reveal shows the rest now and calls every waiting done', () => {
  const w = rig();
  const order = [];
  w.pacer.push(words(100));
  w.pacer.drain(() => order.push('a'));
  w.pacer.finish(() => order.push('b'));
  w.advance(16); w.advance(16);
  w.pacer.flush();
  assert.equal(w.text(), words(100));
  assert.deepEqual(order, ['a', 'b']);
  assert.equal(w.frames(), 0);
  assert.equal(w.timers(), 0);
});

test('a reset during a reveal drops it and its done callbacks, and the pacer works again after', () => {
  const w = rig();
  let called = 0;
  w.pacer.push(words(100));
  w.pacer.drain(() => { called += 1; });
  w.pacer.reset();
  run(w);
  assert.equal(called, 0);
  assert.equal(w.text(), '');
  w.pacer.push('fresh ');
  w.pacer.drain(() => { called += 1; });
  run(w);
  assert.equal(w.text(), 'fresh ');
  assert.equal(called, 1);
});

test('if no frame ever comes (a hidden tab) a reveal is shown whole after the safety time and done is called', () => {
  const w = rig();
  let done = 0;
  w.pacer.push(words(200));
  w.pacer.drain(() => { done += 1; });
  assert.equal(w.timers(), 1);
  w.advance(PACER_DEFAULTS.revealSafetyMs + 10);
  assert.equal(done, 1);
  assert.equal(w.text(), words(200));
});

test('a disposed pacer calls done for a drain without showing anything', () => {
  const w = rig();
  w.pacer.dispose();
  let done = 0;
  w.pacer.drain(() => { done += 1; });
  assert.equal(done, 1);
  assert.equal(w.text(), '');
});

function revealTime(count, trailing = false) {
  const w = rig();
  let done = 0;
  w.pacer.push(words(count) + (trailing ? ' ' : ''));
  w.pacer.drain(() => { done += 1; });
  while (done === 0 && w.time() < 20000) w.advance(16);
  return { ms: w.time(), text: w.text() };
}

test('a very short reply is revealed straight away, including its last word, without waiting for the stall timer', () => {
  const { ms, text } = revealTime(2);
  assert.equal(text, 'w0 w1');
  assert.ok(ms < PACER_DEFAULTS.stallMs / 2, `two words took ${ms} ms: the last word did not wait for a quiet moment`);
});

test('the reveal pace: a small reply goes at the reveal minimum, a big one is worked off in about the reveal catch-up time', () => {
  const small = revealTime(20).ms;
  assert.ok(small >= 300 && small <= 430, `20 words took ${small} ms (55 a second is about 365 ms; 32 a second would be 625 ms)`);
  const big = revealTime(400).ms;
  assert.ok(big >= 2800 && big <= 3600, `400 words took ${big} ms (a flow of about three seconds, not a flash and not a crawl)`);
});
