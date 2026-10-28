"""Function declarations for the native tool-calling engine (backend/services/native_loop.py).

The old tool agent describes its tools in a long text menu and asks the model to answer with a JSON "decision". The native engine hands
the model real function declarations instead. Two decisions keep this from duplicating anything:

- THE DESCRIPTIONS COME FROM THE LIVE MENU. tool_agent_node already builds the menu for this turn (admin-only tools only for admins,
  the folder tools only with a folder connected), and each line carries guidance learned from real failures ("search_code only matches
  exact tokens, use find_file for a colloquial name"). That text becomes the function's description, so the two engines can't drift
  apart and nothing is offered that the menu would not have offered.
- ONLY THE READ-ONLY SUBSET HAS A SCHEMA HERE, plus ONE write: propose_local_edits (a connected folder). It changes nothing itself: the person sees a diff
  and presses Apply. A tool with no entry in REGISTRY is simply not offered natively: the other writes, the browser,
  MongoDB, the test runs and the approval flows stay with the old engine, which keeps all of that machinery. A turn that
  points at one of those is routed to the old engine (native_loop.eligible), never run here without the tool.

Argument names must match what tool_agent_node's dispatcher (`_act`) reads, because the native engine calls the same dispatcher.
"""
import re
from typing import Any, Dict, Iterable, List

_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}


def _object(properties: Dict[str, Any], required: Iterable[str] = ()) -> Dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


REGISTRY: Dict[str, Dict[str, Any]] = {
    # the repository
    "list_repo_tree": _object({}),
    "read_repo_file": _object({
        "path": {"type": "string", "description": "relative file path within the repo"},
        "start_line": {"type": "integer", "description": "1-indexed line to start from"},
        "line_count": {"type": "integer", "description": "how many lines to show from start_line"},
    }, ["path"]),
    "search_code": _object({"query": {"type": "string", "description": "a function, class or variable name, or an exact string"}}, ["query"]),
    "find_file": _object({"query": {"type": "string", "description": "a colloquial or approximate file name"}}, ["query"]),
    "trace_symbol": _object({"symbol": {"type": "string", "description": "an exact function, variable, field or prop name"}}, ["symbol"]),
    "search_literal": _object({"term": {"type": "string", "description": "an exact string or identifier"}}, ["term"]),
    "diff_branches": _object({"base": _STRING, "head": _STRING}, ["base", "head"]),
    "list_commits": _object({"branch": _STRING, "limit": _INTEGER}, ["branch"]),
    "list_pull_requests": _object({
        "state": {"type": "string", "enum": ["open", "closed", "all"]},
        "limit": _INTEGER,
    }),
    # the person's own document libraries (backend/services/library_search.py)
    "search_library": _object({
        "query": {"type": "string", "description": "words to look for in the person's own documents: a name, topic, date or kind of document"},
        "max_results": {"type": "integer", "description": "default 6, max 12"},
    }, ["query"]),
    "list_library_files": _object({}),
    # what Sonic remembers about the current person (backend/services/tool_agent_memory.py): their facts and notes of earlier conversations, never anyone else's
    "search_memory": _object({
        "query": {"type": "string", "description": "what to look for, in plain words: a project, a preference, a decision, a name, something they did or planned"},
        "max_results": {"type": "integer", "description": "default 5, max 8"},
    }, ["query"]),
    "read_library_file": _object({
        "name": {"type": "string", "description": "a file name exactly as list_library_files wrote it"},
        "part": {"type": "integer", "description": "which part of a long file; default 1"},
    }, ["name"]),
    # the web and a sandbox
    "web_search": _object({"query": {"type": "string", "description": "the exact search query"}}, ["query"]),
    "read_url": _object({"url": {"type": "string", "description": "an http(s) URL"}}, ["url"]),
    "run_python": _object({"code": {"type": "string", "description": "a small self-contained snippet; print what you need to see"}}, ["code"]),
    # an admin's view of the running system (only ever in an admin's menu, and only when errAgent reads are configured)
    "list_app_incidents": _object({
        "since": {"type": "string", "description": "30m, 24h or 7d"},
        "status": {"type": "string"},
        "limit": _INTEGER,
    }),
    "read_app_incident": _object({"incident_id": {"type": "string", "description": "an id from list_app_incidents"}}, ["incident_id"]),
    "read_app_logs": _object({
        "level": {"type": "string", "enum": ["info", "warn", "error"]},
        "since": {"type": "string", "description": "30m, 6h or 1d"},
        "contains": {"type": "string"},
        "request_id": {"type": "string"},
        "limit": _INTEGER,
    }),
    "check_deploy_status": _object({}),
    # the current user's own Google account (read-only; the dispatcher resolves their token and checks their granted scopes)
    "search_gmail": _object({
        "query": {"type": "string", "description": "real Gmail search syntax, e.g. 'from:someone@example.com' or 'has:attachment subject:report'"},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_gmail_message": _object({"message_id": {"type": "string", "description": "an id from a search_gmail result"}}, ["message_id"]),
    "get_gmail_attachment": _object({
        "message_id": {"type": "string", "description": "an id from a search_gmail result"},
        "attachment_id": {"type": "string", "description": "an attachment_id from a read_gmail_message result"},
    }, ["message_id", "attachment_id"]),
    "search_drive_files": _object({
        "query": {"type": "string", "description": "a real Google Drive query, e.g. \"name contains 'Report'\" or \"fullText contains 'budget'\""},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_drive_file": _object({"file_id": {"type": "string", "description": "an id from a search_drive_files result"}}, ["file_id"]),
    "list_google_calendar_events": _object({"date": {"type": "string", "description": "YYYY-MM-DD; today in the user's timezone if left out"}}),
    # the current user's own Microsoft account (read-only; backend/services/microsoft_tools.py resolves their token and checks their granted scopes)
    "search_outlook_mail": _object({
        "query": {"type": "string", "description": "plain words, or Microsoft search syntax like 'from:sam subject:report'"},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_outlook_message": _object({"message_id": {"type": "string", "description": "an id from a search_outlook_mail result"}}, ["message_id"]),
    "get_outlook_attachment": _object({
        "message_id": {"type": "string", "description": "an id from a search_outlook_mail result"},
        "attachment_id": {"type": "string", "description": "an attachment_id from a read_outlook_message result"},
    }, ["message_id", "attachment_id"]),
    "list_outlook_calendar_events": _object({"date": {"type": "string", "description": "YYYY-MM-DD; today in the user's timezone if left out"}}),
    "search_onedrive_files": _object({
        "query": {"type": "string", "description": "plain words matched against file names and contents"},
        "max_results": {"type": "integer", "description": "default 10"},
    }, ["query"]),
    "read_onedrive_file": _object({"file_id": {"type": "string", "description": "an id from a search_onedrive_files result"}}, ["file_id"]),
    # the person's target Google Doc (Integrations): a draft the person approves from a card. Nothing is written by the call.
    "propose_append_target_doc": _object({
        "content": {"type": "string", "description": "the finished text to append, composed by you from what you gathered this turn"},
    }, ["content"]),
    # a connected local folder + the person's own GitHub token: commit (and optionally branch and open a pull request). Shows a card; commits nothing until the person approves.
    "propose_commit": _object({
        "message": {"type": "string", "description": "optional: what the change DOES, like a pull request title and a line or two (for example \"feat: add a merge tool that builds a fresh card\"); never \"commit the working changes\". Leave it out when you do not know what changed: one is written from the diff"},
        "new_branch": {"type": "string", "description": "optional: create this branch first and commit to it, for example sonic/fix-clamp"},
        "branch": {"type": "string", "description": "optional: the branch to commit to (or to branch from); default is the branch their folder is on"},
        "open_pull_request": {"type": "boolean", "description": "optional: also open a pull request from the branch"},
        "pr_title": {"type": "string", "description": "the pull request's title (default: the first line of the message)"},
        "pr_base": {"type": "string", "description": "the branch the pull request merges into (default: the repository's default branch)"},
        "pr_draft": {"type": "boolean", "description": "open it as a draft"},
        "paths": {"type": "array", "items": {"type": "string"}, "description": "optional: only these files"},
        "scope": {"type": "string", "enum": ["applied", "working"], "description": "applied = only the files you changed and they applied; working = every file in their folder that differs from the branch on GitHub (what git status would list). Default: applied when any are waiting, otherwise working"},
    }, []),
    # the person's own GitHub token: a card for merging an open pull request, by number or by the branch it comes from. Merges nothing until the person approves.
    "propose_merge": _object({
        "pr_number": {"type": "integer", "description": "the pull request's number"},
        "head": {"type": "string", "description": "or: the branch the pull request comes from, for example feat/checkouts"},
        "base": {"type": "string", "description": "optional: the branch it merges into"},
        "method": {"type": "string", "enum": ["squash", "merge", "rebase"], "description": "optional; default squash"},
    }, []),
    # a connected local folder: run Python tests in the WASI sandbox against a copy of it (backend/services/sandbox_tests.py). Changes nothing.
    "run_sandbox_tests": _object({
        "tests": {"type": "array", "items": {"type": "string"}, "description": "paths of Python test files (unittest) in the folder, or created by `edits`, at most 5"},
        "edits": {
            "type": "array",
            "description": "optional: edits (the same text forms propose_local_edits takes) to apply to the COPY before running, including new test files; nothing is changed or shown",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string", "description": "text copied EXACTLY from the file as you read it, matching once"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                    "content": {"type": "string", "description": "the whole text of a NEW file (a test, or a module)"},
                },
                "required": ["path"],
            },
        },
    }, ["tests"]),
    # a runner on the person's OWN machine (backend/utils/runner_jobs.py, runner/): one of THEIR named checks, run after they press Run on a card. Offered only when one is connected.
    "run_local_check": _object({
        "check": {"type": "string", "description": "the name of one of the person's checks, as listed in this tool's description"},
        "files": {"type": "array", "items": {"type": "string"}, "description": "optional: paths of files in their project, only for a check that takes files"},
    }, ["check"]),
    # plan mode (backend/utils/code_plan.py), offered only on a plan turn: the plan the person approves before anything is edited. Nothing is written to their folder.
    "submit_plan": _object({
        "goal": {"type": "string", "description": "one sentence: what the change is"},
        "files": {
            "type": "array",
            "description": "every file the change touches",
            "items": _object({
                "path": {"type": "string", "description": "the file's path in the folder, exactly as it is there"},
                "why": {"type": "string", "description": "what changes in it, in a short line"},
                "new": {"type": "boolean", "description": "true only for a file the change creates (it must not exist yet)"},
            }, ["path", "why"]),
        },
        "steps": {
            "type": "array",
            "description": "the ordered steps; each names the files it changes, and every file above belongs to a step",
            "items": _object({
                "text": {"type": "string", "description": "what this step does, in a short line"},
                "files": {"type": "array", "items": _STRING, "description": "the paths (from files above) this step changes"},
            }, ["text", "files"]),
        },
        "tests": {"type": "array", "items": _STRING, "description": "the tests worth running after the change (short lines)"},
        "unknowns": {"type": "array", "items": _STRING, "description": "what you could not tell from reading, instead of guessing"},
    }, ["goal", "files", "steps"]),
    # a connected local folder: a proposal the person applies (or not) from a diff card. Nothing is written by the call.
    "propose_local_edits": _object({
        "summary": {"type": "string", "description": "one or two sentences on what the edits do and why"},
        "edits": {
            "type": "array",
            "description": "every edit for the request, in this one call",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "the file's path in the folder"},
                    "old_string": {"type": "string", "description": "text copied EXACTLY from the file as you read it this turn, matching once (to change an existing file)"},
                    "new_string": {"type": "string", "description": "what replaces old_string"},
                    "replace_all": {"type": "boolean", "description": "true when the same text should change in EVERY place it appears"},
                    "content": {"type": "string", "description": "the whole text of a NEW file (instead of old_string/new_string)"},
                    "document": {
                        "type": "object",
                        "description": "a NEW Word document (path ends in .docx)",
                        "properties": {
                            "title": {"type": "string"},
                            "blocks": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "type": {"type": "string", "enum": ["heading", "paragraph", "bullets", "numbered", "table"]},
                                        "text": {"type": "string"},
                                        "level": {"type": "integer", "description": "1-3, for a heading"},
                                        "items": {"type": "array", "items": {"type": "string"}},
                                        "rows": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                        "header": {"type": "boolean"},
                                    },
                                    "required": ["type"],
                                },
                            },
                        },
                    },
                    "word_edits": {
                        "type": "array",
                        "description": "changes to an EXISTING Word document, by the paragraph numbers you read",
                        "items": {
                            "type": "object",
                            "properties": {
                                "op": {"type": "string", "enum": ["replace", "insert_after", "delete"]},
                                "paragraph": {"type": "integer"},
                                "old": {"type": "string", "description": "for replace: text copied exactly from that paragraph, appearing once in it"},
                                "new": {"type": "string", "description": "for replace: the replacement (may be empty)"},
                                "text": {"type": "string", "description": "for insert_after: the new paragraph"},
                            },
                            "required": ["op", "paragraph"],
                        },
                    },
                    "save_as": {"type": "string", "description": "for word_edits: the new copy's name (default 'Name (edited).docx')"},
                },
                "required": ["path"],
            },
        },
    }, ["summary", "edits"]),
}

# Descriptions that replace the menu line's text for the native engine. The old menu line describes the OLD engine's way of calling a tool ("as a query step ... never a field
# inside a final answer"), which would mislead a model that makes real function calls.
NATIVE_DESCRIPTIONS: Dict[str, str] = {
    "propose_merge": (
        "Show the person a card for merging an OPEN pull request, built from the pull request as GitHub has it right now, using THEIR token. Pass the number (pr_number) or the branch it comes from "
        "(head, for example feat/checkouts), and base if they named the target. Nothing is merged until they approve the card. Use it whenever they ask you to merge a pull request or a branch into "
        "another: you CAN do this, never say you cannot. Call it once and then stop: say the card is waiting for their approval, never that it was merged. It is refused in a turn where you read a web "
        "page, email, shared document or logs."
    ),
    "propose_commit": (
        "Commit their work to GitHub, with THEIR token, the way a person commits after a piece of work: the files you changed and they applied (scope applied), or all their working changes, meaning every file in their folder that differs from the branch on GitHub (scope working: use it for \"commit our working changes\" or \"commit everything\"). A short message, onto the branch their folder is on, or onto a "
        "new branch you name (new_branch), optionally opening a pull request (open_pull_request, pr_title, pr_base, pr_draft). It changes nothing by itself: they see a card with the repo, branch, files with line counts and the "
        "message, and nothing is committed until they approve. Use it when they ask you to commit, push, or open a pull request for the work. The message says what the change DOES, as a pull request title and a line or two "
        "(never \"commit the working changes\"); leave it out if you do not know what changed, and one is written from the diff; do not add trailers. Call it once and then stop: say that the card is waiting for their approval, never that it was committed. It is refused in a turn where you read a web "
        "page, email, shared document or logs."
    ),
    "run_sandbox_tests": (
        "Run Python unittest files against a COPY of the person's connected folder, in a locked-down sandbox (no network, nothing outside the copy, a compute limit). It changes nothing and shows "
        "nothing to the person. Pass `edits` (the same text edits propose_local_edits takes, including a NEW test file) and the tests run against the folder as it would be AFTER those edits, so you "
        "can check a change before you propose it and fix it if it fails. Write tests with unittest and the standard library only, put them in .sonic/tests/, and keep them small and specific. The "
        "sandbox has no installed packages: code that imports FastAPI, LangChain, pymongo and the like cannot run there (use it for pure logic, or give the test a stand-in). A pass covers only what "
        "the tests exercise; say that, not that the change is proven. A failing result is real: read the traceback, fix the change, run again."
    ),
    "propose_append_target_doc": (
        "Append text to the Google Doc the person set as their target document under Integrations. It writes nothing by itself: the person sees your text on an approval card and "
        "approves, edits or rejects it. Use it when they ask to add, append, save or put something in their doc, after you have gathered and written the finished content. "
        "Call it once with the complete text, then stop: the card is the answer, so do not say it was added."
    ),
    "submit_plan": (
        "Hand the person your plan for the change they asked for, instead of editing. It changes nothing: they see the plan as a card and approve it, change a step, or cancel. Call it ONCE, "
        "after reading what you need: the goal, every file the change touches with why (an existing file must exist in the folder, exactly as you read it; mark a file you create with new), "
        "ordered steps that each name their files, the tests worth running, and what you could not tell from reading. If it returns an error, fix what it names and call it again."
    ),
    "propose_local_edits": (
        "Make a change to the person's connected local folder. It changes nothing by itself: the person sees a diff with an Apply button, which is their confirmation, and can undo it. "
        "Use it INSTEAD of pasting code in your answer whenever they ask you to make, apply or implement a change to files in their folder. Read the file first, then copy old_string "
        "EXACTLY as you read it this turn, with its indentation, and include enough surrounding lines that it matches exactly once (or set replace_all for every occurrence). "
        "Forms of edit: {path, old_string, new_string} changes an existing text file; {path, content} creates a NEW text file; {path 'Name.docx', document} creates a NEW Word "
        "document (headings, paragraphs, bullets, numbered lists, tables; pick a name that does not exist); {path 'Existing.docx', word_edits, save_as} changes an existing Word "
        "document by the paragraph numbers read_repo_file showed, saved as a NEW copy beside the original, which is never changed (paragraphs marked * cannot be edited, ~ cannot be "
        "deleted, ! cannot have one inserted after). Propose every edit for the request in ONE call. If it returns an error, fix the edits it names and call it again with the complete "
        "corrected list. The call returns what the person decided: APPLIED (the files now contain it; read them to check, propose a follow-up, or finish), DECLINED (nothing changed; adjust to their reason, do not repeat the same proposal) or NO DECISION (nothing changed; say so and stop). Say a change was applied only when the result says APPLIED. Finish with a short answer on what changed and why: do not repeat the code."
    ),
}

_MENU_LINE_RE = re.compile(r"^-\s+([a-z_]+)\s+—\s+(.*)$", re.DOTALL)


def declarations_from_menu(menu_lines: Iterable[str]) -> List[Dict[str, Any]]:
    """Function declarations for the tools this turn's menu offers AND this engine can handle. A tool without arguments has no
    `parameters` (the API rejects an empty object schema)."""
    declarations: List[Dict[str, Any]] = []
    seen = set()
    for line in menu_lines:
        match = _MENU_LINE_RE.match(str(line).strip())
        if not match or match.group(1) not in REGISTRY or match.group(1) in seen:
            continue
        name = match.group(1)
        seen.add(name)
        declaration: Dict[str, Any] = {"name": name, "description": NATIVE_DESCRIPTIONS.get(name) or " ".join(match.group(2).split())}
        schema = REGISTRY[name]
        if schema["properties"]:
            declaration["parameters"] = schema
        declarations.append(declaration)
    return declarations
