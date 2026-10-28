/**
 * The small time stamp shown beside a reply's feedback hearts when the message is hovered (kept apart from the page so it can be tested: messageTime.test.mjs).
 * A time that is today is just the clock ("3:42 PM"); an earlier day adds the date ("Oct 27, 3:42 PM"), and another year adds the year. A message with no stamp (one saved before
 * messages were stamped) or a time that cannot be read shows nothing rather than a guess.
 */

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? null : when;
}

const sameDay = (a: Date, b: Date): boolean => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** "3:42 PM", "Oct 27, 3:42 PM" or "Oct 27, 2025, 3:42 PM", in the person's own time zone and language. "" when there is no usable time. */
export function formatMessageTime(iso: string | null | undefined, now: Date = new Date(), locale?: string): string {
  const when = parse(iso);
  if (!when) return '';
  const clock = when.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit' });
  if (sameDay(when, now)) return clock;
  const date = when.toLocaleDateString(locale, when.getFullYear() === now.getFullYear() ? { month: 'short', day: 'numeric' } : { month: 'short', day: 'numeric', year: 'numeric' });
  return `${date}, ${clock}`;
}

/** The full date and time, for the tooltip on the stamp. */
export function fullMessageTime(iso: string | null | undefined, locale?: string): string {
  const when = parse(iso);
  return when ? when.toLocaleString(locale, { dateStyle: 'full', timeStyle: 'short' }) : '';
}
