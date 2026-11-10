import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const page = readFileSync(new URL('./SonicHealth.tsx', import.meta.url), 'utf8');

test('Sonic Health loads the learned pending count', () => {
  assert.match(page, /const \[learnedWaiting, setLearnedWaiting\] = useState<number>\(0\);/);
  assert.match(page, /api\.getLearnedKnowledge\(\)\.then\(\(data\) => \{ if \(!cancelled\) setLearnedWaiting\(data\.pending\.length\); \}\)/);
});

test('the learned tab gets a badge only when pages are waiting', () => {
  assert.match(page, /if \(learnedWaiting > 0\) badges\.learned = \{ text: String\(learnedWaiting\), tone: 'info' \};/);
});
