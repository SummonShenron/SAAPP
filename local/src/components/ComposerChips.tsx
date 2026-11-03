import React, { useEffect, useId, useRef, useState } from 'react';
import Icon, { type IconName } from './icons';
import { BASE_MODEL, CHIPS, chipLabel, isChanged, modelChipValue, modelsChanged, modelsLabel, specOption, type ChipKey, type ChipSpec } from './composerChipsLogic';
import './__styles__/ComposerChips.css';

export interface ChipControl {
  value: string;
  onChange: (id: string) => void;
  /** Options this person is not offered (for example Sonnet where the server has no price for it); they are left out of the popup. */
  hide?: string[];
}

/** What the chat settings need, in the three places they can live: under the message box, in the column's "Chat settings" card, and down the phone strip (SessionRail). */
export interface ChatSettings {
  /** The code model; null where it is not offered (neither Claude nor GPT is set up on the server, or a guest). */
  model: ChipControl | null;
  /** The base model, the one that writes the reply; null where there is no choice (GPT is not set up on the server, or a guest). It shares the model chip's popup with the code model, as its first column. */
  baseModel: ChipControl | null;
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


// The three model marks as drawings in a 24x24 box, apart from their <svg>, so a mark can be drawn whole (GeminiMark, ClaudeMark, GptMark) or as half of the model chip's combined mark (ModelGlyph).
function GeminiShape() {
  return <path d="M12 2C12.6 7.4 16.6 11.4 22 12C16.6 12.6 12.6 16.6 12 22C11.4 16.6 7.4 12.6 2 12C7.4 11.4 11.4 7.4 12 2Z" fill="#4f8df5" />;
}

function ClaudeShape() {
  return (
    <g transform="translate(12 12)" fill="#d97757">
      {Array.from({ length: 12 }, (_, i) => (
        <rect key={i} x="-1.1" y={i % 2 === 0 ? -10.5 : -8.4} width="2.2" height={i % 2 === 0 ? 6.5 : 4.4} rx="1.1" transform={`rotate(${i * 30})`} />
      ))}
    </g>
  );
}

function GptShape() {
  return (
    <g fill="none" stroke="#10a37f" strokeWidth="1.9" strokeLinejoin="round">
      <path d="M12 2.6 20 7.3v9.4l-8 4.7-8-4.7V7.3z" />
      <circle cx="12" cy="12" r="3.3" />
    </g>
  );
}

/** Gemini's mark: the four-pointed star, in its blue. Drawn here as a small SVG; swap in the official file if you prefer it exact. */
function GeminiMark() {
  return <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark"><GeminiShape /></svg>;
}

/** Claude's mark: the burst of rays, in clay. Drawn here as a small SVG; swap in the official file if you prefer it exact. */
function ClaudeMark() {
  return <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark"><ClaudeShape /></svg>;
}

/** GPT's mark: a rounded hexagon with a ring in it, in a green. Drawn here as a small SVG of our own, not OpenAI's logo; swap in the official file if you prefer it exact. */
function GptMark() {
  return <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark"><GptShape /></svg>;
}

const SHAPES: Record<string, () => React.JSX.Element> = { gemini: GeminiShape, claude: ClaudeShape, sonnet: ClaudeShape, gpt: GptShape };

/**
 * The model chip's icon: ONE svg that is the upper-left half of the Base model's mark and the lower-right half of the Code model's, split on a 45 degree "/" slash from the bottom-left corner to the top-right. Where both are the same maker (the ordinary Gemini and Gemini, or
 * Claude and Claude Sonnet) it is that mark whole, and where only one of the two is offered it is that one's mark.
 */
export function ModelGlyph({ base, code }: { base: string | null; code: string | null }) {
  const id = useId().replace(/:/g, '');
  const left = base ?? code ?? 'gemini';
  const right = code ?? base ?? 'gemini';
  const Left = SHAPES[left] ?? GeminiShape;
  const Right = SHAPES[right] ?? GeminiShape;
  if (Left === Right) return glyph(left);
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" className="cc-mark">
      <defs>
        <clipPath id={`${id}l`}><polygon points="0,0 24,0 0,24" /></clipPath>
        <clipPath id={`${id}r`}><polygon points="24,0 24,24 0,24" /></clipPath>
      </defs>
      <g clipPath={`url(#${id}l)`}><Left /></g>
      <g clipPath={`url(#${id}r)`}><Right /></g>
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
export function glyph(id: string): React.ReactNode {
  if (id === 'gemini') return <GeminiMark />;
  if (id === 'claude' || id === 'sonnet') return <ClaudeMark />;
  if (id === 'gpt') return <GptMark />;
  if (id === 'agent') return <RobotMark />;
  return <Icon name={ICON_NAMES[id] ?? 'checklist'} size={18} />;
}

/** The choices of one setting as a list: its heading, then each choice with its icon, name and what it does, the one in force marked. `compact` puts the short line under each name (the full wording is the tooltip). */
function OptionList({ spec, control, onDone, compact }: { spec: ChipSpec; control: ChipControl; onDone: () => void; compact: boolean }) {
  return (
    <div className="cc-col" role="group" aria-label={spec.title}>
      <div className="cc-pop-title">{spec.title}</div>
      {spec.options.filter(option => !control.hide?.includes(option.id)).map(option => (
        <button
          key={option.id} type="button" role="menuitemradio" aria-checked={option.id === control.value} title={option.hint}
          className={`cc-opt${option.id === control.value ? ' is-selected' : ''}`}
          onClick={() => { if (option.id !== control.value) control.onChange(option.id); onDone(); }}
        >
          <span className="cc-opt-icon">{glyph(option.id)}</span>
          <span className="cc-opt-text"><b>{specOption(spec, option.id).name}</b><span>{compact ? (option.short ?? option.hint) : option.hint}</span></span>
        </button>
      ))}
    </div>
  );
}

/** The choices of one chip, each with its icon, name and what it does; the one in force is marked. `className` is the popup's own (the bar's floats, the phone strip's opens beside its button). */
export function ChipChoices({ chipKey, control, onDone, className }: { chipKey: ChipKey; control: ChipControl; onDone: () => void; className: string }) {
  const spec = CHIPS[chipKey];
  return (
    <div className={className} role="menu" aria-label={spec.title}>
      <OptionList spec={spec} control={control} onDone={onDone} compact={false} />
    </div>
  );
}

/**
 * The model chip's popup: two columns, the Base model (which model writes the reply) and the Code model (which does the reasoning steps of code work), each only where the server offers a choice. Both sit in
 * the same popup so one press shows how Sonic is set up. The columns share the width and stack by themselves where it is too narrow for two (the CSS: cc-cols).
 */
export function ModelChoices({ base, code, onDone, className }: { base: ChipControl | null; code: ChipControl | null; onDone: () => void; className: string }) {
  return (
    <div className={`${className} cc-cols`} role="menu" aria-label="Models">
      {base && <OptionList spec={BASE_MODEL} control={base} onDone={onDone} compact />}
      {code && <OptionList spec={CHIPS.model} control={code} onDone={onDone} compact />}
    </div>
  );
}

/** What the model chip shows for its two settings: its icon (the first one that is not the ordinary model), whether it is highlighted, and its name. Null when neither setting is offered. */
export function modelChip(base: ChipControl | null, code: ChipControl | null): { value: string; changed: boolean; label: string } | null {
  if (!base && !code) return null;
  const baseValue = base ? base.value : null;
  const codeValue = code ? code.value : null;
  return { value: modelChipValue(baseValue, codeValue), changed: modelsChanged(baseValue, codeValue), label: modelsLabel(baseValue, codeValue) };
}

type Open = ChipKey | 'help' | null;

/**
 * The settings that used to be toggles in the "..." menu, as icons in the session column's "Chat settings" card (nothing is drawn under the message box), with Export and Help in the same grid. Each icon
 * shows which of its two states is on (and is highlighted when that is not the ordinary one); pressing it lists both states under the row and says what each does. In the column the open choice is
 * listed under the row at the card's width instead of floating, because the column scrolls and is narrower than a popup. One is open at a time; a press anywhere else, or Escape, closes it.
 */
const ComposerChips: React.FC<ChatSettings> = ({ model, baseModel, mode, thinking, answers, disabled, onExport, exportDisabled, help }) => {
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

  /** The popup of one chip: its title and both choices. */
  const choices = (key: ChipKey, control: ChipControl) => <ChipChoices chipKey={key} control={control} onDone={() => setOpen(null)} className="cc-pop" />;

  const controlOf = (key: ChipKey): ChipControl | null => (key === 'model' ? model : key === 'mode' ? mode : key === 'thinking' ? thinking : answers);

  const chip = (key: ChipKey, control: ChipControl | null) => {
    if (!control) return null;
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
      </div>
    );
  };

  /** The model chip: one icon for the Base model and the Code model together, opening the two-column popup. */
  const modelButton = () => {
    const state = modelChip(baseModel, model);
    if (!state) return null;
    return (
      <div className="cc-item" key="model">
        <button
          type="button" className={`cc-btn${state.changed ? ' is-changed' : ''}${open === 'model' ? ' is-open' : ''}`}
          aria-haspopup="menu" aria-expanded={open === 'model'} aria-label={state.label} title={disabled ? `${state.label} (locked while Sonic is working)` : state.label}
          disabled={disabled} onClick={() => toggle('model')}
        >
          <ModelGlyph base={baseModel ? baseModel.value : null} code={model ? model.value : null} />
        </button>
      </div>
    );
  };

  const row = (
    <>
      {modelButton()}
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
        </div>
      </div>
    </>
  );

  // The open choice is listed under the row, at the card's width.
  const openControl = open && open !== 'help' && open !== 'model' ? controlOf(open) : null;
  return (
    <div className="composer-chips composer-chips-panel" ref={root}>
      <div className="cc-row">{row}</div>
      {open === 'help' && <div className="cc-pop cc-pop-help" role="dialog" aria-label="Help">{help}</div>}
      {open === 'model' && <ModelChoices base={baseModel} code={model} onDone={() => setOpen(null)} className="cc-pop" />}
      {open && open !== 'help' && open !== 'model' && openControl && choices(open, openControl)}
    </div>
  );
};

export default ComposerChips;
