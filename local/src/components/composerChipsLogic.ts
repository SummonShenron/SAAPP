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
    key: 'model', title: 'Tool / Planner model', ordinary: 'gemini',
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
 * The BASE model: which model writes the reply the person reads (the Tool model above does only the reasoning steps of code work). It sits in the same popup as the Tool model, as its first column
 * (components/ComposerChips.tsx ModelChoices), and is offered only where the server can run GPT. Inside a safety context the server always uses Gemini, whatever this says.
 */
export const BASE_MODEL: ChipSpec = {
  key: 'model', title: 'Base model', ordinary: 'gemini',
  options: [
    { id: 'gemini', name: 'Gemini', hint: 'The default. Gemini writes the replies.', short: 'The default.' },
    { id: 'gpt', name: 'GPT-5.4 mini', hint: 'GPT-5.4 mini (OpenAI) writes the replies. New, and still being tested. When someone is going through something hard, Sonic uses Gemini whatever this says.', short: 'New. Still being tested.' },
  ],
};

/**
 * The EXECUTE model: which model carries out an approved plan (Plan mode); the Tool model writes the plan. It is the third column of the same popup (components/ComposerChips.tsx ModelChoices). "Same as Tool"
 * (the default) keeps one model for both. A cheaper model here is the point: the plan already holds the files, the steps and the checks, and a cheaper model carrying it out was tried once (Haiku carried out a
 * six-step plan for $0.03 where Sonnet had used $1.30 for a larger one: not the same plan, so no ratio is claimed; docs/model-eval-plan.md). Choosing a provider sends the code of those steps there, which each hint says.
 */
export const EXECUTE_MODEL: ChipSpec = {
  key: 'model', title: 'Plan executer model', ordinary: 'same',
  options: [
    { id: 'same', name: 'Same as Tool', hint: 'Your Tool model also carries out the plans it writes. The default.', short: 'Your Tool model. The default.' },
    { id: 'gemini', name: 'Gemini', hint: 'Gemini (Google) carries out an approved plan.', short: 'Gemini does the plan steps.' },
    { id: 'claude', name: 'Claude', hint: 'Claude Haiku (Anthropic) carries out an approved plan that your Tool model wrote: much cheaper per step. Your code goes to Anthropic for those steps.', short: 'Haiku does the plan steps.' },
    { id: 'sonnet', name: 'Claude Sonnet', hint: 'Claude Sonnet (Anthropic) carries out an approved plan: the strongest, and about 20 times the cost of Claude per step. Your code goes to Anthropic for those steps.', short: 'Strongest, about 20x the cost.' },
    { id: 'gpt', name: 'GPT-5.4 mini', hint: 'GPT-5.4 mini (OpenAI) carries out an approved plan. Your code goes to OpenAI for those steps.', short: 'GPT does the plan steps.' },
  ],
};

export function specOption(spec: ChipSpec, id: string): ChipOption {
  return spec.options.find(option => option.id === id) ?? spec.options.find(option => option.id === spec.ordinary)!;
}

export function optionOf(key: ChipKey, id: string): ChipOption {
  return specOption(CHIPS[key], id);
}

/** The model that really carries out a plan: the Execute model, or the Tool model when the Execute model is "same" or is not offered. */
export function resolvedExecute(code: string | null, execute: string | null): string | null {
  return execute && execute !== EXECUTE_MODEL.ordinary ? execute : code;
}

/**
 * The model chip stands for three settings, the Base model, the Tool model and the Execute model; any may be missing (null) where the server does not offer a choice for it. Its icon value is the first one that
 * is not the ordinary model (Base, then Code, then Execute), else Gemini.
 */
export function modelChipValue(base: string | null, code: string | null, execute: string | null = null): string {
  if (base && base !== BASE_MODEL.ordinary) return base;
  if (code && code !== CHIPS.model.ordinary) return code;
  if (execute && execute !== EXECUTE_MODEL.ordinary) return execute;
  return CHIPS.model.ordinary;
}

/** Whether the model chip is highlighted: any of its settings is not the ordinary one. */
export function modelsChanged(base: string | null, code: string | null, execute: string | null = null): boolean {
  return (base !== null && base !== BASE_MODEL.ordinary) || (code !== null && code !== CHIPS.model.ordinary) || (execute !== null && execute !== EXECUTE_MODEL.ordinary);
}

/** "Models: Base GPT-5.4 mini, Tool Claude, Execute Same as Tool": the chip's accessible name and tooltip; a setting that is not offered is left out. */
export function modelsLabel(base: string | null, code: string | null, execute: string | null = null): string {
  const parts: string[] = [];
  if (base !== null) parts.push(`Base ${specOption(BASE_MODEL, base).name}`);
  if (code !== null) parts.push(`Tool ${specOption(CHIPS.model, code).name}`);
  if (execute !== null) parts.push(`Execute ${specOption(EXECUTE_MODEL, execute).name}`);
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

export type PresetId = 'lite' | 'smart' | 'max';

/**
 * One-press pairings of the Code and the Execute model (2026-11-05), because three separate model settings are not something a person works out at a glance. The Base model (which writes the reply) is not
 * part of a preset: it is a different kind of choice. "Custom" is not a preset but the raw Tool and Execute columns, shown when the pair matches none of these. Each says plainly that Anthropic receives the
 * code, because choosing a Claude model is what sends it there, and that Smart and Max cost more (no dollar figure is claimed beyond one that was measured: a large Sonnet change has cost over a dollar; docs/model-eval-plan.md). Smart only does what it says in Plan mode: the Execute model only carries out an approved plan, so in Agent mode Sonnet does every step.
 */
export interface Preset { id: PresetId; name: string; hint: string; short: string; code: string; execute: string; }

export const PRESETS: Preset[] = [
  {
    id: 'lite', name: 'Lite', code: 'claude', execute: 'claude', short: 'Haiku plans and writes. The cheapest.',
    hint: 'Claude Haiku writes the plan and carries it out. The cheapest. Your code goes to Anthropic for the coding steps.',
  },
  {
    id: 'smart', name: 'Smart', code: 'sonnet', execute: 'claude', short: 'Sonnet plans and checks; Haiku writes.',
    hint: 'Claude Sonnet writes the plan and reads the finished change; Claude Haiku carries out the steps. Costs several times Lite. Best in Plan mode: in Agent mode Sonnet does every step. Your code goes to Anthropic.',
  },
  {
    id: 'max', name: 'Max', code: 'sonnet', execute: 'sonnet', short: 'Sonnet does everything. Costs the most.',
    hint: 'Claude Sonnet plans and carries out every step: the strongest, and about 20 times the cost of Lite per step. A large change has cost over a dollar, which can use up a daily limit. Your code goes to Anthropic.',
  },
];

/** The preset the Tool and Execute settings add up to, or "custom". A null Execute (not offered) or "same" means the Tool model carries out the plan too. */
export function presetOf(code: string | null, execute: string | null): PresetId | 'custom' {
  const carriesOut = resolvedExecute(code, execute);
  return PRESETS.find(preset => preset.code === code && preset.execute === carriesOut)?.id ?? 'custom';
}

/** Whether a preset can be offered here: its Tool model is not hidden, and its Execute model is (or, with no Execute choice offered, is the same model as the Code one). `hide` lists what the server cannot provide. */
export function presetAvailable(preset: Preset, codeHide: string[] = [], executeOffered: boolean, executeHide: string[] = []): boolean {
  if (codeHide.includes(preset.code)) return false;
  return executeOffered ? !executeHide.includes(preset.execute) : preset.execute === preset.code;
}

/** What pressing a preset sets: the Tool model, and the Execute model when there is such a choice (null otherwise: nothing to set). */
export function presetSettings(preset: Preset, executeOffered: boolean): { code: string; execute: string | null } {
  return { code: preset.code, execute: executeOffered ? preset.execute : null };
}
