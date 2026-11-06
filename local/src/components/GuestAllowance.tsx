import { useEffect, useState } from 'react';
import { api } from '../api';
import { guestAllowanceNote, type GuestAllowance as Allowance } from '../guestAllowanceLogic';

/** One quiet line above the message box for the shared guest: how many free demo turns are left, and from the nudge turn on what an account adds, with a way to sign up. It asks the server again
 *  whenever `refreshKey` changes (the page passes the message count, so it updates after every turn). Nothing for anyone else. */
export function GuestAllowance({ enabled, refreshKey }: { enabled: boolean; refreshKey: number }) {
  const [allowance, setAllowance] = useState<Allowance | null>(null);

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    api.getGuestAllowance().then(found => { if (!cancelled) setAllowance(found); }).catch(() => { if (!cancelled) setAllowance(null); });
    return () => { cancelled = true; };
  }, [enabled, refreshKey]);

  const note = enabled ? guestAllowanceNote(allowance) : null;
  if (!note) return null;
  return (
    <div className={`guest-allowance is-${note.tone}`} data-help="chat.guest_allowance" role="status">
      <span>{note.text}</span>
      {note.tone !== 'info' && (
        <button type="button" className="guest-allowance-signup" data-help="chat.guest_signup" onClick={() => { void api.logout(); }}>Create a free account</button>
      )}
    </div>
  );
}
