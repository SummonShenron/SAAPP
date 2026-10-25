import assert from 'node:assert/strict';
import { test } from 'node:test';
import { loadLocalGitMode, localGitModeLabel, nextLocalGitMode, parseCommittedReply, parseGitReply, parseMergedReply } from './commitResult.ts';

const SHA = 'a'.repeat(40);
const reply = (extra = '') => `**Committed** [\`aaaaaaa\`](https://github.com/SummonShenron/SAAPP/commit/${SHA}) to \`feat/notes\` on \`SummonShenron/SAAPP\` (12 files).${extra}`;

test("Sonic's committed reply is found, with the repository, the full commit id and the branch", () => {
  assert.deepEqual(parseCommittedReply(reply('\n\n**Opened pull request** [#9](https://github.com/x/y/pull/9) into `main`.')), { kind: 'commit', repo: 'SummonShenron/SAAPP', commit: SHA, branch: 'feat/notes' });
});

test('anything else is not a committed reply', () => {
  for (const text of ['', 'Committed it', reply().replace('`feat/notes`', '`bad name`'), reply().replace('on `SummonShenron/SAAPP`', 'on `other/repo`'), reply().replace(SHA, 'abc'), '**Not committed:** nothing', null, undefined]) {
    assert.equal(parseCommittedReply(text), null, String(text));
  }
});

const merged = (extra = '') => `**Merged** pull request #9 into \`main\` (squash) on \`SummonShenron/SAAPP\`: [\`bbbbbbb\`](https://github.com/SummonShenron/SAAPP/commit/${'b'.repeat(40)})${extra}`;

test("Sonic's merged reply is found, with the branch it was merged into and the merge commit", () => {
  assert.deepEqual(parseMergedReply(merged('\n\nTo bring your own copy of `main` up to date')), { kind: 'merge', repo: 'SummonShenron/SAAPP', commit: 'b'.repeat(40), branch: 'main' });
  assert.equal(parseMergedReply(merged().replace('on `SummonShenron/SAAPP`', 'on `other/repo`')), null);
  assert.equal(parseMergedReply(merged().replace('b'.repeat(40), 'bbb')), null);
  assert.equal(parseMergedReply('**Merged** pull request #9 into `main` (squash) on `SummonShenron/SAAPP`.'), null, 'an older reply with no commit link');
  assert.equal(parseGitReply(reply())?.kind, 'commit');
  assert.equal(parseGitReply(merged())?.kind, 'merge');
  assert.equal(parseGitReply('hello'), null);
});

test('the setting cycles through off, button and auto, defaults to the button, and survives a missing or odd stored value', () => {
  assert.equal(loadLocalGitMode(), 'button');
  assert.deepEqual(['button', 'auto', 'off'].map(m => nextLocalGitMode(m)), ['auto', 'off', 'button']);
  for (const mode of ['off', 'button', 'auto']) assert.match(localGitModeLabel(mode), /^Local git: /);
});
