# Launch probe, 2026-11-19: weak points found before opening Sonic to the public

Jack asked to probe Sonic for weak points that should be hardened before public use. This is the first layer: **read-only and free** (code reading, a mechanical sweep of all 195 endpoints, and a stub-backed probe of the real routes; no model was called, no database was touched). It adds to `docs/public-launch-readiness.md` (the earlier list of nine); it does not replace it. Everything marked VERIFIED was reproduced against the real app routes with a FastAPI `TestClient` (a counting stub in place of the model workflow, a fake database); that probe was a one-off script and should become permanent tests when items 1 to 4 are fixed. UNVERIFIED means it depends on something only the deployed server or a live model can show.

## Status: items 1 to 4 FIXED (2026-11-20, uncommitted)

Jack's answers: the synthetic endpoint was an errAgent integration and is fine to sunset (errAgent's synthetic check will stop working); `GITHUB_WEBHOOK_SECRET` is set on Render.

1. **`/api/synthetic/ask` removed**, with its request class and the `run_synthetic_read_only_question` helper. It now answers 404. Pinned by `test_the_synthetic_endpoint_that_ran_the_model_for_anyone_no_longer_exists` (no workflow starts, no route with "synthetic" in its path).
2. **Request size limit for every route** ([backend/utils/body_limit.py](../backend/utils/body_limit.py), registered inside the CORS layer): 15 MB by default, 60 MB for `/api/upload` (document ingestion only), 6 MB for the shared guest. Env: `MAX_REQUEST_BYTES`, `MAX_UPLOAD_BYTES`, `MAX_GUEST_REQUEST_BYTES`. A request that declares itself too big gets 413 without a byte being read; one that does not declare a size is stopped the moment it streams past the limit. The real app refuses a guest's oversize upload (verified through the app's own routes).
3. **GitHub webhook refuses when the secret is unset** (503) and still requires a valid signature when it is (401). The ingest webhook now compares its secret in constant time. Render already has the secret, so nothing changes in production.
4. **Guests are refused by default.** New `get_member_user` dependency ([backend/auth/isolation_auth.py](../backend/auth/isolation_auth.py)) applied to 55 write endpoints (settings incl. the GitHub token, memory, runner, tasks, meetings, billing, notes, documents). The writes a guest may make are written out in full in [backend/auth/guest_policy.py](../backend/auth/guest_policy.py) (the public demo: chat and what hangs off it). `test_public_surface.py` reads the app's real route table and fails for any write endpoint that is neither members-only nor on the list, sends the guest header to every members-only route (all 403), and checks the demo's writes are not refused. The frontend already hides these controls from guests, so the demo is unchanged.

All of it is covered by 89 tests in `backend/tests/test_public_surface.py`; 11 deliberate breakages (member check off, an endpoint re-opened, the webhook unsigned again, each size rule removed, the allowlist trimmed) were each caught.

**Still open from this probe:** items 5 to 10 below (IP rate limiting, `/api/erragent-debug`, the login existence answer, `/api/health` detail, the CORS pattern, JWT issuer/azp), reads by guests of shared guest data, and the guest per-visitor allowance, which is keyed by a header the browser chooses (`x-visitor-id`) so a visitor can reset it by changing the value; the global budget still caps total spend.

## Incident 2026-11-17 to 20: the Claude key expired and nobody saw it (fixed and alerted)

The Anthropic key on Render expired on 2026-11-17 (Console keys now have an expiry). Every Claude call answered 401 and the app quietly fell back to Gemini: Haiku calls went to 0 on 11-18 and 11-19, the same tokens cost about $1.09 and $1.28 instead of about $0.26 and $0.30, and the first visible sign was a crash, because Gemini's schema converter rejects the Maps tool's integer enum. Fixed: tool schemas are made Gemini-safe before binding (`backend/models/gemini_schema.py`), and a refused key now raises an alert: an ERROR in the logs, a Sonic Health counter, `llm: degraded` on `/api/health`, and a red strip under the top bar for admins until the key works (`backend/utils/provider_health.py`, `local/src/components/ProviderAlert.tsx`). Still needed from Jack: a new Anthropic key on Render (Workspace: Default, Expiration: Never).

## The structural fact behind most of this

`get_current_user` ([backend/auth/isolation_auth.py](../backend/auth/isolation_auth.py)) accepts `X-Principal: guest` (no token at all) or `Bearer guest-sandbox-token` as the shared guest identity. This is intentional (the public demo), but it means **every signed-in endpoint is reachable by anyone on the internet as the guest**. Of 195 endpoints, 136 use `get_current_user`; 84 of those write; 70 of those 84 never mention guests in their own handler (some are protected deeper in, which the sweep cannot see). Each one has to refuse guests by itself, and the ones that forgot are open to the world.

## Fix first (cheap, high value)

1. **`POST /api/synthetic/ask` runs the full model workflow for any anonymous caller. VERIFIED: 25 anonymous calls in a row all executed it.** No guest block, no budget-guard check, no rate limit, and nothing in the frontend or docs calls it (a leftover health check). The workflow can route to tools (web search, GitHub with the shared token). It spends the app's money past the $5/day cap, because only the chat path asks the guard. Remove it, or make it admin-only with the guard in front.
2. **No upload size limit. VERIFIED: a 40 MB upload by a guest was read whole into memory and base64'd.** `/api/upload-attachment` (guest-reachable) reads `await file.read()` with no cap and then discards the result; `/api/upload` does the same for ingesters. One worker on one Render instance: a few large uploads can take the whole service down. Add a size cap (reject on Content-Length and stop reading at the cap) and a global request-body limit.
3. **`POST /webhooks/github` accepts unsigned events when `GITHUB_WEBHOOK_SECRET` is not set** (it only logs a warning). A forged `pull_request` event for any repo the shared or a user's token can reach makes the server read the PR, call a model and **post a comment** ([github_service.process_pr_summary](../backend/services/github_service.py)). VERIFIED up to the token lookup. UNVERIFIED: whether the secret is set on Render. Make it refuse (503) when unset, and check Render.
4. **Guests can reach 70 write endpoints that never mention guests.** Settings (including the GitHub token, target repo, models), memory clear/edit/delete, conversations delete/rename, runner pairings and jobs, working notes, billing, feedback. With one shared guest identity, visitors can change each other's state, and some of these cost money or create things that persist. Fix by inversion: a `forbid_guest` dependency applied by default, with an explicit allowlist (chat, the demo, read-only views). Then a test that fails when a new endpoint is neither allowlisted nor guest-guarded.

## Medium

5. **No IP-level rate limiting anywhere** (login, runner pairing, webhooks, guest chat). The per-person and global budgets limit model spend but nothing limits request volume. UNVERIFIED: whether Render or a CDN in front already does.
6. **`GET /api/erragent-debug` is unauthenticated and always returns HTTP 500.** Anyone can generate 500s on demand (alert noise in errAgent, polluted health signals). Remove or gate.
7. **`POST /api/login` tells anyone whether a Clerk id exists** ("authenticated" vs "needs_registration"). VERIFIED. Low value to an attacker (Clerk ids are unguessable) but it is a free existence oracle; the frontend does call it, so change its answer, not its existence.
8. **`GET /api/health` exposes pid, start time, uptime and worker settings** to anyone. Minor; useful to attackers for fingerprinting, useful to Jack for the one-instance check. Consider keeping only status and db for unauthenticated callers.
9. **CORS allows `https://saapp-<anything>.vercel.app`** with credentials. Anyone can create a Vercel project with that name. Low risk today because auth is a bearer token, not a cookie; still tighten to the real preview URLs.
10. **JWT verification checks signature and expiry but not issuer or authorized party (`azp`)** ([_verify_clerk_jwt](../backend/auth/isolation_auth.py)). The signing key comes from our own Clerk instance, so a foreign token fails; adding `iss` and `azp` checks is defence in depth.

## Retrieval quality (from today's eval, `backend/evals/rag_eval.py`)

11. On the invented libraries a "review my progress" question returns the right files in full only 13 to 18% of the time at 3 passages, a forbidden file (cover letter, another job) comes back for about 30 to 35% of questions, and "most recent" returns the oldest week. Hybrid routing is now the default (small gain); the larger levers are a soft tag boost and one passage per file. Nothing yet measures whether Sonic's written answer is correct, only whether the right passages were retrieved.

## Carried over from the earlier list, still open or unverified

- The production Apply bug is still undiagnosed (counters added, nothing read yet). UNVERIFIED in prod.
- Nothing alerts Jack when production fails (counters exist, alerts do not). UNVERIFIED.
- CI was failing on every push until today (collection hit a real database, then six tests that only passed with a `.env`). Fixed locally; **not yet seen green on GitHub**.
- Haiku with thinking on can crash with a thinking-block 400 when the tools list changes, falling back silently to Gemini (2026-11-02 note; fix not built). UNVERIFIED that it is still live.
- Load test designed, not built; clinician review of the support copy deferred; the runner's command language has had an AI reviewer but no human second reader; Terms need a lawyer's read; Stripe not wired.

## What this layer did NOT cover (needs live model calls, which cost money)

- **Prompt injection** through a library document, a web page the agent reads, an email, a GitHub issue: does the model obey text inside them, and do the tool guards (memory guard, untrusted-output sets) hold?
- **Cross-user leakage**: can a crafted question make Sonic reveal another person's memory, library, conversations or settings?
- **Prompt and secret extraction**: can the system prompt, tool menu details or keys be talked out of it?
- **Crisis and distress handling** against the safety ladder with adversarial phrasings.
- **Cost abuse through chat**: how far a guest or a free account can push spend per turn before the guards stop it.
- **Hallucinated actions**: saying it saved, sent, edited or ran something it did not.
- **Dependency vulnerabilities** (`pip-audit`, `npm audit`) and the runner's command language by a second human.

## Suggested order

Items 1 to 4 first (a day's work, each testable offline with the same TestClient probe turned into permanent tests), then confirm Render settings (the webhook secret, rate limiting in front), then the live probes with a stated budget.
