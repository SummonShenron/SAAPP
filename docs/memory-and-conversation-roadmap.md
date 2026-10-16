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

## 4. Chat send errors hide the real cause (not yet built)

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

## 5. Tool agent memory: correct answers about code from earlier messages get flagged and rewritten (not yet built)

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
