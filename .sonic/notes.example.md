# Sonic notes

<!-- An example of the format Sonic uses in notes.md. This file is only an example: Sonic reads notes.md, not this file. -->

## 2026-01-15

**Plan**
- Add retry with backoff to the upload client, then cover it with a test.

**Done**
- Read client.py and found the single call site in send().

**Next**
- Write the retry loop and the test, then run the tests.

**Decided**
- Three attempts, doubling delay, no retry on a 4xx response.

---
