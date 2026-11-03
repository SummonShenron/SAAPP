// The four settings that sit as icons under the message box (components/ComposerChips.tsx): the code model, the mode, how hard to think, and where answers come from. Each is one icon that shows which
// of its two states is on, and opens a two-choice popup. Kept apart from React so the words and the mapping to the page's settings can be tested. These replaced the "..." menu's toggles.

export type ChipKey = 'model' | 'mode' | 'thinking' | 'answers';

/** `hint` says what a choice does in full (the tooltip); `short` is the line under its name where there is little room (the two-column model popup in the 272px column, and the phone strip). */
export interface ChipOption { id: string; name: string; hint: string; short?: string; }

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
      { id: 'gemini', name: 'Gemini', hint: 'The default.', short: 'The default.' },
      { id: 'claude', name: 'Claude', hint: 'Claude Haiku does the reasoning and code-writing steps. The reply is still written by your Base model.', short: 'Haiku does the code steps.' },
      // Offered only where the server has Sonnet's price (ChipControl.hide): it costs about 20 times Haiku per step, so the hint says so.
      { id: 'sonnet', name: 'Claude Sonnet', hint: 'The strongest for hard code changes, and about 20 times the cost of Claude per step. The reply is still written by your Base model.', short: 'Strongest, about 20x the cost.' },
      // Offered only where the server has an OpenAI key and the model's price (ChipControl.hide).
      { id: 'gpt', name: 'GPT-5.4 mini', hint: 'GPT-5.4 mini (OpenAI) does the reasoning and code-writing steps, thinking a little before each one. A middle tier between Claude and Claude Sonnet. The reply is still written by your Base model.', short: 'Thinks before each code step.' },
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

/**
 * The BASE model: which model writes the reply the person reads (the Code model above does only the reasoning steps of code work). It sits in the same popup as the Code model, as its first column
 * (components/ComposerChips.tsx ModelChoices), and is offered only where the server can run GPT. Inside a safety context the server always uses Gemini, whatever this says.
 */
export const BASE_MODEL: ChipSpec = {
  key: 'model', title: 'Base model', ordinary: 'gemini',
  options: [
    { id: 'gemini', name: 'Gemini', hint: 'The default. Gemini writes the replies.', short: 'The default.' },
    { id: 'gpt', name: 'GPT-5.4 mini', hint: 'GPT-5.4 mini (OpenAI) writes the replies. New, and still being tested. When someone is going through something hard, Sonic uses Gemini whatever this says.', short: 'New. Still being tested.' },
  ],
};

export function specOption(spec: ChipSpec, id: string): ChipOption {
  return spec.options.find(option => option.id === id) ?? spec.options.find(option => option.id === spec.ordinary)!;
}

export function optionOf(key: ChipKey, id: string): ChipOption {
  return specOption(CHIPS[key], id);
}

/**
 * The model chip stands for two settings, the Base model and the Code model; either may be missing (null) where the server does not offer a choice for it. Its icon shows the first one that is not the
 * ordinary model (Base first), else Gemini.
 */
export function modelChipValue(base: string | null, code: string | null): string {
  if (base && base !== BASE_MODEL.ordinary) return base;
  if (code && code !== CHIPS.model.ordinary) return code;
  return CHIPS.model.ordinary;
}

/** Whether the model chip is highlighted: either of its two settings is not the ordinary one. */
export function modelsChanged(base: string | null, code: string | null): boolean {
  return (base !== null && base !== BASE_MODEL.ordinary) || (code !== null && code !== CHIPS.model.ordinary);
}

/** "Models: Base GPT-5.4 mini, Code Claude": the chip's accessible name and tooltip; a setting that is not offered is left out. */
export function modelsLabel(base: string | null, code: string | null): string {
  const parts: string[] = [];
  if (base !== null) parts.push(`Base ${specOption(BASE_MODEL, base).name}`);
  if (code !== null) parts.push(`Code ${specOption(CHIPS.model, code).name}`);
  return `Models: ${parts.join(', ')}`;
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
