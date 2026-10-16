// Run with: node --test src/utils/imageShrink.test.mjs   (from local/; Node 22.18+ runs the TypeScript module directly)
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { HEIC_NOTICE, isUnreadableHeic } from './imageShrink.ts';

test('a HEIC or HEIF photo is recognised by its type or its name, in any case', () => {
  assert.equal(isUnreadableHeic({ name: 'IMG_0042.HEIC', type: '' }), true);
  assert.equal(isUnreadableHeic({ name: 'photo', type: 'image/heic' }), true);
  assert.equal(isUnreadableHeic({ name: 'photo.heif', type: 'image/heif' }), true);
});

test('ordinary photos, screenshots and documents are not mistaken for HEIC', () => {
  for (const file of [
    { name: 'letter.jpg', type: 'image/jpeg' },
    { name: 'screenshot.png', type: 'image/png' },
    { name: 'bill.pdf', type: 'application/pdf' },
    { name: 'heic-notes.txt', type: 'text/plain' },
  ]) assert.equal(isUnreadableHeic(file), false, file.name);
});

test('the notice says what happened and what to do, without jargon beyond the format name', () => {
  assert.match(HEIC_NOTICE, /HEIC/);
  assert.match(HEIC_NOTICE, /screenshot/i);
  assert.match(HEIC_NOTICE, /Most Compatible/);
});
