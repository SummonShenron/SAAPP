# Guest mode: from "ask about Jack" to "watch Sonic work, then sign up"

Drafted 2026-11-05 from Jack's first thoughts. Nothing is built. Read with `docs/guest-walkthrough.md` (what a guest sees today).

## The problem
A stranger lands, presses the guest button and sees four questions about Jack and a chat box. The answers are good, but nothing there shows what makes Sonic different (it works in a project: reads code, proposes a fix, runs the tests, remembers you, runs your checks). Nothing asks them to sign up. And every guest is the same account, so one visitor's usage is everyone's.

## What a guest can do today (verified in code)
- Tools: read a GitHub repo (the server's token; Sonic's own repo by default), web search, read a URL, run a small Python snippet in the sandbox, search the "Jack's portfolio" library, a headless browser if configured.
- Not for guests: connect a folder (`apply_sync` refuses guests), propose edits (needs a folder), run sandbox tests (needs a folder), the local runner, memory search, merge, photo cards, progress notes, image generation.
- One shared identity (`guest-recruiter@example.com`): one usage limit for all visitors, and the "your own limit" message when it runs out.

## What the stranger should see (the shape)
1. **A demo project they can watch Sonic work on, with nothing to set up.** A small made-up Flask service ("tallybox", derived from the eval's fixture repo) held on the server as the guest's read-only "folder". Welcome cards that call tools: "Why do invoices go overdue a day late?", "Add a late-fee line to the report and show me the tests pass", "Find every place the rate limit is set and say which one wins". Sonic reads the code, proposes the change as the real diff card, and runs the tests in the sandbox against a copy with the change applied. The card's Apply button says **"Sign up to apply this to your own project"**.
2. **The trace open by default for guests**, so the tool calls are visible: that is the product.
3. **The recruiter lane stays**, second: "About the builder" cards (who is Jack, what changed in the last PR, how isolation works). Same knowledge base, same answers.
4. **Nudges at the moments they matter**, not a banner: when a guest asks for something that needs an account (remember this, run my tests, merge, connect my folder) Sonic answers what it can and shows one card: what an account adds, one button. After the demo's diff card; at the usage limit; once on turn 5.
5. **Limits per visitor, not per guest account**: an anonymous visitor id (stored in the browser, plus an IP rate limit) with its own small daily allowance (about 8 turns), and one shared guest budget lane so strangers can never use up the owner's budget.

## Why this is mostly parts we have
- Sandbox tests against a folder copy with proposed edits already exist (`run_sandbox_tests`): stdlib `unittest` only, no network.
- Diff cards and plan cards exist. The demo reuses them; only the Apply button's behaviour for a guest changes.
- `backend/evals/fixture_repo.py` has 26 files and one stdlib test. **Keep it frozen** (the evals' answers depend on it); make a derived demo pack with 3 planted, fixable issues and stdlib tests for each.
- The welcome cards are one function (`getWelcomeCards`, `portfolioCards`).

## The pieces to build, in order (each shippable alone)
1. **Cards and trace** (small): new guest welcome cards that call tools on the demo and on public data; trace open for guests. No new backend.
2. **Per-visitor limits** (medium, and needed before any traffic): visitor id + IP limit; guest budget lane; clear "sign up to keep going" at the limit. Fixes the shared-identity problem in the walkthrough.
3. **The demo project** (medium): derived demo pack; the guest identity's workspace handle points at it read-only; edits proposed against it; sandbox tests with edits; guest Apply replaced by the sign-up card.
4. **Account nudges** (small): the "what an account adds" card for the refused features; the turn-5 and limit prompts; track clicks (counts only).
5. **Later**: a plan-mode demo (needs an Apply that ticks steps, so a per-visitor overlay on the server), and a short "watch it" replay for people who will not type.

## Risks and costs
- **Spend by strangers.** Guest turns run on the cheap models; at about a cent a turn and 8 turns a visitor, a hundred visitors a day is under $10. The guest lane has its own ceiling.
- **Abuse.** Web search, read URL and Python are open to guests today; the per-visitor limit is what keeps them from being a free tool. Sandbox limits already hold (no network, fuel cap).
- **Honesty.** The demo repo is made up and says so on the card. The recruiter lane stays about the real project.
- **Reading a demo as the product.** The diff card must say "demo project" so nobody thinks Sonic edited a real repo.

## Decisions (Jack, 2026-11-05: yes to all four)
1. First version of the demo: Apply is replaced by "Sign up to apply this to your own project": the guest sees the diff and the passing tests, nothing is applied.
2. A separate derived demo copy of the eval fixture; the fixture itself stays frozen.
3. Front door: the demo lane first, "About the builder" second.
4. Limits of about 8 turns a visitor a day and about $1 a day for all guests, **and they must be easy to change**: every number is an environment setting with these as the defaults (planned names: `GUEST_VISITOR_DAILY_TURNS=8`, `GUEST_DAILY_BUDGET_USD=1.00`, `GUEST_NUDGE_AT_TURN=5`), read where they are used, never written into a constant that needs a code change.

## Progress
- **Step 1, cards and trace: BUILT (uncommitted).** `welcomeCards.ts` has two lanes (`GUEST_LANES`): "Watch Sonic work" (the last pull request; where in the code documents are scoped; search the web; run Python for Fibonacci primes) and "About the builder" (the four portfolio questions, with the story card). `Chat.tsx` shows the lane tabs to the shared guest only, and starts the steps view ON for a guest who has not chosen. The demo lane's cards use only what a guest has today (a GitHub repo, web search, the Python sandbox); the demo project's own cards come with step 3.
- **Step 4 (first part), "With an account" cards: BUILT (uncommitted, 2026-11-06).** A third lane in `GUEST_LANES`: Fix a bug, Run your checks, Be remembered, Tidy a folder. Each is a `signup` card: choosing it sends no message (no model call, no cost); `Chat.tsx` shows the card's `detail` (what an account adds) with "Create a free account" under the cards, and changing lane clears it. Every `detail` has to stay true to what ships (folder + diff + tests, the local runner, Memory page, Tidy page). Still open from step 4: click counts.
- **Step 4 (second part), the mid-chat nudge: BUILT (uncommitted, 2026-11-06).** `backend/utils/guest_nudge.py` picks, from the words of the shared guest's message (no model call; patterns need "my project/tests/folder" or "remember"), which of the four account cards fits (`checks`, `fix`, `tidy`, `memory`). `app.py` counts it (`guest_nudge_<feature>`, counts only) and sends an `account_nudge` event after the reply; `Chat.tsx` shows that card under the reply with "Create a free account". Sonic's own answer is unchanged. Misses are cheap and false hits are noise, so the patterns lean narrow; add a pattern when real guest messages show a miss.
- **Step 4 (third part), sign-up press counts: BUILT (uncommitted, 2026-11-06).** Every guest button that says "Create a free account" or "Sign up to apply" now goes through one counted path, `api.guestSignUp(source)` (`local/src/api.ts`): it sends `POST /api/guest/signup-click` with `keepalive` (so it survives the page leaving), waits at most 800 ms, then signs the guest up whatever happened. The server (`backend/utils/guest_signup.py`) counts only the shared guest and only a known button (`banner`, `limit`, `demo_diff`, `card_<feature>`, `nudge_<feature>`), as `guest_signup_<button>`, and how many of the day's free turns the visitor had used as `guest_signup_after_<none|one_or_two|three_to_five|six_plus>`. Counter keys are letters only by a standing rule (`test_self_counters`), which is why the buckets are words. Nothing else is recorded: no message, name, address or visitor id. A press is the button, not a finished sign-up (that happens on the sign-in provider's page, which this cannot see). **Seen on Sonic Health, Turns tab** (`local/src/pages/sonicHealthGuest.ts`, two cards that stay hidden until something is counted): "Guests and the sign-up buttons" and "Which voice wrote the replies" (the `reply_tier_*` counters of the lite voices; before this the behaviour tab listed counters by hand, so neither these nor the earlier `guest_nudge_*` counts were visible anywhere). Not checked visually: the page is admin-only. Still open from step 4: the nudge after a demo diff, and a real sign-up count (needs the provider's webhook or an account-created event).
- **Step 2, per-visitor limits: BUILT (uncommitted).** `backend/utils/guest_limits.py`; counted at the top of `/api/chat` for the shared portfolio guest only (a turn over the limit is a 429 with `code: "guest_limit"`, before any work). Each visitor has an anonymous id (made once in the browser, sent as `x-visitor-id`); behind it a per-address allowance (for a browser that clears its storage), and one spend ceiling for all guests together (the usage store's own per-user daily cost for the guest identities). The shared guest no longer has a per-person budget (`budget_guard.limit_username`). The page shows "N of M free demo turns left" from the first turn, the nudge and a "Create a free account" button from the nudge turn on, and the same button on the refusal. Stored: a count per hashed (day, kind, value), deleted after three days; no raw id or address.

- **Step 3, the demo project: BUILT (uncommitted, 2026-11-06), unproven on a real model.** `backend/utils/demo_project.py` holds "tallybox", a stdlib-only invoicing core derived from the eval fixture (which stays frozen): 8 source files, 3 test files (10 tests, one failing on purpose). Three planted things, each with a front-lane card: an invoice goes overdue a day late (`>` where the README says the grace day counts), no late-fee line in the report, and the rate limit set in two places (config.py says 60 and nothing reads it; ratelimit.py says 100 and is what runs). It is written to disk by the server under an owner name no account can have (`demo_project.OWNER`) and put back if changed; nothing uploads into it (`apply_sync` refuses). A guest turn works on it only when the page says so (`demo_project: true`, sent from the moment a demo card is chosen until another kind of card or a new chat); every folder call in `tool_agent_node` then uses `workspace_user`. The diff card carries `demo: true`: no Apply or Decline, a "Sign up to apply this to your own project" button, and no turn waits for a decision. Commit tools are withheld. The recruiter lane's questions still read Sonic's own repository. Switch it off with `GUEST_DEMO_PROJECT=0`. **Found on the way: LangGraph silently drops state keys that `GraphState` does not declare, so `folder_in_browser` (the mobile no-folder gating) never reached the agent in production; both keys are declared now, and a test checks every key `app.py` sends.** Not done: a real-model run of the three cards (the guest lane's cost per run is a few cents), a sign-up click count, the nudge after a demo diff.

### The settings (all read when used, so each changes with one environment setting and no code change; set a number to 0 to switch that limit off)
| Setting | Default | What it limits |
|---|---|---|
| `GUEST_VISITOR_DAILY_TURNS` | 8 | turns one visitor may take a day |
| `GUEST_IP_DAILY_TURNS` | 40 | turns one network address may take a day (an office or a university shares one) |
| `GUEST_DAILY_BUDGET_USD` | 1.00 | what all guests together may spend a day |
| `GUEST_NUDGE_AT_TURN` | 5 | the turn after which the page says what an account adds |

Known edges: the address is the first of the forwarded list, which a client could set itself, so the address limit is a second line, and the spend ceiling is the real backstop. A database failure lets turns through (the spend ceiling and the budget guard still apply). The embedded widget's guest (a client's deployment) is not limited here.
