// Run with: node --experimental-test-module-mocks --test src/components/streamFade.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The word-by-word fade of streamed reply text (components/streamFade.ts), run on trees the real Markdown parser makes, and how Chat.tsx and the stylesheet use it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import { FADE_SKIPS, WORD_CLASS, fadeWords, rehypeStreamWords, wordsOf } from './streamFade.ts';

const parse = markdown => unified().use(remarkParse).use(remarkGfm).use(remarkRehype).runSync(unified().use(remarkParse).use(remarkGfm).parse(markdown));
const faded = markdown => { const tree = parse(markdown); rehypeStreamWords()(tree); return tree; };
const textOf = node => (node.type === 'text' ? node.value : (node.children ?? []).map(textOf).join(''));
const find = (node, tagName, out = []) => { if (node.tagName === tagName) out.push(node); (node.children ?? []).forEach(child => find(child, tagName, out)); return out; };
const words = node => find(node, 'span').filter(span => (span.properties?.className ?? []).includes(WORD_CLASS));

test('a text node becomes one span per word with the spaces left as plain text between them, and reads exactly as before', () => {
  const out = wordsOf('Hello  big\nworld');
  assert.deepEqual(out.map(n => n.type), ['element', 'text', 'element', 'text', 'element']);
  assert.deepEqual(out.filter(n => n.type === 'text').map(n => n.value), ['  ', '\n']);
  assert.ok(out.filter(n => n.type === 'element').every(n => n.tagName === 'span' && n.properties.className[0] === 'stream-word'));
  assert.equal(out.map(textOf).join(''), 'Hello  big\nworld');
  // leading and trailing space survive too
  assert.equal(wordsOf(' hi ').map(textOf).join(''), ' hi ');
});

test('whitespace on its own (the newlines between blocks) is left as the one text node it was, and empty text stays too', () => {
  assert.deepEqual(wordsOf('\n'), [{ type: 'text', value: '\n' }]);
  assert.deepEqual(wordsOf('   '), [{ type: 'text', value: '   ' }]);
  assert.deepEqual(wordsOf(''), [{ type: 'text', value: '' }]);
});

test('on a real parsed reply every word of ordinary text, bold text and list items is a fading span, and the reply still says the same thing', () => {
  const markdown = 'Hello **big world**, here is a list:\n\n- first item\n- second item\n\n| a | b |\n| - | - |\n| one two | three |\n';
  const plain = parse(markdown);
  const tree = faded(markdown);
  assert.equal(textOf(tree), textOf(plain), 'no character added, lost or moved');
  const textNodes = (node, out = []) => { if (node.type === 'text') out.push(node.value); (node.children ?? []).forEach(child => textNodes(child, out)); return out; };
  const spokenWords = textNodes(plain).flatMap(value => value.split(/\s+/).filter(Boolean));          // per text node: the comma after the bold text is a text node of its own
  assert.deepEqual(words(tree).map(textOf), spokenWords, 'one span for each word, in order');
  assert.ok(spokenWords.includes(',') && spokenWords.includes('world'));
  // bold text is still bold, and its words are inside the strong element
  assert.deepEqual(words(find(tree, 'strong')[0]).map(textOf), ['big', 'world']);
  assert.equal(find(tree, 'li').length, 2);
});

test('a fenced code block and a link are left exactly as they were: their renderers read them as plain strings', () => {
  assert.deepEqual([...FADE_SKIPS].sort(), ['a', 'pre']);
  const markdown = 'Run this now:\n\n```js\nlet total = 1 + 2;\n```\n\nand read [the full guide](https://example.com/guide) too.';
  const plain = parse(markdown);
  const tree = faded(markdown);
  assert.deepEqual(find(tree, 'pre'), find(plain, 'pre'), 'the code block is untouched');
  assert.deepEqual(find(tree, 'a'), find(plain, 'a'), 'so is the link');
  assert.equal(words(find(tree, 'a')[0]).length, 0);
  assert.equal(words(find(tree, 'pre')[0]).length, 0);
  // but the words around them still fade, and nothing changed in what the reply says
  assert.deepEqual(words(tree).map(textOf), ['Run', 'this', 'now:', 'and', 'read', 'too.']);
  assert.equal(textOf(tree), textOf(plain));
});

test('as a reply grows the words already there come out identical and in the same places, so only the new words are new elements', () => {
  const parts = ['Hello wor', 'Hello world and th', 'Hello world and the rest of it'];
  const trees = parts.map(faded);
  // the word still being written is the same element position each time (it changes text, it is not a new element)
  const paragraph = tree => find(tree, 'p')[0].children.filter(n => n.type === 'element');
  const [a, b, c] = trees.map(paragraph);
  assert.equal(a.length, 2);
  assert.equal(b.length, 4);
  assert.equal(c.length, 7);
  assert.deepEqual(b.slice(0, 1), a.slice(0, 1), 'a finished word does not change');
  assert.deepEqual(c.slice(0, 3), b.slice(0, 3));
  assert.equal(textOf(a[1]), 'wor');
  assert.equal(textOf(b[1]), 'world');
});

test('a tree with nothing to fade comes back unchanged, and the plugin can be used with ReactMarkdown (a function that returns the transformer)', () => {
  const leaf = { type: 'element', tagName: 'hr', properties: {}, children: [] };
  const before = JSON.stringify(leaf);
  fadeWords(leaf);
  assert.equal(JSON.stringify(leaf), before);
  assert.equal(typeof rehypeStreamWords(), 'function');
});

// ---- how Chat.tsx and the stylesheet use it (read as text, the same way the other component tests pin the page) ----

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const chat = read('../pages/Chat.tsx');
const css = read('./__styles__/AppShell.css');

test('the reply fades its words only while it is still arriving, on every token, and stops however the turn ends', () => {
  assert.match(chat, /import \{ rehypeStreamWords \} from '\.\.\/components\/streamFade';/);
  assert.match(chat, /const STREAM_REHYPE = \[rehypeStreamWords\];/);
  assert.match(chat, /rehypePlugins=\{msg\.streaming \? STREAM_REHYPE : undefined\}/);
  assert.equal((chat.match(/rehypePlugins=/g) ?? []).length, 1);
  // a token marks the reply as arriving; the final text and the end of the turn (answered, failed or stopped: the `finally`) clear it
  assert.match(chat, /streaming: true,\s+text: \(updated\[lastIndex\]\.text \|\| ''\) \+ payload\.text/);
  assert.match(chat, /\{ \.\.\.updated\[lastIndex\], streaming: false, text: payload\.text \}/);
  assert.match(chat, /setLoading\(false\);\s+\/\/ However the turn ended[^\n]*\n\s+setMessages\(prev => \(prev\.some\(m => m\.streaming\) \? prev\.map\(m => \(m\.streaming \? \{ \.\.\.m, streaming: false \} : m\)\) : prev\)\);/);
  // the first idea (fading the whole container once) is gone
  assert.doesNotMatch(chat, /message-text-fade/);
  assert.doesNotMatch(css, /message-text-fade/);
  assert.match(chat, /<div className="message-text">/);
});

test('the stylesheet plays the fade once per word, holds nothing afterwards, honours reduced motion, and its comment sits above its own rule', () => {
  assert.match(css, /\.stream-word \{ animation: stream-word-in 0\.3s ease-out backwards; \}/);
  assert.match(css, /@keyframes stream-word-in \{ from \{ opacity: 0; \} to \{ opacity: 1; \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.stream-word \{ animation: none; \} \}/);
  const phones = css.indexOf('/* Phones: the gutters stacked');
  assert.equal(css.slice(phones).split('\n')[1].startsWith('@media (max-width: 768px) {'), true, 'the Phones comment is directly above the media query it describes');
});
