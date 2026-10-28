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


def test_a_folder_name_is_reduced_to_plain_characters_and_cannot_carry_a_command():
    text = scripts(folder_name='my app"; rm -rf ~; "$(id)`x`')["linux"]["text"]
    line = next(l for l in text.splitlines() if l.startswith("EXPECTED="))
    assert line == 'EXPECTED="my app rm -rf ~ idx"'.replace("~", "") or "$" not in line and "`" not in line and ";" not in line and line.count('"') == 2
    assert scripts(folder_name="")["linux"]["text"].count('EXPECTED=""') == 1


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
    assert '"$VENV/bin/python" -m pip install "$INSTALL_URL"' in bash and 'HOME_DIR="$HOME/.saapp-runner"' in bash
    assert '"%VENV%\\Scripts\\python.exe" -m pip install "%INSTALL_URL%"' in cmd and "%USERPROFILE%\\.saapp-runner" in cmd


def test_each_project_is_paired_with_its_own_root_then_set_up_then_started():
    for text in (scripts()["mac"]["text"], scripts()["windows"]["text"]):
        assert text.index("pair --server") < text.index(" init --root") < text.index(" start --root") and text.count("--root") == 3


def test_the_commands_for_typing_are_the_same_setup_with_the_folder_left_to_the_person():
    lines = rs.terminal_commands(SERVER, CODE, INSTALL)
    assert lines[0] == f'pipx install "{INSTALL}"' and lines[1] == f"saapp-runner pair --server {SERVER} --root <your project folder> {CODE}"
    assert lines[2:] == ["saapp-runner init --root <your project folder>", "saapp-runner start --root <your project folder>"]


def test_a_percent_sign_in_an_address_is_doubled_for_the_windows_script_only():
    made = scripts(install_url="https://x.example.com/a%20b.zip#subdirectory=runner")
    assert "a%%20b.zip" in made["windows"]["text"] and "a%20b.zip" in made["mac"]["text"]
