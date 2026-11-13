"""The runner looks for the folder SAAPP asked about (by name) under the person's home folder so the dialog can offer it for a yes/no, and falls back to the chooser (finder.py, picker.py)."""
from pathlib import Path

import pytest

from saapp_runner import finder, picker


def make(root: Path, *parts: str) -> Path:
    folder = root.joinpath(*parts)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---- the search ---------------------------------------------------------------------------------------------------------------------------------

def test_a_folder_with_the_name_is_found_case_aside_and_the_shallowest_comes_first(tmp_path):
    deep = make(tmp_path, "Documents", "work", "clients", "My-App")
    shallow = make(tmp_path, "code", "my-app")
    assert finder.find_candidates("my-app", tmp_path) == [shallow, deep]


def test_hidden_folders_other_programs_files_and_a_match_s_insides_are_not_searched(tmp_path):
    make(tmp_path, ".cache", "my-app")
    make(tmp_path, "node_modules", "my-app")
    make(tmp_path, "AppData", "Local", "my-app")
    make(tmp_path, "my-venv", "env", "my-app")
    wanted = make(tmp_path, "code", "my-app")
    make(wanted, "my-app")                       # a copy inside the match is not offered too
    assert finder.find_candidates("my-app", tmp_path) == [wanted]


def test_nothing_under_a_different_name_and_an_empty_name_find_nothing(tmp_path):
    make(tmp_path, "code", "other")
    assert finder.find_candidates("my-app", tmp_path) == [] and finder.find_candidates("", tmp_path) == [] and finder.find_candidates("   ", tmp_path) == []


def test_the_search_is_bounded_in_depth_in_folders_looked_at_and_in_time(tmp_path):
    make(tmp_path, "a", "b", "c", "d", "e", "f", "my-app")                 # six levels down
    assert finder.find_candidates("my-app", tmp_path, max_depth=5) == []
    assert finder.find_candidates("my-app", tmp_path, max_depth=6) != []
    near = make(tmp_path, "z-late", "my-app")
    for index in range(5):
        make(tmp_path, f"a-folder-{index}")
    assert finder.find_candidates("my-app", tmp_path, max_directories=2) == []          # gave up before reaching it
    assert finder.find_candidates("my-app", tmp_path)[-1] == near
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0, 99.0])
    assert finder.find_candidates("my-app", tmp_path, seconds=1.0, clock=lambda: next(ticks)) == []   # out of time after the first folder


def test_at_most_a_handful_of_candidates_are_returned(tmp_path):
    for index in range(9):
        make(tmp_path, f"group{index}", "my-app")
    assert len(finder.find_candidates("my-app", tmp_path)) == finder.MAX_CANDIDATES


def test_a_link_or_junction_is_never_followed(tmp_path):
    class Entry:
        def __init__(self, symlink=False, junction=False, attributes=0):
            self._s, self._j, self._a = symlink, junction, attributes

        def is_symlink(self):
            return self._s

        def is_junction(self):
            return self._j

        def stat(self, follow_symlinks=True):
            return type("S", (), {"st_file_attributes": self._a})()

    assert finder._is_link(Entry(symlink=True)) and finder._is_link(Entry(junction=True)) and finder._is_link(Entry(attributes=0x400))
    assert not finder._is_link(Entry())
    real = make(tmp_path, "real", "my-app")
    try:
        (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this account cannot create links")
    assert finder.find_candidates("my-app", tmp_path) == [real]            # found once, through the real folder, not again through the link


def test_a_folder_flagged_as_a_link_is_neither_matched_nor_searched_inside(tmp_path, monkeypatch):
    # the same rule as the real-symlink test, for accounts that cannot make links (Windows without developer mode)
    inside = make(tmp_path, "linked-out", "my-app")
    kept = make(tmp_path, "code", "my-app")
    real_is_link = finder._is_link
    monkeypatch.setattr(finder, "_is_link", lambda entry: entry.name == "linked-out" or real_is_link(entry))
    assert finder.find_candidates("my-app", tmp_path) == [kept] and inside.exists()


def test_a_folder_that_cannot_be_a_project_is_not_offered(tmp_path, monkeypatch):
    make(tmp_path, "code", "my-app")
    monkeypatch.setattr(finder.projects, "unsuitable", lambda folder: "no")
    assert finder.find_candidates("my-app", tmp_path) == []


def test_one_candidate_is_offered_and_of_several_only_a_lone_git_clone(tmp_path):
    one = make(tmp_path, "a", "my-app")
    two = make(tmp_path, "b", "my-app")
    assert finder.likely_one([]) is None
    assert finder.likely_one([one]) == one
    assert finder.likely_one([one, two]) is None                       # the person chooses between them
    make(two, ".git")
    assert finder.likely_one([one, two]) == two
    make(one, ".git")
    assert finder.likely_one([one, two]) is None                       # both are clones


# ---- the dialog ---------------------------------------------------------------------------------------------------------------------------------

class Answers:
    """A stand-in for the window toolkit, with the question about a found folder."""

    def __init__(self, found=True, confirm=(True,), folder="", fail=False):
        self.found_answer, self.confirms, self.folder_answer, self.fail = found, list(confirm), folder, fail
        self.asked, self.closed = [], False

    def use_found(self, title, text):
        if self.fail:
            raise RuntimeError("no display")
        self.asked.append(text)
        return self.found_answer

    def confirm(self, title, text):
        self.asked.append(text)
        return self.confirms.pop(0) if self.confirms else False

    def folder(self, title):
        self.asked.append(title)
        return self.folder_answer

    def close(self):
        self.closed = True


def test_one_found_folder_is_a_single_yes_that_shows_the_path_and_adds_it(tmp_path):
    found = make(tmp_path, "code", "my-app")
    ask = Answers(found=True)
    assert picker.run_dialog("my-app", ask, find=lambda name: [found]) == {"result": "chosen", "path": str(found)}
    assert len(ask.asked) == 1 and str(found) in ask.asked[0] and "after you press Run in SAAPP" in ask.asked[0] and ask.closed is True


def test_cancel_on_a_found_folder_adds_nothing_and_no_goes_to_the_chooser(tmp_path):
    found = make(tmp_path, "code", "my-app")
    other = make(tmp_path, "elsewhere", "my-app")
    assert picker.run_dialog("my-app", Answers(found=None), find=lambda name: [found]) == {"result": "declined"}
    chooser = Answers(found=False, folder=str(other))
    assert picker.run_dialog("my-app", chooser, find=lambda name: [found]) == {"result": "chosen", "path": str(other)}
    assert len(chooser.asked) == 2 and "Choose the folder" in chooser.asked[1]            # no second "choose a folder now?" after already answering about one
    assert picker.run_dialog("my-app", Answers(found=False, folder=""), find=lambda name: [found]) == {"result": "declined"}
    renamed = Answers(found=False, confirm=(False,), folder=str(tmp_path / "other-name"))
    assert picker.run_dialog("my-app", renamed, find=lambda name: [found]) == {"result": "declined"}      # the old name check still applies to what they choose


def test_when_nothing_or_nothing_certain_is_found_the_old_dialog_runs_unchanged(tmp_path):
    a, b = make(tmp_path, "a", "my-app"), make(tmp_path, "b", "my-app")
    for find in (lambda name: [], lambda name: [a, b]):
        ask = Answers(confirm=(True,), folder=str(a))
        assert picker.run_dialog("my-app", ask, find=find) == {"result": "chosen", "path": str(a)}
        assert "Choose the folder now?" in ask.asked[0] and len(ask.asked) == 2       # the old two questions, nothing about a found folder

    def broken(name):
        raise OSError("the disk went away")
    assert picker.run_dialog("my-app", Answers(confirm=(True,), folder=str(a)), find=broken)["result"] == "chosen"


def test_a_dialog_given_a_fake_toolkit_never_searches_unless_asked_to(tmp_path, monkeypatch):
    def forbidden(name):
        raise AssertionError("searched the real home folder")
    monkeypatch.setattr(picker.finder_module, "find_candidates", forbidden)
    assert picker.run_dialog("my-app", Answers(confirm=(False,)))["result"] == "declined"


def test_the_real_dialog_searches_by_default_and_a_toolkit_that_fails_is_no_picker(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "tkinter", None)          # no window toolkit here: the import fails
    assert picker.run_dialog("my-app") == {"result": "no_picker"}
    source = Path(picker.__file__).read_text(encoding="utf-8")
    assert "if find is None and ask is None:\n        find = finder_module.find_candidates" in source        # the real dialog (no fake toolkit) looks for the folder
    assert picker.run_dialog("my-app", Answers(found=False, fail=True), find=lambda name: [Path("/x/my-app")])["result"] == "no_picker"
