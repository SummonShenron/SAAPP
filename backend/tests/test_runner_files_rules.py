"""The rules for which files may be sent to SAAPP exist in THREE places: the browser's folder upload (local/src/localWorkspace.ts), the server (backend/services/local_workspace.py), and the runner
(runner/src/saapp_runner/filerules.py). They must say the same thing, or a secret filtered in one place could be sent by another. This pins them equal and checks the runner never sends what the server
would refuse (docs/runner-files-plan.md)."""
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "runner" / "src"))

from saapp_runner import filerules as runner_rules  # noqa: E402

from backend.services import local_workspace as server  # noqa: E402

TS = (ROOT / "local" / "src" / "localWorkspace.ts").read_text(encoding="utf-8")


def ts_set(name: str) -> set:
    """The quoted strings of `const NAME = new Set([...]);` in the browser's file, comments left out."""
    match = re.search(rf"const {name} = new Set\(\[(.*?)\]\);", TS, re.S)
    assert match, f"{name} was not found in localWorkspace.ts"
    body = re.sub(r"//[^\n]*", "", match.group(1))
    return set(re.findall(r"'([^']+)'", body))


def ts_number(name: str) -> int:
    match = re.search(rf"const {name} = ([0-9_]+);", TS)
    assert match, name
    return int(match.group(1).replace("_", ""))


def ts_list(name: str) -> tuple:
    match = re.search(rf"const {name} = \[(.*?)\];", TS, re.S)
    assert match, name
    return tuple(re.findall(r"'([^']+)'", match.group(1)))


def test_the_skipped_folders_are_the_same_in_the_browser_the_server_and_the_runner():
    assert ts_set("SKIP_DIRS") == set(runner_rules.SKIP_DIRS) == set(server._SKIPPED_DIR_NAMES)


def test_the_text_types_secrets_and_lockfiles_are_the_same_in_the_browser_and_the_runner():
    for name in ("TEXT_EXTENSIONS", "TEXT_FILENAMES", "SKIPPED_FILENAMES", "SECRET_FILENAMES"):
        assert ts_set(name) == set(getattr(runner_rules, name)), name
    assert ts_list("SECRET_SUFFIXES") == runner_rules.SECRET_SUFFIXES
    assert ts_list("ENV_TEMPLATE_SUFFIXES") == runner_rules.ENV_TEMPLATE_SUFFIXES


def test_the_limits_are_the_same_in_the_browser_the_server_and_the_runner():
    assert ts_number("MAX_FILE_BYTES") == runner_rules.MAX_FILE_BYTES == server.MAX_FILE_BYTES
    assert ts_number("MAX_TOTAL_BYTES") == runner_rules.MAX_TOTAL_BYTES == server.MAX_TOTAL_BYTES
    assert ts_number("MAX_FILES") == runner_rules.MAX_FILES == server.MAX_FILE_COUNT
    assert ts_number("MAX_DOCX_BYTES") == runner_rules.MAX_DOCX_BYTES
    assert ts_number("BATCH_MAX_BYTES") == runner_rules.BATCH_MAX_BYTES and ts_number("BATCH_MAX_FILES") == runner_rules.BATCH_MAX_FILES


NAMES = [
    "app.py", "App.TSX", "README.md", "Dockerfile", "Makefile", ".gitignore", "notes.txt", "data.csv", "report.docx", "sheet.xlsx", "sheet.xlsm", "config.example", "settings.env.sample", "x.env.template",
    ".env", ".env.local", ".env.production", ".env.json", ".env.production.yaml", "prod.env", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".npmrc", ".pypirc", ".netrc", "credentials.json", "secrets.json", "secrets.yaml", "secrets.yml",
    "secrets.toml", "server.pem", "server.key", "cert.p12", "cert.pfx", "store.keystore", "SERVER.PEM", "package-lock.json", "yarn.lock", "app.min.js", "app.js.map", "~$open.docx", "logo.png", "noextension",
]


@pytest.mark.parametrize("name", NAMES)
def test_a_name_the_runner_would_send_is_never_one_the_server_refuses(name):
    if runner_rules.is_syncable_file_name(name):
        assert server._rejection_reason(f"src/{name}") is None, f"the runner would send {name} but the server refuses it"


@pytest.mark.parametrize("name", NAMES)
def test_a_name_that_looks_like_a_secret_to_the_server_is_never_sent_by_the_runner(name):
    if server._rejection_reason(f"src/{name}") == "looks like a secret/credential file":
        assert not runner_rules.is_syncable_file_name(name), f"the server calls {name} a secret but the runner would send it"


def test_the_runner_skips_a_folder_the_server_would_refuse_and_the_server_never_sees_a_skipped_one(tmp_path):
    from saapp_runner import workspace_sync

    for folder in runner_rules.SKIP_DIRS:
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "inside.py").write_text("x = 1\n")
        assert server._rejection_reason(f"{folder}/inside.py") == "in a skipped directory"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.py").write_text("x = 1\n")
    assert [e.path for e in workspace_sync.scan(tmp_path).entries] == ["src/ok.py"]
