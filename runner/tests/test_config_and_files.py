"""Where the key and the checks live, how a command is read, and which files a check may be given (saapp_runner/config.py and files.py)."""
import os

import pytest

from saapp_runner import config as cfg
from saapp_runner import files as fl


# ---- the key -------------------------------------------------------------------------------------------------------------------------------

def test_the_key_is_saved_per_project_read_back_and_kept_private(runner_home, tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    assert cfg.load_credentials(project) is None
    cfg.save_credentials("https://saapp.example.com/", "abc.def", project)
    assert cfg.load_credentials(project) == {"server": "https://saapp.example.com", "key": "abc.def"}
    if os.name != "nt":
        assert oct(cfg.credentials_path().stat().st_mode & 0o777) == "0o600" and oct(runner_home.stat().st_mode & 0o777) == "0o700"


def test_each_project_has_its_own_key_so_pairing_a_second_one_never_replaces_the_first(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    first.mkdir()
    second.mkdir()
    cfg.save_credentials("https://saapp.example.com", "key-one.secret", first)
    cfg.save_credentials("https://saapp.example.com", "key-two.secret", second)
    assert cfg.load_credentials(first)["key"] == "key-one.secret" and cfg.load_credentials(second)["key"] == "key-two.secret"
    cfg.save_credentials("https://saapp.example.com", "key-one-again.secret", first)                # re-pairing one project changes only that one
    assert cfg.load_credentials(first)["key"] == "key-one-again.secret" and cfg.load_credentials(second)["key"] == "key-two.secret"
    assert cfg.load_credentials(tmp_path / "never-paired") is None


def test_a_damaged_key_file_reads_as_not_paired(runner_home, tmp_path):
    runner_home.mkdir(parents=True)
    cfg.credentials_path().write_text("not json", encoding="utf-8")
    assert cfg.load_credentials(tmp_path) is None
    cfg.credentials_path().write_text('{"server": "x", "key": "y"}', encoding="utf-8")             # not the per-project shape
    assert cfg.load_credentials(tmp_path) is None
    cfg.save_credentials("https://saapp.example.com", "a.b", tmp_path)                             # saving over a damaged file starts it fresh
    assert cfg.load_credentials(tmp_path)["key"] == "a.b"


# ---- how a command is read -------------------------------------------------------------------------------------------------------------------

def test_a_command_is_a_list_of_words_or_a_string_split_like_a_shell_would():
    assert cfg.parse_command(["pytest", "-q", "{files}"]) == ["pytest", "-q", "{files}"]
    assert cfg.parse_command('pytest -q "my tests" {files}') == ["pytest", "-q", "my tests", "{files}"]


@pytest.mark.parametrize("bad", [[], [""], "", "   ", 5, None, ["pytest", 3], ["pytest", "{files}", "{files}"], ["pytest", "--path={files}"], ["pytest", "x{files}"], 'pytest "unclosed'])
def test_a_command_that_is_empty_or_misuses_the_files_placeholder_is_refused(bad):
    with pytest.raises(cfg.ConfigError):
        cfg.parse_command(bad)


def test_the_text_shown_on_the_card_quotes_words_with_spaces_and_is_never_run_through_a_shell():
    assert cfg.command_shown(["C:/Program Files/py/python.exe", "-m", "pytest", "{files}"]) == "'C:/Program Files/py/python.exe' -m pytest {files}"


# ---- the checks, kept in the home folder ------------------------------------------------------------------------------------------------------

def test_checks_are_defined_per_project_and_read_back(tmp_path):
    project = tmp_path / "myapp"
    project.mkdir()
    assert cfg.load_checks(project) == {}
    path = cfg.append_project(project, {"tests": ["python", "-m", "pytest", "-q", "{files}"], "types": ["npx", "tsc", "-b"]})
    assert path == cfg.config_path()
    assert cfg.load_checks(project) == {"tests": ["python", "-m", "pytest", "-q", "{files}"], "types": ["npx", "tsc", "-b"]}
    other = tmp_path / "other"
    other.mkdir()
    assert cfg.load_checks(other) == {}                                                        # another project has its own (here: no) checks


def test_a_second_project_is_added_without_touching_what_was_written_by_hand(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    cfg.home().mkdir(parents=True)
    cfg.config_path().write_text("# my notes, keep them\n[projects.\"somewhere\"]\nx = [\"y\"]\n", encoding="utf-8")
    cfg.append_project(first, {"tests": ["pytest"]})
    cfg.append_project(second, {"tests": ["pytest", "-x"]})
    text = cfg.config_path().read_text(encoding="utf-8")
    assert text.startswith("# my notes, keep them") and cfg.load_checks(first) == {"tests": ["pytest"]} and cfg.load_checks(second) == {"tests": ["pytest", "-x"]}


def test_a_project_that_already_has_a_section_is_not_overwritten(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    cfg.append_project(project, {"tests": ["pytest"]})
    with pytest.raises(cfg.ConfigError) as error:
        cfg.append_project(project, {"tests": ["something-else"]})
    assert "already has a section" in str(error.value) and cfg.load_checks(project) == {"tests": ["pytest"]}


def test_a_bad_check_name_or_a_broken_file_is_reported_in_words(tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    with pytest.raises(cfg.ConfigError):
        cfg.append_project(project, {"Bad Name": ["pytest"]})
    cfg.home().mkdir(parents=True, exist_ok=True)
    cfg.config_path().write_text("this is = not [valid", encoding="utf-8")
    with pytest.raises(cfg.ConfigError):
        cfg.load_checks(project)


def test_paths_with_backslashes_and_quotes_survive_the_round_trip(tmp_path):
    project = tmp_path / "my project"
    project.mkdir()
    words = ["C:\\Users\\you\\work\\.venv\\Scripts\\python.exe", "-m", "pytest", "-k", 'a "quoted" name', "{files}"]
    cfg.append_project(project, {"tests": words})
    assert cfg.load_checks(project) == {"tests": words}


# ---- the files a check may be given ----------------------------------------------------------------------------------------------------------

@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "b.py").write_text("y = 2\n", encoding="utf-8")
    (tmp_path / "outside.py").write_text("secret\n", encoding="utf-8")
    return root


def test_existing_files_inside_the_project_are_accepted_as_plain_relative_paths(project):
    assert fl.clean_files(project, ["tests/test_a.py", "./b.py", "tests\\test_a.py"]) == ["tests/test_a.py", "b.py"]
    assert fl.clean_files(project, None) == [] and fl.clean_files(project, []) == []
    assert fl.as_argument("tests/test_a.py") == "./tests/test_a.py"


@pytest.mark.parametrize("bad", ["/etc/passwd", "C:/Windows/win.ini", "../outside.py", "tests/../../outside.py", "a//b.py", "tests/", "-x.py", "--rootdir=/", "tests/test_a.py\x00", "missing.py", "tests"])
def test_a_file_outside_the_project_missing_or_read_as_an_option_is_refused(project, bad):
    with pytest.raises(fl.Refusal):
        fl.clean_files(project, [bad])


def test_a_link_that_points_out_of_the_project_is_refused(project, tmp_path):
    link = project / "escape.py"
    try:
        link.symlink_to(tmp_path / "outside.py")
    except (OSError, NotImplementedError):
        pytest.skip("this system does not allow creating links")
    with pytest.raises(fl.Refusal) as error:
        fl.clean_files(project, ["escape.py"])
    assert "outside the project" in str(error.value)


def test_a_windows_junction_that_points_out_of_the_project_is_refused_too(project, tmp_path):
    # Windows allows a directory junction without special rights, so this proves the same protection where a file link cannot be made.
    if os.name != "nt":
        pytest.skip("directory junctions are a Windows feature")
    import subprocess

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "x.py").write_text("secret\n", encoding="utf-8")
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(project / "escape_dir"), str(elsewhere)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("could not create a junction here")
    with pytest.raises(fl.Refusal) as error:
        fl.clean_files(project, ["escape_dir/x.py"])
    assert "outside the project" in str(error.value)


def test_a_request_with_too_many_files_or_the_wrong_shape_is_refused(project):
    with pytest.raises(fl.Refusal):
        fl.clean_files(project, ["b.py"] * (fl.MAX_FILES + 1))
    with pytest.raises(fl.Refusal):
        fl.clean_files(project, {"b.py": 1})
