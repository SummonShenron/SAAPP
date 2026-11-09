# Sonic's global knowledge base, and the grounded-mode rework

Status: PROPOSED, not started (2026-11-03). Jack's idea, said to Sonic in chat and then put here so it is not lost. Needs a little tweaking; the design is sound.

## What Jack asked for

1. **A global knowledge base for Sonic** ("a place where all of your reading shelf articles eventually go"): a library of Sonic's own, readable by everyone in grounded mode.
2. **A way to read a site and make a document from it**: given a URL, fetch it, work out what it is (a band: get details about the band; an article: get the text, and so on), write a document with those details, and **ingest it straight into the global knowledge base**.
3. **Rework grounded mode** to include tool calling and web search/browsing **by default**, and **remove the follow-up** "would you like me to break out of my RAG constraints?" when it cannot find an answer.

So Sonic has its own grounded, real knowledge to pull from when it is in grounded mode.

## What I think (design review)

The design is sound and mostly assembled from parts that already exist (below). The tweaks are about three things: what gets stored, who may write to it, and what "grounded" means once the web is in it.

**1. Store cards, not copied pages.** The reading shelf already does this: a short summary in Sonic's own words, the URL, when it was read, never the page copied. Keep that. It limits storage, copyright exposure and the amount of someone else's text that can ride into every user's prompt. For a *knowledge* base (which answers factual questions, unlike the shelf, which only gives Sonic something to mention) the bar is higher than the shelf's: every fact in a card must be traceable to the fetched text (the grounding check the memory writes already use), not just "the summary is supported overall".

**2. Everything ingested is visible to every user, so writing needs a gate.** Anything a user pastes could be private (a link to their own document, a page that reveals who they are). Rules: only public pages (url_reader already refuses private and loopback addresses); a URL a *user* supplies goes to an admin review queue, never straight into the global base; no user text ever reaches the fetch queries or the card (the shelf's own rule). Sonic's own finds can follow the shelf's model (checks, then live, an admin can remove in one click) once the checks are proven, and only for allowed sources at first.

**3. A stored page is a persistent prompt-injection vector.** The shelf treats fetched text as data and stores only a checked summary. Cards in the global base are retrieved into *every* user's prompt, so a poisoned card would hit everyone, repeatedly. Same defences: cards pass the "nothing that reads as an order" check, are shown to the answering model quoted and labelled as source material, and carry provenance so a source can be removed and never re-added.

**4. Start with one URL, not a crawl.** "Crawl sites" can wait. A single fetch, a type guess and a card is the whole first version. A crawler needs robots.txt, per-domain rate limits, depth and size caps, dedupe by canonical URL and content hash, and a budget. Each is a project of its own.

**5. Keep the number of types small.** Two to start: `article` and `entity profile` (a band, a person, a product). The structured-data extractor in url_reader (`structured_data_text`, which reads the page's own JSON-LD) already gives a head start on bands and organisations. Entity cards can also feed the per-knowledge-base graph (`graph_db.scoped_context`).

**6. Quality, or retrieval gets worse as it grows.** The memory notes already record junk being injected by a loose recall cutoff. A global base that grows with arbitrary pages needs: a score threshold, hits labelled by layer (your library / Sonic's base / the web), at most a few global hits per turn, and expiry or refresh so old facts do not stand forever.

**7. Bounded cost and storage**, like the shelf: a per-day ingest cap, an embedding budget, a ceiling on the collection's size, tombstones for removed items.

### Grounded-mode rework: what changes in meaning

Today "Grounded" (rag_mode `strict`) means: answer only from the person's knowledge base. With tools and the web on by default it can no longer mean that. Redefine it as: **every claim is backed by a source Sonic can name (a library document, Sonic's base, a tool result or a web page), and when nothing backs it, it says so**. "Open" stays the mode where it may also use the model's own knowledge. Rename the help text and the "Answers" chip wording to match, or people will think Grounded still means "only my files".

Removing the follow-up removes a consent step, so replace what it did:
- **Privacy:** a question now goes to a search provider (and a cloud browser) without the person agreeing each time. Say so on the Privacy page and in Help. Send only the topic terms of the question, never when the turn is in a safety context, never from guests, and not while the budget guard is throttling (low-power mode).
- **Cost and latency:** web steps run inside the existing step budget; add a counter and a per-user daily cap.
- **Injection:** web pages are untrusted tool results (the existing untrusted-content rule applies; they cannot trigger actions).
- **Honesty:** when the KB, the tools and the web all come up empty, the answer is "I couldn't find this", not a guess. This needs an eval before and after the change.

## What already exists (reuse, do not rebuild)

| Need | Where it is today |
|---|---|
| A real-read record with summary, URL, time, topic; fixed checks plus a second-model support check; admin removal; bounded storage | `backend/utils/reading_shelf.py` (rules in its docstring), searching in `backend/services/shelf_refresh.py` |
| Fetch a public page safely, extract main text and structured data | `backend/services/url_reader.py` (`check_url_allowed`, 2 MB cap, redirect cap, `structured_data_text`, `try_lightweight_fetch`) |
| Libraries and who may read them | `backend/services/library_search.py` (`usable_scope`, `vector_docs`: an empty scope reads nothing) |
| Who may write to a library | `backend/utils/isolation_kb_utils.py` `verify_user_ingest_access`; upload path `app.py` `upload_and_ingest_documents` |
| Entities and relationships per knowledge base | `backend/state/graph_db.py` (`scoped_context`, per-KB graphs in MongoDB) |
| The propose-then-approve card pattern | `propose_local_edits` and its Apply card (a `propose_kb_entry` tool can copy it) |
| Web search, `read_url`, the browser tools | tool agent menu in `backend/services/agent_workflow.py`; the `browser_*` tools run only on the OLD engine today (the word "browser" routes a turn there: `native_loop._OLD_ENGINE_ONLY_RE`) |

## Code the grounded rework touches

- `backend/utils/fallback_utils.py` `rewrite_fallback`: the "break RAG restrictions" message and the `pending_action` of type `web_search` (status `hitl_approval_required`).
- `app.py`: `force_web_search` (about line 870), the `relevance_grade == hitl_approval_required` handling (about line 1118), the rag-mode setting endpoints (about lines 3242-3251, values `strict` / `open`).
- `backend/components/constraints.py`: `GROUNDING_BLOCKS` (line ~203) and `get_system_prompt(rag_mode=...)` with `BASE_RAG_CONSTRAINTS` / `OPEN_ENDED_CONSTRAINTS`.
- Frontend: the "Answers" chip (`composerChipsLogic.ts`: grounded / open) and `helpContent.tsx`, `Privacy.tsx`.
- Tests: `test_main.py` has a test that the fallback asks before searching the web (`test_rewrite_fallback_asks_before_searching_the_web_when_the_documents_do_not_answer`); it will be replaced, not just deleted.
- `backend/components/sonic_changelog.py` (a true "I can now..." entry when it ships) and `backend/utils/self_counters.py` (counters first, per the project rules).

## Order

0. **Finish moving off the old engine** (planned for 2026-11-04): the browser tools only exist there, and Sonic itself said the pipeline waits on "the browser tool running natively".
1. **Grounded-mode rework** (tools plus web by default, follow-up removed, citations, the "I couldn't find it" rule, privacy text). Valuable on its own and needed before anything is stored from the web. Write the grounded-behaviour eval first.
2. **Global base v1 (with the bounded-growth rules at the end of this document: ceiling, eviction by use, tombstones, use counters first):** a public library (for example `Sonic_Global`), appended to every signed-in user's scope in grounded mode, written only by the pipeline. One tool, `propose_kb_entry`: fetch one URL, guess the type, write the card, show it for an admin to approve (the Apply-card pattern), then ingest with provenance. Two types.
3. **Shelf to base:** promote chosen reading-shelf items into the base as `article` cards, after the same checks, with admin approval at first.
4. **Entity cards and graph edges; refresh and expiry; allowlisted sources that can ingest without approval; per-domain limits.** Only then anything like crawling.
5. Throughout: counters in `self_counters.py` first, Sonic Health rows, a retrieval/grounding eval (answers from the base, cites it, does not inject irrelevant hits, resists a poisoned card), a changelog entry at each step that ships.

## Decisions (Jack, 2026-11-03)

- **Who writes:** admins only. **Guests can read** the global base.
- **Copyright (left to me): summaries and short quotes with the source link**, never a copied page. A card keeps a checked summary, the facts traceable to the fetched text, at most a few short quoted lines, the URL, the title and when it was fetched.
- **On demand, not autonomous (for now).** An admin asks for it in chat, and Sonic recognises phrases such as "go learn about <topic or URL>" (and similar: "add this to your knowledge base", "learn this site"). Nothing is fetched or stored without such a request, and nothing runs in the background. Step 3 of the order (shelf items promoted into the base) is therefore also manual: an admin asks for it.
- **Rename "Grounded" to "Cited"** in the Answers chip, its help text and the prompt wording ("Open" stays). The setting's stored value (`strict`) can stay as it is: only the words people see change.
- Still open: the monthly budget for default web search in Cited mode.

Consequence for the build: v1 needs a trigger-phrase detector (a router flag or a pattern, like the other request detectors), an admin check in front of `propose_kb_entry`, and a Cited-mode rule that the global base is always part of the readable scope (guests included).

## Revision 2026-11-07: Sonic's own learned knowledge, shared by everyone (Jack's framing, decided the same day)

**The idea, in Jack's words:** a knowledge base for Sonic itself. When it goes out and has to read about a topic it did not know, or was told about, it fetches the web content, ingests it into its own knowledge base the way a user would, and then references that knowledge in real conversation. **Decided: shared across all users** (one base Sonic learns into, readable by everyone, guests included).

**How this differs from the 2026-11-03 plan above.** That plan was a library people query in grounded mode, with a grounded-mode rework alongside. This is Sonic accumulating what it learns while it works. The grounded rework becomes a separate, later item: counters (2026-11-07, 2,589 turns) show strict-grounded answers are 2 turns of the traffic, so it is not what justifies this. What justifies it is that today a mid-conversation web read is thrown away, so nothing is learned for next time.

**What already exists (see the table above):** the reading shelf (Sonic's own reads, summary cards, checks), `url_reader`, the web tools, the library tools. The new parts are the write step after a research read, and a read tool.

**v1 shape**
- **Write:** after Sonic reads a public page to answer something, or is asked to "go learn about X", it proposes a card: a short summary in its own words, the URL, the title, when it was read, at most a few short quotes. Never a copied page. Fixed checks first (nothing that reads as an order to a model, nothing from a private address; the shelf's second-model support check).
- **Read:** one tool, `search_my_knowledge`, called when a topic might be one it learned. NOT injected into every prompt: that keeps prompts small and means one bad card cannot ride into every reply.
- **Provenance and freshness:** every card shows its source and date to the answering model, quoted and labelled as source material; cards expire or are refreshed.
- **Visibility:** Sonic Health lists what it has learned, with removal; counters first (`self_counters.py`).

**What "shared" forces (do not skip any):**
1. **Two stores, never mixed.** What a PERSON tells Sonic goes to their own memory (as now). Only what SONIC reads from the public web goes into the shared base. A user's claim is never written to the shared base: that is both a poisoning route and a privacy leak.
2. **No user text in a card, and no sign of who asked.** A card's title and body come from the page, not from the question. Nothing records which person's question caused it.
3. **The topic itself can reveal a person.** What Sonic chooses to learn about can expose what someone was privately researching (a health condition, a named private person, a legal problem). So: never write from a turn with an emotional or safety context; never from a guest; skip topics that contain a person's name, an address or other personal data from the question; and (v1) every card is APPROVED by an admin before it goes live, the Apply-card pattern (`propose_local_edits` is the model). Allowlisted public domains may skip approval later, once the approved cards show it is safe.
4. **A poisoned card is read by everyone.** The injection defences above apply, plus a per-day write cap and a ceiling on the collection's size.
5. **Cost:** the embedding and the second-model check per card; bounded by the daily cap.

**Where it sits in the order:** after the first-ten-minutes experience for new signed-in users and the funnel counters; it pays off only with repeat topics and more than one regular user. Before building: add a counter for how often a web read repeats a domain Sonic has read before, so the value is measured, not guessed.

## Bounded growth: the base is a cache of the web, with a hard ceiling (Jack asked, 2026-11-07)

**The question:** how does the base never fill up? Jack's first idea was decaying embeddings. **Decision:** not decay alone. Decay makes size depend on time, so a burst of ingestion can fill the tier long before anything decays; it deletes stable facts nobody asked about lately; and an embedding itself does not change (only a card's score, or the card, can decay). The base is treated as a CACHE of the web instead: forgetting is cheap, because Sonic can fetch the page again if it is asked again.

**Real numbers (measured read-only 2026-11-07, Mongo free tier M0, 512 MB):**
- The database holds about 158 MB of storage today (142 MB data, 5 MB indexes); the largest collection is `fs.chunks` (uploads, 87 MB), then memory chunks (22 MB) and their archive (18 MB).
- An embedded document is about **10.3 to 11 KB** (768-dimension `gemini-embedding-001` vectors: 10,315 bytes for one archived memory chunk; `documents` averages 10,964 bytes). So a card costs about 11 KB.
- **2,000 cards is about 22 MB typically**, about 4% of the tier. The TESTED worst case (every character counted as four bytes, a full waiting queue and full tombstones) is about 43 MB, 8.5% of the tier (`knowledge_base.worst_case_bytes`, pinned below 10%). `KB_MAX_CARDS` cannot be set above 3,000.
- **The free tier allows 3 search indexes and 2 are used** (`documents.vector_index`, `user_memory_chunks.user_memory_vector_index`). So the base reuses the `documents` collection and its existing vector index (a public library scope, as in the plan above) and must NOT add a third index for itself.

**The rules**
1. **Hard ceiling on cards.** An env value (`KB_MAX_CARDS`, default 2,000), checked on every write, with the existing per-day ingest cap beside it. A `worst_case_bytes` function and test like the reading shelf's (`reading_shelf.py`: `MAX_LIVE_ITEMS`, `MAX_TOMBSTONES`, `worst_case_bytes`), so the ceiling in bytes is a tested number, not a hope.
2. **Evict by use, not age.** At the ceiling, the card that goes is a never-used one first (oldest first), then the one used longest ago. "Used" means the card was retrieved AND reached the model for the answer, not merely appeared in a search. Plain code, no model call. The write that triggered the eviction is the one that lands, so the base never refuses to learn.
3. **Tombstones.** An evicted or expired card shrinks to a few fields (canonical URL, content hash, when, why), no text and no vector, capped in number. A tombstone stops the page being re-ingested the same day and still lets a later, deliberate request bring it back.
4. **Freshness expiry by type, separate from the ceiling.** Time still matters for facts that go stale: news and articles expire (the shelf uses 180 days), reference pages are re-checked, evergreen pages last longest. An expired card is not served until it is refreshed. Expiry is about being right; the ceiling is about size.
5. **Dedupe on the way in:** canonical URL plus content hash, so one page cannot take two slots.
6. **Retrieval stays small:** a score threshold and at most a few base hits per turn (plan point 6), so a bigger base does not mean noisier answers.

**Counters first (`self_counters.py`, counts only, letters-only keys):** `kb_card_used` (a card reached the model), `kb_card_written`, `kb_evicted`, `kb_expired`, and a repeat-read count (the same page fetched again within the window: it measures demand and tells us whether the cache is thrashing). Sonic Health rows follow. If `kb_evicted` climbs while `kb_card_used` is flat, the ceiling is too small or Sonic is learning things nobody asks about.

**Not decided:** the exact ceiling (2,000 is a start, to be raised only after real use data); the per-type expiry days; whether memory and uploads growth, which share the same 512 MB, needs a similar ceiling of its own (`fs.chunks` is already more than half of what is used, and is not part of this change).


## Build status (v1 BUILT, uncommitted, 2026-11-07; Jack: "do option 1")

**What exists**
- `backend/utils/knowledge_base.py`: the store and every size rule (ceiling `KB_MAX_CARDS` default 2000 / hard max 3000, eviction by use, tombstones, expiry by kind, dedupe on canonical address and content hash, daily proposal cap `KB_DAILY_PROPOSALS` 10, waiting queue cap 50 / 30 days, search with `KB_MIN_SCORE` 0.80, `worst_case_bytes`). Approved cards are documents in `documents` with `affiliate = Sonic_Global` (the existing `vector_index`; no third search index).
- `backend/services/knowledge_cards.py`: `propose_for_agent`: fixed address rules, daily cap, one safe fetch, duplicate check, a model writes the card, the reading shelf's fixed checks (names and numbers must be in the page; no link, order, first person or sensitive ground) with card-length limits, then a second model call must confirm the page supports it. The card is stored PENDING only.
- Tools in the tool agent: `search_my_knowledge` (everyone, guests included; offered only once a card exists, from a 5-minute cached check) and `propose_kb_entry` (any signed-in member; guests refused by a menu gate plus a dispatcher check; ONLY an admin can approve a card, see "Members may ask" below). Native schemas, trace subject and excerpt, and both untrusted-content lists name them.
- Admin endpoints (`/api/admin/knowledge`, `/approve`, `/reject`, `/remove`) and the Sonic Health section **Learned knowledge** (Knowledge group); a counters card on the Turns tab. The upload path refuses the `Sonic_Global` affiliate for everyone, admins included.
- Counters (`self_counters.KB_KEYS`, all recorded): `kb_proposed`, `kb_proposal_refused`, `kb_approved`, `kb_rejected`, `kb_removed`, `kb_evicted`, `kb_expired`, `kb_searches`, `kb_search_empty`, `kb_card_used`. A changelog entry (`own_knowledge_base`).
- Tests: `test_knowledge_base.py`, `test_knowledge_cards.py`, `test_knowledge_tools.py`, `test_knowledge_admin.py`, `sonicHealthLearned.test.mjs`, and additions to `sonicHealthGuest.test.mjs`.

**Decisions I made that Jack may want to change**
- A card is read through a TOOL (`search_my_knowledge`), not injected into the prompt, as the 2026-11-07 revision says. So it is used only when the model decides to search; the menu line says when.
- "Used" for eviction means a search returned the card to the model, not that the final answer cited it.
- Only removed and rejected pages are blocked from being proposed again; an evicted or expired one can be learned again.
- `reddit.com` is on the base's blocked-source list (the reading shelf's list also blocks Wikipedia and Medium; the base allows both).
- The model that writes and checks cards is `lite_llm` (the shelf's summary model), twice per proposal.

**Not done / not checked**
- Never run against a real model, a real page or the real Atlas index. The `$vectorSearch` pipeline is checked for its shape (library filter, existing index, no vector pulled back), not run. `KB_MIN_SCORE` 0.80 is a guess: read `kb_search_empty` against `kb_card_used` after real use.
- The Sonic Health section was type-checked and its wiring pinned by tests, but not opened on a real signed-in admin screen (no Clerk sign-in from the tooling).
- Not built: the grounded-mode rework (the base is NOT part of grounded answers; only the tool reads it), "learn this" trigger phrases beyond the model choosing the tool, refresh of a stale page (an expired card just expires), promoting reading-shelf items, a pending-count badge on the tab, the repeat-read counter (needs a record of pages read, with its own privacy question), and a guard that refuses to write from a turn with an emotional or safety context (a member names the address, and every card is approved by an admin).
- The Privacy page was not changed: a question put to `search_my_knowledge` is embedded like a library search already is.


## Members may ask, only an admin approves (2026-11-07; Jack: "anyone can tell sonic to learn something but only an admin approves the card to prevent poisoning")

- **Who can ask:** every signed-in member (`propose_kb_entry` is on the menu for anyone who is not a shared guest identity, and the dispatcher refuses a guest even if the menu were bypassed). Guests stay out because they are shared identities, so a per-person limit would mean nothing for them.
- **Who can decide:** admins only. The three decision endpoints are `require_global_admin`; nothing in the tool agent or the proposal service calls `approve` (a test scans for it). A card is not embedded, so not searchable by anyone, until then.
- **Why that is the poisoning defence:** the member, the model and the page can all be hostile; only a person who reads the card against the page makes it live for everyone. The fixed checks (every name and number must be in the page, no links or orders, a second model must confirm support) reduce what an admin has to catch, they do not replace the admin. A page can still be wrong or slanted; the card carries its source and date.
- **Spam and spend:** each proposal costs one fetch and two cheap model calls. A member gets `KB_USER_DAILY_PROPOSALS` (default 3) a day and the whole base `KB_DAILY_PROPOSALS` (default 10); an admin is held to neither, so a crowd cannot lock an admin out. All proposals share the queue cap of 50, which also bounds the worst-case size (unchanged: about 43 MB).
- **What is stored about who asked:** nothing that names anyone. A card says only `by: "member"` or `by: "admin"`, and Learned knowledge shows a stronger "read the page itself" warning for a member's card. A member's count for the day is kept under a one-way hash (`sonic_kb_state`, replaced the next day).
- **Not done:** the member is not told when their card is approved or turned down (the reply says it waits for an admin and that Sonic cannot say when); no way to see "my pending requests"; no per-domain reputation; no block on a member whose cards keep being rejected; a hostile crowd of NEW accounts could still fill the queue of 50 until an admin clears it (they expire after 30 days). Not run against a real model.


## First real try (2026-11-07): the sensitive-ground check refused an ordinary page, and the fix

**What happened:** Jack asked Sonic to learn `https://github.com/SummonShenron/SAAPP` as a non-live test. The tool ran (`propose_kb_entry`) and a fixed check refused the card: "touches ground that is not Sonic's to bring up". Replaying the tool's steps on that page with the real cheap model showed why: the card said a script "runs separate terminal windows", and the reading shelf's sensitive-word list (built to decide which PERSONAL topics Sonic never raises in an opener, so over-matching "only costs an opener") contains `separat\w*` (it is there for breakups). Two of three card attempts were refused for that one word. The same list also holds health, legal, court, money, mental, dead, vote, salary, visa and diet, which ordinary technical and encyclopedic pages use in passing.

**Why that gate was the wrong shape for the base:** the shelf's items can be offered unprompted, so "is this a topic Sonic should bring up" matters there; a card is only read when someone searches for it, and an admin approves every one. Judging the card's OWN wording by a personal-topics list refuses good cards at random, and judging the page by a single word refuses pages that are not about anything sensitive.

**Change:** the knowledge base's check is now about the page's TOPIC. A page is refused when its opening (first 2000 characters) has at least `SENSITIVE_PAGE_TERMS` = 3 DISTINCT sensitive words (a page about health, therapy and depression), and the card's own wording is no longer judged by the list. `reading_shelf.summary_issue` gained two optional arguments (`sensitive_terms`, `sensitive_summary`) and a helper `page_is_sensitive`; the shelf's defaults (one word, summary judged too) are unchanged. Tested: "separate" and "health" in a card pass, a page about health, therapy and depression is refused, two stray words and one word said three times pass, words after the first 2000 characters are ignored, the shelf's own rule is unchanged. On the real page 6 of 6 attempts now pass the fixed check (5 of them contain "separate").

**Still true, and not yet seen:** the second-model support check, the admin approval, the embedding into the vector index and a real `search_my_knowledge` have not been run against this page. 3 is a guess; if a page that is plainly about a sensitive topic gets through, lower it, and if a page about something ordinary is refused, read which words it matched (the log line says only that a card was refused, never why or what; the `reading_shelf` helper can be run on the page).

## Candidates: pages Sonic read on its own (2026-11-09; Jack: "if sonic gets told to look up a site or performs a search to find more information, we could store that url in a health dashboard tab, and if approved, sonic reads and summarizes and ingests the site summary into its kb"; then: "im okay with urls it fetches on its own, the approval gates are what prevent those poor urls from being indexed")

**What it is.** A passive intake in front of the existing proposal. When the tool agent reads a public page (`read_url`, or `browser_navigate`) on a normal turn, `backend/utils/kb_candidates.py` notes the page's cleaned address (https, no query string, no fragment, no port, no trailing slash), its domain, when it was first and last read and how many times. Nothing is fetched, summarised or embedded, so a candidate is free. On Sonic Health, Learned knowledge, a new first section "Pages Sonic read on its own" lists them most-read first (a page Sonic keeps going back to is a gap in what it knows), each with **Learn this page** and **Dismiss**.

**Two gates, on purpose.** (1) Learn runs the existing `propose_for_agent` as an admin (`knowledge_cards.learn_candidate`): the same safe fetch, card, fixed checks and second-model support check, and the card lands PENDING. (2) The card still needs the separate approval before it is embedded and readable by anyone. A poor page is stopped at the first gate (never learned) or the second (rejected, and never proposed again). So capture can be generous: it includes pages Sonic fetched on its own and pages a person pasted.

**Never stored:** who read it, the question that led to it, any page text. Skipped (counted, `kb_candidate_skipped`): not https; a name and password in the address; localhost and private network names or numbers; login, sign-up, checkout, account, admin, dashboard and similar paths; a long token in the path (share links); private-document hosts (Google Docs and Drive, OneDrive, SharePoint, Dropbox, Slack, Zoom, Atlassian, Figma, Airtable...); the base's blocked sources; a page the base already holds, has waiting, or an admin turned away. Not noted at all for shared guest identities or on a turn with any safety concern, nor when the read failed.

**Bounds.** 200 open candidates (when full the least-read, oldest makes room); one not read again for 30 days is dropped; a dismissed one is remembered 90 days so it is not suggested again. A candidate whose Learn failed stays, with the reason (the proposal's own fixed sentence), so the admin can see why or dismiss it; one that turned out to be known already or turned away is removed.

**Counters** (`KB_KEYS`, on the Turns tab's knowledge-base card): `kb_candidate_seen`, `kb_candidate_repeat`, `kb_candidate_skipped`, `kb_candidate_learned`, `kb_candidate_dismissed`.

**Where.** `backend/utils/kb_candidates.py` (store), `knowledge_cards.learn_candidate`, `knowledge_base.admin_view` (adds `candidates`), `app.py` (`POST /api/admin/knowledge/candidates/{key}/learn` and `/dismiss`, both `require_global_admin`), `agent_workflow._note_candidate` (hooked after `read_url` and `browser_navigate`), `SonicHealthLearned.tsx`, `api.ts`. Tests: `test_kb_candidates.py` (23), the two frontend test files.

**Not done / not known.** The query string is dropped, so a page whose address needs it (`?id=7`) is noted as its bare path and Learn will fetch that (it may be a different page or a failure; the admin sees the address). Web search RESULTS are not candidates, only pages actually read. Never run against a real model, a real page or the real database: the capture and the screen were checked with fakes and a mocked browser harness. Admin sign-in on the real screen is still unseen. Learn takes several seconds (one fetch, two cheap model calls) and the button says "Reading the page...". No badge on the tab for the candidate count. The base itself (approve, embed, `search_my_knowledge` against Atlas) is still unproven end to end; run one real page through both gates before relying on this.

