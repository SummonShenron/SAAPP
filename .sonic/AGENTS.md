# SAAPP: where things are and how to work here

SAAPP is a FastAPI backend (`app.py`, `backend/`) with a React frontend (`local/src/`) and a separate local program (`runner/`). Sonic is the assistant inside it.

## Start here, by task (read the named file first, not the whole tree)
- **Chat page, trace panel, plan card**: `local/src/pages/Chat.tsx`; trace: `local/src/components/TracePanel.tsx`, `traceFacts.ts`; plan card: `PlanCard.tsx`. Styles: `local/src/index.css`, `local/src/components/__styles__/`.
- **Sonic Health dashboard**: `local/src/pages/SonicHealth*.tsx` (menu: `sonicHealthTabs.ts`). Counters: add the key to `backend/utils/self_counters.py` first.
- **The tool agent** (what Sonic does with tools): `backend/services/agent_workflow.py` (`tool_agent_node`: menu lines, the `_act` dispatcher; routing in `build_agent_plan`). Loops: `native_loop.py` (the main one) and `react_loop.py`. Native tool schemas: `native_tools.py`.
- **A new tool** needs: a menu line and a dispatch branch in `agent_workflow.py`, a schema in `native_tools.py`, the untrusted-output sets if it returns other people's text, and a changelog entry.
- **Prompts and voice**: `backend/components/constraints.py`, `sonic_profile.py`. What Sonic may say it can do: `backend/components/sonic_changelog.py`.
- **Editing the connected folder**: `local_workspace.py`, `local_edit_decisions.py`, `propose_local_edits`; plans: `backend/utils/code_plan.py`; edit-request detection: `agent_utils.py` (`looks_like_edit_request`).
- **Running checks and commands on someone's machine**: `runner/src/saapp_runner/` (`commands.py` is the command language, `shell.py` runs it, `mcp_server.py` is the MCP server); the server side is `backend/utils/runner_jobs.py`.
- **Places and distances** (Google Maps over MCP): `backend/services/mcp_client.py`, `maps_tools.py`, `backend/utils/place_questions.py`.
- **Memory**: `backend/utils/memory_utils.py` and its neighbours; the Memory page is `local/src/pages/Memory.tsx`.
- **Tests** sit in `backend/tests/` named after what they cover; frontend tests are `*.test.mjs` next to the code.

## How to look (it saves steps)
- Use `search_literal` for a known string, then read only the function you need with `start_line` and `line_count`. Big files: `agent_workflow.py` is about 3,800 lines, so never read it from the top.
- Read the test that covers the code before you change the code. Do not list the whole tree.

## Rules this project keeps
- `app.py` holds endpoints and request classes only. Helpers go in `backend/utils/` or `backend/services/`.
- Add or edit an entry in `sonic_changelog.py` in the same change as any user-visible Sonic feature ("I can now..."; true; at most 260 characters).
- Modules that log use `logging.getLogger("SASS Logger")`. No user content in logs: no messages, paths from users, queries, or tool output.
- Run backend tests with `GOOGLE_API_KEY=test-not-real` (`python -m pytest backend runner -q`). Frontend, from `local/`: `npx tsc -b` and `node --test src/*.test.mjs`.
- Real model calls cost money: do not run evals or live model calls unless asked.
- A script that reads the database: run it from the project folder and use `from backend.utils.db_utils import get_db` (it reads `.env` itself: `MONGO_URI`, database `saapp_database`, needs `USE_DB=true`). Do not use `MONGODB_URI` or ask for credentials. Read only unless asked to write; do not print user text.
- Tests never touch the real database, embeddings or model: stub `get_db` (`monkeypatch.setattr(module, 'get_db', lambda: fake)`) or use `FakeDB` from `backend/tests/fake_runner_db.py`. Only scripts use the real `get_db()`.
- Never claim a test passed, or a change was applied, unless a result says so.
