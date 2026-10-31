"""Where to look, found before the model's first step (backend/utils/orientation.py). Pure code over a temp folder: no model, no database."""
import os

from backend.utils import orientation as o

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _folder(tmp_path, files):
    for path, text in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path, sorted(files)


# ---------------------------------------------------------------------------------------------------------------
# The words searched for
# ---------------------------------------------------------------------------------------------------------------

def test_file_names_identifiers_and_quoted_text_come_before_plain_words():
    terms = o.search_terms(['redesign the Integrations.tsx page, rename build_commit_line and the "Waiting for you" notice'])
    assert terms[0] == "Integrations.tsx"
    assert "build_commit_line" in terms and "Waiting for you" in terms
    assert terms.index("build_commit_line") < terms.index("notice") if "notice" in terms else True


def test_request_verbs_and_filler_are_not_searched_for():
    terms = [t.lower() for t in o.search_terms(["please redesign and simplify the integrations page, collapse the text"])]
    assert "integrations" in terms
    assert not {"please", "redesign", "simplify", "collapse"} & set(terms)


def test_at_most_eight_terms_no_repeats_and_longer_plain_words_first():
    terms = o.search_terms(["terminal terminal Terminal blade overlay plan mode runner folder composer session column rail card"])
    assert len(terms) <= o.MAX_TERMS and len({t.lower() for t in terms}) == len(terms)
    assert len(terms[0]) >= len(terms[-1])


def test_a_pasted_transcript_is_cut_before_its_words_are_taken():
    terms = o.search_terms(["fix the header " + "x" * o.MAX_TEXT_CHARS + " zebracrossing"])
    assert "zebracrossing" not in [t.lower() for t in terms]


def test_a_message_with_nothing_specific_has_no_terms():
    assert o.search_terms(["ok do it", "yes please"]) == []


# ---------------------------------------------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------------------------------------------

def test_a_word_in_the_file_name_beats_a_word_found_in_many_files(tmp_path):
    files = {f"src/other_{i}.py": "claim claim claim\n" for i in range(12)}
    files["src/claim_check.py"] = "def unproven():\n    return 1\n"
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["why does the claim check say it passed"])
    assert "src/claim_check.py" in block.splitlines()[1]


def test_a_file_named_in_the_request_comes_first(tmp_path):
    files = {"pages/Integrations.tsx": "export const IntegrationsPage = () => null;\n", "pages/Privacy.tsx": "integrations privacy integrations privacy integrations\n"}
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["redesign Integrations.tsx and the privacy text"])
    assert block.splitlines()[1].startswith("- pages/Integrations.tsx")


def test_several_different_words_beat_one_word_repeated(tmp_path):
    files = {"a.py": "\n".join("terminal" for _ in range(30)), "b.py": "terminal\nrunner\nblade\n"}
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["terminal runner blade"])
    assert block.splitlines()[1].startswith("- b.py")


def test_tests_count_half(tmp_path):
    files = {"backend/thing.py": "def widget_sync():\n    pass\n", "backend/tests/test_thing.py": "def widget_sync():\n    pass\n"}
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["change widget_sync"])
    assert block.index("backend/thing.py") < block.index("test_thing.py")


# ---------------------------------------------------------------------------------------------------------------
# What is shown
# ---------------------------------------------------------------------------------------------------------------

def test_a_declaration_is_shown_instead_of_a_header_comment(tmp_path):
    files = {"a.py": '"""Handles the widget sync for the page."""\n# widget sync notes\nimport widget_sync\n\ndef widget_sync():\n    return 1\n'}
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["fix widget_sync"])
    assert "line 5: def widget_sync():" in block


def test_a_weak_coincidence_returns_nothing(tmp_path):
    root, paths = _folder(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"})
    assert o.where_to_look(root, paths, ["rework the zebra caching strategy"]) == ""


def test_a_follow_up_finds_the_file_from_the_message_before_it(tmp_path):
    root, paths = _folder(tmp_path, {"pages/Integrations.tsx": "export const IntegrationsPage = () => null;\n", "x.py": "pass\n"})
    block = o.where_to_look(root, paths, ["MAKE THE CHANGE", "redesign Integrations.tsx"])
    assert "pages/Integrations.tsx" in block


def test_the_block_says_it_is_a_map_not_proof_and_stays_small(tmp_path):
    files = {f"src/widget_{i}.py": "\n".join(f"def widget_{j}(): return {j}" for j in range(40)) for i in range(10)}
    root, paths = _folder(tmp_path, files)
    block = o.where_to_look(root, paths, ["change widget_3 and widget_4 and widget_5"])
    assert "not proof" in block and len(block) <= o.MAX_BLOCK_CHARS and block.count("\n- ") <= o.MAX_FILES_SHOWN


def test_it_never_raises_and_binary_or_huge_files_are_skipped(tmp_path):
    root, paths = _folder(tmp_path, {"a.png": "widget_sync", "big.py": "widget_sync " * (o.MAX_FILE_BYTES // 5)})
    assert o.where_to_look(root, paths + ["missing/file.py"], ["fix widget_sync"]) == ""
    assert o.where_to_look(None, ["a.py"], ["fix widget_sync"]) == ""
    assert o.where_to_look(root, [], ["fix widget_sync"]) == ""


# ---------------------------------------------------------------------------------------------------------------
# How it reaches the model
# ---------------------------------------------------------------------------------------------------------------

def test_the_tool_agent_gets_the_block_for_a_folder_turn_but_not_a_carried_out_plan():
    source = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    start = source.index("if plan_phase != code_plan.EXECUTE_PHASE:\n                schema_parts.append(await asyncio.to_thread(orientation.where_to_look")
    assert "local_workspace_handle.root, snapshot_paths(username)" in source[start:start + 400]
    assert "previous_human_texts(state.get(\"messages\", []))" in source[start:start + 400]


def test_the_module_logs_nothing_and_calls_no_model():
    source = open(os.path.join(REPO, "backend", "utils", "orientation.py"), encoding="utf-8").read()
    assert "logger" not in source and "logging" not in source and "llm" not in source.lower().replace("llm call", "")
