import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def runner_home(tmp_path, monkeypatch):
    """Every test gets its own home folder for the runner's key and config, never the real one."""
    home = tmp_path / "runner-home"
    monkeypatch.setenv("SAAPP_RUNNER_HOME", str(home))
    return home
