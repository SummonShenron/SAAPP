// The four settings that sit as icons under the message box (components/ComposerChips.tsx): the code model, the mode, how hard to think, and where answers come from. Each is one icon that shows which
// of its two states is on, and opens a two-choice popup. Kept apart from React so the words and the mapping to the page's settings can be tested. These replaced the "..." menu's toggles.

export type ChipKey = 'model' | 'mode' | 'thinking' | 'answers';

export interface ChipOption { id: string; name: string; hint: string; }

export interface ChipSpec {
  key: ChipKey;
  /** The popup's heading and the icon's accessible name ("Thinking: Swift"). */
  title: string;
  /** The state a person is in without having chosen anything; the icon is quiet there and highlighted in the other one. */
  ordinary: string;
  options: ChipOption[];
}

export const CHIPS: Record<ChipKey, ChipSpec> = {
  model: {
    key: 'model', title: 'Code model', ordinary: 'gemini',
    options: [
      { id: 'gemini', name: 'Gemini', hint: 'The default.' },
      { id: 'claude', name: 'Claude', hint: 'Does the reasoning and code-writing steps. The reply is still written by Gemini.' },
    ],
  },
  mode: {
    key: 'mode', title: 'Mode', ordinary: 'agent',
    options: [
      { id: 'plan', name: 'Plan', hint: 'Proposes a plan for you to approve before it changes your files. Costs an extra reading pass.' },
      { id: 'agent', name: 'Agent', hint: 'Goes straight to the work.' },
    ],
  },
  thinking: {
    key: 'thinking', title: 'Thinking', ordinary: 'swift',
    options: [
      { id: 'swift', name: 'Swift', hint: 'Standard effort. The default.' },
      { id: 'deep', name: 'Deep', hint: 'More steps and retries on a hard question. Slower, and costs more.' },
    ],
  },
  answers: {
    key: 'answers', title: 'Answers', ordinary: 'grounded',
    options: [
      { id: 'grounded', name: 'Grounded', hint: 'Answers from your knowledge base.' },
      { id: 'open', name: 'Open', hint: 'Can also answer beyond it.' },
    ],
  },
};

export function optionOf(key: ChipKey, id: string): ChipOption {
  const spec = CHIPS[key];
  return spec.options.find(option => option.id === id) ?? spec.options.find(option => option.id === spec.ordinary)!;
}

/** Whether a chip is in a state other than its ordinary one (the icon is highlighted then). */
export function isChanged(key: ChipKey, id: string): boolean {
  return id !== CHIPS[key].ordinary;
}

/** "Thinking: Swift": the icon's accessible name and tooltip. */
export function chipLabel(key: ChipKey, id: string): string {
  return `${CHIPS[key].title}: ${optionOf(key, id).name}`;
}

// The page keeps these as a boolean and a string; the chips speak in ids. One place maps both ways.
export const modeId = (planMode: boolean): string => (planMode ? 'plan' : 'agent');
export const modeValue = (id: string): boolean => id === 'plan';
export const thinkingId = (deep: boolean): string => (deep ? 'deep' : 'swift');
export const thinkingValue = (id: string): boolean => id === 'deep';
/** The server's two modes are "strict" (answers from the knowledge base) and "open". */
export const answersId = (ragMode: string): string => (ragMode === 'open' ? 'open' : 'grounded');
export const answersValue = (id: string): string => (id === 'open' ? 'open' : 'strict');
