# Consent audit

Written 2026-11-05, first pass. The rule it checks (Jack's): **nothing sends a person's words or code to a provider they did not choose, and what each provider receives is stated on the Privacy page.** Gemini (Google) is the platform every person is on by default and is named first on the page; everything else is either a choice the person makes or a service that does one job, and is listed. This is a read of the code, not a legal review.

## Who receives what, and who chose it

| Destination | What it receives | Who chose it | On the Privacy page |
|---|---|---|---|
| Google Gemini | Replies, tool steps, memory notes, summaries, safety checks, image reading (the default for everyone) | The platform | Yes, first |
| Anthropic Claude (Haiku, Sonnet) | The tool steps of a request, including emails, events and files Sonic reads in them; a reading of a change; a reading of a whole plan | The person (Tool or Execute model) | Yes (corrected 2026-11-05, see F3 and F4) |
| OpenAI GPT | Replies (Base model) or tool steps (Tool or Execute model); never inside a safety context | The person | Yes |
| Tavily, then public search engines | The search words | Used when the person asks for a web search | Added 2026-11-05 |
| Browserless | The address and what the page shows | Used when the person asks Sonic to look at a page | Added |
| Google Maps | The places and areas named | Used for a place or distance question | Added |
| Google image model | The description of the picture | Used when the person asks for one | Added |
| Google embeddings | The text of Memory notes and uploaded documents, to search them | The platform | Added |
| GitHub | The repository named, with the person's own token | The person (saves a token) | Added |
| The runner (the person's computer) | Commands run only after the person approves each; the output returns to Sonic and goes to the model doing that step | The person (installs it, approves each) | Added |
| Clerk, MongoDB Atlas, hosting, error monitoring | Sign-in, storage, running the service | The platform | Yes |

## Findings
- **F1, fixed (uncommitted): the diff reviewer sent every person's diff to Anthropic** whenever the server had a Claude key, including people on Gemini. Both readings now run only for a person who chose a Claude model for their Tool or Execute model (`claude_may_read` in `tool_agent_node`; tested, including a mutation check). The older version may already be deployed.
- **F2, fixed (uncommitted): `CONVERSATION_SUMMARY_MODEL`** could send anyone's conversation to Claude when the server setting was on. It now also needs the person to have chosen Claude (`conversation_summary._chose_claude`). Default off either way.
- **F3, fixed (page): the page said Claude is used "only in code work".** The "Code model" setting is really the TOOL model: it runs every tool step (the same `tool_agent_llm` call), so a person who chose Claude also sends the emails, events and files Sonic reads for a non-code request. The page now says so. The setting's label still says "Code"; see the open item below.
- **F4, fixed (page): the page said the second reading happens on any server where Claude is set up.** It only happens for people who chose Claude, and a plan-end reading by Claude Sonnet now exists; both are described.
- **F5, fixed (page): seven services were not named** (search, hosted browser, maps, images, embeddings, GitHub, the runner). Now listed.
- **F6, OPEN, needs Jack: `TOOL_AGENT_PROVIDER=anthropic`.** If this is set in the server environment, everyone who never chose a model (the chip shows Gemini) has their tool steps run on Claude Haiku. That would break the rule above and mislead the chip. Check Render's environment. If it is set, either remove it, or keep it and say on the Privacy page, and in the chip, that the default for tool steps is Claude. Also check `CODE_PLAN_EXECUTE_MODEL` (already limited to people on Claude) and `CONVERSATION_SUMMARY_MODEL` (now limited too).
- **F7, OPEN: the free search engines.** When Tavily is not configured or is over its limit, queries go to public search engines through a scraping library. Their terms and what they log are not something we control; the page says they receive the search words.
- **F8, OPEN, small: Browserless** receives the pages the agent opens. Not verified end to end what a page can carry.
- **F9, fixed: the "Code model" label** said "code" for a setting that is really the tool model (`tool_model`, `get_user_tool_model`, `tool_agent_llm`). It now reads "Tool model" in the chip, the presets, Help, the Privacy page, the Terms and what Sonic says about itself. Internal names are unchanged.

## A tripwire so it stays true
`backend/tests/test_privacy_disclosure.py` fails when (1) a service the code talks to is not named on the Privacy page, (2) a new file builds a model client that has not been reviewed, (3) the page's claims about when Claude is used drift from the code, or (4) the Terms lose their statements that Sonic is not a professional or crisis service and that a paid option is only planned.

## Terms (draft for Jack, and ideally a lawyer, to read)
Sections 4 to 7 were added on 2026-11-05: Sonic is an AI and not a professional or emergency service (with a plain statement that its support responses have not yet been reviewed by a clinician); the person is responsible for what they approve and apply, and should keep backups; which models and services receive what; usage limits, with a paid option described as planned only, no price and no date; plus a minimum age of 13 and that the person's content stays theirs. Not covered, because it is not mine to write: governing law, an arbitration or dispute clause, refunds and chargebacks (needed before the paid tier), and any tax wording.

## Data rights: what is stored per person
Names only, from a list of the collections and which fields they carry; no content was read. Collections that carry a person's identifier: chat_history, conversations, saved_conversations, conversation_summaries, session_documents, code_plans, working_state, user_settings, user_memory_facts, user_memory_chunks, user_memory_fact_embeddings, user_memory_domain_backups and their archives (user_memory_chunk_archive, user_memory_fact_archive), memory_compaction_events, user_open_loops, user_clarifications, corrections, directory, login_logs, google_calendar_connections, sonic_calendar_judgments, sonic_calendar_mentions, sonic_meetings, sonic_meeting_notes, sonic_meeting_runs, sonic_user_tasks, sonic_user_task_runs, sonic_progress_notes, sonic_offer_response, sonic_user_activity, saapp_runners, saapp_runner_keys, saapp_runner_jobs, github_token_audit, safety_events, the budget_* collections, the per-person usage counts (llm_usage_user_*), and the stored files (fs.files, fs.chunks).

**What exists today:** a person can delete a conversation, edit or clear Memory (`memory_erase`), disconnect Google, and export a conversation. Deleting an account and all its data is by email to the address on the Privacy page; there is no self-service delete and no whole-account export.

**What is missing:** a registry of every collection above with its person field, so one function can export it and one can erase it, and a test that fails when a new collection with a person field is added without being registered. Proposed, not built. Some rows (safety events, ledger rows, budget requests) may need to be kept for a stated period for safety or accounting reasons; that is a decision to make, then say on the page.

## Added 2026-11-05 (late): the shared guest identity
- **F10, fixed (uncommitted): a guest could list every guest's conversations.** All guests share one identity (`guest-recruiter@example.com`) and one public sign-in token, and guest conversations are saved under that identity (5 of the 9 stored conversations were guest ones when checked). `GET /api/conversations` listed everything stored under the identity, so any visitor using the guest sandbox could list, and then open, other guests' chats. It now returns an empty list for a guest; a guest still reaches the one conversation whose (unguessable) id they hold. Tested. The Privacy page already says guests share one temporary identity, but not that their conversations are stored.
- **OPEN, Jack's call:** the guest conversations already stored (they hold whatever earlier guests typed) could be deleted, and guest conversations could be made to expire. Memory, tasks, working notes and uploads are already skipped for guests (checked: no guest rows in those collections).
