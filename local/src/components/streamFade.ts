// The fade-in of streamed reply text, a word at a time (the way Copilot's replies arrive). A rehype plugin for ReactMarkdown, used only while a reply is still streaming (pages/Chat.tsx).
//
// WHY WORDS, NOT THE WHOLE REPLY: a CSS animation plays once, when its element appears. Fading the reply's container fades it once at the first token and every later token just pops in. Here every word is its own
// <span class="stream-word"> that fades in when IT first appears. ReactMarkdown gives element children the key `tagName-count` (span-0, span-1 ...), so when the text grows the words already on screen keep their
// DOM nodes (no replay) and only the words that have just arrived are new nodes, which is what plays the animation. A word that is still arriving ("Hel" then "Hello") is the same span, so it does not replay either.
// Known limit: when the Markdown structure changes mid-stream (a `**` closing, a list starting) the words inside the changed element are new nodes and fade in again for a moment.
//
// What is left alone: a fenced code block (`pre`: its renderer reads the code as one plain string) and a link (`a`: its renderer reads its label from string children). Whitespace stays plain text between the
// spans, so the reply reads and wraps exactly as it does without them.

/** The parts of a hast node this needs (the tree ReactMarkdown hands to a rehype plugin). */
export interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

/** Elements whose text must stay one plain string. */
export const FADE_SKIPS: ReadonlySet<string> = new Set(['pre', 'a']);
export const WORD_CLASS = 'stream-word';

/** One text node as a list of nodes: every run of non-space characters in its own span, every run of spaces left as plain text. A node with no words (only whitespace) comes back as it was. */
export function wordsOf(value: string): HastNode[] {
  const parts = value.split(/(\s+)/).filter(part => part !== '');
  if (!parts.some(part => !/^\s+$/.test(part))) return [{ type: 'text', value }];
  return parts.map(part => (/^\s+$/.test(part)
    ? { type: 'text', value: part }
    : { type: 'element', tagName: 'span', properties: { className: [WORD_CLASS] }, children: [{ type: 'text', value: part }] }));
}

/** Walks the tree in place, replacing the text of every element except the skipped ones. */
export function fadeWords(node: HastNode): void {
  if (!node.children) return;
  if (node.type === 'element' && node.tagName && FADE_SKIPS.has(node.tagName)) return;
  const next: HastNode[] = [];
  for (const child of node.children) {
    if (child.type === 'text' && typeof child.value === 'string') next.push(...wordsOf(child.value));
    else {
      fadeWords(child);
      next.push(child);
    }
  }
  node.children = next;
}

/** The rehype plugin: pass it to ReactMarkdown's `rehypePlugins` only while the reply is streaming. */
export function rehypeStreamWords() {
  return (tree: HastNode) => fadeWords(tree);
}
