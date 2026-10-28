"""The starter scripts a person downloads to set up the runner (backend/utils/runner_scripts.py). They run on people's machines, so they are checked three ways: what may be put in them, what they say and
do (read as text), and, for real, what they do when run against stand-in commands."""
import pytest

from backend.utils import runner_scripts as rs

SERVER = "https://saapp.example.com"
CODE = "ABCDEFGHJK"
INSTALL = "https://github.com/example/saapp/archive/refs/tags/runner-v0.1.0.zip#subdirectory=runner"


def scripts(**overrides):
    values = {"server_url": SERVER, "code": CODE, "install_url": INSTALL, "folder_name": "myapp", **overrides}
    return rs.starter_scripts(values["server_url"], values["code"], values["install_url"], values["folder_name"])


# ---- what may be put in a script ---------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    'https://x.com"; rm -rf ~ #', "https://x.com/$(whoami)", "https://x.com/`id`", "http://evil.example.com", "ftp://x.com", "x.com", "", "https://", "https://x.com space",
    "https://x.com\nrm -rf /", "https://x.com;ls", "https://x.com|cat", "https://x.com&calc", "https://x.com'", "https://x.com\\", "https://x.com/<script>",
])
def test_a_server_address_that_could_end_a_quote_or_start_a_command_makes_no_script(bad):
    with pytest.raises(ValueError):
        scripts(server_url=bad)


@pytest.mark.parametrize("bad", ["abcdefghjk", "ABCDEFGHJ", "ABCDEFGHJKL", "ABCDEFGHJ1", "ABCDEFGHJ0", "ABCDEFGHIO", "ABC DEFGHJ", "ABCDEFGHJ\n", "ABCDEFGH;K", "", None])
def test_a_pairing_code_not_in_the_form_the_server_makes_makes_no_script(bad):
    with pytest.raises(ValueError):
        scripts(code=bad)


@pytest.mark.parametrize("bad", [
    "http://github.com/x.zip", "https://x.com/a b.zip", 'https://x.com/a".zip', "https://x.com/$(id).zip", "https://x.com/`id`.zip", "https://x.com/a;b.zip", "https://x.com/a|b.zip",
    "https://x.com/a<b.zip", "https://x.com/a>b.zip", "https://x.com/a\\b.zip", "https://x.com/a\nb.zip", "https://x.com/a'b.zip", "https://x.com/a!b.zip", "https://x.com/a%41b.zip", "", None, "x" * 500,
])
def test_an_install_address_that_could_end_a_quote_or_start_a_command_makes_no_script(bad):
    with pytest.raises(ValueError):
        scripts(install_url=bad)


def test_a_folder_name_can_no_longer_reach_a_script_at_all_so_it_cannot_carry_a_command():
    odd = scripts(folder_name='my app"; rm -rf ~; "$(id)`x`')
    plain = scripts(folder_name="")
    for kind in ("linux", "mac", "windows"):
        assert odd[kind]["text"] == plain[kind]["text"] and "EXPECTED" not in odd[kind]["text"] and "rm -rf" not in odd[kind]["text"]        # the name goes to the server (a request for that project), never into a file


def test_the_public_address_is_the_configured_one_or_the_one_the_request_came_to():
    assert rs.public_server_url("https://saapp.onrender.com/", "https", "ignored") == "https://saapp.onrender.com"
    assert rs.public_server_url("", "http", "saapp.onrender.com") == "https://saapp.onrender.com"            # a hosted address is always https, even behind a proxy that says http
    assert rs.public_server_url("", "http", "localhost:8000") == "http://localhost:8000"
    assert rs.public_server_url("", "https", "localhost:8000") == "https://localhost:8000"
    with pytest.raises(ValueError):
        rs.public_server_url("", "https", "bad host/with;stuff")
    with pytest.raises(ValueError):
        rs.public_server_url("http://remote.example.com", "https", "x")


# ---- what they say and do, read as text ---------------------------------------------------------------------------------------------------------

def test_there_is_a_script_for_each_system_with_the_right_name_and_line_endings():
    made = scripts()
    assert {k: v["filename"] for k, v in made.items()} == {"windows": "saapp-runner-setup.cmd", "mac": "saapp-runner-setup.command", "linux": "saapp-runner-setup.sh"}
    assert "\r\n" in made["windows"]["text"] and "\n" not in made["windows"]["text"].replace("\r\n", "")
    assert "\r" not in made["mac"]["text"] and made["mac"]["text"] == made["linux"]["text"]
    assert made["mac"]["text"].startswith("#!/usr/bin/env bash\n") and made["windows"]["text"].startswith("@echo off\r\n")


@pytest.mark.parametrize("kind", ["windows", "mac"])
def test_a_script_carries_the_code_and_addresses_but_never_a_key(kind):
    text = scripts()[kind]["text"]
    assert SERVER in text and CODE in text and INSTALL in text and "0.1.0" in text
    assert "@" not in text.replace("@echo", "").replace("@SERVER@", "") or all(f"@{n}@" not in text for n in ("SERVER", "CODE", "INSTALL", "FOLDER", "VERSION"))      # every placeholder was filled
    for secret_looking in ("secret", "token", "Authorization", "Bearer", "credentials"):
        assert secret_looking not in text, secret_looking


@pytest.mark.parametrize("kind", ["windows", "mac"])
def test_a_script_says_what_it_will_do_and_asks_before_doing_any_of_it(kind):
    text = scripts()[kind]["text"]
    asked = text.index("Continue? [y/N]")
    for first_action in ("-m venv", "pip install", "pair --server", "mkdir"):
        assert text.index(first_action) > asked, f"{first_action} comes before the question"
    assert "Nothing was changed" in text[asked:asked + 400] and "only after you press Run in SAAPP" in text[:asked]


@pytest.mark.parametrize("kind", ["windows", "mac"])
def test_a_script_installs_nothing_system_wide_and_runs_no_downloaded_code_directly(kind):
    text = scripts()[kind]["text"].lower()
    for forbidden in ("sudo", "curl", "wget", "invoke-expression", "iex ", "| sh", "| bash", "eval ", "rm -rf", "del /s", "rmdir /s", "winget", "choco", "brew ", "apt ", "reg add", "setx"):
        assert forbidden not in text, forbidden


def test_the_install_is_the_pinned_release_from_the_given_address_into_a_private_environment_in_the_home_folder():
    bash, cmd = scripts()["mac"]["text"], scripts()["windows"]["text"]
    assert '"$VENV/bin/python" -m pip install --upgrade "$INSTALL_URL"' in bash and 'HOME_DIR="$HOME/.saapp-runner"' in bash
    assert '"%VENV%\\Scripts\\python.exe" -m pip install --upgrade "%INSTALL_URL%"' in cmd and "%USERPROFILE%\\.saapp-runner" in cmd


def test_the_script_pairs_the_computer_sets_up_the_background_runner_and_asks_for_no_folder_or_checks():
    for text in (scripts()["mac"]["text"], scripts()["windows"]["text"]):
        run_lines = [line for line in text.splitlines() if "RUNNER" in line and ("pair --server" in line or "install-service" in line or " start" in line)]
        assert [("pair" if "pair --server" in l else "install-service" if "install-service" in l else "start") for l in run_lines] == ["pair", "install-service", "start"]       # in that order
        assert "--root" not in text and " init " not in text
        assert "Project folder" not in text and "drag the folder" not in text.lower() and "pair this computer with" in text
        assert "window it shows on this computer" in text                      # it says where the project is chosen instead


def test_the_commands_for_typing_are_the_same_setup_with_the_project_folder_left_to_the_person():
    lines = rs.terminal_commands(SERVER, CODE, INSTALL)
    assert lines[0] == f'pipx install --force "{INSTALL}"' and lines[1] == f"saapp-runner pair --server {SERVER} {CODE}"
    assert lines[2:] == ["saapp-runner install-service", "saapp-runner add-project --root <your project folder>"]


def test_a_percent_sign_in_an_address_is_doubled_for_the_windows_script_only():
    made = scripts(install_url="https://x.example.com/a%20b.zip#subdirectory=runner")
    assert "a%%20b.zip" in made["windows"]["text"] and "a%20b.zip" in made["mac"]["text"]


# ---- one version, and an install address built from it ----------------------------------------------------------------------------------------

def _runner_package_version():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[2] / "runner"
    init = (root / "src" / "saapp_runner" / "__init__.py").read_text(encoding="utf-8")
    toml = (root / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', init).group(1), re.search(r'^version = "([^"]+)"', toml, re.MULTILINE).group(1)


def test_the_runner_version_is_the_same_in_the_package_the_project_file_and_what_the_server_asks_for():
    in_package, in_project = _runner_package_version()
    from backend.utils import runner_jobs

    assert in_package == in_project == runner_jobs.WANTED_RUNNER_VERSION == rs.RUNNER_VERSION, "bump all three together: runner/pyproject.toml, saapp_runner/__init__.py and WANTED_RUNNER_VERSION in backend/utils/runner_jobs.py"


def test_the_server_never_asks_for_a_runner_older_than_the_oldest_it_accepts():
    from backend.utils import runner_jobs

    assert runner_jobs.parse_version(rs.RUNNER_VERSION) >= runner_jobs.MIN_RUNNER_VERSION


def test_the_install_address_is_built_from_the_version_so_the_tag_cannot_disagree_with_it(monkeypatch):
    monkeypatch.delenv("SAAPP_RUNNER_REPO", raising=False)
    url = rs.install_url()
    assert url == f"https://github.com/SummonShenron/SAAPP/archive/refs/tags/runner-v{rs.RUNNER_VERSION}.zip#subdirectory=runner" and rs.release_tag() == f"runner-v{rs.RUNNER_VERSION}"
    assert rs.install_address() == (url, "")
    assert url in rs.starter_scripts(SERVER, CODE, url, "myapp")["windows"]["text"] and url in rs.starter_scripts(SERVER, CODE, url, "myapp")["mac"]["text"]


def test_a_fork_points_at_its_own_repository_and_a_bad_setting_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("SAAPP_RUNNER_REPO", "someone/their-fork")
    assert rs.install_url().startswith("https://github.com/someone/their-fork/archive/refs/tags/runner-v")
    for bad in ("no-slash", "a/b/c", "x y/z", "a/b;rm", "../x/y", "a/"):
        monkeypatch.setenv("SAAPP_RUNNER_REPO", bad)
        with pytest.raises(ValueError):
            rs.install_url()
        address, problem = rs.install_address()
        assert address == "" and "owner/name" in problem


def test_an_old_install_address_setting_is_ignored_so_a_stale_one_on_the_server_cannot_serve_an_old_runner(monkeypatch):
    monkeypatch.setenv("SAAPP_RUNNER_INSTALL_URL", "https://github.com/x/y/archive/refs/tags/runner-v0.0.1.zip#subdirectory=runner")
    monkeypatch.delenv("SAAPP_RUNNER_REPO", raising=False)
    assert "runner-v0.0.1" not in rs.install_url() and rs.RUNNER_VERSION in rs.install_url()


def test_typing_the_setup_uses_force_so_it_upgrades_too():
    assert rs.terminal_commands(SERVER, CODE, INSTALL)[0] == f'pipx install --force "{INSTALL}"'
