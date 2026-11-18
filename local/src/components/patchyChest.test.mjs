// Run with: node --test src/components/patchyChest.test.mjs   (from local/)
// Two things about Mini Patchy's body (2026-11-18): the chest badge the original Patchy has (a dark inset with a plus in his accent colour, in the middle of his chest), which the small one was missing, and the arms that
// are not typing on the laptop, which are all drawn BEHIND the laptop (they used to be drawn in front of it, and crossed its screen when it was carried or set down).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

const read = name => readFileSync(new URL(name, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const mini = read('./MiniPatchy.tsx');
const hero = read('./PatchyHero.tsx');
const css = read('./__styles__/MiniPatchy.css');

const at = text => mini.indexOf(text);

test('he has exactly the original Patchy\'s chest badge: a small dark inset with a plus in the accent colour', () => {
  const inset = '<rect x="43" y="63" width="14" height="14" rx="3" fill="#0C1016" stroke="#1F242D" />';
  const plus = '<path className="mp-stroke-accent" d="M50 66 V74 M46 70 H54" strokeWidth="2" strokeLinecap="round" opacity="0.6" />';
  // the full-size one's (the original), and the small one's, identical
  assert.ok(hero.includes(inset), 'the original inset');
  assert.ok(hero.includes(plus), 'the original plus');
  assert.ok(mini.includes(inset), 'the same inset on the small one');
  assert.ok(mini.includes(plus), 'the same plus on the small one');
});

test('the badge is in the middle of his chest, the plus is centred on its inset, and it is drawn on the torso and under the laptop', () => {
  // the torso is x 34 to 66, y 56 to 86: its middle is x 50
  const torso = mini.match(/<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" rx="7" fill="#121316"/).slice(1).map(Number);
  const torsoMiddle = torso[0] + torso[2] / 2;
  const inset = mini.match(/<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" rx="3" fill="#0C1016" stroke="#1F242D" \/>/).slice(1).map(Number);
  assert.equal(inset[0] + inset[2] / 2, torsoMiddle, 'in the middle across his chest');
  assert.ok(inset[1] >= torso[1] && inset[1] + inset[3] <= torso[1] + torso[3], 'inside the torso, top to bottom');
  const [px, py1, py2, bx1, by, bx2] = mini.match(/d="M(\d+) (\d+) V(\d+) M(\d+) (\d+) H(\d+)"/).slice(1).map(Number);
  assert.equal(px, inset[0] + inset[2] / 2);
  assert.equal(by, inset[1] + inset[3] / 2);
  assert.equal((py1 + py2) / 2, by);
  assert.equal((bx1 + bx2) / 2, px);
  assert.equal(py2 - py1, bx2 - bx1, 'a plus: the arms are as long as each other');
  // it is drawn after the torso, before the laptop (which hides most of it, as it hides most of him), in the layer that rises and leans with him
  assert.ok(at('<rect x="43" y="63"') > at('<rect x="34" y="56" width="32" height="30"'));
  assert.ok(at('<rect x="43" y="63"') < at('<g className="mp-lap"'));
});

test('every arm that is not typing on the laptop is drawn behind it: the two that carry it, the left one hanging, and the left one up', () => {
  const lap = at('<g className="mp-lap"');
  const torso = at('<rect x="34" y="56" width="32" height="30"');
  // the carrying arms, both of them, in one group after the torso and before the laptop
  const back = at('<g className="mp-rig mp-rig-back">');
  assert.ok(back > torso && back < lap, 'after the torso and before the laptop');
  const backGroup = mini.slice(back, mini.indexOf('</g>', back));
  for (const part of ['<path ref={leftArmRef}', '<circle ref={leftHandRef}', '<path ref={rightArmRef}', '<circle ref={rightHandRef} className="mp-accent"']) assert.ok(backGroup.includes(part), part);
  assert.equal((mini.match(/<g className="mp-rig">/g) ?? []).length, 0, 'there is no group of carrying arms in front of the laptop any more');
  // the arm hanging and the arm up, after he has let go: also before the laptop
  for (const name of ['mp-arm-hang', 'mp-arm-up']) {
    const arm = at(`<g className="mp-arm-free ${name}">`);
    assert.ok(arm > back && arm < lap, `${name} is behind the laptop`);
  }
  // only the hand that types on it is in front of it, and the head and the right arm after that
  const typing = at('<g className="mp-arm-typing">');
  assert.ok(typing > lap, 'the typing hand is on the laptop');
  assert.ok(typing < at('<PatchyHead'));
  assert.ok(at('<g className="mp-rarm">') > at('<PatchyHead'), 'the right arm, in front of his head, is the one that is not a carrying arm');
  // they fade in and out with the rest of the carrying arms
  assert.match(css, /\.mp-rig \{ opacity: 0; transition: opacity 0\.3s ease; \}/);
  assert.match(css, /\.mp-rigging \.mp-rig \{ opacity: 1; \}/);
});
