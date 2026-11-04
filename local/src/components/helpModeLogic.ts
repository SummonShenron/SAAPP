// The parts of Help Mode (components/HelpMode.tsx) that need no React and no page, so they can be tested. Help Mode: the `?` at the top right turns it on; while it is on, pressing anything explains it instead
// of using it. Any element is made helpable with one attribute, `data-help="some.id"`, and one entry in helpTips.ts; nothing is passed down through components.

export interface HelpTip {
  /** What the thing is called, as the card's heading. */
  title: string;
  /** What it does, in a sentence or two. */
  what: string;
  /** When to use it. */
  when?: string;
  /** What it will NOT do, where that is worth saying (it never runs a command without a press, it never changes your files until you apply it). */
  wont?: string;
}

/** What the card says for something nobody has written a tip for yet. Help Mode still catches the press, so a stray one can never send, apply, delete or pay. */
export const NO_TIP: HelpTip = {
  title: 'No tip for this yet',
  what: 'Nothing was pressed: Help Mode is on, so a press only explains things.',
  when: 'Press the ? again, or Escape, to go back to using the page.',
};

export const HELP_ATTR = 'data-help';
export const HELP_UI_ATTR = 'data-help-ui';

/** The tip for an id, or NO_TIP when there is no id or no entry. */
export function tipFor(id: string | null | undefined, tips: Record<string, HelpTip>): HelpTip {
  return (id && Object.prototype.hasOwnProperty.call(tips, id) ? tips[id] : null) ?? NO_TIP;
}

/** Whether a key press, while Help Mode is on, should open the tip of the focused thing: Enter always (it is what presses a button, follows a link or sends a message), Space only on something that is not a text field
 *  (in a text field it is a space). Pure. */
export function keyOpensHelp(key: string, targetIsTextField: boolean): boolean {
  if (key === 'Enter') return true;
  if (key === ' ' || key === 'Spacebar') return !targetIsTextField;
  return false;
}

/** Whether a tag, role or flag makes a place where typing letters and spaces is the point. */
export function isTextField(tag: string, editable: boolean, inputType = ''): boolean {
  const name = tag.toLowerCase();
  if (editable || name === 'textarea' || name === 'select') return true;
  if (name !== 'input') return false;
  return !['button', 'submit', 'reset', 'checkbox', 'radio', 'image', 'file', 'range', 'color'].includes(inputType.toLowerCase());
}

export interface Box { left: number; top: number; width: number; height: number; }

export const CARD_WIDTH = 340;
export const CARD_GAP = 10;
export const CARD_MARGIN = 12;

/** Where the card goes: below the thing pressed when there is room, otherwise above it, otherwise at the top of the window; kept inside the window with a margin on every side. `cardHeight` is the card's own height as measured. */
export function placeCard(target: Box, cardHeight: number, viewport: { width: number; height: number }, cardWidth: number = CARD_WIDTH): { left: number; top: number; placement: 'below' | 'above' | 'top' } {
  const width = Math.min(cardWidth, viewport.width - 2 * CARD_MARGIN);
  const left = Math.min(Math.max(target.left, CARD_MARGIN), Math.max(viewport.width - width - CARD_MARGIN, CARD_MARGIN));
  const below = target.top + target.height + CARD_GAP;
  if (below + cardHeight + CARD_MARGIN <= viewport.height) return { left, top: below, placement: 'below' };
  const above = target.top - CARD_GAP - cardHeight;
  if (above >= CARD_MARGIN) return { left, top: above, placement: 'above' };
  return { left, top: CARD_MARGIN, placement: 'top' };
}

/** The ids used as `data-help="..."` in some source text, in the order they appear. Used by the test that keeps the tips and the page in step. */
export function helpIdsIn(source: string): string[] {
  return [...source.matchAll(/data-help="([^"{}]+)"/g)].map(match => match[1]);
}
