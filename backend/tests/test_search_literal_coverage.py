"""search_literal must look at the whole project (backend/services/agent_workflow.py).

WHY. Real turns on 2026-10-29: a connected folder of ~720 files was searched through its first 300 files in whatever order the filesystem listed them, the answer said "exhaustively scanned 300 of 300
candidate files", and a search for `UsageRing`, which `Chat.tsx` imports and renders, came back empty; 56 of that day's 83 searches found nothing. The agent then circled the repository re-reading files."""
import asyncio
from unittest.mock import AsyncMock

from backend.services import agent_workflow as aw
from backend.services import local_workspace as lw
from backend.services import repo_checkout as rc
from backend.tests.test_tool_agent_node import _b64, _http_response, _llm_response, _setup_github_repo, _state


def test_the_order_is_fixed_and_puts_source_before_tests_before_docs_and_data():
    paths = [
        "docs/plan.md", "eval_results/run.json", "backend/tests/test_a.py", "local/src/pages/Chat.tsx", "app.py", "local/src/pages/Chat.test.mjs",
        "backend/services/agent_workflow.py", "README.md", "local/src/components/UsageRing.tsx", "runner/tests/test_b.py", "local/package-lock.json",
    ]
    ordered = sorted(paths, key=aw._search_priority)
    source = ordered[:4]
    assert set(source) == {"app.py", "backend/services/agent_workflow.py", "local/src/components/UsageRing.tsx", "local/src/pages/Chat.tsx"}
    assert set(ordered[4:7]) == {"backend/tests/test_a.py", "local/src/pages/Chat.test.mjs", "runner/tests/test_b.py"}
    assert set(ordered[7:]) == {"docs/plan.md", "eval_results/run.json", "README.md", "local/package-lock.json"}
    assert sorted(reversed(paths), key=aw._search_priority) == ordered                    # the same whatever order the files were listed in


async def _search_connected_folder(monkeypatch, files, term):
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    monkeypatch.setattr(aw, "get_db", lambda: None)
    monkeypatch.setattr(aw, "safe_emit_event", AsyncMock())
    lw.apply_sync("jack", "big-project", [{"path": path, "content": text} for path, text in files.items()], [], True)
    prompts = []
    replies = [_llm_response(action="query", purpose="find it", tool_action="search_literal", args={"term": term}), _llm_response(action="final", answer="done")]

    async def fake(prompt, *args, **kwargs):
        prompts.append(str(prompt))
        return replies.pop(0) if replies else _llm_response(action="final", answer="done")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake)
    await aw.tool_agent_node(_state(f"find where {term} is used", username="jack"))
    return "\n".join(prompts)


def test_a_connected_folder_is_searched_in_full_so_a_term_in_the_last_files_is_found(monkeypatch):
    files = {f"backend/tests/test_{i:03}.py": "value = 1\n" for i in range(400)}                   # more than a 300-file window of it, all sorting before the frontend
    files["local/src/pages/Chat.tsx"] = "import UsageRing from '../components/UsageRing';\nexport const x = 1;\n"
    files["local/src/components/UsageRing.tsx"] = "export default function UsageRing() { return null; }\n"
    seen = asyncio.run(_search_connected_folder(monkeypatch, files, "UsageRing"))
    assert "local/src/pages/Chat.tsx:1:" in seen and "local/src/components/UsageRing.tsx:1:" in seen
    assert "were not searched at all" not in seen


def test_a_term_that_is_nowhere_says_it_looked_at_every_file_and_makes_no_warning_about_files_it_did_not_skip(monkeypatch):
    files = {f"backend/f{i:03}.py": "value = 1\n" for i in range(350)}
    seen = asyncio.run(_search_connected_folder(monkeypatch, files, "NoSuchThingAnywhere"))
    assert "No occurrences of 'NoSuchThingAnywhere'" in seen and "were not searched at all" not in seen and "were not reached" not in seen


def test_the_github_path_still_stops_at_its_file_cap_and_now_says_so(monkeypatch):
    monkeypatch.setattr(aw, "load_user_directory_groups", lambda username: ["Guest"])
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("TOOL_AGENT_ENGINE", "react")
    total = aw._SEARCH_LITERAL_MAX_FILES_SCANNED + 40
    tree = _http_response(200, {"tree": [{"path": f"backend/f{i:04}.py", "type": "blob", "size": 10, "sha": f"sha{i}"} for i in range(total)]})

    def fake_get(url, headers=None, params=None, **kwargs):
        if url.endswith("/repos/SummonShenron/SAAPP"):
            return _http_response(200, {"default_branch": "main"})
        if "/git/trees/" in url:
            return tree
        if "/git/blobs/" in url:
            return _http_response(200, {"encoding": "base64", "content": _b64("value = 1\n")})
        return _http_response(200, {"content": _b64("import os\n")})

    monkeypatch.setattr(aw.requests, "get", fake_get)
    monkeypatch.setattr(aw, "extract_github_repo", lambda text, fallback="SummonShenron/SAAPP": "SummonShenron/SAAPP")
    prompts = []
    replies = [_llm_response(action="query", purpose="find it", tool_action="search_literal", args={"term": "NoSuchThingAnywhere"}), _llm_response(action="final", answer="done")]

    async def fake(prompt, *args, **kwargs):
        prompts.append(str(prompt))
        return replies.pop(0) if replies else _llm_response(action="final", answer="done")

    monkeypatch.setattr(aw.lite_llm, "ainvoke", fake)
    monkeypatch.setattr(aw.lite_llm_deep, "ainvoke", fake)
    asyncio.run(aw.tool_agent_node(_state("find where NoSuchThingAnywhere is used")))
    seen = "\n".join(prompts)
    assert "40 more candidate file(s) were not searched at all" in seen and "NOT a guaranteed-complete answer" in seen
