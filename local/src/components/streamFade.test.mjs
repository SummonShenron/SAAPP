// Run with: node --experimental-test-module-mocks --test src/components/streamFade.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
// The word-by-word fade of streamed reply text (components/streamFade.ts), run on trees the real Markdown parser makes, and how Chat.tsx and the stylesheet use it.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import { FADE_SKIPS, SEEN_CLASS, WORD_CLASS, fadeWords, makeSeenWordsPlugin, rehypeStreamWords, wordsOf } from './streamFade.ts';

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

test('the reply is shown a word at a time through a pacer, fades only while it is still being shown, and stops however the turn ends', () => {
  const markdown = read('./MessageMarkdown.tsx');
  assert.match(markdown, /import \{ makeSeenWordsPlugin \} from '\.\/streamFade';/);
  assert.match(markdown, /rehypePlugins = useMemo\(\s*\(\) => \(streaming \? \[makeSeenWordsPlugin\(/);
  assert.equal((markdown.match(/rehypePlugins=/g) ?? []).length, 1);
  assert.match(chat, /<MessageMarkdown content=\{cleanContent\} streaming=\{!!msg\.streaming\}/);
  // the page no longer builds ReactMarkdown itself, and no longer owns the fade list
  assert.doesNotMatch(chat, /<ReactMarkdown|STREAM_REHYPE|rehypeStreamWords/);
  // a token goes to the pacer, not straight into the message: the words come out a few at a time (components/streamPacer.ts)
  assert.match(chat, /pacer\.push\(payload\.text\);/);
  assert.doesNotMatch(chat, /\(updated\[lastIndex\]\.text \|\| ''\) \+ payload\.text/, 'no token is appended to the message the moment it arrives');
  assert.match(chat, /new StreamPacer\(words => setMessages\(prev => prev\.map\(m => \(m\.id === aiMessageId \? \{ \.\.\.m, streaming: true, text: \(m\.text \|\| ''\) \+ words \} : m\)\)\)\)/);
  // the final text replaces the shown one only after the words still waiting were shown; the end of the turn clears the flag after the drain; a Stop or an error shows what had arrived first
  assert.match(chat, /const settle = \(\) => setMessages\(prev => prev\.map\(m => \(m\.id === aiMessageId \? \{ \.\.\.m, streaming: false, text: finalText \} : m\)\)\);/);
  assert.match(chat, /pacer\.finish\(settle\);/);
  // a reply that arrives whole (no tokens came) is pushed through the pacer and revealed, never set in one go; one that already streamed keeps the quick finish
  assert.match(chat, /if \(!streamedText && typeof finalText === 'string' && finalText\.trim\(\)\) \{\s+[^\n]*\n\s+streamedText = true;\s+pacer\.push\(finalText\);\s+pacer\.drain\(settle\);/);
  assert.match(chat, /let streamedText = false;/);
  assert.match(chat, /streamedText = true;\s+pacer\.push\(payload\.text\);/);
  assert.match(chat, /pacer\.reset\(\);[^\n]*\n\s+streamedText = false;/, 'a rewrite starts over: the next reply may arrive whole');
  assert.match(chat, /setLoading\(false\);\s+\/\/ However the turn ended[^\n]*\n[^\n]*\n\s+pacer\.finish\(\(\) => setMessages\(prev => \(prev\.some\(m => m\.streaming\) \? prev\.map\(m => \(m\.streaming \? \{ \.\.\.m, streaming: false \} : m\)\) : prev\)\)\);/);
  assert.match(chat, /pacer\.flush\(\);[^\n]*\n\s+if \(err instanceof DOMException && err\.name === 'AbortError'\) \{/);
  assert.match(chat, /pacer\.reset\(\);[^\n]*\n\s+streamedText = false;\s+setMessages\(prev => \{[\s\S]{0,500}text: ''/, 'a rewrite drops the words of the attempt that was waiting');
  assert.match(chat, /useEffect\(\(\) => \(\) => pacerRef\.current\?\.dispose\(\), \[\]\);/);
  // the first idea (fading the whole container once) is gone
  assert.doesNotMatch(chat, /message-text-fade/);
  assert.doesNotMatch(css, /message-text-fade/);
  assert.match(chat, /<div className="message-text">/);
});

test('words already on screen are numbered and not faded a second time when the Markdown changes shape and they are re-created', () => {
  const classOf = node => node.properties.className;
  const words = tree => { const out = []; const walk = n => { if (n.type === 'element' && n.tagName === 'span' && classOf(n)?.[0] === 'stream-word') out.push(n); (n.children ?? []).forEach(walk); }; walk(tree); return out; };
  const run = (markdown, seen) => { const tree = parse(markdown); makeSeenWordsPlugin({ seen: () => seen })()(tree); return words(tree); };
  const fresh = run('one two three four', 0);
  assert.deepEqual(fresh.map(classOf), [['stream-word'], ['stream-word'], ['stream-word'], ['stream-word']]);
  const partly = run('one two three four', 2);
  assert.deepEqual(partly.map(classOf), [['stream-word', SEEN_CLASS], ['stream-word', SEEN_CLASS], ['stream-word'], ['stream-word']], 'the first two were on screen: only the new ones animate');
  assert.ok(run('one two', 5).every(n => classOf(n).includes(SEEN_CLASS)), 'a re-created word that was already shown stays still');
  // the numbering runs in reading order across elements, so a closing `**` that re-creates the words inside it does not change which words count as seen
  const after = run('hello **bold** and more', 3);
  assert.deepEqual(after.map(n => classOf(n).includes(SEEN_CLASS)), [true, true, true, false]);       // hello, bold, and: on screen already; more: new
});

test('the plugin reports how many words the render produced, and reads how many were seen when it runs, not when it was made', () => {
  let seen = 0;
  let counted = -1;
  const attacher = makeSeenWordsPlugin({ seen: () => seen, counted: total => { counted = total; } });
  const tree = parse('a b c');
  attacher()(tree);
  assert.equal(counted, 3);
  assert.ok(!JSON.stringify(tree).includes(SEEN_CLASS));
  seen = 3;
  const again = parse('a b c d');
  attacher()(again);
  assert.equal(counted, 4);
  assert.ok(JSON.stringify(again).includes(SEEN_CLASS), 'the new count of seen words is read at run time');
  assert.equal(typeof attacher, 'function');
});

test('the stylesheet plays the fade once per word, holds nothing afterwards, honours reduced motion, and its comment sits above its own rule', () => {
  assert.match(css, /\.stream-word \{ animation: stream-word-in 0\.3s ease-out backwards; \}/);
  assert.match(css, /@keyframes stream-word-in \{ from \{ opacity: 0; \} to \{ opacity: 1; \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{ \.stream-word \{ animation: none; \} \}/);
  assert.match(css, /\.stream-word-seen \{ animation: none; \}/, 'a word that was already on screen does not fade again');
  const phones = css.indexOf('/* Phones: the gutters stacked');
  assert.equal(css.slice(phones).split('\n')[1].startsWith('@media (max-width: 768px) {'), true, 'the Phones comment is directly above the media query it describes');
});
