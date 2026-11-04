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
2. **Global base v1:** a public library (for example `Sonic_Global`), appended to every signed-in user's scope in grounded mode, written only by the pipeline. One tool, `propose_kb_entry`: fetch one URL, guess the type, write the card, show it for an admin to approve (the Apply-card pattern), then ingest with provenance. Two types.
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
