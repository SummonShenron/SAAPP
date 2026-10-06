import pytest

from backend.utils import pr_context as pc


def _file(name, additions=5, deletions=1, patch="@@ -1 +1 @@\n-old\n+new", status="modified"):
    return {"filename": name, "status": status, "additions": additions, "deletions": deletions, "patch": patch}


# ---------------------------------------------------------------------------
# What counts as noise / tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "package-lock.json", "web/yarn.lock", "poetry.lock", "go.sum", "static/app.min.js", "dist/bundle.js",
    "web/dist/app.js", "node_modules/x/index.js", "logo.png", "assets/icon.svg", "src/__snapshots__/a.snap",
])
def test_lockfiles_generated_output_and_assets_are_noise(path):
    assert pc.is_noise_file(path)


@pytest.mark.parametrize("path", ["app.py", "src/pages/Chat.tsx", "README.md", "backend/services/github_service.py", "package.json"])
def test_real_source_is_not_noise(path):
    assert not pc.is_noise_file(path)


@pytest.mark.parametrize("path", [
    "backend/tests/test_x.py", "tests/unit/a.py", "src/Chat.test.tsx", "src/Chat.spec.ts", "test_main.py", "pkg/thing_test.go",
])
def test_test_files_are_recognized(path):
    assert pc.is_test_file(path)


@pytest.mark.parametrize("path", ["app.py", "src/contest.py", "latest.py", "docs/testing-guide-notes.txt.bak"])
def test_non_test_files_are_not_mistaken_for_tests(path):
    assert not pc.is_test_file(path)


# ---------------------------------------------------------------------------
# build_diff_context
# ---------------------------------------------------------------------------

def test_source_comes_before_tests_before_docs_and_bigger_changes_first():
    files = [
        _file("README.md", 50, 0), _file("tests/test_a.py", 40, 0), _file("small.py", 1, 1), _file("big.py", 100, 20),
    ]
    text = pc.build_diff_context(files).text
    assert text.index("File: big.py") < text.index("File: small.py") < text.index("File: tests/test_a.py") < text.index("File: README.md")


def test_noise_is_named_but_its_patch_never_enters_the_evidence():
    ctx = pc.build_diff_context([_file("app.py"), _file("package-lock.json", patch="LOCKFILE-CHURN")])
    assert "LOCKFILE-CHURN" not in ctx.text
    assert "package-lock.json" in ctx.text and "left out" in ctx.text
    assert ctx.noise_skipped == ["package-lock.json"]
    assert ctx.shown == 1 and ctx.total == 2


def test_each_file_shows_its_status_and_line_counts():
    text = pc.build_diff_context([_file("app.py", 12, 3, status="added")]).text
    assert "File: app.py (added, +12/-3)" in text


def test_a_long_patch_is_cut_and_the_cut_is_recorded():
    ctx = pc.build_diff_context([_file("big.py", patch="x" * 5000)], per_file_chars=300)
    assert "[patch truncated]" in ctx.text
    assert ctx.truncated_files == ["big.py"]
    assert not ctx.complete


def test_the_total_budget_leaves_files_out_and_says_which():
    files = [_file(f"f{i}.py", patch="y" * 900) for i in range(10)]
    ctx = pc.build_diff_context(files, per_file_chars=900, total_chars=2000)
    assert ctx.shown < 10 and ctx.omitted
    assert f"{len(ctx.omitted)} more file(s) not shown" in ctx.text
    assert not ctx.complete


def test_the_file_count_cap_leaves_the_rest_out():
    ctx = pc.build_diff_context([_file(f"f{i}.py", patch="z") for i in range(8)], max_files=3)
    assert ctx.shown == 3 and len(ctx.omitted) == 5


def test_a_small_pr_is_complete():
    ctx = pc.build_diff_context([_file("a.py"), _file("b.py")])
    assert ctx.complete and ctx.shown == 2


def test_a_file_with_no_patch_is_still_listed_with_a_reason():
    text = pc.build_diff_context([_file("blob.bin", patch=None)]).text
    assert "File: blob.bin" in text and "no text patch" in text


def test_no_files_and_junk_entries_are_handled():
    assert pc.build_diff_context([]).text == "(no readable text changes)"
    assert pc.build_diff_context([{"no": "filename"}, None, "x"]).total == 0


def test_test_and_source_files_are_tracked_separately():
    ctx = pc.build_diff_context([_file("app.py"), _file("tests/test_app.py"), _file("README.md")])
    assert ctx.source_files == ["app.py"]
    assert ctx.test_files == ["tests/test_app.py"]


# ---------------------------------------------------------------------------
# Header: intent, size, history, and whether tests moved
# ---------------------------------------------------------------------------

def test_commit_subjects_keep_the_first_line_and_drop_merges():
    commits = [
        {"commit": {"message": "feat: add retries\n\nlong body here"}},
        {"commit": {"message": "Merge branch 'main' into feat"}},
        {"commit": {"message": "  fix: typo  "}},
        {"commit": {"message": ""}},
    ]
    assert pc.commit_subjects(commits) == ["feat: add retries", "fix: typo"]
    assert len(pc.commit_subjects([{"commit": {"message": f"c{i}"}} for i in range(40)], limit=5)) == 5


def test_the_header_carries_the_authors_own_intent_and_size():
    pr = {"number": 7, "title": "Add retries", "user": {"login": "jack"}, "base": {"ref": "main"}, "head": {"ref": "feat/retry"},
          "changed_files": 4, "additions": 80, "deletions": 9, "draft": True, "body": "Retries flaky GitHub calls."}
    ctx = pc.build_diff_context([_file("app.py")])
    header = pc.build_pr_header(pr, ["feat: add retries"], ctx)
    assert "PR #7: Add retries" in header
    assert "by @jack" in header and "feat/retry -> main" in header and "4 files, +80/-9" in header and "draft" in header
    assert "Retries flaky GitHub calls." in header
    assert "- feat: add retries" in header


def test_a_missing_description_is_stated_not_skipped():
    header = pc.build_pr_header({"number": 1, "title": "t", "body": None}, [], pc.build_diff_context([]))
    assert "Author's description: (none)" in header


def test_a_very_long_description_is_cut():
    header = pc.build_pr_header({"number": 1, "title": "t", "body": "w" * 5000}, [], pc.build_diff_context([]))
    assert "[truncated]" in header and len(header) < 2200


def test_the_header_says_when_source_changed_with_no_tests():
    ctx = pc.build_diff_context([_file("app.py")])
    assert "NONE" in pc.build_pr_header(None, [], ctx)


def test_the_header_lists_the_tests_that_changed_with_the_source():
    ctx = pc.build_diff_context([_file("app.py"), _file("tests/test_app.py")])
    header = pc.build_pr_header(None, [], ctx)
    assert "tests/test_app.py" in header and "NONE" not in header


def test_a_docs_only_pr_does_not_claim_tests_are_missing():
    ctx = pc.build_diff_context([_file("README.md")])
    assert "Tests changed" not in pc.build_pr_header(None, [], ctx)


def test_the_fit_note_tells_the_writer_how_much_it_saw():
    assert "full diff of all 2" in pc.fit_note(pc.build_diff_context([_file("a.py"), _file("b.py")]))
    partial = pc.fit_note(pc.build_diff_context([_file("a.py", patch="q" * 5000)], per_file_chars=100))
    assert "only part" in partial and "never describe code you were not shown" in partial


# ---------------------------------------------------------------------------
# The prompt itself
# ---------------------------------------------------------------------------

def test_the_review_prompt_is_fully_filled_in():
    """Regression: the old code checked for a "{diffs}" placeholder that the prompt never had, so the
    model was sent the literal text "{repo}" and "{formatted_diffs}" with the diffs bolted on after."""
    pr = {"number": 7, "title": "Add retries", "body": "why"}
    prompt, ctx = pc.build_review_prompt("acme/app", pr, [{"commit": {"message": "feat: retries"}}], [_file("app.py")])
    assert "'acme/app'" in prompt
    assert "PR #7: Add retries" in prompt and "feat: retries" in prompt and "File: app.py" in prompt
    for placeholder in ("{repo}", "{pr_header}", "{formatted_diffs}", "{fit_note}"):
        assert placeholder not in prompt
    assert ctx.shown == 1


def test_the_review_prompt_tells_the_model_to_stay_grounded_and_to_say_what_it_could_not_see():
    prompt, _ = pc.build_review_prompt("a/b", None, [], [_file("app.py", patch="q" * 20000)])
    assert "Ground every claim" in prompt
    assert "Not reviewed" in prompt
    assert "only part of this PR" in prompt


def test_the_review_prompt_works_with_no_pr_details_at_all():
    prompt, _ = pc.build_review_prompt("a/b", None, None, [])
    assert "(no readable text changes)" in prompt
