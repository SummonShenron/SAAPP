import React, { useEffect, useRef, useState } from 'react';
import Icon, { type IconName } from './icons';
import { CHIPS, chipLabel, isChanged, optionOf, type ChipKey } from './composerChipsLogic';
import './__styles__/ComposerChips.css';

export interface ChipControl { value: string; onChange: (id: string) => void; }

interface Props {
  /** The code model; null where it is not offered (Claude is not set up on the server, or a guest). */
  model: ChipControl | null;
  /** Plan or Agent; null without a connected folder that can be edited (it only matters there). */
  mode: ChipControl | null;
  thinking: ChipControl;
  answers: ChipControl;
  /** Locked while a turn runs: a setting changed in the middle would not apply to it. */
  disabled: boolean;
  /** Export this conversation: next to Help at the far right, off while there is nothing to export or a turn is running. */
  onExport: () => void;
  exportDisabled: boolean;
  /** What the Help button opens: the tips, as the card's content. */
  help: React.ReactNode;
}

/** Gemini's mark: the four-pointed star, in its blue. Drawn here as a small SVG; swap in the official file if you prefer it exact. */
function GeminiMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark">
      <path d="M12 2C12.6 7.4 16.6 11.4 22 12C16.6 12.6 12.6 16.6 12 22C11.4 16.6 7.4 12.6 2 12C7.4 11.4 11.4 7.4 12 2Z" fill="#4f8df5" />
    </svg>
  );
}

/** Claude's mark: the burst of rays, in clay. Drawn here as a small SVG; swap in the official file if you prefer it exact. */
function ClaudeMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark">
      <g transform="translate(12 12)" fill="#d97757">
        {Array.from({ length: 12 }, (_, i) => (
          <rect key={i} x="-1.1" y={i % 2 === 0 ? -10.5 : -8.4} width="2.2" height={i % 2 === 0 ? 6.5 : 4.4} rx="1.1" transform={`rotate(${i * 30})`} />
        ))}
      </g>
    </svg>
  );
}

/** The little robot the follow-up suggestions carry (Chat.tsx), in the chip's own colour. */
function RobotMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <line x1="12" y1="6" x2="12" y2="3.6" />
      <circle cx="12" cy="2.6" r="1.6" fill="currentColor" stroke="none" />
      <rect x="1.2" y="11" width="2" height="4.5" rx="1" fill="currentColor" stroke="none" />
      <rect x="20.8" y="11" width="2" height="4.5" rx="1" fill="currentColor" stroke="none" />
      <rect x="4" y="6" width="16" height="14" rx="4" />
      <circle cx="9.5" cy="12.2" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="14.5" cy="12.2" r="1.3" fill="currentColor" stroke="none" />
      <path d="M10 15.2 Q12 16.9 14 15.2" strokeWidth="1.4" />
    </svg>
  );
}

const ICON_NAMES: Record<string, IconName> = { plan: 'checklist', swift: 'bolt', deep: 'brain', grounded: 'book', open: 'globe' };

/** The icon for one state of one chip. */
function glyph(id: string): React.ReactNode {
  if (id === 'gemini') return <GeminiMark />;
  if (id === 'claude') return <ClaudeMark />;
  if (id === 'agent') return <RobotMark />;
  return <Icon name={ICON_NAMES[id] ?? 'checklist'} size={18} />;
}

type Open = ChipKey | 'help' | null;

/**
 * The settings that used to be toggles in the "..." menu, as four icons under the message box, and the Help button at the far right. Each icon shows which of its two states is on (and is
 * highlighted when that is not the ordinary one); pressing it opens a small popup that names both states and says what each does. One popup is open at a time; a press anywhere else, or Escape, closes it.
 */
const ComposerChips: React.FC<Props> = ({ model, mode, thinking, answers, disabled, onExport, exportDisabled, help }) => {
  const [open, setOpen] = useState<Open>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    const outside = (event: PointerEvent) => { if (root.current && !root.current.contains(event.target as Node)) setOpen(null); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(null); };
    document.addEventListener('pointerdown', outside);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); };
  }, [open]);

  const toggle = (which: Open) => setOpen(current => (current === which ? null : which));

  const chip = (key: ChipKey, control: ChipControl | null) => {
    if (!control) return null;
    const spec = CHIPS[key];
    const label = chipLabel(key, control.value);
    return (
      <div className="cc-item" key={key}>
        <button
          type="button" className={`cc-btn${isChanged(key, control.value) ? ' is-changed' : ''}${open === key ? ' is-open' : ''}`}
          aria-haspopup="menu" aria-expanded={open === key} aria-label={label} title={disabled ? `${label} (locked while Sonic is working)` : label}
          disabled={disabled} onClick={() => toggle(key)}
        >
          {glyph(control.value)}
        </button>
        {open === key && (
          <div className="cc-pop" role="menu" aria-label={spec.title}>
            <div className="cc-pop-title">{spec.title}</div>
            {spec.options.map(option => (
              <button
                key={option.id} type="button" role="menuitemradio" aria-checked={option.id === control.value}
                className={`cc-opt${option.id === control.value ? ' is-selected' : ''}`}
                onClick={() => { if (option.id !== control.value) control.onChange(option.id); setOpen(null); }}
              >
                <span className="cc-opt-icon">{glyph(option.id)}</span>
                <span className="cc-opt-text"><b>{optionOf(key, option.id).name}</b><span>{option.hint}</span></span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="composer-chips" ref={root}>
      {chip('model', model)}
      {chip('mode', mode)}
      {chip('thinking', thinking)}
      {chip('answers', answers)}
      <div className="cc-right">
        <button type="button" className="cc-btn" aria-label="Export this conversation" title="Export this conversation" disabled={exportDisabled} onClick={onExport}>
          <Icon name="download" size={18} />
        </button>
        <div className="cc-item cc-help">
          <button type="button" className={`cc-btn${open === 'help' ? ' is-open' : ''}`} aria-haspopup="dialog" aria-expanded={open === 'help'} aria-label="Help" title="Help" onClick={() => toggle('help')}>
            <Icon name="help" size={18} />
          </button>
          {open === 'help' && <div className="cc-pop cc-pop-help" role="dialog" aria-label="Help">{help}</div>}
        </div>
      </div>
    </div>
  );
};

export default ComposerChips;
