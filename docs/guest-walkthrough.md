# What a first-time guest sees

Walked on 2026-11-05 against the local app in a clean browser (saved data cleared), desktop and phone width. No message was sent, so no model call was made. To repeat it, open a private window.

## Steps
1. **Landing page.** The new logo; "Sonic Assistant: RAG Pipeline & Multi-Tenant Data Isolation System. Built by Jack Harper."; an orange "Sign In / Sign Up"; a notice that the first request after inactivity can be slow while the server spins up; links to Privacy and Terms. On the right, under the logo, "Sign in as Guest (One-Click Sandbox)" with "Guest mode provides interactive, safe access to sandboxed features and Jack's portfolio knowledge base."
2. **Press the guest button.** It signs in at once (no account) and opens Chat.
3. **Chat, empty.** The robot, "Sonic Assistant", and four suggested questions: About Jack ("Who is Jack Harper, and what has he built?"), About this project ("What is Sonic Assistant, and why did Jack build it?"), Watch it work ("What was changed in the last pull request?"), Under the hood ("How does Sonic Assistant keep one person's data separate from another's?"). The message box says "Ask a Question...". Pressing a card sends it (a model call).
4. **Left navigation.** Only Chat (Self Service is hidden for guests since 2026-11-05), and "No conversations yet." under Recent (a guest cannot list anyone else's chats). The bottom-left chip says Guest.
5. **Right column** (on a phone, a strip of icons): Connection ("No folder connected"), GitHub (a target repo box), Gauges (a usage ring), This conversation (a scope menu with one choice, "Affiliate C"), and Chat settings (Thinking, Answers, Export, Help). No plan mode, no model choices, no runner.
6. **Top right.** The trace button, the session column toggle, and the `?`, which is Help Mode: press anything to see what it does; Escape leaves it.
7. **Self Service.** A "Multi-Tenant Ingestion Gateway" page: the knowledge base "Affiliate C", a locked upload step, and "No documents indexed inside Affiliate C."
8. **Account settings** (user chip, bottom left). "Signed in as Guest (shared sandbox)", a note that guests share one temporary identity, the robot toggle, the theme, and a Terms link. No profile, memory, runner or paid use.

## Rough edges found
1. **The landing line said how it is built, not what it is for.** FIXED 2026-11-05: the headline is now "An AI assistant that remembers you, helps you code, and runs your checks." and the architecture label is the small line under it.
2. **"Affiliate C" was an internal name** (scope menu and Self Service). FIXED 2026-11-05: `getAffiliates` (api.ts) renames the built-in knowledge bases with `KB_LABELS` (`nameKnowledgeBases`), so the guest scope reads "Jack's portfolio". Personal and unknown knowledge bases keep their server name.
3. **Self Service was a dead end for a guest** (jargon, a locked upload and an empty list). FIXED 2026-11-05 (Jack's rule: hide it unless the person may add documents to a knowledge base): the navigation link now shows only for a global admin or someone with the Ingesters role on a knowledge base they can use (`selfServiceAccess.ts`, the page's own rule). A guest sees only Chat. The page is still reachable by its address.
4. **All guests share one usage limit.** The usage ring reads "Your usage: 6% of your 5 hours", but it is the shared guest identity's figure, and the per-person daily and five-hour limits apply to that one identity. A few visitors at once can use it up for each other, and the message then says "your own usage limit".
5. **A guest can set any GitHub repo** in the target-repo box (it falls back to the server's token). Worth confirming that is intended.
6. **Phone:** the icon strip overlapped the edge of the right-hand welcome card. FIXED 2026-11-05: the empty chat's cards keep 38px clear of the strip (`SessionRail.css`); measured at 375px, no card touches the strip.
7. Fixed in this pass: pressing a welcome card in Help Mode said "No tip for this yet"; it now explains the card.
