// Smooths a streamed reply: tokens are held in a buffer and shown a WORD at a time, on animation frames, at a steady and self-adjusting pace (pages/Chat.tsx).
//
// WHY. The server sends tokens in whatever bursts the network and the model produce them: a few characters, then nothing, then forty at once. Showing each token the moment it arrives made the text jump in
// uneven lumps, and a word that was still arriving ("Hel", then "Hello") was drawn half finished, wrapped onto the next line, and then moved again when it grew. The fade-in could not hide either (it fades a
// word that is already moving). Here arrival and display are separate: the pacer takes what arrives, and hands out only COMPLETE words, a few per frame, at a rate that is steady while the backlog is small
// and speeds up when it grows, so the display never falls far behind the model and never stalls waiting for a burst.
//
// THE PACE. At least `minWordsPerSecond` (a comfortable reading-along speed), and more when words are waiting: the rate is the backlog divided by `catchUpMs`. That makes the display follow a fast stream with
// a lag of about `catchUpMs` (a stream of 100 words a second settles at a backlog of 60 words, shown at 100 a second) and ease down smoothly when the stream slows, instead of dumping or stalling.
// A frame is never counted as longer than `maxFrameMs` (a tab that was in the background does not release the whole backlog in one frame when it comes back; it catches up at the normal fast rate).
//
// A WORD IS COMPLETE once whitespace follows it. The last word of the buffer is held until the next token shows where it ends, or until the stream has been quiet for `stallMs` (a pause mid-word is shown rather
// than hidden for ever), or until the reply ends. Each unit released is a word together with the whitespace after it, so a paragraph break arrives with the word before it.
//
// ENDING. `finish(done)` works the backlog off within `finishMs`, then calls `done`; it is also called (after a safety timeout) if the frames never come (a hidden tab). `flush()` shows everything at once
// (a Stop, an error); `reset()` drops what is waiting (the reply is being rewritten); `dispose()` just stops. Nothing here touches the page: it calls `onRelease(text)` and the caller shows it.
// Every `done` that was asked for is called, once, in the order asked: a second `finish` does not replace the first.
//
// A REPLY THAT ARRIVES WHOLE (no tokens: the server sends only the finished text) is shown the same way, a word at a time: push it, then `drain(done)`. The pace is a gentler one than the end-of-stream catch-up
// (`revealMinWordsPerSecond`, backlog worked off in about `revealCatchUpMs`), so a long answer flows in over a second or two instead of flashing up, and a `finish` that arrives while it is revealing (the turn
// ending) only waits for it: it does not speed it up.

export interface PacerOptions {
  minWordsPerSecond: number;
  catchUpMs: number;
  stallMs: number;
  finishMs: number;
  maxFrameMs: number;
  /** The most words shown in one frame, whatever the backlog. */
  maxWordsPerFrame: number;
  /** A reply that arrived whole is revealed at least this fast, and its backlog worked off in about `revealCatchUpMs`. */
  revealMinWordsPerSecond: number;
  revealCatchUpMs: number;
  /** If the frames never come (a hidden tab), a reveal is shown whole after this long. */
  revealSafetyMs: number;
}

export const PACER_DEFAULTS: PacerOptions = {
  minWordsPerSecond: 32,
  catchUpMs: 600,
  stallMs: 260,
  finishMs: 350,
  maxFrameMs: 100,
  maxWordsPerFrame: 12,
  revealMinWordsPerSecond: 55,
  revealCatchUpMs: 1100,
  revealSafetyMs: 6000,
};

/** The scheduler the pacer runs on; the browser's by default, a fake one in tests. */
export interface PacerClock {
  now(): number;
  frame(callback: () => void): number;
  cancelFrame(handle: number): void;
  timeout(callback: () => void, ms: number): number;
  clearTimeout(handle: number): void;
}

export const browserClock: PacerClock = {
  now: () => performance.now(),
  frame: callback => window.requestAnimationFrame(callback),
  cancelFrame: handle => window.cancelAnimationFrame(handle),
  timeout: (callback, ms) => window.setTimeout(callback, ms),
  clearTimeout: handle => window.clearTimeout(handle),
};

const UNIT = /\s*\S+\s*/y;

/** The buffer as units: each a word with the whitespace around it, and whether the unit is COMPLETE (whitespace follows its word). A buffer with no word in it has no units. Pure. */
export function unitsOf(buffer: string): { text: string; complete: boolean }[] {
  const units: { text: string; complete: boolean }[] = [];
  UNIT.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = UNIT.exec(buffer)) !== null) {
    units.push({ text: match[0], complete: /\s$/.test(match[0]) });
    if (UNIT.lastIndex >= buffer.length) break;
  }
  return units;
}

/** Splits the buffer into what to show now and what stays: up to `count` complete units, and the unfinished last word too when `includePartial`. Whitespace with no word after it stays in the buffer. Pure. */
export function takeUnits(buffer: string, count: number, includePartial: boolean): { out: string; rest: string } {
  const units = unitsOf(buffer);
  let taken = 0;
  let used = 0;
  for (const unit of units) {
    if (taken >= count) break;
    if (!unit.complete && !includePartial) break;
    used += unit.text.length;
    taken += 1;
  }
  return { out: buffer.slice(0, used), rest: buffer.slice(used) };
}

/** How many words to show in a frame of `dtMs`, and the fraction carried to the next frame. The rate is the faster of the steady minimum and the backlog worked off in `windowMs`. Pure. */
export function planWords(backlog: number, dtMs: number, carry: number, minWordsPerSecond: number, windowMs: number, maxWords: number): { count: number; carry: number } {
  if (backlog <= 0) return { count: 0, carry: 0 };
  const rate = Math.max(minWordsPerSecond, backlog / (Math.max(windowMs, 1) / 1000));
  const owed = carry + (rate * Math.max(dtMs, 0)) / 1000;
  const count = Math.min(Math.floor(owed), maxWords, backlog);
  return { count, carry: count >= maxWords || count >= backlog ? 0 : owed - count };
}

export class StreamPacer {
  private buffer = '';
  private carry = 0;
  private last = 0;
  private lastPush = 0;
  private frame: number | null = null;
  private finishing = false;
  private revealing = false;
  private callbacks: (() => void)[] = [];
  private safety: number | null = null;
  private dead = false;
  private finishStart = 0;
  private readonly options: PacerOptions;
  private readonly onRelease: (text: string) => void;
  private readonly clock: PacerClock;

  constructor(onRelease: (text: string) => void, clock: PacerClock = browserClock, options: Partial<PacerOptions> = {}) {
    this.onRelease = onRelease;
    this.clock = clock;
    this.options = { ...PACER_DEFAULTS, ...options };
  }

  /** What has arrived and is not shown yet. */
  pending(): string {
    return this.buffer;
  }

  /** Takes what just arrived. */
  push(text: string): void {
    if (this.dead || !text) return;
    this.buffer += text;
    this.lastPush = this.clock.now();
    this.start();
  }

  /** Shows what is waiting within `finishMs`, then calls `done` (at once when nothing is waiting). Several calls each get their `done` called, in order. While a `drain` is revealing a whole reply this only
   *  waits for it, at the gentle pace. */
  finish(done: () => void): void {
    if (this.dead) return done();
    this.callbacks.push(done);
    if (!this.buffer) return this.complete();
    if (this.revealing) return this.start();      // the reveal is already running at its own pace, with its own safety timer
    if (!this.finishing) this.finishStart = this.clock.now();
    this.finishing = true;
    this.start();
    if (this.safety === null) this.safety = this.clock.timeout(() => this.flush(), this.options.finishMs * 4);      // the frames may never come (a hidden tab)
  }

  /** For a reply that arrived whole and was just pushed: shows it a word at a time at the reveal pace, then calls `done` (at once when nothing is waiting). The last word needs no whitespace after it. */
  drain(done: () => void): void {
    if (this.dead) return done();
    this.callbacks.push(done);
    if (!this.buffer) return this.complete();
    this.revealing = true;
    this.start();
    if (this.safety === null) this.safety = this.clock.timeout(() => this.flush(), this.options.revealSafetyMs);
  }

  /** Shows everything that is waiting now, and finishes if a finish was asked for. */
  flush(): void {
    if (this.dead) return;
    this.stopFrames();
    if (this.buffer) {
      const all = this.buffer;
      this.buffer = '';
      this.carry = 0;
      this.onRelease(all);
    }
    this.complete();
  }

  /** Drops what is waiting (the reply is being written again) and any finish or drain that was asked for. */
  reset(): void {
    this.stopFrames();
    this.buffer = '';
    this.carry = 0;
    this.finishing = false;
    this.revealing = false;
    this.callbacks = [];
    this.clearSafety();
  }

  /** Stops for good, showing nothing more. */
  dispose(): void {
    this.reset();
    this.dead = true;
  }

  private start(): void {
    if (this.frame !== null) return;
    this.last = this.clock.now();
    this.frame = this.clock.frame(() => this.tick());
  }

  private stopFrames(): void {
    if (this.frame !== null) this.clock.cancelFrame(this.frame);
    this.frame = null;
  }

  private clearSafety(): void {
    if (this.safety !== null) this.clock.clearTimeout(this.safety);
    this.safety = null;
  }

  private complete(): void {
    this.clearSafety();
    const waiting = this.callbacks;
    this.callbacks = [];
    this.finishing = false;
    this.revealing = false;
    waiting.forEach(done => done());
  }

  private tick(): void {
    this.frame = null;
    if (this.dead) return;
    const now = this.clock.now();
    const dt = Math.min(Math.max(now - this.last, 0), this.options.maxFrameMs);
    this.last = now;
    const stalled = now - this.lastPush >= this.options.stallMs;
    const includePartial = this.finishing || this.revealing || stalled;      // a whole reply has no more words coming, so its last word needs no whitespace after it
    const backlog = unitsOf(this.buffer).length;
    // While finishing, the window SHRINKS as time passes, so the backlog is worked off by `finishMs` (a window that stayed the same would only decay it, never empty it on time).
    const windowMs = this.finishing ? Math.max(this.options.finishMs - (now - this.finishStart), this.options.maxFrameMs / 4) : this.revealing ? this.options.revealCatchUpMs : this.options.catchUpMs;
    const maxWords = this.finishing ? this.options.maxWordsPerFrame * 4 : this.options.maxWordsPerFrame;
    const minWps = this.revealing && !this.finishing ? this.options.revealMinWordsPerSecond : this.options.minWordsPerSecond;
    const plan = planWords(backlog, dt, this.carry, minWps, windowMs, maxWords);
    this.carry = plan.carry;
    if (plan.count > 0) {
      const { out, rest } = takeUnits(this.buffer, plan.count, includePartial);
      if (out) {
        this.buffer = rest;
        this.onRelease(out);
      }
    }
    if (!/\S/.test(this.buffer)) {
      // Nothing but whitespace (or nothing) is left: whitespace has no word to wait for, so it goes now.
      if (this.buffer) {
        this.onRelease(this.buffer);
        this.buffer = '';
      }
      this.carry = 0;
      if (this.finishing || this.revealing) this.complete();
      return;
    }
    this.start();       // words are waiting, or one is unfinished and the stream may yet finish it (or go quiet, which releases it)
  }
}
