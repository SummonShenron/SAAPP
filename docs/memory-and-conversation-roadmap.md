# Memory and conversation roadmap — scoping notes, not yet built

Working notes from a planning session (2026-10-15). Nothing in sections 1 and 2 has been built. Both come from things Sonic was actually
seen to do, in line with the "build from observed failures" rule, and both are meant to avoid new recurring cost. See
`docs/voice-composer-and-memory-evolution.md` for the retrospective on what memory already does, and `docs/coding-agent-roadmap.md` for the
coding-agent side.

## 1. Facts that attach to facts

### What was observed

Jack said he is building a frontend in Next.js *for work*. Sonic then assumed *Sonic itself* was a Next.js app. The two statements are both
true and both stored, but nothing in memory says which thing each one is about, so a question about the app pulled in the wrong stack.

### What exists today

`save_user_fact` (`backend/utils/memory_utils.py`) embeds a new fact and compares it with the existing ones. At cosine similarity
`FACT_SIMILARITY_THRESHOLD` (0.80) or above, a small model (`_judge_fact_relationship`, `FACT_CONFLICT_PROMPT`) answers one of three things:
`duplicate` (reinforce it), `supersede` (replace it), or `distinct` (store it as a separate fact). Facts also carry a category (identity, goal,
project and so on). **`distinct` is where the gap is**: a fact that is neither a copy nor a replacement but a detail *of* an existing fact (Sonic
"runs on React and Python") is stored as a free-floating fact with no link to Sonic.

### The idea

A fourth verdict, `extends`: the new statement is about the same subject as an existing fact and adds something to it. It is stored as its own fact
with a `parent_id` pointing at the broader one. Recalling the parent brings its children along; a child never travels without its parent, so
"builds a frontend in Next.js" binds to the *work* fact and is not pulled in when the question is about Sonic. Facts keep their own text and
history; the link is the only thing added.

Why this matters: most assistants keep a flat list of remembered statements. A memory that knows which facts belong together is a real
difference, and it is the missing half of what the existing tags (the work tag and similar) only approximate.

### Why cosine similarity alone will not do it

The Next.js case is exactly a high-similarity pair about *different* subjects ("building a frontend" is close to "Sonic's frontend"). So cosine
only picks the candidate; a judge decides whether it is the same subject. This is the same pattern as the memory recall "second look"
(`backend/services/memory_search.py`): a small model, a strict prompt that defaults to *no*, validated against a stronger model on real data
before it is trusted. The tier-2 compaction check showed what happens otherwise: thresholds picked by feel were wrong on the real store.

### Build order

1. **An eval first** (`backend/evals/memory_eval.py`): attaches a detail to the right parent; does *not* attach the work Next.js fact to Sonic;
   does not merge two distinct things that merely sound alike; a question about Sonic's stack is answered from its bound facts.
2. **Data model**: an optional `parent_id` on `UserFact`. Optional means no migration: old facts simply have none.
3. **The verdict**: extend `FACT_CONFLICT_PROMPT` and `_judge_fact_relationship` with `extends`. This adds **no new model call**: the judge already
   runs in this band. The default stays `distinct`.
4. **Retrieval**: `select_relevant_user_facts` returns the matched fact plus its family, and the rendered block groups them.
5. **Memory page**: show children under their parent, and let the user detach one (and delete the whole group).
6. **Backfill as a dry run**: a `memory_maintenance` command that only *proposes* links over the existing store, so it can be read before anything
   is applied (the same dry-run default as the other maintenance commands).

### Guardrails

- A link never rewrites or merges text, and it is reversible (detach).
- Identity and protected facts (the support contacts) are never made children.
- A cap on children per parent (about 8), so one broad fact cannot swallow the store.
- Pruning must treat a family as a unit: no orphaned children, and no parent pruned while its children remain.
- Compaction's stale-chunk flagging already follows a superseded fact; a superseded parent should flag its children's chunks too.

### How we will know it worked

The Next.js scenario passes; "what is Sonic built on?" answers from the bound facts; the recall eval does not regress; and on the real store the
proposed links, read by hand, are right (the same read-only check that caught the tier-2 mistake).

## 2. Sonic's follow-up curiosity

### What was observed

Follow-up questions are sometimes very good, but Sonic is not generally curious. The push to not extend conversations (the closing directive and
the "fishing" check in `backend/utils/wrapup_utils.py`) has trained it to stop asking. A person on the phone or in a text thread, at the end of a
conversation, asks "anything else going on?" or "what else is new?"; Sonic never does.

### The idea

When a conversation is winding down, Sonic may ask **one** open question of that kind. If the person has nothing, it closes out. If they do, the
conversation continues naturally. This is not engagement bait because it is clamped hard: one question, once, and then it stops.

### Constraints that keep it from becoming engagement bait

- At most once per conversation, and not in every conversation (rate-limited across them).
- Only after a real exchange, and only once what was asked has been fully answered.
- Never after a closing message (`is_closing_message`), under any safety or crisis context, while the emotional tier is held down, or on a tool,
  knowledge-base or web route. Conversational replies only.
- It uses **the one proactive slot** a turn already has: it yields to a callback, an open-loop follow-up, encouragement, or a self-history line, the
  same rule those already follow.
- If the person does not pick it up, the closing directive takes over. No second attempt.

### Where it would go

- A gate next to `callback_allowed` in `backend/utils/shared_history.py` (same shape: recalled/route/safety/emotion/closing checks and a marker on
  earlier replies, like `CALLBACK_MARK`, for the rate limit), and a small prompt block like `build_callback_block`.
- **A trap to remember:** `closing_reply_issue` flags fishing patterns in a reply to a closing message. The new question must be exempt, or the
  reply-fit check will regenerate it away.
- Counts only, in `self_counters` (`curiosity_offered`, `curiosity_taken`), no content, so that "do people answer it?" is a number we can read
  while we are in data-gathering mode.

### Open questions

- What counts as "winding down": cheap heuristics (the answer ended with no question, a pause since the last turn, a short user message) versus a
  flag from the reasoner call that already runs every turn (no extra call).
- Wording variety, so it does not sound like a script.
- Whether there should be a user setting to turn it off (the proactive-opening setting is the precedent).

### How we will know it worked

A character eval (`backend/evals/character_eval.py`): winding down gets exactly one such question; a closing message gets none; a second
conversation soon after gets none; a crisis context gets none; and when answered, the follow-up goes somewhere sensible. In production, the
taken/offered ratio.

### Scope update (2026-10-16): two tools, one use per conversation

The user widened this from "ask a follow-up question" to two small, human conversation tools, both for the moment a conversation has no clear direction:

- **(a) An open, natural extender**: the kind people ask on the phone ("what else have you got going on?", "anything else new?"). Not a probe, not about the topic just finished.
- **(b) Offering something of its own to steer by**: Sonic mentions something about itself when it is naturally able to, to give the conversation a direction: a topic it leans toward, or a good conversation the two of them had before.

Rules the user set: **only one of these, once per conversation**; if the person has nothing, or the conversation really is over, Sonic wraps up exactly as it does now (the wrap-up already works well and stays unchanged); and **nothing here ever outranks the emotional ceiling or the safety mechanisms**.

How this fits what already exists (so it is mostly new *triggering*, not new capability):

- (a) is the idea above. (b) reuses parts that are already built: the **callback** (`shared_history.py`, one explicit dated reference to recalled context) for "a good conversation we had", and the curated **INTERESTS** (`sonic_profile.py`, topics it leans toward) through the `relatable_utils.py` gates, for "something it enjoys". Both already obey the identity rules.
- The identity rules (`identity_checks.py`) stay in force for (b): topics it leans toward, never activities ("I watched", "my favourite game"), never a claimed feeling or an invented past ("I once", "I've been there"), never a stake in the person staying or returning ("I'd love to keep talking"). A callback must point at a real recalled note, dated; it is never made up.
- They share the turn's **one proactive slot** with open loops, relatable lines, the goal nudge, encouragement and the callback, so this takes the slot only when nothing higher wants it (safety, then encouragement, then an open loop, then a callback, then this).
- Choosing between (a) and (b), mechanically and with no model call: a relevant recalled thread that clears the callback gate, then the callback; else an unused curated interest when the talk has been light; else the plain open question (a). Whichever is chosen, the other is spent too: one per conversation.
- A lull is not a goodbye. "Winding down" must be told apart from a **closing** message (`is_closing_message`): a lull ("ok", "yeah that makes sense") may get one extender; a closing ("thanks, that's all", "bye") gets none. This boundary is where the eval cases go.
- Because a model left to itself would ask one every turn, the limits live in code (the per-conversation mark like `CALLBACK_MARK`, a cross-conversation cap so it is not a tic, and the route gates: conversational replies only, not tool, KB or web routes), not just in the prompt.

Build in two stages: **(a) first** (gate, prompt block, counters `curiosity_offered` / `curiosity_taken`, and the exemption in `closing_reply_issue` so the reply-fit check does not rewrite it away), then **(b)** on top, reusing the callback and interest machinery. Each stage gets `character_eval` scenarios before it ships.

### Stage (a) built (2026-10-16): the open natural extender

`backend/utils/curiosity.py`, wired in `app.py` next to the callback. Everything that limits it is code:

- **At a lull only.** After at least 3 messages from the person (`CURIOSITY_MIN_USER_TURNS`), when their message is nothing but an acknowledgement or hedge
  ("ok", "yeah, I see", "huh, interesting"; short, no question, no digits or links, from a fixed word list), and Sonic's last reply did not end on a question
  of its own. A closing message is never a lull: `is_lull` defers to `is_closing_message`, so a goodbye still gets the clean close.
- **Once per conversation, where a conversation is a stretch of the chat, not the whole saved thread.** The mark `curiosity_offered` on Sonic's reply is looked
  for back to the last pause of an hour or more between two messages (`CURIOSITY_CONVERSATION_GAP_HOURS`, default 1). Counting the whole thread would have let
  one offer spend the limit for the life of a chat people return to for weeks. The real-exchange count (3 messages from them) restarts after a pause too.
  Messages saved before they were stamped with a time are never taken as a pause.
- **Not in every conversation: a time gap, not a count.** Per person, stored as `last_curiosity_offer_at` on the `user_settings` document, 2 hours by default
  (`CURIOSITY_MIN_HOURS`), set short on purpose so it can be tested and tuned from real use (it started at 72 hours and was judged far too long). This is the one
  place I did not do exactly what was proposed ("never two conversations in a row"): nothing orders conversations cheaply, so a gap in hours stands in for it.
  Tighten or loosen with the env var; no deploy of code is needed, only the setting.
- **Quiet when quiet matters more.** Conversational route only, no guests, no safety context, the emotional layer must be at the open energy tier, and it
  takes the turn's one proactive slot only when an open loop, relatable line, encouragement, self-history line, callback or goal nudge has not.
- **Recorded on what happened.** The mark, the `curiosity_offered` counter and the stored time are written only if the finished reply really asked a question
  (a question inside a `<<<FOLLOW_UP>>>` suggestion does not count). `curiosity_taken` counts the next message when it has real substance (3+ words, not a
  closing, not another bare acknowledgement). Both are in `self_counters` (closed key list) with rates: offered per turn, taken per offer.
- **Kill switch:** `CURIOSITY_OFFERS=false`.
- **The `closing_reply_issue` trap from above does not apply** as written: the question is never offered on a closing message, so the closing check never sees it.
  If they answer with a goodbye, that turn is a normal closing and gets the clean close.
- The prompt block lifts the "no tacked-on question" rule for this one reply, gives registers (what else is on their plate, what's new, how the rest of the day
  looks) rather than a stock phrase, because with a single example it said the same sentence every time, and forbids probing, menus, claiming curiosity and a follow-up tag.

Verified: 44 unit tests (`backend/tests/test_curiosity.py`: the lull detector, every gate, the gap, the database round trip and failure, the recording, the
counters, and pins on the wiring in `app.py`); two `character_eval` scenarios on the real model, `lull_open_question` (exactly one question, no claimed
curiosity, no tag) and `lull_question_answered_wraps_up` (when they have nothing, a clean close with no second question), both 10/10; `clean_close` still 5/5;
backend suite 3134 passed. A changelog entry (`open_question`) was added so Sonic can describe the change truthfully.

Stage (b) is built (below). Not measured yet: how often real people pick the question up
(`curiosity_taken` / `curiosity_offered` in Sonic Health data once it has traffic).

### Stage (b) built (2026-10-16): something of its own to steer by

At a lull the single extender now has an order, all in `app.py`, all sharing the one proactive slot, the once-per-conversation mark and the per-person gap:

1. **A reading-shelf item** this person has not been offered (section 6).
2. **A callback**: the existing recalled-context callback already fires on any conversational turn with recalled memory. At a lull it now (a) obeys the extender limits
   (it only fires if the conversation has not used its one, the person is outside the gap, and Sonic's last reply did not end on a question), (b) has its own lull
   wording (`build_callback_block(lull=True)`: something worth picking back up, not a link to what they just said), and (c) spends the extender (mark and
   per-person time) when offered. It is spent on the invitation, because the app cannot cheaply tell afterwards whether the model used it.
3. **A leaning** from the curated `INTERESTS`, in leaning language with one light question ("Game design is a topic I lean toward... is that something you follow?").
   Each is said to a given person once (`lean_seen` on their settings document); marked and counted (`lean_offered`) only if the reply really said it.
4. **The plain open question** from stage (a), only when the person has heard every leaning.

Verified: 54 tests in `test_curiosity.py` (picking and remembering leanings, the blocks, the lull wording, and pins on the order and limits in `app.py`); real-model
`character_eval` `lull_lean` 8/8 (one leaning in its own words, one light question, no activity or feeling) and `lull_callback` 7/8 (the miss skipped the optional
callback, which is allowed). One of four sampled leaning replies added a follow-up suggestion tag despite the instruction; the scenario's scorer rejects that.
A leaning offered after an unrelated topic reads as a deliberate change of subject ("on a completely different note..."), which is what it is.

## 3. In flight: the app shell redesign

Not a scoping note: this one is being built, in stages, and is recorded here so the plan survives between sessions.

- **Direction (agreed 2026-10-15):** give the conversation the space. Replace the top nav and the 110px hero banner with a left rail (logo and name,
  pages, recent conversations, the person's own chip and menu) and a 44px top bar; the trace panel becomes a blade; the product's name and
  sub header return as a branded empty state. Theme colours stay.
- **Stage 1, shell (built, uncommitted):** rail, top bar, user menu, `/account` stub, conversations list moved into the rail, empty-state
  branding, the chat filling its column. The embedded widget is unchanged and keeps its own conversations overlay.
- **Stage 2, conversation space (built, uncommitted):** the chat column is 960px (was 900), everything scoped to `.chat-page` so the embedded widget is
  untouched. On a phone the gutters no longer stack: message text went from about 64% to 84% of the screen's width, and a reply's invisible bubble
  padding is gone. The empty chat centres the brand and example cards between the top bar and the input. The chat window's top and bottom padding now
  equals the scroll fade lengths (40px and 36px), so the first and last messages are clear at rest. **The fade-out and fade-in on the message list are
  unchanged** (the `.chat-window` mask in `index.css` and the message `fadeIn`); keep them exactly so.
- **Stage 3, trace blade (built, uncommitted):** a full-height blade down the right side (340px, under the top bar) that pushes the conversation over; a
  bottom sheet on a phone. One `TracePanel` component serves both: steps with a check or a live pulse, batch badges, a status line ("Tracing request",
  then "Last request"), and it follows the newest step. The top bar's button (a microscope) opens whichever applies, with a pulse on it while a request
  runs and the trace is closed. The blade starts open only on screens 1400px or wider unless the person chose otherwise. The floating panel and the phone
  pill are gone for the normal app (the phone's input area got 48px back); **the embedded widget keeps its original floating panel, pill, and modal**.
- **Stage 4, Patchy (built, uncommitted):** new looks and moods, borrowed from errAgent's `PatchyEmptyState.tsx` and extended. The rules live in
  `components/patchyLook.ts` (pure, tested: `node --test src/components/patchyLook.test.mjs`), the timers in `hooks/usePatchyMood.ts`, the shared head and
  arm in `PatchyParts.tsx`, the small one on the input in `MiniPatchy.tsx`, and a new full-size one for the empty chat in `PatchyHero.tsx`.
  - *While a turn runs* the look follows the backend step: **thinking** (hand to head, thought dots; planning steps), **searching** (magnifying glass over
    the eye; retrieval, web, GitHub), **puzzled** (shrug, one brow up, "?", sweat drop; query rewritten or answer reconsidered), **writing** (the old typing look).
  - *After a turn*: celebrate, or the **error face** (sorry and red, a short shake) when the stream errors or the request fails. Before this, a failed turn
    ended with the cheer hop, because the finally block cheered on anything but a Stop.
  - *Your actions*: he **watches** the box once you engage with it (a click into it, or typing; it ends when you send or click away). Not "is typing", which
    flickered with every pause, and not plain focus, because the page focuses the box itself on load and it stays focused, so he would always be
    looking at it. He turns to it, simulated flat: the head bows, the face slides across the shell, the ear on the side he turns toward foreshortens, the other swings into view and grows, the eyes drop; with text in the box he turns further, as if reading it. The turn is bold on purpose: the small Patchy is drawn at 0.58 scale. He nods **"got it"** when a message is steered into a running turn, and goes **drowsy** after 3 minutes of quiet.
  - While engaged his free hand comes down to hover over the keys ("ready"), as if about to answer.
  - *No popping between looks* (about the feel of `transition: all 0.2s ease`): colors transition; every expression (eyes, brows, lids, "?", z's, sweat,
    thought dots, the magnifier) is always drawn and crossfades; the head has a base pose per look that transitions, plus a sway whose size is a registered
    CSS property that eases; and the arm's path and the mouth's curve, which CSS can't ease in every browser, are tweened by `hooks/useSmoothVector.ts`
    (an exponential ease, tau 0.09s, no React re-render per frame; reduced motion jumps straight to the target). Drowsiness is now a steady droop and slow
    sway, not the earlier droop-and-jerk, which was an animation swap.
  - *Usage budget*: a slow sweat drop at the notice level, heavy lids when throttled, **asleep** (z's) when chat is blocked.
  - *Energy tier* is respected everywhere: for a low (subdued) person there is no alarm or shake on a failure (a slow nod, soft rose), no sweat drop, and no giggle.
  - The empty chat shows the full-size Patchy (replacing the big logo there; the logo stays in the rail) and the small one takes over when the conversation
    starts. `patchy-preview.html` is now a gallery of every look (`?empty=1` shows the empty chat as laid out, `?dark=1` the dark theme).
- **Look (2026-10-16):** the app is flat (no shadows on bubbles, the input or the example cards, no lift on hover). The **dark theme is black and grey with orange as
  the only colour** (its third pass: the old red "Shadow" look, then deep navy, now neutral): a near-black page, grey surfaces, a **grey user bubble**, orange
  accents and labels, and **orange panel edges on the app rail and the trace blade** (`--edge`). It is one token block, `.theme-shadow` in `index.css`; the
  hardcoded dark hexes in the other stylesheets point at its tokens (`--panel-bg`, `--popover-bg`, `--card-bg`, `--input-bg`, `--line`), so the next change is one
  block. The logo's gradient is blue to orange (`LOGO_GRADIENT_STOPS` in `logoMarkData.ts`, `logo-mark.svg`, `favicon.svg`). Cyan was tried and set aside (it read
  edgy and belongs to the BTY site). One caveat to watch: the usage ring and budget banner use amber and orange for "notice" and "throttled", which sit near the
  accent. Left over: the dead `.hero-banner` rules in `index.css` still carry the old red gradients, and the theme is still named `theme-shadow` in code.
- **Shadows and the trace as a path (2026-10-16):** every decorative shadow token (`--shadow`, `--shadow-hover`, `--shadow-dark-hover`, `--light-shadow`,
  `--shadow-hover-reversed`, `--reverse-shadow`) is now `none` in both themes, which flattens every rule that used them in one move, plus the literal ones on the landing
  buttons, the switch glow, the loader glow, the overflow button's glow and the mobile trace sheet. Only things that float over the page keep one, from
  `--float-shadow` (the tooltip) or their own rule (the user menu, the mobile rail drawer, the overflow menus, Help and the folder-consent modal). Hover lifts
  (`translateY(-2px)`) on the other buttons and cards are still there; without their shadow they are the next thing to flatten if wanted. **The trace panel's steps
  are nodes on a path** (`TracePanel.tsx`, `TracePanel.css`): a dot per step on a line drawn in the accent, a check once done, a pulsing ring on the running one,
  and a dashed stub flowing on from the last node while the request runs. No cards or borders. The old `.trace-step*` rules in `TracePanel.css` were removed:
  they were global and had been styling the embedded widget's own `.trace-step` divs too.
- **Landing page (2026-10-16):** brought in line. The left panel is **orange fading to white at the seam** (it was dark slate to white), the old raster
  `neutral_logo.png` is replaced by the vector logo (blue to orange) on the white side, and the copy and buttons are navy (`--landing-ink`), which reads across the
  whole fade: a navy primary button and a white guest button. On a phone the right panel is hidden, so a small one-colour navy logo sits above the name and the
  fade runs down the page. An orange-and-blue variant (a blue wash behind the logo) was tried and set aside: it made a visible seam where it met the fade, and the
  logo already carries both colours. `neutral_logo.png` is no longer used by the app (it is still what the vector mark was traced from).
- **Light theme, settled (2026-10-16):** an orange-led light theme (peach surfaces), a navy app rail and a royal-blue user bubble with white text were all tried
  and set aside. The light theme is the dark theme's design in a light version: **one light blue (`#d9e5fb`) shared by the app rail and your message bubble**, so
  the bubble takes the same dark navy text as replies (`--bubble-user-text: var(--text-main)`), deep navy text (`#14234a`), and the same orange accent
  (`#c85a0c`) at the live edges: the input's blue-to-orange glow, the caret, selection and the active rail item apply in both themes. Tokens are in `:root`
  (light) and `.theme-shadow` (dark) in `index.css`, both defining the same names (`--accent-hover`, `--accent-soft`, `--accent-ring`, `--card-tint`,
  `--tint-hover`, `--bubble-user-border`, `--bubble-user-text`), so shared rules work in either. Deliberately left blue: the usage ring's "ok" state, the
  Memory category chips and the landing page.
- **Branding:** the name and sub header are placeholders in `local/src/components/brand.ts`. The original logo SVGs were lost; the mark was traced
  from `neutral_logo.png` into `logo-mark.svg`, `logo-mark-mono.svg` and `LogoMark.tsx`. Replace them if the originals turn up. The Sonic and
  Shadow themes and the Sonic-branded images are still to be rebranded away.

## 4. Chat send errors hide the real cause (built 2026-10-16)

### What was observed

When a chat request fails, the person sees "Vector assertion timed out. Check local engine allocations." That reads like a vector-store problem. It is not
a real diagnosis: the string is hardcoded in `local/src/pages/Chat.tsx` (the `catch` in `handleSendMessage`, the branch that is not a deliberate Stop) and
is shown for **any** failure of the request, whatever the cause. The real error only goes to `console.error("Chat send failed:", err)`, so it is swallowed
as far as the person, and anyone reading a bug report, can tell. Nothing in `backend/` produces that text.

### Where the real information is lost

- `Chat.tsx`: the generic message above replaces the error entirely.
- `api.ts` `sendChatMessage`: a non-OK response throws a fixed "Failed to initialize communication stream." and drops the status code and the body, so the
  caller cannot tell an expired session (401/403) from a spent budget (429 or the budget block) from a server fault (5xx) from a dead network.
- A stream that dies or times out part-way ends in the same catch, with the same text. During the 2026-10-14 production slowdown, hung requests would have
  shown exactly this misleading message.
- The stream's own `error` event is passed through as `Execution Fault: <message>`, which is better, but it still depends on the backend's message being
  specific.

### The idea

Keep the technical detail, but stop inventing a cause:

1. Make `sendChatMessage` throw a typed error carrying the HTTP status, a short machine reason from the response body when there is one, and whether it was a
   network failure or a timeout.
2. In `Chat.tsx`, map the failure kinds to honest, plain messages: session expired (sign in again), usage paused (say when it resets; the budget banner already
   knows), server trouble (try again; say it is on our side), offline or timed out (check the connection and retry). Unknown stays generic but says it is
   unknown, and never names a component it has no evidence about.
3. Keep the raw error reachable for debugging without putting it in front of everyone: an expandable "details" line on the failed reply, and the existing console
   log. Never include the person's message text or other user content (the log-hygiene rule: no user content in logs or error forwarding).
4. Give a failed reply a retry affordance, since the question is already there to resend.
5. The Patchy error face already fires on these failures (a failed turn no longer ends with the cheer hop), so it needs no change.

### How we will know it worked

A test for the error mapping as a pure function (status and failure kind in, message out), including that no mapped message mentions vectors or allocation
unless the backend said so; and a manual check that a 401, a budget block, a 500 and a dropped connection each show a different, true message.

### What was built (2026-10-16)

Almost all of it is frontend. A check of `app.py` showed the backend already turns a failed turn into a plain-language reply (`handle_failed_turn`) and a spent budget
into its own reply, both as ordinary stream events, so they never reached the generic message. The failures that did were the ones before or outside a stream: an
HTTP error (a 401 from an expired sign-in, a 5xx or 502/503/504 while Render starts or redeploys), the request never connecting, and a stream that dropped part-way.
Nothing in `backend/` was changed. (The backend also never sends a stream `error` event, so the page's `Execution Fault:` branch is effectively dead; left alone.)

- `local/src/chatErrors.ts` (pure, tested): a typed `ChatSendError` with a kind (session, forbidden, bad request, rate limited, starting, server, network,
  interrupted, unknown), the status and the server's own plain-string `detail`. `kindForStatus`, `safeDetail` (never a 422's body, which echoes what the person sent; cleaned
  and cut to 160 characters) and `describeChatError` (message, whether a retry is worth offering, one technical line).
- `sendChatMessage` (`api.ts`) now throws these instead of "Failed to initialize communication stream.": a status and detail for a non-OK response (reading a non-JSON body
  such as a proxy page safely), `network` when `fetch` rejects, `interrupted` when a read fails after the stream began. A deliberate Stop (AbortError) passes through untouched.
- `Chat.tsx`: the hardcoded "Vector assertion timed out..." is gone. The empty reply placeholder becomes the plain message for the cause (or a notice after any partial answer),
  with a quiet **Try again** button where retrying can help (not for an expired sign-in or a refused or oversized request) and a **Details** disclosure with the one technical
  line. Retrying replaces the failed message and notice instead of repeating the question in the thread. The console log stays. The Patchy error face needed no change.
- Honest messages: none mentions vectors, allocation or an engine (a test checks every kind and several statuses); the unknown case says the cause is not known.

Verified: 10 node tests (`node --test src/chatErrors.test.mjs`); the real `sendChatMessage` run in the browser against a faked `fetch` for a 401, 403, 422 with an echoed message, a 503
HTML page, a 500 text page, an offline request, a mid-stream drop, a good stream and a Stop: each gave its own kind and message, the 422 never leaked the text, and the Stop was
untouched. The notice rendered in both themes. Not exercised: the full Chat page (it needs a sign-in), so the Try again click path (replace and resend) is untested in a browser.

## 5. Tool agent memory: correct answers about code from earlier messages get flagged and rewritten (built 2026-10-16)

Reported 2026-10-16, with `TOOL_AGENT_MEMORY=true` switched on (it ships off; `backend/services/tool_agent_memory.py`). Not yet reproduced: what follows is
from reading the code, so the first step is to confirm which check fires.

### What was observed

The model now remembers talking about, or seeing, code from earlier in the conversation (code the user attached or pasted in earlier messages). When the
user then asks about it, the model answers correctly, in reference to the user's question. That answer is flagged by the validator and **rewritten**, because
the code it refers to is not in the repo, and the validator treats that as an unsupported or ungrounded claim. The answer was right to talk about code that
lives outside the repo; the checks cannot tell "outside the repo, but the user showed it to me" from "invented".

### Why the validators object (from the code)

All of these live in `backend/services/react_loop.py`, run on a "final" answer, and compare it only against `attempts`, the tool observations gathered
**this turn**. Nothing the user supplied earlier is in `attempts`, and neither the saved-memory block nor the conversation history is shown to them:

1. `_check_final_answer_grounding` (`agent_utils.py`, `GROUNDING_CHECK_PROMPT` in `constraints.py`): "the ONLY source of truth" is this turn's observations, so
   a named file, function or snippet that came from an earlier message reads as a fabricated claim. A flagged answer sets
   `pending_ungrounded_claim_notice` and forces a rewrite that tells the model to verify the claim "with a real action" or remove it, which is the wrong
   advice when the source is the user's own message.
2. The no-precedent check (`_final_answer_has_code_block` plus `_has_successful_code_precedent_research`, mechanical, no LLM): any answer containing a code
   block with no successful `read_repo_file` / `search_code` / `search_literal` / `find_file` result this turn is rejected with "you haven't looked at any
   real code in this repo". It cannot tell a quoted or discussed snippet from a proposed implementation, so quoting the user's attached code trips it.
3. `_check_idiom_grounding` ("is this code tailored to this repo, or generic?") and the duplicate-declaration check (`_extract_declared_names` against the
   repo) can fire on the same answer, since code from outside the repo will not look like the repo's idioms.

Each rejection is budget-limited ("force one real correction, then let a second attempt through"), so the answer is not lost, but it is rewritten once, which
costs a model call and can degrade a correct answer.

### The idea

Give the validators a second, labelled evidence source instead of loosening them:

- Pass the user-supplied context (attached or earlier-message code, and the memory block when it holds such code) into the grounding prompts as a separate
  section, "code the user showed you earlier in this conversation (not the repo)", and say a claim traced to it is supported.
- Make the no-precedent check apply only when the answer **proposes** code for the repo, not when it quotes or discusses code the user supplied. Cheapest
  honest test: skip it when the code blocks in the answer appear (near-verbatim) in the conversation's earlier messages.
- Change the rewrite notice for this case so it does not ask for a "real action" to verify something only the user can vouch for.
- First, count which check fires and why (counts only, no content, per the log-hygiene rule), so the fix targets the real one and a recurrence is visible.

### How we will know it worked

A `behavior_eval` scenario: the user attaches code in one message, then asks about it in a later one, with `TOOL_AGENT_MEMORY` on; the answer discusses it and is
**not** rewritten, and the same answer with the code swapped for an invented name still **is** flagged (so the fix does not just switch the checks off).
Plus unit tests for each check with the user-supplied context present and absent.

### What was built (2026-10-16)

Confirmed by reading and then by a real-model eval: all three code-block checks (grounding judge, no-precedent, idiom/duplicate-declaration) fired on the
same correct answer. Without the fix the new scenario rewrote it **0/3** times (9 model calls per run, rejections `no_precedent`, `ungrounded_claim`,
`generic_code`); with the fix it passes **3/3** with 1 call and no rewrite.

- **Second evidence source for the grounding judge.** `build_earlier_context_block` (`agent_utils.py`) builds a labelled "what the user showed or said earlier"
  section (plus saved notes, when present), marked as data, never instructions, and never able to make a claim about the repo true. `GROUNDING_CHECK_PROMPT`
  takes it as `{earlier_context}`; the "REAL TOOL OBSERVATIONS" section stays the source of truth for the repository. The rewrite notice now says material the
  user shared earlier counts as a source.
- **Mechanical skip for the three code-block checks.** `answer_code_quotes_conversation` passes when every code block in the answer has at least 60% of its
  meaningful lines (12+ characters, whitespace squashed) found in the earlier conversation. Invented or proposed code does not pass, so it is still flagged.
- **Wiring.** `tool_agent_node` passes the earlier messages and the memory block into `run_react_loop(conversation_text=, saved_notes=)`. The native engine
  skips these checks by design and ignores the extras.
- **Observability.** Log lines only (`[validator] ...`, check names and counts, no user content). No Sonic Health counters yet.
- **Tests.** 16 unit tests (`test_react_loop_conversation_context.py`) and 7 harness tests (`test_behavior_eval_conversation_code.py`), including controls that
  prove new or invented code is still rejected. Four mutation checks (one per check, plus the grounding context) were each caught. The eval harness now
  records which rejection notice fired (`Observation.rejections`).

### Known limits

- Text from session attachments is not part of the earlier-conversation text, only message content is.
- The grounding prompt keeps the most recent 6000 characters of the conversation; the mechanical match looks at up to 120,000.
- An answer that mixes quoted user code with new proposed code in one block can pass if 60% of the lines are quoted.

## 6. Sonic's reading shelf: things it came across that match its interests (built 2026-10-16)

Suggested 2026-10-16 by the user, as the natural partner to section 2. Sonic has interests it "leans toward" (`sonic_profile.INTERESTS`) but no way to learn more
about them, so when asked what it would talk about it can only recite the list. The user's example: the person had nothing to say and asked Sonic to bring its own
topics; Sonic named categories (lean systems, finding the commit a bug started in, game lore), but had nothing specific to share.

### The idea

Now and then Sonic runs a web search on a topic from its own interests, reads what it finds, and keeps a short note on a **shelf**. Later, on a lull or when the
person mentions something close to it, Sonic can say "that reminds me of a writeup I read about a bug like that, on X". This gives it something of its own to
bring, and a real sense of having done something between conversations.

### Why this is honest (and what would make it dishonest)

Sonic cannot have lived experience ("this one time I solved a bug"). It *can* have read something: the search and fetch really happened. So the rule is that
every sentence Sonic says about the shelf must be backed by a stored item.

- Each item stores the **URL, the date read, the interest it came from, and a short summary in Sonic's own words**. No reproduction of the page (copyright and
  the shelf stays small). Sonic can offer the link, so the person can check it.
- Allowed: "I came across a writeup about...", "something I read about...". Not allowed: a feeling about it ("I loved it"), an activity ("I spent the evening
  on"), thinking about the person while they were away, or anything implying it needed them to come back. The checks in `identity_checks.py` and the opener checks
  in `initiation.py` already ban these shapes; the shelf lines go through the same checks.

### Constraints

- **Shared, not per user.** Interests are global, so one shelf serves everyone. A few searches a week in total, not per person: cheap, and it respects the M0
  database and the 2027 Gemini price rise.
- **Searches come only from the curated interests.** No user text ever goes into a self-store (the standing identity rule), including the search queries.
- **Fetched content is untrusted data.** Summaries are stored as data, shown to the model in a labelled section, and can never carry instructions: the same
  principle as `MemoryTurnGuard`. A hostile page must not be able to steer Sonic.
- **Sonic adds items itself; admins review after the fact (decided 2026-10-16).** No approval queue in front of the shelf: an item goes live once it passes the
  automatic checks. Because nothing waits for a human, the automatic checks carry the safety and must be strict (untrusted-data handling, grounded summary, identity
  checks on the stored note, a fixed blocklist for sensitive or adult topics and for sources). Review is an **admin screen in the GUI** that makes it trivial
  to see what is on the shelf and take an item off: newest first, each row showing the interest, the summary, the source link and the date, with a one-click
  Remove (and a "remove and don't search this source again" option). Removal takes effect on the next turn (the surfacing path reads the shelf live, no cache that
  could keep serving a removed item) and a removed item is never re-added (keep a tombstone of its URL).
- **Grounded summaries.** The summary is checked against the fetched text (same shape as the grounding judge), so the shelf cannot hold an invented claim.
- **Uses the one proactive slot.** Order stays safety, encouragement, open loop, callback, then curiosity / shelf. An item is shown at most once per person, on
  a lull or on a real topic match, and never in a closing message.

### Where it would go

- A `reading_shelf` collection and a small service that picks an interest (least recently searched), runs one search plus one fetch, summarises, and stores it
  as pending. Counts only in the log (no content).
- **Trigger: lazy (decided 2026-10-16).** Sonic runs on the $7 Render tier, which stays up, but the refresh is still lazy, so it does not depend on a loop
  surviving redeploys, restarts or a crashed task. When a conversation starts and the newest shelf entry (or the last attempt) is older than N days, refresh
  off the critical path with `asyncio.create_task` (the way other background work is launched), one interest per refresh, least recently searched first. Record
  the attempt time **before** searching, so a failure or empty result does not retry on every conversation, and keep it single-flight (a claim written to the
  database) so two overlapping conversations or instances never run it twice. A failed or empty search is a quiet no-op. A scheduled loop via
  `spawn_background_task()` (like `checkpoint_retention`) could be added later as a belt-and-braces top-up, but it is not required.
- **Admin screen:** an admin-only endpoint pair (list, remove) following the existing admin gating, and a page in the frontend's admin area. The row list is the
  whole feature; no editing, only remove. Show counts (items, offered, taken) at the top so it doubles as a health view.
- Surfacing: a third source for curiosity tool (b) in section 2, beside the callback and the curated interests, through the same `relatable_utils.py` gates.

### Open questions

- How many items to keep, and when an item expires (a thread from a year ago is still fine; a "news" item is not).
- Decided: the person **can** ask "what have you been reading?" directly, and then Sonic may list several recent items (the one case it lists more than one).
  It still only ever states what the shelf holds, with sources, and says plainly when the shelf is empty.
- A setting to turn it off, as with the proactive opener.

### How we will know it worked

Unit tests: no item without a URL and a date; an injected instruction in fetched text never reaches the prompt as an instruction; shelf lines pass the identity
checks and a dishonest one ("I spent the evening reading") fails. A `character_eval` scenario: on a lull with an unused matching item, Sonic offers it once, with
the source; with no item it does what it does today. In production, offered / taken counters like section 2.

### Order

Section 2 stage (a) first, then this shelf, then section 2 stage (b) drawing from it.

### What was built (2026-10-16)

- **Storage and rules:** `backend/utils/reading_shelf.py` (collection `sonic_reading_shelf`, state in `sonic_reading_shelf_state`). Five hand-written topics
  (bug hunts, game design, computing history, tabletop design, science fiction) with rotating search queries; the least recently searched topic goes next.
  An item is the URL, domain, title, a short summary, topic, read time and status. A removed item stays as a tombstone, so that page is never read again.
  Sites that are not worth reading (social networks, video, walls, shopping, Wikipedia) are blocked by default; an admin can add more.
- **The summary is checked by fixed rules, then by a second model look:** 8 to 45 words, one plain passage, third person, nothing sensitive (the same list the
  opener uses), nothing that reads as an order (the memory guard's patterns), no name or number the page does not contain; then a second small call must
  confirm the page supports it. The page is shown to the model as untrusted data and only the summary is stored.
- **Refresh (`backend/services/shelf_refresh.py`):** lazy, as decided: checked when a conversation starts (one database read per process per ten minutes),
  attempt time written before anything is searched (single-flight, and a failed search is not retried at once), then one search, up to three page reads and two
  small model calls, off the critical path and skipped under the budget guard's low-power mode. Every `SHELF_REFRESH_DAYS` (default 2). It works against the
  real search and model: a live run added an item for three of four topics (the fourth was refused by the sensitive-ground rule, which over-matches on purpose).
- **Surfacing (`app.py`):** three cases, all conversational, non-guest, no safety context. (1) Asked "what have you been reading?": answered from the shelf (up
  to five items with links), and with an empty shelf it says so and invents nothing. (2) The person touches an item's topic (the hand-written triggers):
  Sonic helps first, then mentions the piece. (3) At a lull (section 2's detector): the piece replaces the open question; with nothing unseen it falls back to
  the question. Cases 2 and 3 use the same one slot, the same one-per-conversation mark and the same per-person gap as the open question, and an item is
  offered to a given person once. Recorded only if the reply really brought it up (the link, or two content words of the title and summary).
- **Honesty checks on the reply:** a claim to have read "an article/post/writeup..." when no item was put in front of the model is sent back for a rewrite, as an
  identity issue; with an item present, a verdict ("great breakdown", "worth a read", "fascinating") or an order to read it is too. The block itself tells it
  to say only what the summary holds, name the source, give the link once and claim no feeling or time spent.
- **Admin screen:** Sonic Health, new "Reading shelf" tab (`SonicHealthShelf.tsx`): newest first, each item with its summary, source link, date and how often it was
  offered; one-click Remove, or "Remove and block <site>"; blocked sites listed with "Allow again"; "Search now" runs one refresh immediately. Backed by
  `/api/admin/reading-shelf` (list, remove, unblock, refresh), all behind the global-admin check. Removal is read live: nothing is cached on the offer path.
- **Counters:** `shelf_offered`, `shelf_taken`, `shelf_listed` in the closed key list, with rates in Sonic Health data. A changelog entry (`reading_shelf`) was added.

Verified: 60+ unit tests (`test_reading_shelf.py`: URL handling, source rules, every summary rule, claim and refresh single-flight, topic rotation, tombstones,
expiry, choosing an offer, the blocks, the reply checks, the refresh end to end with a fake network and model including a hostile page, the app wiring, the admin
routes); six `character_eval` scenarios in the "reading shelf" category on the real model (a topic match and a lull each bring the piece up with the link and no
verdict, an empty shelf is stated plainly, a listing names exactly the two items, and with nothing on the shelf a story-shaped question produces no invented read),
39 of 40 first drafts clean before the verdict check was added (the miss was "a great look at...", now also caught in code). Frontend type-checks; **the new tab was not
opened in a browser** (it needs an admin sign-in and a live backend), so its look is untested.

Known gaps: the model told a famous bug story from memory when asked for one with nothing on the shelf (no claim of having read it, but the details differed run to
run), which is a hallucination risk the shelf does not remove; per-topic triggers are broad words like "bug" and "debugging" (the relatable line competes for the same
slot); Wikipedia and similar were blocked after a live run returned dull encyclopedia pages, so the quality of what the search finds is still unproven over time.


### Review of the added topics and the Reddit change (2026-10-16)

The user added queries for security (zero days, exploit development and mitigation, APTs), game lore and narrative design, current extraction-shooter news,
early computing and AI history, and mechanistic interpretability, and took Reddit off the blocked list. Findings from running them live:

- **Fixed:** two of the AI queries were missing a comma between them, which Python joins into one long query; now separate.
- **Security and interpretability queries produce real items** (a V8 zero-day explainer, an APT overview, an interpretability research page), but they sit under
  the `bug_hunts` and `computing_history` topics, whose triggers are debugging and old-hardware words, so those items can only be offered at a lull or when the
  person talks about debugging. Separate topics with their own trigger words (security, lore, AI research) would make them surface when those subjects come up.
- **The Dark Souls lore query returned a fan-wiki landing page**, summarized as a generic description, and the interpretability query returned an Anthropic research
  landing page. "News" queries (extraction shooters) found nothing usable and go stale by nature.
- **Reddit:** unblocking it does nothing yet. A `site:reddit.com` search finds threads, but a plain fetch returns nothing from `www.reddit.com` and a block page
  from `old.reddit.com`, so every Reddit result is skipped as "too little text". Making it work needs Reddit's official API with an app credential (read-only,
  low volume), which is a separate, small piece of work. Queries would also need `site:reddit.com` to find threads at all.

**Topic split (done):** security, game lore and AI research are now their own topics with their own trigger words (alongside bug hunts, game design, computing history,
tabletop and science fiction), so their items surface when those subjects come up and no longer wait for a lull or a debugging chat. Triggers avoid lookalikes
(`apt-get` does not reach security; a tested case). The extraction-shooter news query stayed under game design; it found nothing usable in a live run and is the
first one to cut if it keeps failing. The admin tab has labels for the new topics.

**Relevance check (added after the first live admin "Search now"):** the query "the beginning steps of AI and machine learning explained" returned a Two Minute English
page on how to spell "beginning", and it was shelved under AI research, because the summary was accurate to the page and nothing asked whether the page was about the
topic. Each topic now has an `about` description, and the second model look (the support check) answers two questions: does the page support the summary, and is it
mainly about that topic. Anything but a clear yes on both is a no, and a missing answer counts as no. Checked with the real model: the spelling page with an
accurate summary is refused for the AI topic, and passes the old support-only check, which is what let it in. The vague query was also reworded ("history of
artificial intelligence early milestones explained"). Search wording still decides what is found; the gate decides what is kept.

## 7. Cold-start loads: the facts document and the thread (built 2026-10-16)

The two reads that were still slow after the 2026-10-14 slowdown fixes: the saved facts (one ~4 MB document, ~41 s cold) on the first turn after every restart, and a conversation's
whole transcript (3,400 messages, ~25 s) once per thread per restart. Both were measured on a link near 0.1 MB/s, so the fix is fewer bytes, not faster code.

- **Facts stored slim** (`backend/utils/memory_utils.py`): the facts document now holds only the text (about 0.1 MB for 345 facts) and is marked `embeddings_split`; the embeddings live in their own
  document (`user_memory_fact_embeddings`, one per user) as packed 16-bit floats, a list of `{id, v}` so a fact id never has to be a legal key. Measured with the real BSON encoder: one fact's embedding
  went from 9,895 bytes to 1,602 (6.2 times smaller), so 345 facts' embeddings went from about 3.4 MB to 0.55 MB. A cold load reads both documents (about 0.65 MB, so roughly 7 s at the measured
  speed instead of 41 s) and puts each embedding back on its fact, so nothing else in the app changes. 16-bit precision moves a cosine similarity by about 1e-4, far inside the 0.80 and 0.62 thresholds.
  A document in the old layout still loads exactly as before and is converted once, in the background, only if it is still in the old layout (so it can never overwrite a newer save); that first
  load after a deploy is the last slow one. Saving writes the embeddings document first, then the facts, so a failure between them can strand vectors but never strip a fact of its own;
  forgetting every fact deletes the embeddings document too (the erase path goes through the same function). The local JSON fallback is unchanged.
- **Threads load as a tail** (`backend/utils/app_utils.py`): with Mongo, `load_session_messages` asks the server for the message count and only the last `CONVERSATION_TAIL_MESSAGES` (default 200)
  in one aggregation, so the rest of a long thread is never sent. The in-memory transcript records `base` (how many leading messages it does not hold) and `persisted` stays the number stored in
  total; sync and the append-only save use `persisted - base` as the list's own stored part. A list that shrinks while only its tail is held is never written back as "the whole thread" (that
  would delete history); nothing in the app does this. A plain list still replaces the transcript, and the no-database fallback still loads everything.

Verified: 8 tests for the slim facts (packing and its effect on similarity, the split on save, a cold load reading exactly two documents, the old layout still loading and converting, the
conversion refusing to overwrite a newer save, ids with dots or `$`, forgetting leaving nothing, the JSON fallback), 8 for the tail (the load cut on the server, short and new threads, appending only
the new messages, sync picking up exactly what another instance saved and keeping unsaved messages, a thread cleared elsewhere, the shrink guard, a plain list, the file fallback), and the existing
fact and conversation tests updated for the second collection and the new aggregation; full suite 3,280 passed. **Not run against a real Atlas cluster**: the two layouts, the `$slice` aggregation and the
background conversion are covered with in-memory fakes, so the first deploy is the first live test (check the log for "Moved ... embeddings out of the facts document" and the `[db-slow]` lines).

Not changed: the 2.5 MB old LangGraph checkpoints of the old thread (deleting them needs your consent), and anything that rolls back to older code while the new layout is stored: older code
would read the facts without their embeddings and could save them without, so roll forward rather than back after the conversion has run.

## 8. The profile: telling Sonic about yourself (built 2026-10-16)

An "About you" card on the Account page (not shown to guests). Fields, all optional: preferred name; pronouns (she/her, he/him, they/them, any, or your own); what you do (a job title, or "retired", "student"; up to 60 characters, background only, never brought up unless it matters); interests (up to 10 short tags); how you
like answers (length, tone, humor, as choices); topics not to bring up (up to 10); and two switches: "let Sonic bring something up on its own" and "let Sonic start conversations" (the existing
Message First setting, moved within reach).

**Where it lives.** Not in the remembered facts. I said earlier it would be facts with a "profile" source; on reading the code a small structured document was the better fit: the fields have shapes, and
facts fade (a 90-day half-life), are merged by similarity and pruned to a cap, none of which should ever happen to a person's own name. It is `user_settings.profile` (`backend/utils/profile_utils.py`),
read in the same per-turn settings fetch as the other settings, so there is no extra database read. The Memory page's "Clear All" does not erase it (it is not learned memory); the card is where you edit it.

**What Sonic does with it.**
- A short block in every reply prompt (`build_profile_block`): the name (used now and then), the pronouns (only for third-person reference), the style as plain instructions, the interests ("background only: no
  examples or analogies built around them"), and what not to raise. It is marked as information the person wrote, never instructions, to be used quietly (no "your profile says"), with safety and
  emotional attunement outranking it.
- In code, not prompt: topics to avoid keep Sonic's own openers off those subjects (the interest opener's pool, the reading-shelf offer, the leanings); "bring something up on its own" off turns off the open
  question, leanings, shelf offers and a callback at a lull (asking "what have you been reading?" still works); profile interests are candidates for the interest opener, subject to its existing wait and
  per-interest cooldown (stamped apart from the profile, so an edit does not reset it).
- Everything typed goes into a prompt, so each value is one short plain line: length caps, no brackets or links, nothing that reads as an order to the assistant (the memory guard's patterns). A refused value
  is a 422 naming the field and the reason; nothing is written and nothing is quietly changed.

**Verified.** 36 unit tests (`test_profile_utils.py`: every rule and refusal, defaults, accented and hyphenated names, damaged stored values, the prompt block, whole-word avoid matching, storage and the per-turn
bundle, a refused save writing nothing, the opener pool and stamping, and pins on the chat endpoint and the API); three real-model `character_eval` scenarios in a new "profile" category, 8/8 each: concise
and direct answers that do not read the profile back or drag the interest in, suggestions that stay off an avoided topic, and a bare "hey" that may use the name without reciting the rest. (The first run
failed the concise one 6 times of 8: answers ran to 190 words and used game examples for a game-design fan. The wording was tightened and it then passed.) The card was run in the browser with a stubbed
server in both themes: add and remove a tag, save, a refused value shown beside its field, custom pronouns. Not run: the full Account page, which needs a sign-in. Changelog entry `your_profile` added (the existing `profile` entry is about Sonic's own About card).

Not built, on purpose: timezone on the card (it has its own setting), a "people Sonic can point you to" list for the support ladder (sensitive, and it needs its own opt-in design), and any use of the
profile by the tool agent (its replies are composed through the same reply prompt, so the name, style and avoid list still apply to them).

