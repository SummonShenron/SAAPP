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
- **Look (2026-10-16):** the app is flat (no shadows on bubbles, the input or the example cards, no lift on hover). The **dark theme was redone**: a deep navy
  backdrop, **a plain blue bubble for your own messages and orange as the accent, used at the live edges (the input's blue-to-orange glow, the active rail item, the caret and selection, the labels) and not as a fill (a solid orange bubble and a thin orange edge on the blue one were both tried; plain blue won)**, instead of the old red-and-black "Shadow" look. It is one token block, `.theme-shadow`
  in `index.css`; the hardcoded dark hexes in the other stylesheets now point at its tokens (`--panel-bg`, `--popover-bg`, `--card-bg`, `--input-bg`,
  `--line`), so the next change is one block. The logo's gradient is now blue to orange (`LOGO_GRADIENT_STOPS` in `logoMarkData.ts`, `logo-mark.svg`,
  `favicon.svg`). Cyan and red were tried and set aside (cyan read edgy and belongs to the BTY site; red was the old Shadow look). One caveat to watch:
  the usage ring and budget banner use amber and orange for "notice" and "throttled", which now sit near the accent. Left over: the dead `.hero-banner`
  rules in `index.css` still carry the old red gradients, and the theme is still named `theme-shadow` in code.
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
