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

1. Reference-classifying trace tool
2. Tier 2 — idiom-matching via retrieval
3. Tier 1 — real Python execution against a repo's actual dependencies
4. Per-user GitHub token scoping (design informed by `workflow_builder`)

Rationale for this order: 1 and 2 are pure tool/prompt additions with no new
infrastructure and directly serve the thing named as mattering most —
"understand what the issue is and find the necessary pieces, since it has full
access to the code." Tier 1 answers a different question (does code that's
already written actually run) and is a genuinely bigger build. Token scoping only
matters once the others make it worth pointing at someone else's repo at all.

---

## 1. Reference-classifying trace tool (start here)

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

**Open questions to resolve before building:**
- Classification heuristic: AST-based (accurate, more implementation work,
  language-specific — would need separate handling for Python vs. TS/TSX) vs.
  regex-based (cheaper, matches this repo's existing `search_code`/`find_file`
  style, less precise). Given `find_file`'s fuzzy matcher already leans
  regex/heuristic rather than a full parser, probably worth staying consistent
  with that rather than introducing an AST dependency for one tool.
  wonder if a shared helper between JS/TS and Python cases is feasible, or if it
  needs two small language-specific classifiers.
- Does this replace `search_code` or sit alongside it as a second action? Leaning
  alongside — `search_code` stays the "does this exist at all" tool,
  this becomes the "which of these hits actually matters" tool.
- How does it interact with the existing retry-nudge mechanism in
  `run_react_loop`? Probably no special-casing needed — it's just another
  tool_action and gets the same empty/error tracking already fixed this session.

---

## 2. Tier 2 — idiom-matching via retrieval

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

## 3. Tier 1 — real execution against a repo's actual dependencies

**Confirmed gap (checked this session):** `run_python_sandboxed`
(`backend/services/python_sandbox.py`) is a WASM sandbox restricted to a
stdlib-only `SAFE_IMPORT_ALLOWLIST` (`math`, `re`, `json`, `itertools`, etc.). It
cannot import `langgraph`, `pymongo`, or any real module from a target repo. The
only real-execution path today is `run_repo_tests`
(`backend/services/ci_test_runner.py`), which requires an *existing* pytest file,
is admin-gated, and takes minutes (dispatches the real
`.github/workflows/patchy-tests.yml` CI workflow). There is no rung between
"reason about whether this would work" and "run the full test suite."

**Rough shape:** a lighter-weight `run_snippet_against_repo`-style action: check
out the real repo with its real `requirements.txt` installed, run a short scratch
script with real imports allowed — no test discovery, no fixtures, just "import
the thing, call it, print the result" — before presenting new code as verified
to work.

**Open questions to resolve before building:**
- Where does this actually run? Reusing the existing CI workflow again (like
  `run_repo_tests` does) is the path of least new infrastructure, but that
  workflow is built around `pytest <path>` invocations validated by a strict
  regex — it would need either a new, narrowly-scoped workflow input, or a
  separate lightweight execution path entirely (ephemeral container, a
  short-lived cloud sandbox). This is the piece most likely to introduce a real
  recurring cost, so it needs its own sizing pass before committing to an
  approach.
- Gating: presumably admin-only like `run_mongo_query`/`run_repo_tests`, at
  least initially.
- Timeout/resource limits analogous to the WASM sandbox's fuel accounting, sized
  for "install deps + run a few lines" rather than a full suite.

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
