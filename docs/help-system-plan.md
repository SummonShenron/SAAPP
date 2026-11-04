# Help system redesign: one Help Mode, in the style of FromSoftware's menus

Written 2026-11-05 from Jack's idea. Status: STAGES 1 TO 3 BUILT (2026-11-05, uncommitted): the machinery, the top-right toggle, and 136 tips covering the shell, the chat page and every other page. Reference implementation read: `summonshenron/circuit` (public), `frontend/src/App.tsx` (commit d580cec).

## The idea
Today there are three ways in to help, and they do not feel alike. The new one is a single **Help Mode**: the `?` at the top right turns it on; while it is on, pressing any part of the app explains that part instead of using it ("what it does, when to use it"); pressing `?` again or Escape leaves it. FromSoftware's menus work this way: help is a layer on the thing itself, not a manual somewhere else.

## What the three are now (confirm which is the odd one)
1. The top bar `?` (`TopBar.tsx`) opens `HelpPanel`: a full-screen guide with topics (`helpTopics.ts`, 530 lines of words in `helpContent.tsx`).
2. The user chip's menu, "Help" (`UserMenu.tsx`, `Rail.tsx`), opens the same guide. This one stays as it is.
3. The chat settings' Help chip (`ComposerChips.tsx`, and the phone strip's `SessionRail.tsx`) opens a small popup of tips about those settings. This looks like the odd one: a different kind of thing in a different place.

## How Circuit does it (what is worth copying, what is not)
- One boolean, `helpMode`, toggled by a `CircleHelp` button in the toolbar, highlighted while on.
- While on, a click on a helpable element is captured (`onClickCapture`, `preventDefault`, `stopPropagation`) and opens a floating card near the cursor instead of doing the click. The card has three parts: **What it does**, **Use it when**, **Example**.
- Content lives in three registries: `blockHelp` (by block kind), `panelHelp` (by region) and `fieldHelp` (by `"<kind>.<field>"`).
- Copy: the mode, the capture-phase interception, the card with a fixed shape, and registries as the single source of words.
- Do not copy: it is wired by hand (a `Field` wrapper and `helpMode` passed down as a prop through every component), an element with no entry just behaves normally (so in help mode a click can still do something destructive), and nothing shows which things are helpable. That is fine for one screen of 30 blocks; SAAPP has many screens.

## Layout rule for every tip (agreed for this run)
Every Help Mode card has the same four parts, in this order, and nothing else:
1. **Title**: what the thing is called (`title`).
2. **What it does**: one or two plain sentences (`what`).
3. **Use it when**: when to reach for it (`when`, optional).
4. **It will not**: what it will never do, where that is worth saying (`wont`, optional). Keep it to the hard limits, such as "Send anything until you press Send".
No lore, no flavour text. The layout is borrowed from the Circuit design; the words are SAAPP's own and must be true to the code.

## Proposed design for SAAPP
**One declarative hook, no prop drilling.** Mark any element with an attribute: `data-help="chat.toolModel"`. A single provider at `Layout` level holds `{ active, topic }`. While active it installs ONE capture-phase listener on the document: the nearest `[data-help]` ancestor of a click opens its card, anchored to that element's box; adding help to a component is one attribute and one registry entry.
**Everything is intercepted while it is on**, not only elements that have a tip. A click on something with no tip opens a card that says "No tip for this yet", so a stray press in Help Mode can never send, apply, delete or pay. (Jack to confirm.)
**Show what is helpable.** While on, helpable elements get a quiet outline and the pointer becomes `help`; on desktop, hovering one puts its one-line summary in a bar at the bottom of the window (the Souls menu's text line), and pressing it opens the full card. On a phone there is no hover: pressing opens the card straight away.
**The card** (the same shape as Circuit's): title; what it does; use it when; and where it helps, "what it will not do" (for example the runner never runs a command without a press). A link, "Open the full guide", goes to the existing `HelpPanel`, which stays as the long-form reference.
**The entry points (Jack, 2026-11-05).** The top-bar `?` stops opening the guide and becomes the Help Mode toggle: it replaces one way to the standard help menu. The standard help menu (`HelpPanel`, the guide) stays exactly as it is and is still reached from the user chip's menu ("Help", `UserMenu.tsx`). The Help Mode card also carries an "Open the full guide" link to it. Still open: what happens to the chat settings' Help chip and the phone strip's help button (my suggestion: remove them and move their tips onto the settings they describe).
**Registry** (`local/src/components/helpRegistry.ts`): id to `{ title, what, when?, wont?, guide? }`, written in plain words, one place. Tips that depend on state (for example "the Tool model is Claude right now") come later as functions of the page's settings; version 1 is static.
**Accessibility:** the toggle is a real button with `aria-pressed`; in Help Mode Enter or Space on a focused element opens its card; the card is a dialog with focus moved into it and returned on close; Escape closes the card first, then the mode.
**Tests that keep it honest** (the tripwire pattern used for the Privacy page): every `data-help="..."` id in the source exists in the registry; every registry entry is used somewhere; no card is empty; the listener is removed when the mode is off; with the mode off a click is untouched.

## What it costs
- **The machinery** (provider, hook, card, bottom bar, styles, the tests above): about a day. It touches `Layout.tsx`, `TopBar.tsx`, `UserMenu.tsx`, `ComposerChips.tsx`, `SessionRail.tsx` and adds four small files.
- **The tips are the real work:** each is a few true sentences. Estimated 80 to 120 across the app. Do it in stages, in the order people meet them: (1) the shell and the chat page (top bar, rail, composer and its five chips, send, attach, message actions, the session column's cards, the plan card, the Apply card); (2) Memory, Account (the runner, paid use), Integrations; (3) Tasks, Meetings, Reading, Self Service, Tidy up, Sonic Health.
- Tips are written from what the code actually does, and the ones that touch privacy or cost link to the pages that state them (Privacy, Terms).

## Open questions for Jack
1. The top-bar `?` becomes the Help Mode toggle and the guide stays under the user chip (decided). What about the chat settings' Help chip (and the phone strip's help button): remove them, with their tips moved onto the settings they describe?
2. Intercept every click in Help Mode (my recommendation), or only helpable things like Circuit?
3. The hover line at the bottom on desktop as well as the card on press: yes or no?
4. Stage 1 first (the shell and the chat page), then the rest?
5. The name: "Help Mode", or something in Sonic's voice?
6. Should Sonic be able to open a tip itself ("press the shield icon and I'll explain it")? Not in version 1; it would use the same registry.

## Decisions (Jack, 2026-11-05)
Intercept every press while Help Mode is on (yes). Stage 1 first, the shell and the chat page (yes). The name stays "Help Mode" (yes). The top-right `?` is the toggle; the full guide stays under the user chip's Help item (yes). The bottom hover line is NOT built (Jack was unsure what it meant; it is a small addition later). The chat settings' Help chip and the phone strip's help button are left as they are until Jack decides.

## Stage 1 as built
`helpModeLogic.ts` (pure: tips lookup, which keys open a tip, card placement, id scan), `HelpMode.tsx` (provider, the document-level capture listeners, the card, the banner), `helpTips.ts` (63 tips), `__styles__/HelpMode.css`, `helpMode.test.mjs` (9 tests, including the one that fails when a page uses an id with no tip or a tip has no page). Wired into `Layout.tsx`, `TopBar.tsx`, `Rail.tsx`, `UserMenu.tsx`, the chat page, the composer chips, the session column and the plan, Apply, runner-check and steps cards. Checked in a browser on a page mounting the real top bar and navigation under the real provider: outlines and banner while on; a press explains and does not act; a button with no tip says so and does not act; Enter in a focused message box opens its tip and sends nothing; Escape closes the card and then the mode; "Open the full guide" leaves the mode and opens the guide; with the mode off everything behaves as before. NOT checked on the real chat page (it needs a login).
Known limits: a disabled button gets no click from the browser, so a locked chip cannot explain itself; a popup that is already open when the mode is switched on is not closed; the Help Mode card does not describe things inside the open model popup (it explains the chip).

## Stages 2 and 3 as built
73 more tips, one page at a time: Memory (17), Account including the profile, chat, runner and paid-use cards (30), Integrations (5), Activities (11), Office Hours (10), Reading (2), Self Service (3), Tidy up (5) and Sonic Health's menu (2, for admins). Tips sit on cards and on the controls whose meaning is not obvious, not on every field: where a page already says what a field does underneath it, the card-level tip is enough. Written from the page text and the code (for example the task kinds and meeting coaching levels use the pages' own descriptions). The card's two labels now end in a colon ("Use it when:", "It will not:"). Checked on the real Tidy up page under the real provider; the other pages need a login or data, so they are checked by the coverage test (every id has a tip, every tip a page) and by tsc, not by eye.
Not covered: the long tail of Sonic Health's own charts and tables (the menu is covered; each chart is not), the Landing, Privacy and Terms pages (they are not inside the app shell), and the model popup's own columns.
