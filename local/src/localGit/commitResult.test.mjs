import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseCommittedReply } from './commitResult.ts';

const SHA = 'a'.repeat(40);
const reply = (extra = '') => `**Committed** [\`aaaaaaa\`](https://github.com/SummonShenron/SAAPP/commit/${SHA}) to \`feat/notes\` on \`SummonShenron/SAAPP\` (12 files).${extra}`;

test("Sonic's committed reply is found, with the repository, the full commit id and the branch", () => {
  assert.deepEqual(parseCommittedReply(reply('\n\n**Opened pull request** [#9](https://github.com/x/y/pull/9) into `main`.')), { repo: 'SummonShenron/SAAPP', commit: SHA, branch: 'feat/notes' });
});

test('anything else is not a committed reply', () => {
  for (const text of ['', 'Committed it', reply().replace('`feat/notes`', '`bad name`'), reply().replace('on `SummonShenron/SAAPP`', 'on `other/repo`'), reply().replace(SHA, 'abc'), '**Not committed:** nothing', null, undefined]) {
    assert.equal(parseCommittedReply(text), null, String(text));
  }
});
