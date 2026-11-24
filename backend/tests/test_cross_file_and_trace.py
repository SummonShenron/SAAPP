"""Multi-file changes (2026-11-24 coding audit): trace_symbol on the connected folder (backend/utils/symbol_refs.py) and the cross-file check on every proposal (backend/utils/cross_file.py), pure and
through tool_agent_node's real dispatcher with a connected folder. No model, no database."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import local_workspace as lw
from backend.services import repo_checkout as rc
from backend.tests.legacy_loop import react_style
from backend.utils import cross_file, self_counters, symbol_refs

# ---------------------------------------------------------------------------------------------- trace_symbol, pure

REPO = {
    "backend/utils/budget.py": "LIMIT = 5\n\ndef notice_level(spent, limit):\n    return spent / limit\n\nclass Budget:\n    def notice_level(self):\n        return 1\n",
    "app.py": "from backend.utils.budget import notice_level\n\nlevel = notice_level(3, 4)\nprint(level)\nnotice_levels = []\n",
    "local/src/Banner.tsx": "import { noticeLevel } from './api';\nexport function Banner() {\n  const x = noticeLevel(1);\n  return x;\n}\n",
    "local/src/api.ts": "export function noticeLevel(n: number) {\n  return n;\n}\nexport const other = 1;\n",
    "local/src/a.css": ".notice-level { color: red; }\n.notice-level-big { color: blue; }\n",
}


def test_references_are_whole_words_case_sensitive_and_sorted_into_defined_imported_and_used():
    found = symbol_refs.find_references(REPO.items(), "notice_level")
    assert [(p, n) for p, n, _t in found["definitions"]] == [("backend/utils/budget.py", 3), ("backend/utils/budget.py", 7)]
    assert found["imports"] == {"app.py": [1]}
    assert found["uses"] == {"app.py": [(3, "level = notice_level(3, 4)")]}              # notice_levels on line 5 is another word
    assert found["files"] == 2
    ts = symbol_refs.find_references(REPO.items(), "noticeLevel")
    assert [(p, n) for p, n, _t in ts["definitions"]] == [("local/src/api.ts", 1)]
    assert ts["imports"] == {"local/src/Banner.tsx": [1]} and list(ts["uses"]) == ["local/src/Banner.tsx"]
    css = symbol_refs.find_references(REPO.items(), "notice-level")
    assert [(p, n) for p, n, _t in css["definitions"]] == [("local/src/a.css", 1)]              # not .notice-level-big
    assert symbol_refs.find_references(REPO.items(), "LIMIT")["definitions"][0][:2] == ("backend/utils/budget.py", 1)
    assert symbol_refs.find_references(REPO.items(), "Notice_level")["files"] == 0


@pytest.mark.parametrize("line, kind", [
    ("def greet(name):", "define"), ("    async def greet(self):", "define"), ("export const greet = () => 1;", "define"), ("export default function greet() {", "define"),
    ("interface greet {", "define"), ("  greet(a, b) {", "define"), ("    self.greet = 1", "define"), ("greet = make()", "define"),
    ("    greet = make()", "use"), ("  greet(a, b);", "use"), ("x = greet(1)", "use"), ("from m import greet", "import"), ("import { greet } from './m';", "import"),
    ("const { greet } = props;", "define"), ("if greet == 1:", "use"),
])
def test_each_line_is_classified(line, kind):
    assert symbol_refs.classify("greet", line) == kind


def test_the_report_puts_definitions_first_caps_each_file_and_says_plainly_when_nothing_matched():
    many = {"a.py": "def f():\n    pass\n" + "f()\n" * 10, **{f"m{i}.py": "f()\n" for i in range(symbol_refs.MAX_FILES_SHOWN + 3)}}
    text = symbol_refs.format_references(symbol_refs.find_references(many.items(), "f"), "f")
    assert text.index("DEFINED:") < text.index("USED")
    assert "a.py:1: def f():" in text and "... 6 more in this file" in text and "... and 4 more file(s)" in text
    assert "No whole-word matches" in symbol_refs.format_references(symbol_refs.find_references(many.items(), "nothing"), "nothing")
    assert "no definition found" in symbol_refs.format_references(symbol_refs.find_references({"x.py": "print(f)\n"}.items(), "f"), "f")


def test_only_code_and_text_files_are_searched():
    assert symbol_refs.find_references({"logo.png": "greet", "notes.md": "greet"}.items(), "greet")["files"] == 1


# ---------------------------------------------------------------------------------------------- cross_file, pure

FOLDER = {
    "pkg/__init__.py": "",
    "pkg/greet.py": "def greet(name, punct='!'):\n    return 'hi ' + name + punct\n\ndef bye(name):\n    return name\n\nVERSION = 1\n",
    "pkg/sub/__init__.py": "",
    "pkg/sub/tool.py": "def tool():\n    return 1\n",
    "app.py": "from pkg.greet import greet, VERSION\nimport pkg.greet as g\n\nprint(greet('a'), g.bye('b'))\n",
    "other.py": "from pkg import greet as greet_module\n\ngreet_module.greet('x', punct='?')\n",
    "rel/__init__.py": "", "rel/a.py": "from .b import helper\n\nhelper()\n", "rel/b.py": "def helper():\n    return 1\n",
    "web/api.ts": "export function noticeLevel(n: number) { return n; }\nexport const other = 1;\nexport { noticeLevel as nl };\n",
    "web/Banner.tsx": "import { noticeLevel, other } from './api';\nexport const B = () => noticeLevel(other);\n",
    "web/index.ts": "export * from './api';\n",
    "web/User.tsx": "import { nl } from './index';\n",
}


def _check(changes, folder=FOLDER):
    after = {path: text for path, text in changes.items()}
    before = {path: folder.get(path) for path in changes}
    return cross_file.check(before, after, sorted(set(folder) | set(after)), lambda p: folder.get(p))


def test_a_removed_python_name_that_another_file_imports_is_named_with_file_and_line():
    problems = _check({"pkg/greet.py": FOLDER["pkg/greet.py"].replace("VERSION = 1\n", "")})
    assert problems == ["app.py:1 imports VERSION from pkg.greet, which this proposal removes from pkg/greet.py. Update that import (and its uses) in the same proposal"]


def test_a_rename_is_caught_through_every_way_a_module_is_reached():
    renamed = FOLDER["pkg/greet.py"].replace("def bye(", "def farewell(").replace("def greet(", "def hello(")
    problems = _check({"pkg/greet.py": renamed})
    assert any(p.startswith("app.py:1 imports greet from pkg.greet") for p in problems)
    assert any(p.startswith("app.py:4 uses g.bye") for p in problems)                       # import pkg.greet as g; g.bye(...)
    assert any(p.startswith("other.py:3 uses greet_module.greet") for p in problems)         # from pkg import greet as greet_module


def test_a_deleted_module_breaks_its_importers_and_a_relative_import_is_resolved():
    assert any(p.startswith("rel/a.py:1 imports helper from rel.b") for p in _check({"rel/b.py": None}))


@pytest.mark.parametrize("new_def, caught, fragment", [
    ("def greet(name, greeting, punct='!'):", True, "app.py:4 calls greet(...) and passes 1 positional argument, the new signature requires 2"),
    ("def greet(name):", True, "other.py:3 calls greet_module.greet(...) and passes punct=, which the new signature no longer accepts"),
    ("def greet(name, *, loud, punct='!'):", True, "does not pass loud="),
    ("def greet(name, punct='!', loud=False):", False, ""),                                    # an optional parameter added: every call still fits
    ("def greet(*args, **kwargs):", False, ""),
])
def test_a_changed_signature_flags_only_the_calls_it_really_breaks(new_def, caught, fragment):
    problems = _check({"pkg/greet.py": FOLDER["pkg/greet.py"].replace("def greet(name, punct='!'):", new_def)})
    assert bool(problems) is caught
    if caught:
        assert any(fragment in p for p in problems), problems


def test_calls_in_the_edited_file_itself_are_checked_against_its_new_signature():
    text = FOLDER["pkg/greet.py"].replace("def greet(name, punct='!'):", "def greet(name, greeting):") + "\nX = greet('a')\n"
    assert any(p.startswith("pkg/greet.py:9 calls greet(...)") for p in _check({"pkg/greet.py": text}))


def test_a_new_import_of_a_name_the_module_lacks_is_caught_but_a_library_or_a_submodule_or_an_old_import_is_not():
    edited = FOLDER["app.py"] + "from pkg.greet import shout\nfrom requests import nothing_checked\nfrom pkg.sub import tool\nfrom pkg import sub\n"
    assert _check({"app.py": edited}) == [
        "app.py:5 imports shout from pkg.greet, but pkg.greet does not define shout. Import a name that exists, or add it to that module in the same proposal"]
    broken_before = {**FOLDER, "app.py": "from pkg.greet import gone\n"}
    assert _check({"app.py": "from pkg.greet import gone\nprint(1)\n"}, broken_before) == []               # it was already there: not this proposal's doing


def test_a_name_added_to_the_module_in_the_same_proposal_makes_the_new_import_fine():
    assert _check({"app.py": FOLDER["app.py"] + "from pkg.greet import shout\n", "pkg/greet.py": FOLDER["pkg/greet.py"] + "\ndef shout():\n    return 1\n"}) == []


def test_a_removed_ts_export_still_imported_elsewhere_is_caught_and_a_new_import_of_a_missing_export_too():
    problems = _check({"web/api.ts": FOLDER["web/api.ts"].replace("export const other = 1;", "const other = 1;")})
    assert problems == ["web/Banner.tsx:1 imports other from './api', which this proposal removes from web/api.ts. Update that import (and its uses) in the same proposal"]
    added = _check({"web/Banner.tsx": "import { noticeLevel, other, missing } from './api';\n"})
    assert added == ["web/Banner.tsx:1 imports missing from './api', but web/api.ts does not export missing. Import a name it exports, or export it in the same proposal"]
    assert _check({"web/User.tsx": "import { nl, anything } from './index';\n"}) == []        # export * : cannot tell, so silent


def test_an_unchanged_proposal_and_unrelated_files_are_silent_and_results_are_capped():
    assert _check({"pkg/greet.py": FOLDER["pkg/greet.py"]}) == []
    many = {**FOLDER, **{f"m{i}.py": "from pkg.greet import VERSION\n" for i in range(20)}}
    assert len(_check({"pkg/greet.py": FOLDER["pkg/greet.py"].replace("VERSION = 1\n", "")}, many)) == cross_file.MAX_PROBLEMS


def test_a_module_that_cannot_be_judged_is_left_alone():
    star = {**FOLDER, "pkg/greet.py": "from somewhere import *\n"}
    assert _check({"pkg/greet.py": "x = 1\n"}, star) == []                     # what it had before is unknown
    lazy = FOLDER["pkg/greet.py"] + "\ndef __getattr__(name):\n    return 1\n"
    assert _check({"app.py": FOLDER["app.py"] + "from pkg.greet import shout\n"}, {**FOLDER, "pkg/greet.py": lazy}) == []


def test_names_defined_under_a_guard_still_count():
    guarded = "try:\n    from fast import loads\nexcept ImportError:\n    def loads(x):\n        return x\n"
    folder = {**FOLDER, "pkg/js.py": guarded}
    assert _check({"app.py": FOLDER["app.py"] + "from pkg.js import loads\n"}, folder) == []


# ---------------------------------------------------------------------------------------------- through the real dispatcher

FILES = {
    "src/__init__.py": "",
    "src/app.py": "def greet(name):\n    return 'hi ' + name\n",
    "src/main.py": "from src.app import greet\n\nprint(greet('x'))\n",
}


def _run(coro):
    return asyncio.run(coro)


async def _drive(monkeypatch, decisions):
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state

    lw.apply_sync("jack", "proj", [{"path": p, "content": c} for p, c in FILES.items()], [], True)
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    counted = []
    monkeypatch.setattr(self_counters, "record_turn_counts", lambda counts, now=None: counted.append(dict(counts)))
    observed = []

    async def fake_loop(**kwargs):
        for decision in decisions:
            observed.append(await kwargs["act"](decision))
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", react_style(fake_loop))
    await aw.tool_agent_node(_state("rename greet to hello", username="jack"))
    cards = [c for c in emit.call_args_list if c.args[0] == "local_edit_proposal"]
    return observed, cards, counted


RENAME = {"tool_action": "propose_local_edits", "args": {"summary": "rename", "edits": [{"path": "src/app.py", "old_string": "def greet(name):", "new_string": "def hello(name):"}]}}


def test_trace_symbol_searches_the_connected_folder_itself(monkeypatch):
    observed, _cards, _ = _run(_drive(monkeypatch, [{"tool_action": "trace_symbol", "args": {"symbol": "greet"}}]))
    assert "DEFINED:\n  src/app.py:1: def greet(name):" in observed[0]
    assert "src/main.py (1 use)" in observed[0] and "IMPORTED IN:\n  src/main.py:1" in observed[0]


def test_a_proposal_that_breaks_another_file_is_sent_back_with_the_place_and_no_card_is_shown(monkeypatch):
    observed, cards, counted = _run(_drive(monkeypatch, [RENAME]))
    assert observed[0].startswith("ERROR: these edits would break other code")
    assert "src/main.py:1 imports greet from src.app" in observed[0]
    assert cards == [] and {"code_cross_file_refused": 1} in counted


def test_the_fixed_proposal_goes_through(monkeypatch):
    fixed = {"tool_action": "propose_local_edits", "args": {"summary": "rename", "edits": [
        RENAME["args"]["edits"][0],
        {"path": "src/main.py", "old_string": "from src.app import greet\n\nprint(greet('x'))", "new_string": "from src.app import hello\n\nprint(hello('x'))"},
    ]}}
    observed, cards, counted = _run(_drive(monkeypatch, [RENAME, fixed]))
    assert observed[0].startswith("ERROR") and len(cards) == 1 and "Proposed 2 edit(s)" in observed[1]
    assert "cross-file" not in observed[1]


def test_the_same_problems_twice_let_the_proposal_through_with_them_named(monkeypatch):
    observed, cards, counted = _run(_drive(monkeypatch, [RENAME, RENAME]))
    assert observed[0].startswith("ERROR") and len(cards) == 1
    assert "Proposed despite the cross-file check, which still reports: src/main.py:1 imports greet" in observed[1]
    assert {"code_cross_file_overridden": 1} in counted


def test_a_failing_check_never_stops_a_sound_proposal(monkeypatch):
    monkeypatch.setattr(cross_file, "check", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    observed, cards, _ = _run(_drive(monkeypatch, [RENAME]))
    assert len(cards) == 1 and not observed[0].startswith("ERROR")


def test_the_new_counters_are_known_keys():
    assert {"code_cross_file_refused", "code_cross_file_overridden"} <= self_counters.KEYS


# ---- what the mutation check found untested ----

def test_a_file_with_the_name_only_inside_a_longer_word_is_not_counted():
    assert symbol_refs.find_references({"a.py": "greeting = 1\n", "b.py": "greet()\n"}.items(), "greet")["files"] == 1


def test_a_call_with_star_arguments_is_never_judged():
    folder = {**FOLDER, "app.py": "from pkg.greet import greet\n\nargs = ['a']\ngreet(*args)\n"}
    problems = _check({"pkg/greet.py": FOLDER["pkg/greet.py"].replace("def greet(name, punct='!'):", "def greet(name, greeting, punct='!'):")}, folder)
    assert not any(p.startswith("app.py") for p in problems)
    assert any(p.startswith("other.py:3") for p in problems)                    # a plain call to it elsewhere is still judged


def test_a_module_with_a_star_import_is_not_judged_for_removals():
    folder = {**FOLDER, "pkg/greet.py": "from somewhere import *\nVERSION = 1\n"}
    assert _check({"pkg/greet.py": "from somewhere import *\n"}, folder) == []           # VERSION may still come in through the star


def test_an_import_a_ts_file_already_had_is_not_blamed_on_the_proposal():
    folder = {**FOLDER, "web/Banner.tsx": "import { gone } from './api';\nexport const B = 1;\n"}
    assert _check({"web/Banner.tsx": "import { gone } from './api';\nexport const B = 2;\n"}, folder) == []


def test_problems_are_capped_even_within_one_file():
    big = "".join(f"V{i} = {i}\n" for i in range(12))
    folder = {**FOLDER, "pkg/many.py": big, "app.py": "".join(f"from pkg.many import V{i}\n" for i in range(12))}
    assert len(_check({"pkg/many.py": "X = 1\n"}, folder)) == cross_file.MAX_PROBLEMS


def test_a_two_dot_relative_import_resolves_from_the_parent_package():
    folder = {**FOLDER, "rel/deep/__init__.py": "", "rel/deep/x.py": "from ..b import helper\n"}
    assert any(p.startswith("rel/deep/x.py:1 imports helper from rel.b") for p in _check({"rel/b.py": None}, folder))
