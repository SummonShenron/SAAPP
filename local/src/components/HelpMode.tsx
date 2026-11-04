import React, { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import Icon from './icons';
import { HELP_ATTR, HELP_UI_ATTR, isTextField, keyOpensHelp, placeCard, tipFor, type Box } from './helpModeLogic';
import { HELP_TIPS } from './helpTips';
import './__styles__/HelpMode.css';

interface HelpModeValue { active: boolean; toggle: () => void; }

const HelpModeContext = createContext<HelpModeValue>({ active: false, toggle: () => undefined });

/** Help Mode's state, for the button that turns it on. Outside a provider it is off and does nothing. */
export function useHelpMode(): HelpModeValue {
  return useContext(HelpModeContext);
}

interface OpenCard { id: string | null; target: Element; box: Box; }

const inHelpUi = (node: EventTarget | null): boolean => node instanceof Element && node.closest(`[${HELP_UI_ATTR}]`) !== null;

function boxOf(element: Element): Box {
  const rect = element.getBoundingClientRect();
  return { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
}

/**
 * Help Mode, in the style of FromSoftware's menus (docs/help-system-plan.md). While it is on, EVERY press in the page is caught before it does anything and opens a card that says what the thing pressed does:
 * the nearest ancestor with a `data-help="id"` gives the tip (components/helpTips.ts), and something with none says there is no tip yet, so a stray press can never send, apply, delete or pay. Enter or Space on a
 * focused thing does the same. Escape closes the card, then leaves the mode. The toggle button, the card and the banner carry `data-help-ui` and are the only things not caught. Nothing is listening while it is off.
 * `onOpenGuide` is the long help guide (the Help item in the user menu): the card links to it.
 */
export function HelpModeProvider({ children, onOpenGuide }: { children: React.ReactNode; onOpenGuide?: () => void }) {
  const [active, setActive] = useState(false);
  const [card, setCard] = useState<OpenCard | null>(null);
  const cardRef = useRef<OpenCard | null>(null);
  cardRef.current = card;
  const returnFocus = useRef<HTMLElement | null>(null);

  const toggle = useCallback(() => {
    setActive(current => !current);
    setCard(null);
  }, []);

  useEffect(() => {
    document.body.classList.toggle('help-mode', active);
    return () => document.body.classList.remove('help-mode');
  }, [active]);

  useEffect(() => {
    if (!active) return undefined;
    const openFor = (node: Element) => {
      const marked = node.closest(`[${HELP_ATTR}]`);
      if (!returnFocus.current && document.activeElement instanceof HTMLElement) returnFocus.current = document.activeElement;
      const subject = marked ?? node;
      setCard({ id: marked ? marked.getAttribute(HELP_ATTR) : null, target: subject, box: boxOf(subject) });
    };
    const catchPress = (event: MouseEvent) => {
      if (inHelpUi(event.target)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.type === 'click' && event.target instanceof Element) openFor(event.target);
    };
    const quiet = (event: Event) => {
      if (inHelpUi(event.target)) return;
      event.stopPropagation();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        if (cardRef.current) setCard(null); else setActive(false);
        return;
      }
      if (inHelpUi(event.target) || !(event.target instanceof Element)) return;
      const target = event.target;
      const editable = target instanceof HTMLElement && target.isContentEditable;
      const typing = isTextField(target.tagName, editable, target instanceof HTMLInputElement ? target.type : '');
      if (keyOpensHelp(event.key, typing)) {
        event.preventDefault();
        event.stopPropagation();
        openFor(target);
      }
    };
    const clickLike = ['click', 'auxclick', 'dblclick', 'contextmenu'] as const;
    const pressLike = ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'touchstart', 'touchend'] as const;
    clickLike.forEach(name => document.addEventListener(name, catchPress, true));
    pressLike.forEach(name => document.addEventListener(name, quiet, true));
    document.addEventListener('keydown', onKey, true);
    return () => {
      clickLike.forEach(name => document.removeEventListener(name, catchPress, true));
      pressLike.forEach(name => document.removeEventListener(name, quiet, true));
      document.removeEventListener('keydown', onKey, true);
    };
  }, [active]);

  // The thing the card is about is marked while the card is open, so it is clear what is being explained.
  useEffect(() => {
    if (!card) return undefined;
    card.target.classList.add('help-target');
    return () => card.target.classList.remove('help-target');
  }, [card]);

  const close = () => {
    setCard(null);
    returnFocus.current?.focus?.();
    returnFocus.current = null;
  };
  const guide = () => {
    setCard(null);
    setActive(false);
    onOpenGuide?.();
  };

  const value = useMemo(() => ({ active, toggle }), [active, toggle]);
  return (
    <HelpModeContext.Provider value={value}>
      {children}
      {active && (
        <div className="help-banner" data-help-ui role="status">
          Help Mode: press anything to see what it does. Escape leaves it.
        </div>
      )}
      {active && card && <HelpCard card={card} onClose={close} onGuide={onOpenGuide ? guide : undefined} />}
    </HelpModeContext.Provider>
  );
}

function HelpCard({ card, onClose, onGuide }: { card: OpenCard; onClose: () => void; onGuide?: () => void }) {
  const tip = tipFor(card.id, HELP_TIPS);
  const ref = useRef<HTMLElement>(null);
  const [place, setPlace] = useState<{ left: number; top: number } | null>(null);

  // Placed once the card has its real height, before it is painted, so it never flashes in the wrong place.
  useLayoutEffect(() => {
    const height = ref.current?.offsetHeight ?? 160;
    const { left, top } = placeCard(boxOf(card.target), height, { width: window.innerWidth, height: window.innerHeight });
    setPlace({ left, top });
    ref.current?.querySelector<HTMLElement>('button')?.focus();
  }, [card]);

  return (
    <aside ref={ref} className="help-card" data-help-ui role="dialog" aria-label={`Help: ${tip.title}`} style={place ? { left: place.left, top: place.top } : { left: 0, top: 0, visibility: 'hidden' }}>
      <div className="help-card-head">
        <h2 className="help-card-title">{tip.title}</h2>
        <button type="button" className="help-card-close" onClick={onClose} aria-label="Close this tip"><Icon name="close" size={16} /></button>
      </div>
      <p className="help-card-what">{tip.what}</p>
      {tip.when && <p className="help-card-row"><strong>Use it when:</strong> {tip.when}</p>}
      {tip.wont && <p className="help-card-row"><strong>It will not:</strong> {tip.wont}</p>}
      <div className="help-card-foot">
        {onGuide && <button type="button" className="help-card-link" onClick={onGuide}>Open the full guide</button>}
        <span className="help-card-hint">Escape closes this.</span>
      </div>
    </aside>
  );
}
