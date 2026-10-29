import React, { useEffect, useRef, useState } from 'react';
import { GROUPS, groupBadge, groupOf, labelOf, nextEntry, nextTop, type Badges, type MenuGroup, type TabId } from './sonicHealthTabs';

interface Props {
  tab: TabId;
  onSelect: (tab: TabId) => void;
  badges: Badges;
}

/**
 * The Sonic Health sections as a two-level menu: Home and Safety open on a click; the other groups open a list of their sections. Built as a "disclosure navigation": each group is a button that says whether
 * its list is open (aria-expanded), the list is a set of buttons, and the section on show is marked aria-current.
 *
 * Keyboard: Left/Right/Home/End move between the groups; Down, Enter or Space opens a group and lands on the section on show (or the first); Up/Down/Home/End move inside it; Enter or Space picks;
 * Escape or Tab closes it (Escape puts focus back on the group); a click anywhere else closes it. Picking a section closes the list and keeps focus on its group.
 */
export default function SonicHealthMenu({ tab, onSelect, badges }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [focusRequest, setFocusRequest] = useState(0);      // bumped by a key that opens (or re-opens) a list, so focus moves in even when it was already open from a click
  const barRef = useRef<HTMLElement | null>(null);
  const triggers = useRef<Record<string, HTMLButtonElement | null>>({});
  const entries = useRef<Record<string, HTMLButtonElement | null>>({});
  const landOn = useRef<'active' | 'first' | 'last' | null>(null);
  const current = groupOf(tab);

  // Opening by keyboard puts focus inside the list once it exists.
  useEffect(() => {
    if (!open || !landOn.current) return;
    const group = GROUPS.find(g => g.id === open);
    if (!group) return;
    const target = landOn.current === 'first' ? group.tabs[0] : landOn.current === 'last' ? group.tabs[group.tabs.length - 1] : (group.tabs.includes(tab) ? tab : group.tabs[0]);
    landOn.current = null;
    entries.current[target]?.focus();
  }, [open, tab, focusRequest]);

  // A click outside the bar closes an open list.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event: MouseEvent) => {
      if (!barRef.current?.contains(event.target as Node)) setOpen(null);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const choose = (group: MenuGroup, picked: TabId) => {
    onSelect(picked);
    setOpen(null);
    triggers.current[group.id]?.focus();
  };

  const onTriggerKey = (event: React.KeyboardEvent, group: MenuGroup, index: number) => {
    const move = nextTop(index, event.key, GROUPS.length);
    if (move !== null) {
      event.preventDefault();
      setOpen(null);
      triggers.current[GROUPS[move].id]?.focus();
      return;
    }
    if (group.tabs.length > 1 && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
      event.preventDefault();
      landOn.current = event.key === 'ArrowUp' ? 'last' : 'active';
      setOpen(group.id);
      setFocusRequest(n => n + 1);
    } else if (event.key === 'Escape' && open) {
      setOpen(null);
    }
  };

  const onEntryKey = (event: React.KeyboardEvent, group: MenuGroup, index: number) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(null);
      triggers.current[group.id]?.focus();
      return;
    }
    if (event.key === 'Tab') {
      setOpen(null);
      return;
    }
    const move = nextEntry(index, event.key, group.tabs.length);
    if (move === null) return;
    event.preventDefault();
    entries.current[group.tabs[move]]?.focus();
  };

  return (
    <nav className="sh-tabs sh-tabs-menu" aria-label="Sonic Health sections" ref={barRef}>
      {GROUPS.map((group, index) => {
        const single = group.tabs.length === 1;
        const showing = group.id === current.id;
        const badge = groupBadge(group, badges);
        const expanded = open === group.id;
        const label = showing && !single ? `${group.label} · ${labelOf(tab)}` : group.label;
        return (
          <div className="sh-menu-group" key={group.id}>
            <button
              ref={node => { triggers.current[group.id] = node; }}
              id={`sh-tab-${single ? group.tabs[0] : group.id}`}
              type="button"
              className={`sh-tab${showing ? ' sh-tab-active' : ''}`}
              aria-current={showing && single ? 'page' : undefined}
              aria-haspopup={single ? undefined : 'true'}
              aria-expanded={single ? undefined : expanded}
              aria-controls={single ? undefined : `sh-menu-${group.id}`}
              tabIndex={showing ? 0 : -1}
              onClick={() => (single ? choose(group, group.tabs[0]) : setOpen(expanded ? null : group.id))}
              onKeyDown={event => onTriggerKey(event, group, index)}
            >
              {label}
              {badge && <span className={`sh-tab-badge sh-tab-badge-${badge.tone}`}>{badge.text}</span>}
              {!single && <span className="sh-menu-caret" aria-hidden="true">▾</span>}
            </button>
            {!single && expanded && (
              <ul className="sh-menu-pop" id={`sh-menu-${group.id}`} aria-label={`${group.label} sections`}>
                {group.tabs.map((entry, position) => {
                  const entryBadge = badges[entry];
                  return (
                    <li key={entry}>
                      <button
                        ref={node => { entries.current[entry] = node; }}
                        id={`sh-tab-${entry}`}
                        type="button"
                        className={`sh-menu-entry${tab === entry ? ' sh-menu-entry-active' : ''}`}
                        aria-current={tab === entry ? 'page' : undefined}
                        onClick={() => choose(group, entry)}
                        onKeyDown={event => onEntryKey(event, group, position)}
                      >
                        <span>{labelOf(entry)}</span>
                        {entryBadge && <span className={`sh-tab-badge sh-tab-badge-${entryBadge.tone}`}>{entryBadge.text}</span>}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        );
      })}
    </nav>
  );
}
