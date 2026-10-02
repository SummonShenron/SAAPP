"""The "your code redeclares something that already exists" gate in run_react_loop, plus the
pure helpers and the local-checkout scanner behind it. Observed failure this guards against: a
proposal for `const overflowItems = [...]` presented as new when one already existed in a region
of Chat.tsx the model never read, which would have dropped six real menu items."""
import asyncio
import functools
import json
from types import SimpleNamespace

from backend.services import agent_workflow as aw
from backend.services import repo_checkout as rc
from backend.utils import agent_utils as au

GROUNDING_CHECK_MARKER = "REAL TOOL OBSERVATIONS GATHERED THIS TURN"
IDIOM_CHECK_MARKER = "PROPOSED CODE is genuinely tailored"

CODE_ANSWER = "```tsx\nconst overflowItems = [1, 2];\n```"
EXISTING_LINE = "const overflowItems: OptionWheelItem[] = ["
EXISTING = {"overflowItems": [{"path": "local/src/pages/Chat.tsx", "line": 1371, "text": EXISTING_LINE}]}
UNRELATED_READ = {
    "purpose": "Read some other file",
    "action_desc": "read_repo_file(path=local/src/components/Other.tsx)",
    "observation": "export function Other() { return null; }",
}


def run_async(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))
    return wrapper


def _llm_response(**payload):
    return SimpleNamespace(content=json.dumps(payload))


async def _run_loop(responses, *, lookup, initial_attempts=None, act_result="unused"):
    """Drives run_react_loop with a canned model; grounding/idiom judge calls are answered
    'grounded' so only the gate under test can cause a rejection. Returns (result, prompts)."""
    prompts = []

    async def fake_ainvoke(prompt):
        if GROUNDING_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, unsupported_claims=[])
        if IDIOM_CHECK_MARKER in prompt:
            return _llm_response(grounded=True, reason_category=None, reason=None)
        prompts.append(prompt)
        return responses[len(prompts) - 1]

    async def act(decision):
        return act_result

    orig = aw.lite_llm.ainvoke
    aw.lite_llm.ainvoke = fake_ainvoke
    try:
        kwargs = {"declaration_lookup": lookup} if lookup is not None else {}
        result = await aw.run_react_loop(
            question="move the filter controls into the menu",
            schema="repo=x",
            prompt_template="{question} | {schema} | {attempts}",
            act=act,
            max_iterations=6,
            node_name="test_node",
            initial_attempts=initial_attempts if initial_attempts is not None else [UNRELATED_READ],
            **kwargs,
        )
    finally:
        aw.lite_llm.ainvoke = orig
    return result, prompts


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------

@run_async
async def test_redeclaring_an_unseen_existing_declaration_is_rejected_once_then_accepted():
    async def lookup(names):
        return EXISTING

    responses = [
        _llm_response(action="final", answer=CODE_ANSWER),
        _llm_response(action="query", purpose="Read the existing menu", tool_action="read_repo_file",
                      args={"path": "local/src/pages/Chat.tsx"}),
        _llm_response(action="final", answer=CODE_ANSWER),
    ]

    result, prompts = await _run_loop(responses, lookup=lookup, act_result=EXISTING_LINE + "\n  { label: 'Help' },\n]")

    assert len(prompts) == 3
    assert "already exists in this repo" in prompts[1]
    assert "`overflowItems` at local/src/pages/Chat.tsx:1371" in prompts[1]
    assert result["final_answer"] == CODE_ANSWER


@run_async
async def test_redeclaring_a_declaration_the_model_already_read_is_accepted_immediately():
    """False-positive guard: a legitimate "here's the updated X" must pass once the existing X
    was actually read this turn."""
    async def lookup(names):
        return EXISTING

    seen = {
        "purpose": "Read the existing menu",
        "action_desc": "read_repo_file(path=local/src/pages/Chat.tsx, start_line=1360)",
        "observation": "URL: ...\n  " + EXISTING_LINE + "\n    { label: 'Help' },\n  ];",
    }
    result, prompts = await _run_loop(
        [_llm_response(action="final", answer=CODE_ANSWER)], lookup=lookup, initial_attempts=[seen],
    )

    assert len(prompts) == 1
    assert result["final_answer"] == CODE_ANSWER


@run_async
async def test_duplicate_declaration_rejection_is_budget_limited():
    async def lookup(names):
        return EXISTING

    responses = [
        _llm_response(action="final", answer=CODE_ANSWER),
        _llm_response(action="final", answer=CODE_ANSWER),
    ]
    result, prompts = await _run_loop(responses, lookup=lookup)

    assert len(prompts) == 2  # rejected once, then accepted despite still being unseen
    assert result["final_answer"] == CODE_ANSWER


@run_async
async def test_no_lookup_means_no_gate():
    result, prompts = await _run_loop([_llm_response(action="final", answer=CODE_ANSWER)], lookup=None)

    assert len(prompts) == 1
    assert result["final_answer"] == CODE_ANSWER


@run_async
async def test_lookup_failure_fails_open():
    async def lookup(names):
        raise RuntimeError("checkout gone")

    result, prompts = await _run_loop([_llm_response(action="final", answer=CODE_ANSWER)], lookup=lookup)

    assert len(prompts) == 1
    assert result["final_answer"] == CODE_ANSWER


@run_async
async def test_answer_without_a_code_block_never_triggers_a_lookup():
    calls = []

    async def lookup(names):
        calls.append(names)
        return EXISTING

    result, prompts = await _run_loop(
        [_llm_response(action="final", answer="You should rename overflowItems in prose only.")], lookup=lookup,
    )

    assert calls == []
    assert len(prompts) == 1


@run_async
async def test_generic_declared_names_are_never_looked_up():
    calls = []

    async def lookup(names):
        calls.append(names)
        return {}

    answer = "```python\ndef helper():\n    result = 1\n    return result\n```"
    await _run_loop([_llm_response(action="final", answer=answer)], lookup=lookup)

    assert calls == []


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_extract_declared_names_reads_js_and_python_declarations_from_code_blocks_only():
    answer = (
        "I'd rename `const proseOnlyName` here.\n"
        "```tsx\nexport const overflowItems = [];\nfunction runOverflowAction() {}\n```\n"
        "```python\nasync def fetch_everything():\n    pass\nclass WidgetBuilder:\n    pass\n```"
    )
    assert au._extract_declared_names(answer) == [
        "overflowItems", "runOverflowAction", "fetch_everything", "WidgetBuilder",
    ]


def test_extract_declared_names_drops_short_and_generic_names_and_dedupes():
    answer = "```ts\nconst abc = 1;\nconst result = 2;\nconst realThing = 3;\nlet realThing = 4;\n```"
    assert au._extract_declared_names(answer) == ["realThing"]


def test_find_unseen_skips_names_declared_in_too_many_places():
    common = {"loadStuff": [{"path": f"f{i}.ts", "line": 1, "text": "const loadStuff = 1"} for i in range(5)]}
    assert au._find_unseen_existing_declarations(common, []) == []


def test_find_unseen_reports_only_declarations_absent_from_every_observation():
    existing = {
        "seenThing": [{"path": "a.ts", "line": 3, "text": "const seenThing = 1"}],
        "unseenThing": [{"path": "b.ts", "line": 9, "text": "const unseenThing = 2"}],
    }
    attempts = [{"observation": "...\nconst seenThing = 1\n..."}]

    assert au._find_unseen_existing_declarations(existing, attempts) == [
        {"name": "unseenThing", "path": "b.ts", "line": 9},
    ]


# ---------------------------------------------------------------------------
# The local-checkout scanner
# ---------------------------------------------------------------------------

def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scanner_finds_js_ts_and_python_declarations_with_path_and_line(tmp_path):
    _write(tmp_path, "src/Chat.tsx", "import x from 'y';\n\n  const overflowItems: Item[] = [\n];\n")
    _write(tmp_path, "backend/util.py", "def build_thing():\n    pass\n\nclass Builder:\n    pass\n")

    found = rc.find_declarations_in_checkout(str(tmp_path), ["overflowItems", "build_thing", "Builder", "missing"])

    assert found["overflowItems"] == [{"path": "src/Chat.tsx", "line": 3, "text": "const overflowItems: Item[] = ["}]
    assert found["build_thing"][0]["path"] == "backend/util.py"
    assert found["Builder"][0]["line"] == 4
    assert "missing" not in found


def test_scanner_ignores_usages_comments_and_substring_names(tmp_path):
    _write(tmp_path, "a.ts", "// const overflowItems = old\nuse(overflowItems);\nconst overflowItemsExtra = 1;\n")

    assert rc.find_declarations_in_checkout(str(tmp_path), ["overflowItems"]) == {}


def test_scanner_skips_vendored_dirs_non_source_files_and_bad_input(tmp_path):
    _write(tmp_path, "node_modules/pkg/index.js", "const overflowItems = 1;\n")
    _write(tmp_path, "notes.md", "const overflowItems = 1;\n")

    assert rc.find_declarations_in_checkout(str(tmp_path), ["overflowItems"]) == {}
    assert rc.find_declarations_in_checkout(str(tmp_path), []) == {}
    assert rc.find_declarations_in_checkout(str(tmp_path), ["not an identifier!"]) == {}
    assert rc.find_declarations_in_checkout("", ["overflowItems"]) == {}


def test_scanner_caps_hits_per_name(tmp_path):
    for i in range(8):
        _write(tmp_path, f"f{i}.ts", "const sharedName = 1;\n")

    found = rc.find_declarations_in_checkout(str(tmp_path), ["sharedName"], max_per_name=3)

    assert len(found["sharedName"]) == 3


# ---------------------------------------------------------------------------
# tool_agent_node wiring: the lookup it hands the loop is backed by the turn's real checkout
# ---------------------------------------------------------------------------

@run_async
async def test_tool_agent_node_hands_the_loop_a_checkout_backed_declaration_lookup(monkeypatch, tmp_path):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _make_fake_checkout, _state

    handle = _make_fake_checkout(tmp_path, {"src/Chat.tsx": "const overflowItems = [\n];\n"})
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: handle)
    _setup_github_repo(monkeypatch)
    captured = {}

    async def fake_run_react_loop(**kwargs):
        captured.update(kwargs)
        found = await kwargs["declaration_lookup"](["overflowItems"])
        captured["found"] = found
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("move the controls"))

    assert captured["found"]["overflowItems"][0]["path"] == "src/Chat.tsx"


@run_async
async def test_tool_agent_node_declaration_lookup_is_empty_without_a_checkout(monkeypatch):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    def _no_checkout(*a, **k):
        raise rc.RepoCheckoutError("tarball download failed")

    monkeypatch.setattr(aw, "fetch_and_extract_checkout", _no_checkout)
    _setup_github_repo(monkeypatch)
    captured = {}

    async def fake_run_react_loop(**kwargs):
        captured["found"] = await kwargs["declaration_lookup"](["overflowItems"])
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw, "run_react_loop", fake_run_react_loop)

    await aw.tool_agent_node(_state("move the controls"))

    assert captured["found"] == {}
