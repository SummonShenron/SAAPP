---
name: add-a-sonic-tool
description: Add or change a tool that Sonic (the SAAPP assistant) can call, or a write it proposes for approval. Use whenever the task is "give Sonic the ability to...", or touches a tool's menu line, native schema, dispatcher branch, Apply/Decline card, counters, changelog entry or reasoner rule in backend/services/agent_workflow.py, native_tools.py, change_cards.py or self_counters.py.
---

# Add a tool to Sonic

A tool is wired in about ten places, and the tests only catch some of them. Work through this list in order. Read `AGENTS.md` first for the project rules (log hygiene, `app.py` scope, counters, changelog).

## 0. Start from a sibling

Pick the existing tool most like yours and grep its name across the repo; every file that shows up is a place yours needs too.

- A read tool: `find_google_docs`, `list_google_calendar_events`, `ci_status`.
- A write that waits for the person's press: `propose_doc_change` or `propose_calendar_change` (the shared Apply/Decline card, `backend/services/change_cards.py`).
- A question with options: `ask_choice` (`backend/services/choice_cards.py`).

```
grep -rn "propose_doc_change" backend local/src --include=*.py --include=*.ts --include=*.tsx
```

## 1. The tool itself, in order

1. **Pure logic first** in `backend/utils/<thing>.py`: validate the arguments, build the request, describe the card. No network, no clock. This is where most of the tests go.
2. **Service** in `backend/services/<thing>_service.py` if it talks to an outside system. Raise `ChangeError` (`backend/utils/change_error.py`) with a sentence the person can act on; never pass an outside system's own error text on, and never log a title, text, address or id.
3. **Schema** in `backend/services/native_tools.py`: an entry in `REGISTRY` (plain types only: strings, integers, booleans, arrays of those; no integer enums, Gemini rejects them) and a description in `NATIVE_DESCRIPTIONS`. The description is what the model reads: say when to use it, what it returns, and what the outcome words mean.
4. **Menu line** in `tool_agent_node` in `backend/services/agent_workflow.py` (`menu_lines`). Its name must start the line as `- name — args: ...`, or `declarations_from_menu` will not offer it. Gate it (by a request flag, a connection, guest or not) in the same place you gate its dispatch.
5. **Dispatcher branch** in the same file (`_act`, found by grepping an existing `tool_action ==`). Check the gate again there: the model can call a tool that was not offered.
6. **Untrusted text.** If the tool returns text other people wrote (a shared document's title, an email, a web page, logs), add its name to `_UNTRUSTED_CONTENT_TOOLS`. That is what stops a pull request or a merge being opened in the same message.
7. **Counters** in `backend/utils/self_counters.py` BEFORE anything records them, then record them and test the recording. A key nothing increments is dead.
8. **Trace icon**: `local/src/components/stepKinds.ts`.
9. **Router.** If the reasoner must send the request to the tool agent, edit the rule in `backend/components/constraints.py` (the `needs_*` flags), and add a case to `backend/evals/routing_eval.py`. Do not run the eval: it calls a model and costs money.
10. **Changelog** in `backend/components/sonic_changelog.py` in the same change, for any user-visible ability: true, at most 260 characters, `requires` paths that exist (`path::def name` for code). Keep the `keywords` specific: a keyword like "calendar" belongs to the general entry and made a test fail when a new entry stole the match.

## 2. If it writes: use the card, not a text approval

Never add a new `WRITE_ACTIONS` text card ("Approval Required ... type approve"). Use the shared change card:

- `prepare()` looks at the real thing and **changes nothing**; it returns a proposal dict `{proposal_id, username, title, subject, card, apply, ...}`; `apply(proposal)` makes the call, after the press, and returns one sentence.
- `agent_workflow._propose_change(args, prepare, prefix)` streams the card, waits at no model cost, and counts `<prefix>_shown/applied/declined/failed/timeout/refused`. Add a thin wrapper for your tool and a dispatcher branch.
- Guard what an approval cannot see: for an edit by position, pass the revision the card was drawn from so the outside system refuses a stale one; for an existing item, compare an etag in `apply`.
- Anything that emails or notifies other people must say so on the card (`notice`).

## 3. If the page must know about it (a new request flag or stream event)

A change has two ends. Do both and say if you did not:

- `app.py`: the `ChatRequest` field, the `initial_state` key, and (for a stream event) the `custom_event` forwarder.
- `backend/state/graph_state.py`: declare the key. LangGraph silently drops undeclared input keys.
- `local/src/api.ts`: send the flag. `local/src/pages/Chat.tsx`: parse the event.
- A new write endpoint must use `get_member_user` or be listed in `backend/auth/guest_policy.py`; `test_public_surface.py` fails otherwise.

## 4. Tests to write

Copy a sibling's tests rather than inventing a shape: `backend/tests/test_doc_changes*.py` (pure, service with a fake Google that records every call, and the whole tool loop through `_drive`), `test_choice_cards.py`, `test_tool_agent_node.py`.

- Test inputs come from the real shape of the data (a real Docs body with real indexes, not an invented one).
- The loop test must prove: the card is streamed, **nothing is sent before the press**, Apply sends exactly the stored request, Decline sends nothing, a timeout sends nothing, and a refused proposal shows no card.
- Test the tool is not offered (and refuses) where it should not exist: a page without the flag, the shared guest, the off switch.
- A log test: parse the module's logger calls and assert none is built from data.
- Then run the `mutation-check` skill on what you wrote. Today it found four real bugs that the first green run did not.

## 5. Finish

Use the `finish-a-change` skill. Say what you checked and what you did not; nothing here has run against the real outside system or a real model until someone has tried it.

## Mistakes already made here (do not repeat)

- Writing patch scripts with apostrophes inside a bash heredoc: the shell breaks. Write the script with the file tool and run it.
- A bulk regex rename that also hit an unrelated name (`getCalendarStatus` became `getChangeStatus`). After any rename, `git diff` each touched file for names that should not have changed.
- A test that only passed because the mocked model's canned final answer contained the word it asserted on. Assert on what the tool actually returned to the model.
- Backend tests are labelled `runner\tests\...` in pytest output on Windows; do not trust a summary to name the right file.
