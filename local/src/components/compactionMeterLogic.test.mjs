// Run with: node --test src/components/compactionMeterLogic.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { FORWARD_MS, RETURN_MS, cleanMeter, didCompact, gaugesSummary, isOverloaded, markerFraction, meterCaption, meterLevel, meterSummary, meterText, meterTitle } from './compactionMeterLogic.ts';

const view = (uncompacted, threshold = 100) => ({ uncompacted, threshold });

test('what the server sends is kept only when it is two real counts, and a zero threshold is refused', () => {
  assert.deepEqual(cleanMeter({ uncompacted: 37, threshold: 100 }), { uncompacted: 37, threshold: 100 });
  assert.deepEqual(cleanMeter({ uncompacted: 3.9, threshold: 100.2 }), { uncompacted: 3, threshold: 100 });
  for (const bad of [null, undefined, 'x', 5, {}, { uncompacted: -1, threshold: 100 }, { uncompacted: 'a', threshold: 100 }, { uncompacted: 5, threshold: 0 }, { uncompacted: NaN, threshold: 100 }, { uncompacted: 5 }]) {
    assert.equal(cleanMeter(bad), null, JSON.stringify(bad));
  }
});

test('the ring is a fraction from 0 to 1 of the way to the threshold, and never leaves that range', () => {
  assert.equal(markerFraction(view(0)), 0);
  assert.equal(markerFraction(view(37)), 0.37);
  assert.equal(markerFraction(view(100)), 1);
  assert.equal(markerFraction(view(250)), 1);                      // past the threshold the line stays at the end
  assert.equal(markerFraction(view(5, 5)), 1);
});

test('the words say how many of how many, and what happens past the threshold', () => {
  assert.equal(meterText(view(37)), '37 of 100 notes');
  assert.equal(meterText(view(0)), '0 of 100 notes');
  assert.equal(meterText(view(1, 1)), '1 of 1 note');
  assert.match(meterText(view(130)), /130 notes: they are folded together on the next note/);
  assert.equal(meterSummary(view(37)), 'Memory 37/100');
  assert.equal(meterSummary(null), '');
});

test('the colour is quiet until the ring is nearly full', () => {
  assert.equal(meterLevel(view(0)), 'ok');
  assert.equal(meterLevel(view(89)), 'ok');
  assert.equal(meterLevel(view(90)), 'notice');
  assert.equal(meterLevel(view(100)), 'notice');
});

test('a count that falls is a compaction; the first reading, an unchanged count and a rising one are not', () => {
  assert.equal(didCompact(view(99), view(12)), true);
  assert.equal(didCompact(view(101), view(0)), true);
  assert.equal(didCompact(null, view(50)), false);
  assert.equal(didCompact(view(50), view(50)), false);
  assert.equal(didCompact(view(50), view(51)), false);
  assert.equal(didCompact(view(50), null), false);
  assert.equal(didCompact(null, null), false);
});

test('the folded card and the phone strip say what there is: the meter, the usage, both or neither', () => {
  assert.equal(gaugesSummary(view(37), { fraction: 0.12 }), 'Memory 37/100 · Usage 12%');
  assert.equal(gaugesSummary(view(37), null), 'Memory 37/100');
  assert.equal(gaugesSummary(null, { fraction: 0.12 }), 'Usage 12%');
  assert.equal(gaugesSummary(null, null), '');
});

test('the tooltip keeps the context figure the ring used to show', () => {
  assert.equal(meterTitle(view(37)), 'Memory notes waiting to be folded together: 37 of 100 notes');
  assert.equal(meterTitle(view(37), 'Context 40%'), 'Memory notes waiting to be folded together: 37 of 100 notes. Context 40%');
});

test('it is overloaded only past the threshold, and the words say so', () => {
  assert.equal(isOverloaded(view(100)), false);                    // at the threshold the ring is full, but nothing has been folded yet
  assert.equal(isOverloaded(view(101)), true);
  assert.equal(meterCaption(view(37)), '37 of 100 notes');
  assert.equal(meterCaption(view(130)), 'Overloaded: folding notes together');
});

test('the ring fills smoothly, traces back to the start more slowly after a compaction, pulses when overloaded, and stands still for reduced motion', () => {
  assert.ok(RETURN_MS > FORWARD_MS * 2);
  const css = readFileSync(new URL('./__styles__/Ring.css', import.meta.url), 'utf8');
  assert.match(css, /\.ring-animated \.ring-arc \{ transition: stroke-dasharray 0\.7s cubic-bezier\(0\.2, 0\.8, 0\.2, 1\); \}/);
  assert.match(css, /\.ring-returning \.ring-arc \{ transition: stroke-dasharray 1\.8s cubic-bezier\(0\.45, 0, 0\.2, 1\); \}/);       // no overshoot: a dash length below 0 is not valid
  assert.match(css, /\.ring-overloaded \.ring-arc \{ animation: ring-pulse/);
  assert.match(css, /\.ring-caption-overloaded \{ color: var\(--accent-danger/);
  assert.match(css, /prefers-reduced-motion: reduce/);
});

test('the ring is the same drawing for both gauges, at the sizes the usage ring and its small form always were', () => {
  const ring = readFileSync(new URL('./Ring.tsx', import.meta.url), 'utf8');
  assert.match(ring, /size = 44, stroke = 4, percent = true/);
  assert.match(ring, /strokeLinecap="round"/);
  assert.match(ring, /transform=\{`rotate\(-90 \$\{size \/ 2\} \$\{size \/ 2\}\)`\}/);                           // filled clockwise from the top, as before
  assert.match(ring, /\$\{animated \? ' ring-animated' : ''\}\$\{returning \? ' ring-returning' : ''\}\$\{overloaded \? ' ring-overloaded' : ''\}/);
  const source = readFileSync(new URL('./CompactionMeter.tsx', import.meta.url), 'utf8');
  assert.match(source, /size=\{14\} stroke=\{3\} percent=\{false\} trackClass="rail-ring-track" animated/);              // the strip's size
  assert.match(source, /const level = overloaded \? 'blocked' : meterLevel\(view\);/);
  assert.match(source, /const compacted = didCompact\(previous\.current, view\);/);
  assert.match(source, /setReturning\(true\);/);
  assert.match(source, /window\.setTimeout\(\(\) => setReturning\(false\), RETURN_MS \+ 200\)/);
});

test('the meter replaced the context ring in the column and the phone strip, keeps the usage ring as it was, and keeps the context figure as its tooltip', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  const rail = readFileSync(new URL('./SessionRail.tsx', import.meta.url), 'utf8');
  // the memory ring sits beside its words in the same layout the usage ring uses (.sc-gauge: the picture, then the name and the text)
  assert.match(column, /<div className="sc-gauge">\s*<CompactionMeter view=\{compaction\} contextText=\{contextView\?\.text\} \/>\s*<div className="sc-gauge-body">\s*\{onSwitchMeter\s*\?[^]*?: <span className="sc-gauge-name">\{meterName\(compaction\)\}<\/span>\}/);
  assert.match(column, /meterCaption\(compaction\)/);
  assert.doesNotMatch(column, /name="Context"/);
  assert.match(column, /\{usage && <Gauge name="Usage"/);
  assert.match(column, /<Ring fraction=\{fraction\} level=\{level\} label=\{`\$\{name\}: \$\{text\}`\} trackClass="sc-gauge-track" numberClass="sc-gauge-number" \/>/);
  assert.match(rail, /<CompactionMeter view=\{compaction\} contextText=\{contextView\?\.text\} mini \/>/);
  assert.match(rail, /: <span className="sc-gauge-name">\{meterName\(compaction\)\}<\/span>\}/);
  assert.match(rail, /\{usage && <MiniRing fraction=\{usage\.fraction\} level=\{usage\.level\} \/>\}/);
  assert.match(rail, /<Ring fraction=\{fraction\} level=\{level\} size=\{14\} stroke=\{3\} percent=\{false\} trackClass="rail-ring-track" \/>/);
  assert.doesNotMatch(rail, /name="Context"|MiniRing fraction=\{contextView/);
  // no speedometer is left
  assert.doesNotMatch(column + rail, /Dial|USAGE_ZONES|usageOverloaded/);
});


test('the page reads the meter on load, after each reply and once more a few seconds later, with no polling loop, and never for a guest', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /if \(!principal \|\| isEmbedded \|\| isGuestPrincipal\(principal\)\) return;/);
  assert.match(chat, /api\.getCompactionMeter\(\)\.then\(raw => setCompactionMeter\(cleanMeter\(raw\)\)\)/);
  assert.match(chat, /window\.setTimeout\(refreshCompactionMeter, COMPACTION_RECHECK_MS\)/);
  assert.doesNotMatch(chat, /setInterval\(refreshCompactionMeter/);
  assert.equal((chat.match(/compaction=\{shownMeter\}/g) || []).length, 2);                      // the column and the phone strip: the chosen reading
  assert.equal((chat.match(/onSwitchMeter=\{switchMeter\}/g) || []).length, 2);
  assert.match(chat, /if \(payload\.event === 'context'\)/);                                       // the context event is still handled: it feeds the tooltip
});

// ---- the one ring, switched between Sonic's memory and this conversation --------------------------------------------------------------------------------------------

import { canSwitchMeter, conversationMeter, meterName, otherMeterMode, pickMeter, readMeterMode, writeMeterMode } from './compactionMeterLogic.ts';

const convo = (uncompacted, threshold = 14) => ({ uncompacted, threshold, kind: 'conversation' });
const memory = (uncompacted, threshold = 100) => ({ uncompacted, threshold });

test('the conversation reading comes from the summary endpoint and is refused when it is not two real counts', () => {
  assert.deepEqual(conversationMeter({ unfolded: 9, fold_at: 14, keep: 6, sections: [] }), { ...convo(9), foldable: true });
  assert.equal(conversationMeter({ unfolded: 7, fold_at: 14, keep: 6 }).foldable, false, 'one message beyond the newest six is not worth a fold');
  assert.equal(conversationMeter({ unfolded: 8, fold_at: 14, keep: 6 }).foldable, true);
  assert.equal(conversationMeter({ unfolded: 300, fold_at: 14 }).foldable, false, 'no keep count from the server: no button');
  assert.equal(conversationMeter({ unfolded: 9, fold_at: 0 }), null);
  assert.equal(conversationMeter({ unfolded: -1, fold_at: 14 }), null);
  assert.equal(conversationMeter({ fold_at: 14 }), null);
  assert.equal(conversationMeter(null), null);
});

test('the words say notes for memory and messages for the conversation, and name the ring', () => {
  assert.equal(meterText(convo(9)), '9 of 14 messages');
  assert.equal(meterText(convo(16)), '16 messages: they are folded into the summary after the next reply');
  assert.equal(meterCaption(convo(16)), 'Overloaded: folding into the summary');
  assert.equal(meterCaption(memory(120)), 'Overloaded: folding notes together');
  assert.equal(meterText(memory(37)), '37 of 100 notes');
  assert.equal(meterName(convo(1)), 'Chat');
  assert.equal(meterName(memory(1)), 'Memory');
  assert.equal(meterSummary(convo(9)), 'Chat 9/14');
  assert.equal(meterSummary(memory(37)), 'Memory 37/100');
  assert.match(meterTitle(convo(9)), /^Messages waiting to be folded into this conversation's summary: 9 of 14 messages/);
  assert.match(meterTitle(memory(37)), /^Memory notes waiting to be folded together/);
  assert.equal(markerFraction(convo(7)), 0.5);
});

test('a fold traces the conversation ring back, but switching readings never looks like a fold', () => {
  assert.equal(didCompact(convo(14), convo(7)), true);
  assert.equal(didCompact(convo(7), convo(8)), false);
  assert.equal(didCompact(memory(100), convo(7)), false, 'a hundred notes to seven messages is a different ring, not a compaction');
  assert.equal(didCompact(convo(7), memory(3)), false);
  assert.equal(didCompact(memory(100), memory(2)), true);
});

test('the chosen reading is drawn, falling back to whichever exists, and the ring switches only when both do', () => {
  assert.equal(pickMeter('memory', memory(3), convo(5)).kind, undefined);
  assert.equal(pickMeter('conversation', memory(3), convo(5)).kind, 'conversation');
  assert.deepEqual(pickMeter('conversation', memory(3), null), memory(3));
  assert.deepEqual(pickMeter('memory', null, convo(5)), convo(5));
  assert.equal(pickMeter('memory', null, null), null);
  assert.equal(canSwitchMeter(memory(3), convo(5)), true);
  assert.equal(canSwitchMeter(memory(3), null), false);
  assert.equal(canSwitchMeter(null, convo(5)), false);
  assert.equal(otherMeterMode('memory'), 'conversation');
  assert.equal(otherMeterMode('conversation'), 'memory');
});

test('the choice is remembered in the browser if it can be, and a blocked or missing store just means memory', () => {
  const store = new Map();
  const storage = { getItem: key => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  assert.equal(readMeterMode(storage), 'memory');
  writeMeterMode('conversation', storage);
  assert.equal(readMeterMode(storage), 'conversation');
  writeMeterMode('memory', storage);
  assert.equal(readMeterMode(storage), 'memory');
  const blocked = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.equal(readMeterMode(blocked), 'memory');
  assert.doesNotThrow(() => writeMeterMode('conversation', blocked));
  assert.equal(readMeterMode(null), 'memory');
});

test('the page reads the conversation count with the memory count, ignores an answer for another conversation, and the choice is a per-viewer convenience', () => {
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  assert.match(chat, /api\.getConversationSummary\(sessionId\)\.then\(raw => \{ if \(sessionIdRef\.current === sessionId\) setConversationMeterView\(conversationMeter\(raw\)\); \}\)/);
  assert.match(chat, /useEffect\(\(\) => \{ setConversationMeterView\(null\); \}, \[sessionId\]\);/);
  assert.match(chat, /const \[meterMode, setMeterMode\] = useState<MeterKind>\(\(\) => readMeterMode\(\)\);/);
  assert.match(chat, /writeMeterMode\(next\);/);
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  assert.match(column, /className="sc-gauge-name sc-gauge-switch" onClick=\{onSwitchMeter\}/);
});

test('Fold now sits beside the Chat reading of the ring, in the column and the phone strip, and only when a fold has something to do', () => {
  const column = readFileSync(new URL('./SessionColumn.tsx', import.meta.url), 'utf8');
  const rail = readFileSync(new URL('./SessionRail.tsx', import.meta.url), 'utf8');
  const chat = readFileSync(new URL('../pages/Chat.tsx', import.meta.url), 'utf8');
  for (const source of [column, rail]) assert.match(source, /\{onFold && compaction\.foldable && <button type="button" className="sc-gauge-fold" disabled=\{folding\} onClick=\{onFold\}>\{folding \? 'Folding…' : 'Fold now'\}<\/button>\}/);
  assert.match(chat, /const foldFromRing = shownMeter\?\.kind === 'conversation' && shownMeter\.foldable \? \(\) => \{/);
  assert.match(chat, /api\.compactConversationSummary\(sessionId\)/);
  assert.equal((chat.match(/onFold=\{foldFromRing\}/g) || []).length, 2);
});
