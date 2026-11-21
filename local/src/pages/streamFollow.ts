// Following a reply down the page while it streams (pages/Chat.tsx). Replaces "snap to the bottom on every token".
//
// WHY. Every token used to call scrollTo(bottom) with no animation. The reply grows by a line at a time, so the page moved in small instant jumps, each landing on a different frame from the text that caused it:
// the view lurched down a line, paused, lurched again. This follows instead: once a frame, it moves a fraction of the way to the bottom (an ease-out, quick when far behind and gentle when close), so the page
// glides with the growing text and a tall block (a code box, a list arriving) is caught up with in a smooth sweep rather than a jump.
//
// LETTING GO. It follows only while the person is following. The moment they steer (the wheel up, a finger dragging down, an arrow, PageUp or Home, a drag on the scrollbar that moves the page up) it stops moving
// the page, and it does not take over again until they scroll back to the bottom themselves. (This is what stops a reply from pulling you away from the paragraph you scrolled up to re-read.) A page that shrinks
// under it (a card closing) is not read as the person scrolling up: the browser lowers scrollTop by itself then, and that is told apart by being at the bottom.
//
// ENDING. `release()` lets the follower finish the last sweep to the bottom (up to `settleMs`) and then stop, so the end of a reply does not leave the page a few lines short.

/** The parts of a wheel, touch or key event it reads (the browser's events have all of them). */
export interface FollowEvent {
  deltaY?: number;
  key?: string;
  touches?: ArrayLike<{ clientY: number }>;
}

/** What it needs of the chat window (an HTMLElement has all of it). */
export interface FollowWindow {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
  scrollTo(options: { top: number; behavior: ScrollBehavior }): void;
  addEventListener(type: string, listener: (event: FollowEvent) => void, options?: { passive: boolean }): void;
  removeEventListener(type: string, listener: (event: FollowEvent) => void): void;
}

export const FOLLOW = {
  /** The share of the remaining distance covered in one 60 fps frame (about 16.7 ms), scaled by the real frame time. */
  ease: 0.2,
  /** Closer than this to the bottom, it lands on it. */
  snapPx: 0.75,
  /** At or within this of the bottom the person counts as following again. */
  nearPx: 32,
  /** A frame is counted as no longer than this. */
  maxFrameMs: 100,
  /** The last sweep after the reply ends gets this long at most. */
  settleMs: 700,
  /** A drop in scrollTop smaller than this is rounding, not the person. */
  slackPx: 3,
};

/** The new scrollTop one frame later: a fraction of the way to `target`, the fraction growing with the frame time and the gap's size (a long way off is caught up faster); on it when within snapPx. Pure. */
export function easedTop(current: number, target: number, dtMs: number, ease: number = FOLLOW.ease): number {
  const gap = target - current;
  if (Math.abs(gap) <= FOLLOW.snapPx) return target;
  const frames = Math.min(Math.max(dtMs, 0), FOLLOW.maxFrameMs) / (1000 / 60);
  const boost = 1 + Math.min(Math.abs(gap) / 600, 2);            // up to 3x as fast when a whole screen behind
  const share = 1 - Math.pow(1 - Math.min(ease * boost, 0.95), frames);
  return current + gap * share;
}

/** Whether the person moved the page UP, given what we last set it to: the page is higher than we left it, by more than rounding, and not merely at the bottom of a page that got shorter. Pure. */
export function movedUpByPerson(scrollTop: number, lastSet: number, maxTop: number): boolean {
  return scrollTop < lastSet - FOLLOW.slackPx && scrollTop < maxTop - FOLLOW.slackPx;
}

/** Keys that move a page up. */
export const UP_KEYS: ReadonlySet<string> = new Set(['ArrowUp', 'PageUp', 'Home']);

export class StreamFollower {
  private handle = 0;
  private running = false;
  private releasedAt: number | null = null;
  private following = true;
  private lastSet = 0;
  private touchY: number | null = null;
  private attached: FollowWindow | null = null;
  private last = 0;
  private readonly onScroll = () => this.scrolled();
  private readonly onWheel = (event: { deltaY?: number }) => { if ((event.deltaY ?? 0) < 0) this.following = false; };
  private readonly onTouchStart = (event: { touches?: ArrayLike<{ clientY: number }> }) => { this.touchY = event.touches?.[0]?.clientY ?? null; };
  private readonly onTouchMove = (event: { touches?: ArrayLike<{ clientY: number }> }) => {
    const y = event.touches?.[0]?.clientY;
    if (y !== undefined && this.touchY !== null && y > this.touchY + 2) this.following = false;      // the finger moved down: the page moves up
    if (y !== undefined) this.touchY = y;
  };
  private readonly onKey = (event: { key?: string }) => { if (event.key && UP_KEYS.has(event.key)) this.following = false; };

  private readonly getWindow: () => FollowWindow | null;
  private readonly raf: (callback: () => void) => number;
  private readonly caf: (handle: number) => void;
  private readonly now: () => number;
  private readonly reducedMotion: () => boolean;

  constructor(
    getWindow: () => FollowWindow | null,
    raf: (callback: () => void) => number = callback => window.requestAnimationFrame(callback),
    caf: (handle: number) => void = handle => window.cancelAnimationFrame(handle),
    now: () => number = () => performance.now(),
    reducedMotion: () => boolean = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches,
  ) {
    this.getWindow = getWindow;
    this.raf = raf;
    this.caf = caf;
    this.now = now;
    this.reducedMotion = reducedMotion;
  }

  /** Whether it is moving the page (false once the person has scrolled away). */
  isFollowing(): boolean {
    return this.following;
  }

  /** Starts following (and follows again from now on, as when the person sends a message). */
  start(): void {
    this.following = true;
    this.releasedAt = null;
    const win = this.getWindow();
    if (win) this.attach(win);          // listening from now, not from the first frame: a wheel turned in that first moment still counts
    if (this.running) return;
    this.running = true;
    this.last = this.now();
    this.handle = this.raf(() => this.tick());
  }

  /** The reply is over: finish the last sweep to the bottom, then stop. */
  release(): void {
    if (!this.running) return;
    if (this.releasedAt === null) this.releasedAt = this.now();
  }

  /** Stops at once. */
  stop(): void {
    this.running = false;
    this.caf(this.handle);
    this.detach();
  }

  private attach(win: FollowWindow): void {
    if (this.attached === win) return;
    this.detach();
    this.attached = win;
    this.lastSet = win.scrollTop;
    win.addEventListener('scroll', this.onScroll, { passive: true });
    win.addEventListener('wheel', this.onWheel, { passive: true });
    win.addEventListener('touchstart', this.onTouchStart, { passive: true });
    win.addEventListener('touchmove', this.onTouchMove, { passive: true });
    win.addEventListener('keydown', this.onKey);
  }

  private detach(): void {
    const win = this.attached;
    if (!win) return;
    win.removeEventListener('scroll', this.onScroll);
    win.removeEventListener('wheel', this.onWheel);
    win.removeEventListener('touchstart', this.onTouchStart);
    win.removeEventListener('touchmove', this.onTouchMove);
    win.removeEventListener('keydown', this.onKey);
    this.attached = null;
  }

  private scrolled(): void {
    const win = this.attached;
    if (!win) return;
    const maxTop = Math.max(0, win.scrollHeight - win.clientHeight);
    if (movedUpByPerson(win.scrollTop, this.lastSet, maxTop)) this.following = false;      // a drag on the scrollbar, a trackpad fling: anything that moved the page up
    else if (maxTop - win.scrollTop <= FOLLOW.nearPx) this.following = true;               // back at the bottom: following again
  }

  private tick(): void {
    if (!this.running) return;
    const win = this.getWindow();
    const now = this.now();
    const dt = Math.min(Math.max(now - this.last, 0), FOLLOW.maxFrameMs);
    this.last = now;
    if (win) {
      this.attach(win);
      const target = Math.max(0, win.scrollHeight - win.clientHeight);
      if (!this.following && target - win.scrollTop <= FOLLOW.nearPx) this.following = true;      // came back to the bottom by themselves
      if (this.following && Math.abs(target - win.scrollTop) > 0.5) {
        const next = this.reducedMotion() ? target : easedTop(win.scrollTop, target, dt);
        this.lastSet = next;
        win.scrollTo({ top: next, behavior: 'instant' as ScrollBehavior });
      } else if (this.following) {
        this.lastSet = win.scrollTop;
      }
    }
    if (this.releasedAt !== null) {
      const settled = !win || !this.following || Math.abs(Math.max(0, win.scrollHeight - win.clientHeight) - win.scrollTop) <= FOLLOW.snapPx;
      if (settled || now - this.releasedAt >= FOLLOW.settleMs) {
        if (win && this.following) {
          const rest = Math.max(0, win.scrollHeight - win.clientHeight);
          if (rest - win.scrollTop > 0.001) win.scrollTo({ top: rest, behavior: 'instant' as ScrollBehavior });      // the last fraction of a pixel: it ends exactly on the bottom
        }
        this.stop();
        return;
      }
    }
    this.handle = this.raf(() => this.tick());
  }
}
