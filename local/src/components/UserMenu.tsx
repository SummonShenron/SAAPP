import React, { useEffect, useRef, useState } from 'react';
import Icon from './icons';

interface UserMenuProps {
  /** What the person is called in the interface: the part of their email before the @, or "Guest". */
  name: string;
  /** Their full sign-in identity, shown once inside the menu; null for a guest. */
  email: string | null;
  theme: string;
  compact: boolean;
  onToggleTheme: () => void;
  onAccount: () => void;
  onHelp: () => void;
  onLogout: () => void;
}

function initialsOf(name: string): string {
  const parts = name.split(/[^a-zA-Z0-9]+/).filter(Boolean);
  const letters = parts.length > 1 ? parts[0][0] + parts[1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}

/**
 * The person's chip at the foot of the rail, and the menu it opens: account settings, theme, help and disconnect. It replaces the green identity
 * text that used to sit in the banner, which looked like a label and gave no hint that there were settings behind it.
 */
const UserMenu: React.FC<UserMenuProps> = ({ name, email, theme, compact, onToggleTheme, onAccount, onHelp, onLogout }) => {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent | TouchEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('touchstart', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('touchstart', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const choose = (action: () => void) => () => { setOpen(false); action(); };
  const dark = theme === 'shadow';

  return (
    <div className="user-menu" ref={root}>
      {open && (
        <div className="user-menu-popover" role="menu">
          <div className="user-menu-who">
            <div className="user-menu-name">{name}</div>
            {email && <div className="user-menu-email">{email}</div>}
          </div>
          <button type="button" role="menuitem" className="user-menu-item" onClick={choose(onAccount)}><Icon name="user" size={16} />Account settings</button>
          <button type="button" role="menuitem" className="user-menu-item" onClick={choose(onToggleTheme)}>
            <Icon name={dark ? 'sun' : 'moon'} size={16} />{dark ? 'Light theme' : 'Dark theme'}
          </button>
          <button type="button" role="menuitem" className="user-menu-item" onClick={choose(onHelp)}><Icon name="help" size={16} />Help</button>
          <div className="user-menu-divider" />
          <button type="button" role="menuitem" className="user-menu-item user-menu-danger" onClick={choose(onLogout)}><Icon name="logout" size={16} />Disconnect</button>
        </div>
      )}
      <button
        type="button"
        className="user-chip"
        onClick={() => setOpen(v => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        title={compact ? name : undefined}
      >
        <span className="user-avatar">{initialsOf(name)}</span>
        {!compact && <span className="user-chip-name">{name}</span>}
        {!compact && <Icon name="chevronUp" size={14} className="user-chip-caret" />}
      </button>
    </div>
  );
};

export default UserMenu;
