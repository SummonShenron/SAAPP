# Coding Agent Roadmap — scoping notes, not yet built

Working notes from a planning session, kept here to keep scoping out before any of
this gets implemented. Nothing in this doc has been built yet — see
`docs/voice-composer-and-memory-evolution.md` for the retrospective-style doc once
something here actually ships.

## The strategic thesis

Sonic Assistant doesn't need to out-reason frontier general-purpose coding
assistants. It needs to win on **context access that they structurally don't
have** — full-repo visibility, cross-file connections, and (for the frontend) live
render/cascade information — while still being "good enough" that the context
advantage isn't wasted on wrong answers delivered confidently. Two threads follow
from that:

1. **Python coding competence is a gate, not a nice-to-have.** The long-term plan
   is to let each user connect their own GitHub token so Sonic can work against
   *their* repos, not just this one. That's only worth building once Sonic is
   reliably good at writing/understanding Python against a repo it just met —
   otherwise it's handing users a confident wrong-answer machine pointed at their
   own code.
2. **A live-browser copilot could be a genuine, hard-to-replicate differentiator
   for frontend work** — but it's a separate, later slice (see "Tabled" below).

Cost constraint stated explicitly this session: this runs out of pocket, so no
automatic escalation to more expensive reasoning (e.g. auto-triggering deep
thinking mode on "why does X happen"-shaped questions). Deep thinking stays an
explicit, user-controlled toggle. Everything in the priority list below is scoped
to avoid new *recurring* cost — new tool actions and prompt rules, not standing
infrastructure, except where called out.

## Priority order (agreed this session)

1. Reference-classifying trace tool — **done**
2. Tier 2 — idiom-matching via retrieval — **done**
3. Tier 1 — real Python execution against a repo's actual dependencies — **done**
4. Per-user GitHub token scoping (design informed by `workflow_builder`) — **only item left**

Rationale for this order: 1 and 2 are pure tool/prompt additions with no new
infrastructure and directly serve the thing named as mattering most —
"understand what the issue is and find the necessary pieces, since it has full
access to the code." Tier 1 answers a different question (does code that's
already written actually run) and is a genuinely bigger build. Token scoping only
matters once the others make it worth pointing at someone else's repo at all —
research is done (see section 4 below), implementation hasn't started.

---

## 0. Confirmed live in production — two real failures (both fixed)

A real trace from production (latest pushed changes, not stale code — confirmed)
caught both open gaps in this roadmap actually happening, in a single request:
the user asked Sonic to inspect the real page to fix a z-index conflict between
the hero-banner and the trace panel.

**Failure A — `find_file` exists and is never used.** The trace shows repeated
literal-keyword searches (`'trace-panel'`, then `'hero'`/`'Trace'`, then
`'panel'`, then `'trace'` again) across ~15 steps, every one of them either
`search_code` or a guessed `read_repo_file` (it tried `LandingPage.tsx`,
`App.tsx`, `Layout.tsx` — none of which came from an actual search hit, all
guessed from "how a project like this is probably organized," the exact
antipattern `constraints.py` already has a rule against). `'trace-panel'` isn't
a real string anywhere in the repo — the actual class is
`desktop-trace-sidebar` — which is precisely the case `find_file` was built for
tonight, and `constraints.py` already says explicitly to reach for it once
`search_code` comes back empty for a name-like query. It never did. Since this
ran against real current code, this rules out the "prompt guidance exists but
isn't deployed" explanation — **the guidance exists, is live, and still isn't
being followed.** That's now a confirmed finding, not a hypothesis: a prose rule
buried in `constraints.py` isn't strong enough on its own to redirect behavior
once a model is a dozen steps into a losing strategy.

**Fixed:** `run_react_loop` now takes an optional `stuck_action_redirects: dict`
mapping a `tool_action_name` to `(consecutive_miss_threshold, redirect_message)`.
It tracks a CONSECUTIVE-misses streak per tool_action regardless of args (unlike
the existing args-signature retry tracking, which treats any differently-worded
retry as "honest" and clears itself — precisely the loophole that let the real
trace burn ~15 steps). Once a listed tool's streak hits its threshold, the
redirect message is injected into the prompt AND a further call to that same
stuck tool is mechanically rejected (not executed, not recorded) — the same
enforcement already used for a premature "final". `tool_agent_node` wires
`TOOL_AGENT_STUCK_ACTION_REDIRECTS = {"search_code": (2, ...)}`. 2 new tests in
`test_react_loop_retry_enforcement.py` reproduce the exact production trace
(3 differently-worded `search_code` calls, 3rd one rejected, forced switch to
`find_file`) and confirm no false-positive on a normal success.

**Failure B — it never opened a browser at all**, despite being explicitly asked
to "inspect the actual page." Confirmed the browser-copilot routing gap (see
"Tabled" below) is real and observable today, not hypothetical — a z-index
stacking conflict cannot be resolved from source alone (it depends on the full
runtime DOM stacking context, not just one file's CSS).

**Fixed:** since there's no natural "miss" signal for an action that was simply
never attempted (unlike Failure A), this uses a different mechanism — a regex
(`_mentions_visual_inspection` in `agent_workflow.py`) matching the real
phrasing ("inspect the actual page", "z-index", "stacking", "how it
looks/renders", "css/layout/rendering issue", etc.) against the user's own
message. When it matches, `tool_agent_node` appends a directive straight onto
*that turn's own question text* — before calling `run_react_loop` — telling it
to use `browser_navigate` before concluding, since this is a stronger per-turn
signal than a static system-prompt paragraph competing against many turns of
code-reading momentum. The existing `TOOL_AGENT_PROMPT` browser-tools paragraph
was also strengthened to name layout/CSS/z-index bugs explicitly, as a second,
belt-and-suspenders layer. 4 new tests (2 for the regex, 2 confirming the
directive is/isn't injected into the question passed to `run_react_loop`).

---

## 0b. `tool_agent_node` had zero conversation history (fixed)

Discovered from a real, fresh production trace (not this session's earlier
"Failure A/B" trace — a separate, later conversation): user asks Sonic to open
`btyfitness.app` and click a chat widget icon to verify it works. Sonic stalls
for two turns, then flatly and confidently denies having a browser tool at all
("I don't actually have a live browser tool... I promise I'm not holding out on
you") — while `browser_navigate` sat right there in that same turn's action
menu, and had already been used successfully earlier in that exact
conversation. Only after the user insisted directly ("dude you literally
navigated your own site earlier today") did it finally navigate — then
immediately closed the browser having verified nothing, having lost the actual
task ("click the widget and verify it works").

**Root cause, confirmed from the code, not guessed:** `TOOL_AGENT_PROMPT` had
exactly one content slot for the request — `USER REQUEST: {question}` — filled
from `state["messages"][-1]` alone. No `{history}` slot existed. `{schema}`
isn't history either (`"repo=X, default_branch=Y..."` — pure infra metadata).
`reasoner_node` already builds a real `formatted_history` from the whole
message list for its own prompt (`agent_workflow.py` ~line 582) — that was
never threaded into `tool_agent_node`'s loop at all. By the turn it finally
acted, the ENTIRE prompt driving that decision was "dude you literally
navigated your own site earlier today. you can indeed navigate to
https://btyfitness.app" — an assertion of capability, not a restatement of what
to check. It didn't forget in some vague sense; the information was
structurally never in its context to begin with.

**Fixed:**
- New `{history}` slot in `TOOL_AGENT_PROMPT` (`constraints.py`), filled from
  the prior N messages (capped — see below), pre-substituted via the same
  `.replace()` pattern `actions_menu` already uses (with the same `{`/`}`
  escaping, since past message content could itself contain literal braces).
- `TOOL_AGENT_HISTORY_MAX_MESSAGES` (default 10, env-configurable) —
  deliberately capped even though `reasoner_node`'s equivalent is uncapped:
  `reasoner_node` builds its prompt once per turn, but `tool_agent_node`
  rebuilds its prompt on every single ReAct step, so uncapped history would
  multiply cost across every step of every turn, not pay for it once.
- New `constraints.py` rules: (1) check AVAILABLE ACTIONS THIS TURN before
  ever claiming a capability is missing — a fluent, detailed denial isn't more
  trustworthy than a short one if the action is sitting in the menu; (2) when
  RECENT CONVERSATION shows an instruction was given a few turns back and the
  current request is a short confirmation ("yes", "go ahead"), find what it
  was actually confirming in the history rather than treating the confirming
  reply itself as the complete scope of the task.
- 3 new tests in `test_tool_agent_node.py`: history threaded into the prompt
  (reproducing the exact multi-turn scenario), capped at
  `TOOL_AGENT_HISTORY_MAX_MESSAGES` (an old message is verifiably dropped, a
  recent one kept), and the "(no prior messages)" fallback on a first message.

**Follow-up (same session): mechanical backstop for the capability-denial rule
itself.** The new "check your own menu before denying a capability" prose rule
above got tested against the exact real trace and, predictably given tonight's
own repeated lesson, needed a mechanical backstop too — the prose rule alone is
an assumption, not a guarantee, until proven otherwise.

**Shipped:** `run_react_loop` gained `capability_denial_watchlist` (optional:
a list of `(capability_keyword_regex, tool_action_menu_substring)` pairs),
alongside a generic `_CAPABILITY_DENIAL_RE` (denial-shaped phrases — "I don't
have", "I can't", "I'm not able to", etc.). When a `"final"` answer matches
both a denial phrase AND one of the watchlist's capability keywords, AND that
pair's menu substring is actually present in the turn's real `prompt_template`
(proving the capability genuinely is available), the `"final"` is rejected
once — same mechanical shape as the premature-final and stuck-action
rejections, budget-limited to 1 so a genuinely correct "I don't have that" (a
capability that really isn't available) still gets through. `tool_agent_node`
wires `TOOL_AGENT_CAPABILITY_DENIAL_WATCHLIST` covering browser access
(`browser_navigate`), code execution (`run_snippet`), and database access
(`run_mongo_query`) — the three "real backend action" tools most plausible to
falsely deny. 6 new tests: rejected-once-then-corrected, budget-limited (a
second denial gets accepted rather than looping), a false-positive guard
(genuinely unavailable capability is never rejected), watchlist wiring, and a
full end-to-end reproduction through `tool_agent_node`'s real menu
construction (denial → rejection → real `browser_navigate` call → accepted
final). Full suite: 377 passed, same one pre-existing unrelated failure as
every other run this session.

---

## 1. Reference-classifying trace tool (done)

**Problem it solves:** `search_code` (added earlier this session) already finds
every file that references a symbol, but it doesn't distinguish "this is where
the value is actually decided" (an assignment, a state setter, a prop's source)
from "this just reads or passes it through." Sonic has to open every hit manually
to figure that out, which burns step budget fast on a chain more than 1-2 hops
deep — and a shallow step budget means it can hit `forced_final` and have to guess
before reaching the real root cause, no matter how good its per-step judgment is.

This is the single existing prompt rule already covering *part* of this, in
`backend/components/constraints.py` (~line 706): "the first place you find
something *related* to the symptom is not the same as the place actually
*causing* it... trace one level further." That rule assumes one extra hop is
enough. A genuinely deep chain needs a tool that shows the whole shape at once,
not a rule that says "go one step further."

**Rough shape:** a new tool action (name TBD — `trace_symbol`? `find_definition_sites`?)
that takes a symbol/field/prop name and returns every reference already found by
the existing `search_code` machinery, but sorted into two buckets:
- **write/decide sites** — assignment targets, state-setter calls (`setX(...)`),
  a prop's defining `useState`/`useReducer`, a function's `return` of that value
- **read/pass-through sites** — everything else (reads, prop consumption,
  logging, re-exports)

**Decisions made:**
- Regex-based classification, not AST — consistent with `find_file`'s own
  precedent, no new parser dependency. Validated against 13 real lines pulled
  from this repo (the trace-panel `useState`, `get_accessible_affiliates`,
  personal-KB fields, an equality-check negative case) — 100% correct. Real
  limits identified and accepted: a multi-line assignment where the symbol and
  `=` land on different lines, or GitHub's truncated search fragment cutting off
  before the `=`, can misclassify a write as a read. Accepted because the
  failure mode is soft — a misclassified hit still shows up in the "read"
  bucket, nothing is silently dropped, worst case is one extra file opened.
  Genuine AST would need `tree-sitter` (no stdlib JS/TS parser) and a real
  architecture change (parse each candidate file instead of one search call) —
  deferred until false negatives actually show up in practice, not built
  preemptively. Cheap half-measure if that day comes: AST just `.py` hits
  (stdlib `ast`, free) and keep regex for `.ts`/`.tsx`.
- Sits alongside `search_code`, doesn't replace it — `search_code` stays "does
  this exist at all," `trace_symbol` is "which of these hits actually matters."
- No special-casing needed in `run_react_loop` — it's just another tool_action,
  covered by the existing empty/error retry tracking.

**Shipped:**
- `_classify_symbol_line(symbol, line)` — regex heuristic recognizing: a
  def/class that IS the symbol, a plain assignment (with a negative lookbehind
  so `==`/`!=`/`<=`/`>=` don't false-positive), an attribute/dict-style
  assignment (`self.x =`, `state["x"] =`), a React state setter call
  (`setSymbol(...)`, derived from the symbol name), a `useState`/`useReducer`/
  `useRef`/`useMemo`/`useContext` destructuring that defines the symbol, and a
  function returning it. Everything else is "read".
- `_code_search_items()` — extracted the GitHub code-search call
  `_search_code` already made (it was requesting `text-match+json` and
  discarding the match fragments) into a shared helper, so `trace_symbol` reuses
  the exact same request and actually uses the fragments for classification.
- `trace_symbol` tool_agent_node action (args: `symbol`) — sorts every hit into
  WRITE/DECIDE (with a sample line) vs. READ/PASS-THROUGH, available to
  everyone (not admin-gated, same tier as `search_code`/`find_file`).
- Tests: 5 unit tests on the classifier (Python def, plain assignment, React
  state setter, a read, and the `==` negative case), 3 integration tests via
  `tool_agent_node` (correct sorting end-to-end, empty-symbol error, no-matches
  with a genuine retry). Full suite: 369 passed, same one pre-existing
  unrelated failure as every other run this session.

---

## 2. Tier 2 — idiom-matching via retrieval (done)

**Shipped:** new paragraph in `constraints.py`'s `TOOL_AGENT_PROMPT`, right after
the existing "citing a real function" rule — before writing new code for an
existing file, pull 1-2 real analogous functions from the same repo
(`search_code`/`find_file` → `read_repo_file`) and match their actual patterns,
with concrete examples from this repo itself (closures inside `tool_agent_node`,
plain `assert`/`monkeypatch` test style) rather than generic Python idiom. Pure
prompt addition, no new tool, no dedicated test (matches how every other prose
rule in this file is verified — by behavior, not a unit test of the string).

**Problem it solves:** Sonic writes syntactically fine Python that doesn't match
*this* codebase's actual conventions (this repo's specific error-handling style,
logging calls, closures-inside-`tool_agent_node` pattern, this repo's test style
with `monkeypatch`/plain `assert`) because it composes from generic
training-data Python instead of the real patterns already sitting in the repo.

**Rough shape:** a `constraints.py` addition (no new tool needed — this is pure
tool-*usage* discipline on tools that already exist): before writing new code for
an existing file/module, pull 1-2 real analogous functions from the same repo via
`search_code`/`find_file` and explicitly match their patterns, rather than
answering from a generic idea of "how Python code like this usually looks."

This is the cheapest item on this list — no new tool, no new infra, just a
prompt rule — and it directly extends the same "cite it → verify it" discipline
already hardened into `TOOL_AGENT_PROMPT` this session, applied to *generation*
instead of just fact-retrieval.

---

## 3. Tier 1 — real execution against a repo's actual dependencies (done)

**Confirmed gap (checked this session):** `run_python_sandboxed`
(`backend/services/python_sandbox.py`) is a WASM sandbox restricted to a
stdlib-only `SAFE_IMPORT_ALLOWLIST` (`math`, `re`, `json`, `itertools`, etc.). It
cannot import `langgraph`, `pymongo`, or any real module from a target repo.
`run_repo_tests` was the only real-execution path, and requires an *existing*
pytest file — there was no rung for "run this ad hoc snippet."

**Design decision (multi-repo/multi-user from day one, not scoped to SAAPP's
own repo):** initially considered running snippets as a subprocess in the
server's own already-installed venv — free and fast, but only works for SAAPP's
own repo (an arbitrary repo needs its OWN dependencies installed somewhere), and
installing a possibly-untrusted repo's dependencies on the production host is a
real code-execution risk regardless of env-var scoping. Explicitly chose to
build this the way it'll actually need to work once per-user repos exist:
reusing `.github/workflows/patchy-tests.yml`'s existing GitHub Actions dispatch
(same pattern `run_repo_tests` already uses) — an ephemeral, secret-free runner
per call, already parameterized on `repo`/token by construction, so it's
multi-tenant-ready with no rework later. Traded away: speed (minutes, not
seconds — accepted explicitly, this doesn't need to be fast for code
verification/investigation) and no warm-environment caching (each run reinstalls
deps fresh, same as every `run_repo_tests` call already does).

**Shipped:**
- `.github/workflows/patchy-tests.yml`: added an optional `python_snippet` input
  alongside the existing `test_commands` one (now also optional — backward
  compatible, existing callers unaffected). The snippet is written to a file via
  `printf '%s\n'` (never `eval`'d — no shell-injection surface regardless of
  content) and run with a 60s `timeout`; job-level `timeout-minutes: 10` as a
  backstop. This workflow is shared with errAgent's own separate Patchy
  pipeline — the change is additive only, existing `test_commands` behavior is
  untouched.
- `backend/services/ci_test_runner.py`: extracted the dispatch/poll/lookup logic
  shared by `run_repo_tests` into `_dispatch_and_wait()`, and added
  `run_python_snippet()` on top of it. Unlike `run_repo_tests` (log fetched only
  on failure), this always returns the log excerpt — the printed output is the
  actual point of running a snippet, not just a pass/fail verdict. Validates
  non-empty and a 20,000-char cap before ever dispatching.
- `agent_workflow.py`: new admin-gated `run_snippet` tool_agent_node action
  (`_run_snippet` closure, menu entry, `_act` dispatch branch), mirroring
  `run_repo_tests`'s existing wiring exactly.
- Tests: 6 new in `test_ci_test_runner.py` (empty/oversized rejection without
  dispatching, correct `python_snippet` input name — not `test_commands` — sent
  to the dispatch payload, success always includes output, failure includes the
  real traceback), 4 new in `test_tool_agent_node.py` (admin-only menu
  visibility, defense-in-depth execution block, correct repo/branch/code
  dispatch). Full suite green (361 passed, same one pre-existing unrelated
  failure as every other run this session).

**Deferred, not forgotten:** no warm-environment caching (every call reinstalls
dependencies from scratch) and no dedicated low-latency sandbox — both explicitly
traded away for zero new recurring cost and immediate multi-tenant readiness.
Revisit if the multi-minute wait ever actually becomes the blocker, not before.

---

## 4. Per-user GitHub token scoping

**Goal:** once Sonic is reliably good at Python (gated on the above), let each
user connect their own GitHub token so the agent works against *their* repos
instead of the one shared, hardcoded token SAAPP uses today.

**Research findings (`workflow_builder`, sibling repo at
`C:\Users\jackh\workflow_builder`):** it already solves exactly this problem —
multi-tenant, Clerk-authenticated, per-user GitHub PATs + Google OAuth tokens.

- **Storage:** `ConnectionDocument` (`backend/app/models/connection.py:20-22`)
  stores `access_token_encrypted` / `refresh_token_encrypted`, plus `owner_id`,
  `provider`, `scopes`, `expires_at`. Encryption is symmetric **Fernet**
  (`cryptography` lib), key from an env var (`Settings.token_encryption_key`,
  `backend/app/config.py:18`). GitHub specifically stores a raw PAT — validated
  once against `GET https://api.github.com/user` before it's ever persisted
  (`services/github_connection.py:17-25`) — no OAuth flow, no refresh token, no
  scope tracking (`scopes=[]`). Google uses full OAuth2/PKCE with both tokens
  encrypted. A separate generic `secrets` collection uses the same Fernet
  pattern for arbitrary per-user API keys.
- **The scoping mechanism worth copying directly:** every repository method
  takes `owner_id` and filters by it *inside the Mongo query itself* —
  `{"id": connection_id, "owner_id": owner_id}`
  (`repositories/connections.py:23-25`) — never a "fetch the doc, then check who
  owns it" two-step. `owner_id` comes only from the Clerk-verified JWT subject
  (`auth.py:37-41`) and is threaded as an explicit parameter through the entire
  call chain (route → workflow engine → node runner) — there is no ambient
  "current user" global anywhere. A wrong `owner_id` just returns nothing; cross-
  user leakage would require an explicit bug passing the wrong id, not a missing
  check in a shared lookup. This is the same shape as `get_accessible_affiliates`/
  `verify_user_ingest_access` already taking `username` explicitly rather than
  reading it from somewhere ambient — SAAPP's existing convention already lines
  up with this, which is a good sign for how naturally this would fit.
- **Refresh/expiry/revocation:** Google tokens check `credentials.expired` and
  refresh + re-encrypt on demand (`google_oauth.py:56-69`), with explicit revoke
  on disconnect. **GitHub PATs have none of this** — deletion is the only
  "revocation." SAAPP would inherit the same gap for GitHub specifically, since
  it's a PAT, not an OAuth token — worth accepting explicitly rather than
  discovering it later.
- **Security measures worth copying:** encrypted fields are explicitly excluded
  from list-endpoint projections (`{"access_token_encrypted": 0}`,
  `repositories/connections.py:17`) so a token can never leak into a list
  response; OAuth flows use PKCE + a server-side pending-state nonce bound to
  `owner_id`; redirect targets are allowlisted.
- **Explicit gap, not a pattern to copy:** no rate limiting and no audit logging
  of token usage anywhere in `workflow_builder`'s backend. For SAAPP this is a
  bigger deal than it is for `workflow_builder` — an LLM agent autonomously
  deciding which GitHub calls to make with a real user PAT is a materially
  different risk profile than a fixed, deterministic workflow node using a
  token. Audit logging (which repo/endpoint was hit, when, by which tool_action)
  should probably be treated as required for SAAPP even though the reference
  implementation skipped it.

**Reference map for later:** `backend/app/models/connection.py`
(`ConnectionDocument`), `backend/app/repositories/connections.py`,
`backend/app/services/github_connection.py`
(`GitHubConnectionService.access_token`), `backend/app/api/connections.py`,
`backend/app/auth.py` (`get_current_user_id`), `backend/app/config.py`
(`TOKEN_ENCRYPTION_KEY`).

**Why this matters beyond "just store a token per user":** this changes SAAPP's
threat model the same way the per-user personal-KB work earlier this session
changed document isolation — a bug here doesn't just mis-answer a question, it
can leak one user's repo access to another user's session. Worth designing this
with the same rigor as the KB isolation work (a real cross-tenant test case, not
just "add a token column"), once the workflow_builder research lands.

---

## Tabled — real, but not next

### Live-browser copilot for frontend (CSS/DOM) work

Discussed and deliberately deferred this session in favor of the Python-focused
priorities above. The idea: give Sonic real render+inspect access (it already has
`browser_navigate`/`browser_screenshot` via browserless) so it can trace *which
CSS rule actually wins the cascade* (via something like CDP's
`CSS.getMatchedStylesForNode` — file + line for every matching rule, in cascade
order) instead of guessing from source, plus temporary/ephemeral live style
mutation to test a hypothesis before presenting a fix (never touches source
files — stays a copilot, no write access, matches what was explicitly said this
session).

**Real blocker:** browserless is a cloud service and cannot reach `localhost` —
it can only load something with a real reachable URL (e.g. `saapp.onrender.com`).
Agreed as an acceptable scope: verify against what's actually deployed, not local
dev-in-progress. Local-dev reachability would need a tunnel (ngrok/Cloudflare
Tunnel) and wasn't scoped further.

### The `.chat-window` white-fade bug

Root-caused this session but the fix itself was tabled: `.chat-window`'s
"dual fade mask" (`local/src/index.css` ~line 581) unconditionally fades the
top 40px / bottom 36px of the chat log to transparent regardless of whether
there's actually anything scrolled off-screen — confirmed via
`scrollHeight === clientHeight` (nothing to scroll) while the mask still faded
content. Most visible under codeblocks specifically because a solid, high-contrast
dark rectangle (`--code-bg: #0f172a`) fading to transparent reads as an obvious
pale band, where thin text fading in the same zone is subtle. Real fix: make the
fade conditional on actual scroll position (only mask the top once
`scrollTop > 0`, only mask the bottom while `scrollTop + clientHeight < scrollHeight`)
instead of a static always-on gradient. Not yet implemented.

Also worth noting for whenever this gets revisited: Sonic's own prior attempt at
this bug (visible in a screenshot shared this session) proposed editing
`--hero-overlay`, a CSS custom property declared in `local/src/index.css` but
referenced **nowhere else in the codebase** (confirmed via repo-wide grep) — a
concrete, real example of exactly the "recommend a plausible-sounding fix without
checking it's actually wired to anything" failure mode this whole roadmap is
trying to close.

---

## 4b. Readiness check for Section 4 — asked Sonic to plan it, it wasn't ready (done)

Before starting Section 4 for real, ran the actual test: asked Sonic (in its own
production chat, not this session) to scan the repo and produce the full
per-user-GitHub-token migration plan. It got the storage design right
(`user_settings` collection, matching the existing `target_repo`/`deep_thinking`
pattern) and correctly enumerated every test file referencing `GITHUB_TOKEN` — but
its core wiring claim was fabricated: it said to edit the node in
`agent_workflow.py` that calls `process_pr_summary`, and no such node exists —
`process_pr_summary` (`backend/services/github_service.py:23`) is only ever
called from a webhook handler in `app.py:1576`, with no LangGraph state, no
session, no logged-in user to hang a per-user token on. It also missed that
`os.getenv("GITHUB_TOKEN")` is independently re-fetched in **five separate
places** inside `agent_workflow.py` (search/trace, PR-summarizer's
`resolve_pr_number`, `fetch_branch_diff_summary`, `_execute_create_pr`,
`_execute_create_issue`) — the actual refactor surface — and never mentioned
`ci_test_runner.py` at all despite it consuming the same token via `headers`
passed down from `tool_agent_node`.

**Root-caused to two concrete, fixable mechanisms — not "the model just
hallucinates":**

1. `search_code`/`trace_symbol` ride GitHub's hosted `/search/code` index —
   capped at 20 results per query, subject to indexing lag, and explicitly not
   guaranteed complete per GitHub's own docs. Verified the gap directly: the
   same "every file that reads GITHUB_TOKEN" question answered instantly and
   completely (6 hits, no ambiguity) with a plain repo-wide grep, which is a
   capability Sonic's tool loop didn't have — only a lossy remote index.
2. `TOOL_AGENT_MAX_ITERATIONS = 7` (14 with deep thinking) is tuned for "find
   this one function," not "confirm every candidate call site before claiming
   completeness" — a real audit needs one search plus a `trace_symbol`/
   confirmatory call per candidate site, which the flat budget doesn't leave
   room for.

**Fix shipped:**
- **`search_literal`** (new tool_agent_node action, `agent_workflow.py`):
  fetches the real file tree (`_fetch_repo_tree_items`, extracted from the
  existing `_fetch_repo_paths`) and actually fetches+greps candidate blob
  content via the Git Blobs API — slower (one request per candidate file, capped
  at `_SEARCH_LITERAL_MAX_FILES_SCANNED`) but exhaustive by construction instead
  of index-based. Skips binaries/lockfiles/oversized files
  (`_SEARCH_LITERAL_SKIP_EXTENSIONS`, `_SEARCH_LITERAL_MAX_FILE_BYTES`). Menu
  text explicitly tells the model to reach for this over `search_code`
  specifically before claiming "every place X is used" is complete.
- **`_is_audit_style_task`**: a keyword-triggered detector (same mechanical
  pattern as `_mentions_visual_inspection`/the capability-denial watchlist —
  prose alone doesn't stick) that recognizes "scan the repo," "every file,"
  "plan a refactor," etc. in the user's own message and grants the same
  `deep_thinking` step/nudge budget regardless of whether deep thinking is
  actually toggled on.
- Both are mechanical, not prompt-only, per this session's established pattern:
  a real trace showed prose guidance alone doesn't reliably change behavior once
  the model has momentum toward a plausible-sounding answer. 8 new tests in
  `backend/tests/test_tool_agent_node.py`.

**Still on hold:** Section 4 itself. This readiness check was a useful, cheap way
to test "is Sonic actually proposing correct solutions yet" without committing to
the real migration — the honest answer today is not yet, but the two gaps found
are now fixed, and the same test should be re-run before starting Section 4 for
real.

---

## 4c. Second opinion (external Claude) on "Architecture Discovery," and today's increment

Independently asked a separate Claude session (outside SAAPP, with real local
file access) to review the repo and diagnose what caps multi-file "architecture
discovery" tasks — the same failure shape as 4b. Notably more accurate than the
per-user-token plan: every specific line number it cited (`run_react_loop` at
`agent_workflow.py:2351`, `TOOL_AGENT_MAX_ITERATIONS` at line 60,
`_is_audit_style_task` at line 2882, `search_literal` at line 3246,
`_build_definition_index` at line 146) checked out exactly against the real
file — it had actually read the code rather than gone through a lossy search
tool. Real findings, cross-checked before acting on any of them:

- `search_literal` (built for 4b) is opt-in — the model still has to choose it
  over `search_code` mid-loop. Same "capability exists but must be invoked"
  trap as `find_file` sitting unused in Section 0.
- No persistent/per-turn architecture map — proposed auto-injecting a
  stdlib-`ast`-based import graph (Python) + regex import extraction (TS/JS),
  gated on `_is_audit_style_task`, spliced into the prompt the same way
  `schema` already is — mechanical injection instead of a new opt-in tool,
  consistent with this project's own proven lesson.
- `_fetch_repo_tree_items()`/`_read_file()` had no caching — a multi-step
  investigation could re-fetch the same tree or file multiple times in one turn.

A second follow-up round (comparing its own investigation process to
`tool_agent_node`'s) proposed four more ideas, evaluated on their merits rather
than adopted wholesale — one contained a false claim (`asyncio.gather` "which
the codebase already uses elsewhere" — grepped, it appears nowhere):

1. **Real git checkout instead of per-action GitHub API calls.** Diagnosis (API
   round-trip cost, GitHub's lossy search index, rate limits) is correct, but
   its own risk comparison to `run_snippet` doesn't hold up — `run_snippet`'s
   whole design point was keeping execution risk on a disposable CI runner with
   zero shared state on SAAPP's own host; a git-cloned repo "cached per
   session" is shared-tenant state on SAAPP's own disk, which is a different
   (not smaller) risk shape, and exactly what Section 4's own notes already
   flagged about changing SAAPP's threat model. **Decision: hold a
   session-cached/persistent version until it can be co-designed with Section
   4's per-user isolation, not before.** A per-turn-only ephemeral clone
   (`tempfile.mkdtemp()`, cleaned up in a `finally` like `browser_session_holder`
   already is) would be the safe version if this is revisited sooner.
2. **Batch independent read-only actions per ReAct step** (concurrent
   dispatch, one step instead of N for independent file reads). Real budget
   relief with no new attack surface — but `run_react_loop`'s per-step
   bookkeeping (stuck-action tracking, `unretried_inconclusive_tools`) assumes
   one action per step, so this needs real re-threading, not just adding
   `asyncio.gather`. **Decision: next up after the architecture map**, restricted
   to read-only actions only (never batch `run_snippet`/`run_mongo_query`/writes).
3. **Compact old ReAct attempts** past a certain step count. Reasonable, but
   doing it via an LLM summarization pass (as proposed) reintroduces the exact
   fabrication risk this whole roadmap fights — a lossy AI-generated summary
   silently dropping a detail needed later. **Decision: if built, deterministic
   truncation (keep last N attempts verbatim, older ones reduced to tool name +
   paths touched), not another LLM call.** Not urgent — only matters once
   14-step deep traces are actually happening and getting noisy in practice.
4. **Fan-out/fan-in parallel mini sub-loops** per candidate area. Most
   speculative of the four — real added cost (N parallel LLM loops per turn)
   and coordination complexity. **Decision: held entirely**, revisit only if
   the cheaper fixes above prove insufficient.

**Shipped today** (the two zero-risk items, done before committing to the
larger architecture-map work):

- **Per-turn caching**: `_fetch_repo_tree_items()` and a new `_fetch_file_content()`
  (extracted from `_read_file`, returns raw decoded content with no URL/truncation
  formatting — also the clean primitive the future AST map should build on)
  are now memoized in dicts scoped to one `tool_agent_node` call, same lifetime
  as `browser_session_holder`. Never persisted across turns, so it can't go
  stale the way a session-level cache could. 2 new tests confirming a repeated
  path/tree fetch only hits GitHub once per turn.
- **Verified `_is_audit_style_task` against real phrasing** instead of just
  hand-written test cases — it does fire on the verbatim original failing
  prompt (typo included), but missed "how many files touch X" entirely; added
  that pattern. Deliberately did NOT broaden it to bare "refactor" — that would
  bump the budget for small, single-file refactors too, against the "zero
  added cost for normal turns" goal. 4 new tests, including one asserting the
  bare-"refactor" case stays negative on purpose.

**Also shipped this session — the AST-based architecture map itself (done):**

- New module-level, independently-testable functions in `agent_workflow.py`:
  `_extract_python_imports` (real `ast.parse`, walks `Import`/`ImportFrom`,
  handles relative imports including the `from . import utils` case where the
  reference lives in the imported names rather than `node.module`),
  `_extract_js_imports` (regex — `import x from '...'`, bare `import '...'`,
  `require('...')`, matching this repo's existing `_classify_symbol_line`
  precedent for TS/JS), `_is_internal_python_import`/`_repo_top_level_segments`
  (filters out stdlib/third-party noise like `os`/`requests`/`react` from the
  reverse map — derived from the real tree every time, never hardcoded, so it
  works on any repo), and `_build_architecture_map` (the forward file→imports
  and reverse module→imported-by graph, built from real fetched file content —
  deliberately takes `fetch_content` as a plain parameter rather than closing
  over turn state, so it's testable with a fake function and no GitHub mocking
  at all).
- Wired into `tool_agent_node`: gated on `_is_audit_style_task`, built from the
  same cached `_fetch_repo_tree_items()`/`_fetch_file_content()` from the
  caching work above (so this feature made that one directly useful, not just
  faster), and injected via a new `{architecture_map}` placeholder in
  `TOOL_AGENT_PROMPT` (`backend/components/constraints.py`) — empty string on
  every ordinary turn, so zero added prompt size/cost outside audit-style tasks.
  `run_react_loop` gained a matching `architecture_map: str = ""` parameter
  (its only real caller is `tool_agent_node`, so this was a safe, low-risk
  signature change). A `trace_detail` event ("Building architecture map...")
  fires while it's being built, matching this session's own established rule
  that a loading state should describe what's actually happening.
- Caught and fixed a real bug in my own first pass while writing its tests:
  `from . import utils` was producing a bare `"."` (module is `None` for this
  form; the actual reference lives in the imported names) instead of `.utils`
  — a good concrete reminder that even code written specifically to fix a
  fabrication problem needs the same "verify, don't trust the first plausible
  version" treatment.
- 13 new tests: pure-function coverage for both languages' import extraction,
  the internal/external filter, the forward+reverse graph builder (including
  skipping fetch errors and files with only external imports), and two
  `tool_agent_node`-level integration tests confirming the map is actually
  injected for an audit-style prompt and stays empty for an ordinary one.
- Full suite: 401 passed, same pre-existing unrelated `test_voice_composer.py`
  failure.
- **Not yet done:** a live end-to-end browser verification (the pattern used
  for every other feature this session) — the local backend the frontend
  actually talks to on `localhost:8000` runs in a terminal outside this
  session's visibility, the same gap hit earlier verifying the Stop-button
  feature's backend half. Unit/integration coverage is solid, but this hasn't
  been watched happen against a real repo yet.

**Next up:** batched independent read actions (point 2 of the second review).

---

## 4d. Tried to have SAAPP self-drive the batching feature — hit a real capability gap

Asked SAAPP (production chat) to investigate and implement the batching
feature, ending with "open a PR when you're done" — it just kept retrying PR
creation without ever producing code. Root cause, confirmed by reading
`_draft_create_pr`/`_execute_create_pr` directly: `create_pr` has no
mechanism to create a branch or commit real file changes at all — it only
computes a diff between a `head_branch`/`base_branch` that must **already
exist** (defaulting to a literal, non-existent `"feature-branch"` if
unspecified) and asks an LLM to write a title/body *describing* that diff.
It's a "describe my already-pushed branch as a PR" tool, not an "implement
this and commit it" tool — so the experiment was structurally blocked
regardless of SAAPP's actual planning/coding quality.

**Decision:** don't build "give SAAPP real code-commit capability" reactively
right now — it's a much bigger trust/blast-radius jump (autonomous
self-modification of its own repo) than anything shipped this session, and
deserves the same deliberate co-design treatment as Section 4's per-user
token work, not a quick patch. For now, the self-drive test continues by
asking for code directly in the response instead of a PR (see 4e below,
which is what that redirected prompt actually surfaced).

---

## 4e. Real production trace — false-positive repo extraction cascaded into a 10-step 404 loop (both fixed)

Tried the redirected prompt from 4d above — investigate `run_react_loop` and
show the code changes directly instead of opening a PR. Real production logs:

```
WARNING - Could not fetch repo metadata for 'functions/diffs' (status 404)
INFO - Resolved repo='functions/diffs', default_branch='main'
INFO - Step 1 ... action=list_repo_tree() | observation='ERROR: could not fetch tree (404)'
INFO - Step 2 ... action=find_file(query=tool_agent_node) | observation='ERROR: could not fetch tree (404)'
INFO - Step 5/7/9 ... action=list_repo_tree() | observation='ERROR: could not fetch tree (404)'  [x3, reworded purpose each time]
ERROR - step 10 failed to produce a usable decision. [504 Gateway Timeout from the LLM API]
```

**Root cause 1 (the actual bug):** `extract_github_repo` read the literal
phrase "the updated **functions/diffs** for whatever needs to change" — an
entirely ordinary bit of English, not a repo mention — as owner/repo
`functions/diffs`, past its own `_GENERIC_PATH_SEGMENTS` denylist (this exact
false-positive class was already fixed once before, for "in
backend/services" — see `extract_github_repo`'s own docstring; a denylist can
never cover every ordinary word pair with a slash in it). Every GitHub action
that turn then targeted a repo that doesn't exist, 404ing consistently, with
`_get_default_branch`'s old behavior silently keeping the bad repo and only
defaulting the *branch* to `"main"` — the model had no way to ever learn the
real problem was the repo itself, not a missing file/path.

**Root cause 2 (why it didn't recover):** `TOOL_AGENT_STUCK_ACTION_REDIRECTS`
only ever covered `search_code` (the original Failure A). `list_repo_tree` —
a no-argument action — had no mechanical backstop at all, so the model kept
calling it again with a differently-worded `purpose` each time
("transient?", "one more time", "one last time") — technically satisfying
"try something different" without being able to change anything real, since
a no-arg action has nothing to vary. Two independent real instances of this
exact shape (search_code, now list_repo_tree) is real evidence for
generalizing rather than hand-authoring a third entry later.

**Fixed, both in `agent_workflow.py`:**
- `_get_default_branch` now returns `None` (not a guessed `"main"`) on a
  failed repo-metadata fetch. The caller retries once against the pinned/
  default repo (`state["repo"]` or `SummonShenron/SAAPP`) if it differs from
  the one that just failed, and surfaces a plain `NOTE`/`WARNING` into
  `schema` either way — so the model sees "the repo you might have meant
  doesn't exist, I used X instead" (or an honest "this repo may be
  inaccessible") instead of a silent, undiagnosable string of 404s. 3 new
  tests.
- `TOOL_AGENT_STUCK_ACTION_REDIRECTS`'s lookup now falls back to a generic
  `_DEFAULT_STUCK_ACTION_THRESHOLD`/`_DEFAULT_STUCK_ACTION_MESSAGE` (3
  consecutive misses) for any tool_action not explicitly listed, instead of
  giving unlisted tools no backstop at all. A listed entry (like
  search_code's) still wins when present, since it can give more targeted
  advice than the generic message. 1 new test (`list_repo_tree` stuck-loop,
  reproducing the exact production shape).
- Full suite: 405 passed, same pre-existing unrelated `test_voice_composer.py`
  failure.

**Noted, not fixed (real, but bounded/minor):** a no-arg action's
`args_signature` never changes, so `unretried_inconclusive_tools` can never
self-clear for it — the ordinary retry-nudge will reject one "final" attempt
near the end of the turn even after the model has since done unrelated
productive work, since that tool's flag just sits there for the rest of the
turn. Costs at most one extra step (bounded by `max_retry_nudges`), not a
budget-destroying loop like the two fixed above — left alone per the
reactive-fixes-only discipline until it actually causes a real problem.

**Also surfaced, unrelated to the above — and now fixed too (see 4f):** a 504
Gateway Timeout from the underlying Gemini API crashed the node with an
unhandled exception rather than a graceful partial-answer recovery. First
observed instance was logged here as "not acted on yet" — it recurred
immediately on the very next real trace (4f) and directly caused a fully
fabricated answer to ship, which was enough real repeated evidence to act on.

---

## 4f. `LazyLLM.ainvoke` had zero graceful-degradation handling — a 504 crashed the loop into fabricating a full fake implementation (fixed)

Redirected the batching self-drive prompt (per 4d/4e) to skip the broken
`create_pr` flow and just show code directly. What came back was a
confidently-detailed, fully fabricated `run_react_loop` rewrite — wrong
function signature, wrong return shape, references to module-level functions
that don't exist (`search_code`/`read_repo_file` as importable names, when
they're actually closures nested inside `tool_agent_node`), a fabricated
`constraints.py` diff whose hunk content doesn't match the real file, and a
test with a literal typo (`return_return_value`) that would silently no-op.
It also deleted every mechanical fix built this session (stuck-action
redirects, retry-nudge tracking, capability-denial watchlist, the
architecture map, the per-turn cache) without mentioning any of them.

**But the real production logs told a different, more useful story than "it
ignored real code":** it genuinely investigated first —
`list_repo_tree()` → `search_code(query=tool_agent_node)` →
`read_repo_file(path=backend/services/agent_workflow.py)` — and that last
call really did fetch the live file (the observation's first lines match the
real current imports exactly). But `agent_workflow.py` is ~4,300 lines and
`read_repo_file` truncates to `_READ_FILE_CHAR_CAP` (3500 chars) by design —
nowhere near reaching `run_react_loop`'s real definition (~line 2350). It
needed one more `read_repo_file(..., start_line=2350)` call to ever see the
real loop. It never got the chance: step 4 hit a 504 Gateway Timeout from
Gemini's own API, an unhandled exception that broke the loop immediately and
forced the "you're out of steps — synthesize honestly, never invent beyond
what you found" emergency prompt from just those 3 sparse attempts. Despite
that explicit instruction, it fabricated a complete, wrong implementation
anyway — the same core "prose says don't fabricate, it fabricates anyway"
lesson this roadmap keeps re-learning in new shapes, this time triggered by
an infra failure forcing a high-pressure synthesis rather than a normal
budget exhaustion.

**Root cause, found by reading `backend/models/models.py` directly:**
`LazyLLM` (the wrapper behind `lite_llm`/`lite_llm_deep`/every LLM singleton
in the app) only ever defined graceful-degradation handling on `.invoke()`
(sync) and `.astream()` (streaming) — `.ainvoke()` was never defined on the
class at all, so calling it fell through `__getattr__` straight to the real
LangChain client's own `.ainvoke()`, completely bypassing every bit of
`LazyLLM`'s protection. `run_react_loop` — the entire ReAct loop behind
`tool_agent_node` — calls exactly `.ainvoke()`. Compounding it: even the
existing protection only matched `"503"`/`"UNAVAILABLE"` in the error text,
never `"504"`/`"DEADLINE_EXCEEDED"`, the exact error this trace (and the 4e
trace before it) hit.

**Fixed in `backend/models/models.py`:**
- Extracted the transient-error check into a shared
  `_is_transient_llm_error(e)` (matches `"503"`, `"UNAVAILABLE"`, `"504"`,
  `"DEADLINE_EXCEEDED"`, `"Gateway Timeout"`) used by `invoke`, `astream`,
  and the new `ainvoke` — a pattern added once now protects every call path
  instead of three separately-drifting copies.
- Added `LazyLLM.ainvoke`, mirroring `invoke`'s existing try/except shape:
  on a transient error, returns the same graceful
  `"[System Note: AI model is experiencing high traffic...]"` `AIMessage`
  instead of raising. Back in `run_react_loop`, that non-JSON content just
  becomes one recorded failed step via the loop's own existing
  `_parse_agent_json` → `{}` → "model did not return a recognized action"
  path — the loop already knew how to recover from this, it just never got
  the chance because the exception never reached it as a normal step
  failure.
- New `backend/tests/test_models.py` (didn't exist before): 11 tests —
  transient-error detection (503/504/negative), `ainvoke`
  success/504/503/non-transient-reraise/dev-mode, and parity coverage for
  `invoke`/`astream`'s existing behavior now sharing the same helper.
- Full suite: 416 passed, same pre-existing unrelated `test_voice_composer.py`
  failure.

This also retroactively explains part of the 4e trace's 10-step 404 loop:
its step-10 crash was the exact same `ainvoke`-has-no-protection gap, just
on top of an already-exhausted budget from the repo-resolution bug — fixing
`LazyLLM` doesn't undo the need for that fix, but it means a transient API
blip won't independently end a turn early ever again.

---

## 4g. Cleanest fabrication evidence yet — confidently stopped early with 3 steps of budget still unused (fixed)

Gave SAAPP the same corrected batching prompt a third time, after the 4f fix.
No crash this time — the loop ran cleanly start to finish. And it still
produced the same generic, fabricated `run_react_loop` rewrite. This is
better evidence than 4e/4f, not worse: with the infra excuse gone, what's
left is a pure judgment failure with nothing else to blame.

Real production logs:
```
Step 1 (Locate the file...) — action=list_repo_tree()
Step 2 (Read the current implementation...) — action=read_repo_file(path=backend/services/agent_workflow.py)
    | observation='URL: .../agent_workflow.py\nfrom __future__ import annotations\nimport ast\n...'
Step 3 (Locate the run_react_loop function definition...) — action=search_code(query=def run_react_loop)
    | observation='docs/coding-agent-roadmap.md\nbackend/services/browser_tool.py\n...\nagent_workflow.py\n...' [paths only, no line content]
Step 4: accepted final answer after 3 real action(s) — [fabricated implementation]
```

Not audit-style-detected (this prompt doesn't match `_is_audit_style_task`),
so the ordinary 7-step budget applied — and it stopped at step 4 with 3 full
steps still unused. Step 2's `read_repo_file` truncated at
`_READ_FILE_CHAR_CAP` (3500 chars) — nowhere near `run_react_loop`'s real
body (~line 2350) — and its own truncation note explicitly says "do not
assume the file's contents past this point... call read_repo_file again with
start_line." Step 3's `search_code` only ever returns file *paths*, never
matched-line content, so it added no new information at all — pure
confirmation of something already known. The model never made the one
obvious follow-up call (`read_repo_file(..., start_line=2350)`) and instead
confidently fabricated a complete implementation, in direct violation of
the truncation note's own explicit instruction — voluntarily, with plenty of
budget left and no external pressure forcing the stop.

**Fixed in `agent_workflow.py`**, reusing existing machinery rather than
building new tracking: `run_react_loop`'s `still_failing` check (which
already treats an `ERROR:`-prefixed or empty observation as "this step
didn't get you anywhere," gating the retry-nudge that rejects a premature
`final`) now also treats a truncated-and-unresolved `read_repo_file` result
the same way, via a new `_mentions_unresolved_truncation` helper matching
`_read_file`'s own truncation marker text. A follow-up `read_repo_file` call
with a real `start_line` has a genuinely different `args_signature`, so it
clears the flag through the exact same "was this a genuine retry" logic an
ERROR or empty result already uses — no new tracking dict, no new mechanism,
just widening what already-proven code considers "inconclusive." 5 new
tests (helper coverage for both truncation-note variants plus a negative
against the unrelated `_truncate_observation` marker, and an integration test
reproducing the exact scenario: premature `final` after a truncated read gets
rejected once, a real `start_line` re-read is required, then the second
`final` is accepted). Full suite: 419 passed, same pre-existing unrelated
failure.

**Why this one crossed the bar for the claim-grounding idea held earlier:**
the standing rule this session has held to is that speculative hardening
waits for real evidence, not a hunch about what might break next. This is
now three real instances of confident fabrication under insufficient
grounding (4b's plan, 4e/4f's crash-forced synthesis, this clean voluntary
stop) — but the fix that shipped isn't the broad, fuzzy "verify every claim"
backstop discussed and deferred back then. It's a narrow, mechanical,
three-line extension of an already-existing, already-tested check, with a
precise trigger (a specific marker string) and no fuzzy-matching risk —
proportionate to the evidence rather than a generalized answer to a still-
underspecified problem.

---

## 4h. The 4g fix had a real gap — two more, found by trying again for real

Gave SAAPP the corrected prompt a fourth time. Real production logs:

```
Step 1 — action=list_repo_tree()
Step 2 — action=read_repo_file(path=backend/services/agent_workflow.py)  [truncated, no start_line]
Step 3 — action=search_code(query=def run_react_loop)  [paths only, no new info]
[Step 4 missing from the log — the 4g fix correctly rejected a premature "final" here]
Step 5 — action=read_repo_file(path=..., start_line=400)  [still incomplete — file is 4552 lines]
Step 6: accepted final answer  [fabricated again]
```

The 4g fix genuinely fired (step 4's absence proves it) and the model
responded exactly right — retried with a real `start_line`. But `start_line=400`
landed nowhere near `run_react_loop` (~line 2350), the read was still
incomplete, and step 6 fabricated anyway with zero further resistance. Two
real gaps, found by testing the actual fix instead of assuming it was done:

1. **`_mentions_unresolved_truncation` only covered two of three truncation
   message shapes.** `_read_file` has a third: a *windowed* read (one with
   `start_line` already set) that still has content remaining below emits
   "N more lines below — re-call with a higher start_line," completely
   different wording from the "whole file too long" variants. That's exactly
   the shape a forced retry produces, so it was invisible to the check that
   exists specifically to catch forced retries. Added it to the same helper.
2. **A deeper bug in the existing retry-nudge logic itself**, only visible
   once a real multi-page file was involved: the "one genuine retry earns a
   pass" rule (documented, deliberate, correct for a truly doomed
   ERROR/empty action) silently *cleared* the flag on the first different-args
   retry and never re-armed it — even when that retry was itself still
   incomplete. A confirming test with `deep_thinking=True` (3 allowed
   rejections) proved it: the second "final" sailed through completely
   unblocked after only one retry, because the flag was gone. An
   unresolved-truncation observation isn't "doomed" the way an error is —
   it's real, ongoing progress — so it now always re-arms the flag instead of
   taking the one-pass-and-clear route, keeping the nudge live until a read
   of that path genuinely reaches the file's end.

**A third thing had to be gotten right at the same time**: the generic
stuck-action backstop (4e) tracks consecutive misses on one tool_action
regardless of args, with a default 3-miss threshold. Once truncation started
correctly re-arming the retry-nudge on every incomplete page, a genuinely
huge file needing 4+ sequential `read_repo_file` calls would trip that
*separate* mechanism and get outright rejected — punishing exactly the
correct behavior (reading further into the same file) as if it were the same
doomed call repeating. Fixed by excluding an unresolved truncation from the
stuck-streak update entirely (treated like a success there) while still
requiring it to re-arm the retry-nudge — the two mechanisms now agree that
"different start_line each time" is progress, not stuckness.

**Fixed, all in `agent_workflow.py`:**
- `_mentions_unresolved_truncation` now also matches the windowed-read
  "more lines below" marker.
- The retry-nudge tracking splits unresolved-truncation from ERROR/empty:
  truncation always re-arms `unretried_inconclusive_tools`; ERROR/empty keep
  the original one-genuine-retry-clears-it behavior.
- The stuck-action streak update now excludes unresolved-truncation misses,
  so legitimate multi-page reads of one large file never trip it.
- 3 new tests: the missing truncation-message variant, a `deep_thinking=True`
  end-to-end reproduction of the exact real bug (two rejections in a row,
  each forcing real pagination progress, only accepted once a read
  genuinely reaches the file's end), and 5 sequential paginated reads of a
  simulated 650-line file confirming the stuck-action backstop never fires
  despite far exceeding its own threshold.
- Full suite: 422 passed, same pre-existing unrelated failure.

**The pattern worth naming**: this fix only reached its correct, final shape
by actually testing it against SAAPP's real behavior a second time rather
than considering 4g done once its own unit tests passed. The first version's
tests only exercised a single rejection (the non-deep-thinking default,
`max_retry_nudges=1`), which happened to never expose the "clears and never
re-arms" bug — it took the real multi-rejection, multi-page trace to surface
it. Worth remembering next time a fix's own tests all pass: passing tests
prove the fix does what the tests check, not that the tests check the right
thing.

---

## 4i. `LazyLLM`'s transient-error detection was too narrow a second time — a bare `TimeoutError` (fixed)

Tried again after 4h. This time the model correctly pushed further (step 3's
`search_code` genuinely found nothing new, matching the codebase's real
layout — no `find_file`/`trace_symbol` fallback attempted, a real but
separate quality gap not chased here) before crashing at step 7 with a raw
`TimeoutError` from aiohttp's own internal request timer, chained from an
`asyncio.CancelledError` — a different exception shape than 4f's descriptive
"504 Gateway Timeout" text. `LazyLLM.ainvoke` (built in 4f) correctly ran and
called `_is_transient_llm_error`, but that check is purely text-based
(`"503"`/`"504"`/etc. substrings), and `str(TimeoutError())` is typically
empty — nothing to match, so it correctly-per-its-own-logic re-raised.

**Fixed in `backend/models/models.py`:** `_is_transient_llm_error` now also
checks the exception's *type* (`TimeoutError`, `ConnectionError`), not just
its text, specifically because an exception with no message can't be caught
by string matching no matter how many markers are added. Confirmed this is
safe to treat as gracefully-degradable rather than a real error: this
`TimeoutError` originates from google-genai's own HTTP client giving up on a
slow request, not from this app's Stop-button cancellation path (built
earlier this session), which goes through `Request.is_disconnected()`
polling and an explicit generator `.aclose()` — never through an exception
raised out of `llm.ainvoke()` itself — so this fix cannot suppress a genuine
user-initiated stop. 4 new tests (type-based detection for both new types,
a negative case confirming an unrelated empty-message exception like a bare
`ValueError`/`RuntimeError` isn't swept in just because it also has no text,
and an `ainvoke`-level integration test for the bare `TimeoutError` case).
Full suite: 425 passed, same pre-existing unrelated failure.

Two real, differently-shaped transient-error crashes in two consecutive
attempts (4f's descriptive 504, this bare `TimeoutError`) is a reasonable
signal that "the LLM call itself failed for infra reasons" is a real,
recurring category here — worth remembering if a third shape shows up,
rather than continuing to add one marker/type at a time indefinitely.

---

## 4j. The 4h fix's budget was the real limiter — it can outlast even deep thinking's retry allowance (fixed)

Tried again after 4i. This attempt showed genuinely better investigative
behavior than any before it — reading the real test file, dispatching the
actual CI suite for a baseline (`run_repo_tests`, which failed with a
`ModuleNotFoundError` — a separate, real finding not chased here; worth
sharing more of that log if it recurs), and even attempting a `run_snippet`
verification (though that snippet tested an isolated, invented
proof-of-concept unrelated to the real codebase — "verification theater"
that looks like real verification but proves nothing about the actual
change, a distinct quality gap noted but not fixed here). Steps 7-9 are
missing from the log — 3 rejections in a row, matching deep thinking's full
`TOOL_AGENT_MAX_RETRY_NUDGES_DEEP` budget — and then step 10 fabricated the
same answer anyway.

Confirmed by direct reproduction (not guesswork) that 4h's own fix correctly
rejects this exact action sequence when tested in isolation — so the gap
wasn't in the truncation *detection*, it was in the budget: 4h's "always
re-arm" fix made the flag persistent, but the rejection that checks it was
still gated by `retry_nudge_count < max_retry_nudges`, shared with the
ERROR/empty case. The model never once called `read_repo_file` with a
`start_line` on any of the 3 files it had left truncated (`agent_workflow.py`,
`constraints.py`, the test file) — it just kept re-submitting "final" itself
three times, exhausted deep thinking's entire rejection allowance doing
nothing to fix the actual problem, and walked through unblocked on the 4th.

**Fixed in `agent_workflow.py`**: split truncation-driven rejection from
ERROR/empty-driven rejection entirely. A new `truncated_unresolved_tools`
set tracks which tool_actions have a live, unresolved truncation; a "final"
is rejected **unconditionally** (no `retry_nudge_count` check at all) while
that set is non-empty, since — unlike an ERROR that might be a genuine dead
end deserving a bounded budget before an honest "I couldn't" is accepted —
a truncated file is never actually a dead end; the rest of it is right
there. Also strengthened the nudge text specifically for this case: it now
names the actual unresolved path (parsed back out of the tracked args
signature) and states plainly that this rejection has no limit, instead of
sharing the generic "one or more actions failed" message with ordinary
errors. `forced_final` (the last iteration) is still the one unconditional
escape hatch, so the loop still always terminates.

1 new test reproducing the exact real shape: 4 premature "final" attempts in
a row (deliberately more than even deep thinking's 3-rejection budget)
against a single still-truncated file, all rejected, only accepted once a
real `start_line` read actually reaches the end — with `max_retry_nudges=1`
explicitly set, proving the budget genuinely doesn't apply here at all.
Updated one earlier test whose second retry (`start_line=260`) turned out to
still leave 100 lines unread in its own fixture — previously invisible
because that rejection budget silently absorbed it; now correctly required
to actually reach the file's end. Full suite: 426 passed, same pre-existing
unrelated failure.

---

## 5. Batched independent actions in the ReAct loop (done — built directly, not via self-drive)

After the extensive 4b-4j diagnostic loop kept surfacing real infrastructure
gaps rather than a working batching implementation (4d's capability gap, 4e's
repo-resolution bug, 4f/4i's `LazyLLM` gaps, 4g/4h/4j's truncation-fabrication
chain), built this one directly instead of continuing the self-drive
experiment — the diagnostic value had been thoroughly extracted, and the
feature itself (a real re-threading of `run_react_loop`'s core state
machine) was worth building carefully rather than iterating against
SAAPP's own attempts further.

**Design, in `backend/services/agent_workflow.py`:**
- New `TOOL_AGENT_BATCHABLE_ACTIONS` — a frozenset restricted to genuinely
  side-effect-free, independent lookups (`list_repo_tree`, `read_repo_file`,
  `search_code`, `find_file`, `trace_symbol`, `search_literal`,
  `diff_branches`, `list_commits`, `web_search`, `run_python`).
  `run_mongo_query`/`run_repo_tests`/`run_snippet` (writes or real slow CI
  dispatches) and every `browser_*` action (inherently stateful/sequential —
  a click depends on whatever page a prior navigate loaded) are excluded
  regardless of what a caller passes.
- `run_react_loop` gained an optional `batchable_actions: frozenset | None`
  parameter. A "query" step can now submit `{"queries": [{"tool_action",
  "args", "purpose"}, ...]}` instead of one `tool_action`/`args` pair — each
  item runs concurrently via `asyncio.gather`.
- The critical design constraint: **a batch must never get weaker mechanical
  scrutiny than the equivalent sequential steps would have** — the exact
  bookkeeping this whole diagnostic loop hardened (retry-nudge/
  `unretried_inconclusive_tools` tracking, unresolved-truncation tracking,
  the stuck-action streak) is applied once per item in a batch, in order,
  via a `_record_action_result` helper extracted (behavior-preserving,
  confirmed by the full suite passing unchanged before any new code was
  added) from what was previously inline single-action logic. A stuck tool
  slipped into a batch rejects the *entire* batch (not just that one item) —
  otherwise the model could route around a stuck-action redirect by hiding
  the stuck call alongside legitimate ones. A non-batchable action inside
  `queries` is rejected individually with a synthetic `ERROR: ... cannot be
  batched` observation (which, same as any other ERROR, correctly earns its
  own ordinary retry-nudge — no special-casing). A batch capped at
  `_MAX_BATCH_SIZE` (5) — excess items get their own "too many actions,
  retry in a later step" observation rather than unbounded concurrent GitHub
  calls.
- `backend/components/constraints.py`'s `TOOL_AGENT_PROMPT` documents
  `"queries"` as an alternative to `tool_action`/`args`, with a new
  `{batchable_actions}` placeholder spliced in from the real
  `TOOL_AGENT_BATCHABLE_ACTIONS` constant (not hand-copied text) so the
  prompt can never drift from what's actually enforced.
- 8 new tests: real concurrency (three actions that each sleep 0.2s finish
  in well under the 0.6s sequential time), non-batchable action rejected
  while its batch-mates still execute, a stuck tool anywhere in a batch
  rejects the whole thing, a truncated read inside a batch still triggers
  the unconditional (no-budget) rejection from Section 4j, a single-item
  `queries` list executes correctly (it puts the real action inside the
  list item, not at the top level — a real gap caught before it shipped),
  batching is fully inert when a caller doesn't pass `batchable_actions`
  (backward-compatible default), the batch-size cap, and one full
  `tool_agent_node`-level integration test with real (mocked) GitHub calls
  proving the prompt-splicing and constant-wiring work together end to end.
- Full suite: 435 passed, same pre-existing unrelated `test_voice_composer.py`
  failure.
- **Not yet done**: live end-to-end verification against the real deployed
  backend (the same recurring gap noted for the architecture map in Section
  4c and the Stop-button feature earlier) — verified thoroughly at the unit/
  integration level with real mocked GitHub responses, but nobody has
  watched a real production turn actually emit and execute a `"queries"`
  batch yet.

**Next up:** per the original ask, try having SAAPP self-drive a smaller,
better-scoped backend change now that the repo-resolution, transient-error,
and truncation-fabrication infrastructure gaps this diagnostic loop found are
fixed — see whether a properly-scoped task succeeds where the batching
feature (a genuine core-loop rewrite) kept hitting real gaps instead.

---

## 6. Second self-drive attempt: search_literal opt-in nudge — a new, worse fabrication shape (fixed, built directly)

Gave SAAPP a smaller, well-scoped task per 5's "next up": `search_literal` is
still opt-in (4c's finding) — nudge audit-style tasks toward it, reusing
whatever existing "detected task-type → inject something onto the turn"
mechanism already exists in this codebase, rather than building a new one.
The prompt deliberately didn't say where that mechanism lives, matching this
session's established "point at the problem, let it investigate" style.

**What came back, and what's actually true, checked line-by-line against the
real file:**

- SAAPP opened by declaring `_is_audit_style_task` "does not exist in the
  repository yet... only a conceptual design note." It has existed, real and
  tested, since Section 4c — `agent_workflow.py:3063`, with 6 passing tests in
  `test_tool_agent_node.py`. This isn't a stale claim from old training data;
  it had just been told to go investigate this exact file.
- The real mechanism it was asked to find already exists and does exactly
  what was wanted: `agent_workflow.py:3884-3899` already gates building an
  `architecture_map` string on `is_audit_task` and splices it straight into
  the prompt via `run_react_loop(architecture_map=...)`. SAAPP walked past it
  and instead pointed at `classify_intent`/`build_agent_plan` — real
  functions, but they route between graph nodes and have nothing to do with
  tool selection inside one ReAct loop.
- Its fix injects into `state["agent_scratchpad"]` — not a real field.
  `GraphState` (`backend/state/graph_state.py`) has no such key, checked
  exhaustively against the full TypedDict.
- Its own proposed test would crash before testing anything: `tool_agent_node`
  is `async def` (called synchronously, no `await`), and the state dict it
  constructs is missing nearly every field `tool_agent_node` reads before
  reaching the code path in question (schema, repo resolution, prompt
  templating).
- The "clean diff" is fabricated hunk content against invented line numbers —
  same shape as 4f/4g/4h, describing a plausible-looking patch rather than
  deriving one from the real file.

**The new failure shape worth naming:** every prior fabrication (4f-4j)
invented code that doesn't exist. This one is the inverse and arguably worse —
it actively asserted that real, live, already-tested code *doesn't* exist,
got it backwards rather than just under-informed. Had this been pasted in
uncritically, `_is_audit_style_task` would have been silently redefined,
colliding with its tested version.

This was a review-and-reject, consistent with "we just audit and review the
code it suggests, we don't paste it in unchecked." Given how small the real
fix turned out to be once the actual precedent was identified, built it
directly rather than retrying the self-drive a third time.

**Fixed in `agent_workflow.py`:** a new `_AUDIT_TASK_SEARCH_NUDGE` constant,
and the existing `is_audit_task` block (~line 3887) now sets
`architecture_map = _AUDIT_TASK_SEARCH_NUDGE` up front and appends the real
map to it (`+=`) once built, instead of starting from `""`. This decouples
the nudge from whether the map itself successfully builds — the model should
be told to prefer `search_literal` even when the tree fetch fails or the repo
happens to have no internal imports to map. No new state field, no new
prompt placeholder — rides the exact `{architecture_map}` splice point
already proven safe by the architecture-map feature itself. 2 new tests:
the nudge is present when the tree is empty (map itself is `""`), and the
nudge is present even when the tree fetch fails outright (404) — the two
cases that would have silently dropped it if the nudge were appended after
the map instead of before it. Full suite: 437 passed, same pre-existing
unrelated `test_voice_composer.py` failure.

---

## 7. Third self-drive attempt: "verification theater" in run_snippet — a third fabrication shape (fixed, built directly)

Gave SAAPP a smaller task per 6's methodology, this time with an explicit
guardrail added to the prompt after 6's failure: don't assert something
doesn't exist without actually running a real search for it first and citing
a genuine empty result. Asked it to fix `run_snippet`'s "verification
theater" gap (4j) — a snippet that "verifies" a change by testing an
invented, isolated stand-in instead of the real modified code.

**What came back, checked line-by-line against the real files:**

- It correctly found and accurately quoted two real existing prose rules in
  `constraints.py` (the "don't treat an empty search as proof of absence"
  rule and the "browser task first, source-reading second" rule) — genuinely
  read the file this time, roughly the right line range, no guardrail
  violation on those.
- But the actual fix invents a `CONSTRAINTS = {...}` dictionary keyed by tool
  name, edits `CONSTRAINTS["run_snippet"]`, and writes a test that does
  `from backend.components.constraints import CONSTRAINTS`. No such name
  exists anywhere in this repo — confirmed by a repo-wide search, not just
  one file. `run_snippet`'s real menu text is built inline inside
  `agent_workflow.py`'s dynamic `menu_lines.append(...)` construction
  (~line 3736), gated on admin status; `constraints.py` holds one long
  `TOOL_AGENT_PROMPT` string, never a per-tool dict. The diff and test both
  target a structure that was never there to begin with.

**A third distinct fabrication shape, worth naming alongside the other two:**
4f-4j invented code that doesn't exist; 6 asserted real code doesn't exist;
this one invents a plausible-sounding *data structure* that was never
verified to exist, then edits and tests it as if it were real — the common
thread across all three is the same root cause (composing a plausible answer
instead of running one more real read/search), just surfacing in a different
place each time. The new guardrail against denying real code's existence
didn't (and couldn't) catch this — it needed the opposite check: verify a
structure exists before editing it, not just before denying one.

**Fixed directly in `agent_workflow.py`** (review-and-reject, same as
Section 6): strengthened the real inline `run_snippet` menu text — it now
explicitly requires importing and calling the actual function/module being
verified, from its real path already read this turn, and states plainly that
a hand-rolled stand-in proves the stand-in works, not that the real change
does. Prompt-only, no new tool, no new tracking — matches the cheapest-shape
precedent (the idiom-matching rule) this task was asked to reuse. 1 new test
confirming the real admin-only menu text contains the anti-theater language.
Full suite: 438 passed, same pre-existing unrelated failure.

**Worth deciding before a fourth attempt:** three fabrication shapes in three
tries, all caught by review-before-paste rather than by anything mechanical
yet. That's the process working as designed, but it hasn't yet produced a
self-drive success on any of the three "smaller" tasks tried since batching.
Decision: treat "verify a structure exists before claiming to edit it" as its
own mechanical gap rather than another one-off prompt correction — see
Section 8.

---

## 8. Mechanical backstop for the third fabrication shape: ungrounded diffs (fixed, built directly)

Section 7's rejected diff fabricated a whole data structure (`CONSTRAINTS`)
that never existed, presented alongside genuine quotes from elsewhere in the
same file — confident-looking, thoroughly-researched-looking, and still
wrong. Unlike the capability-denial and stuck-action backstops, there was no
existing mechanism to extend here — this is a new one, same budget-limited
shape as the others.

**Built in `agent_workflow.py`:**
- `_extract_diff_file_grounding_lines(final_answer)` — parses any `diff --git
  a/PATH b/PATH` block in a "final" answer and, per file, collects every
  non-added line inside its hunks (context and removed lines — the lines the
  diff claims already existed before the change). A `new file mode` diff is
  skipped entirely — there's nothing pre-existing to verify for a brand-new
  file. String/regex based, not a real diff parser, matching this file's own
  accepted-soft-failure precedent (`trace_symbol`'s regex classifier,
  `find_file`'s fuzzy match).
- `_final_diff_disagrees_with_fetched_content(final_answer, attempts)` —
  for each file a diff touches, checks whether at least one of its claimed
  pre-existing lines actually appears in a real `read_repo_file` observation
  for that exact path recorded THIS turn (matched via the real
  `action_desc` format, `read_repo_file(path=...)`). Returns the first file
  path where none of the claimed lines were ever actually seen.
- `run_react_loop` gained a new budget-limited rejection gate (same shape as
  `capability_denial_watchlist`, no new parameter needed since this doesn't
  require caller-supplied domain knowledge): a "final" containing a diff
  that fails this check is rejected once, with a corrective notice naming
  the specific file and instructing a real `read_repo_file` call before
  trying again — not unconditional like the truncation gate, since a diff
  legitimately grounded in an earlier, out-of-loop part of the conversation
  must still get through eventually rather than loop forever on a false
  positive.
- 7 new tests: the real fabricated-`CONSTANTS`-diff shape rejected once then
  a corrected (really-grounded) diff accepted, budget-limited to one
  rejection, a false-positive guard (a diff genuinely grounded in a real
  read is accepted immediately), a brand-new-file diff never flagged
  regardless of prior reads, plus direct unit coverage of both new helper
  functions. Full suite: 444 passed, same pre-existing unrelated
  `test_voice_composer.py` failure.

**Known real limitation, accepted rather than solved:** this only grounds
diffs against files fetched via `read_repo_file` inside THIS SAME
`run_react_loop` call — a diff resting on real content read earlier in the
conversation (a prior turn, or `initial_attempts` from a resumed
clarification) is invisible to this specific check unless it also shows up
as `initial_attempts` this call already threads through. Matches the same
tradeoff already accepted for `unretried_inconclusive_tools` and the
stuck-action streak — both are also scoped to one call's own `attempts`,
not cross-turn memory. Revisit only if a real trace shows this scope is
actually too narrow, not preemptively.

---

## Operational — automatic checkpoint retention (done)

**Shipped:** `backend/services/checkpoint_retention.py` —
`prune_old_checkpoints(keep_per_thread=3)` runs the same keep-last-N-per-thread
logic used for the manual cleanup below, and `run_checkpoint_retention_loop()`
runs it on a daily interval (`CHECKPOINT_RETENTION_INTERVAL_SECONDS` env var,
default 24h) for the life of the process, resilient to a single failed pass.
Wired into `app.py`'s `lifespan` via the existing `spawn_background_task`
helper, cancelled cleanly on shutdown. 5 new tests in
`backend/tests/test_checkpoint_retention.py`. Original incident notes below,
kept for context.

**Incident this session:** the Atlas cluster hit its 512MB storage quota and
started rejecting all writes app-wide (new signups, uploads, memory saves —
anything). Diagnosed live: `saapp_database` (real app data — documents, memory
facts, conversations) was only ~23MB. **The other ~492MB — 96% of the entire
quota — was `checkpointing_db`**, LangGraph's checkpoint persistence. Nothing has
ever pruned it, so every single turn of every conversation writes a new
checkpoint forever. Almost all of it (915 of 927 checkpoints) belonged to one
single long-running dev thread.

**Immediate fix applied (with explicit go-ahead):** deleted all but the 3 most
recent checkpoints per `thread_id` in `checkpointing_db.checkpoints`, plus the
matching `checkpoint_writes` entries (joined on `checkpoint_id`). Freed ~488MB,
confirmed via a live write test afterward. Old checkpoints are only needed for
LangGraph's time-travel/replay of past turns, not for continuing a conversation —
keeping the last few per thread preserves resumability without unbounded growth.

**Still needed — this will silently recur without it:** an automatic pruning
step (e.g. a scheduled job, or prune-on-write triggered periodically) that keeps
only the last N checkpoints per `thread_id` going forward, so this doesn't
require another manual cluster-wide diagnosis next time it creeps up. Rough
shape: reuse the exact keep-last-N-per-thread query used for tonight's manual
cleanup, run on a schedule (daily/weekly) or opportunistically (e.g. after every
Kth checkpoint write for a given thread). Open question: what's the right N —
tonight used 3 as a safe manual-cleanup default; the real automated policy might
want to key retention off something more meaningful (e.g. time-based: keep
everything from the last 7 days regardless of count, prune anything older) rather
than a flat per-thread count, since a single very active thread (like the one
that caused this) would otherwise still grow unbounded within its "last N."
