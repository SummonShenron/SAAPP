import json
import logging
import pathlib

import pytest

from backend.models import models

PRICED = json.dumps({"gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50}, "gemini-3.8-flash": {"input": 0.75, "output": 3.75}})


def test_with_no_setting_every_job_keeps_the_model_it_has_today(monkeypatch):
    for name in ("CHAT_MODEL", "LITE_MODEL", "REPLY_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert models._job_model("LITE_MODEL", "gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"
    assert models._job_model("REPLY_MODEL", "gemini-3.6-flash") == "gemini-3.6-flash"
    monkeypatch.setenv("LITE_MODEL", "   ")
    assert models._job_model("LITE_MODEL", "gemini-3.1-flash-lite") == "gemini-3.1-flash-lite"      # a blank value is no setting


def test_the_configured_models_of_today_are_unchanged():
    """With nothing set (as in the tests) the module's own models are exactly the ones the plan's table lists."""
    assert models.lite_llm.model_name == "gemini-3.1-flash-lite" and models.lite_llm.fallback_model == "gemini-3.6-flash"
    assert models.stream_llm.model_name == "gemini-3.6-flash" and models.embedded_llm.model_name == "gemini-3.6-flash" and models.llm.model_name == "gemini-3.6-flash"
    assert models.stream_llm.fallback_model == "gemini-3.1-flash-lite" and models.stream_llm.thinking_level == "minimal" and models.llm.thinking_level == "high"


def test_a_priced_override_applies(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", PRICED)
    monkeypatch.setenv("LITE_MODEL", "gemini-3.5-flash-lite")
    assert models._job_model("LITE_MODEL", "gemini-3.1-flash-lite") == "gemini-3.5-flash-lite"


def test_an_unpriced_override_is_ignored_because_the_budget_guard_would_not_see_its_spend(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PRICES", PRICED)
    monkeypatch.setenv("REPLY_MODEL", "gemini-9-future")
    with caplog.at_level(logging.WARNING, logger="SASS Logger"):
        assert models._job_model("REPLY_MODEL", "gemini-3.6-flash") == "gemini-3.6-flash"
    assert "REPLY_MODEL=gemini-9-future is ignored" in caplog.text and "no price in LLM_PRICES" in caplog.text
    monkeypatch.delenv("LLM_PRICES")
    monkeypatch.setenv("REPLY_MODEL", "gemini-3.8-flash")
    assert models._job_model("REPLY_MODEL", "gemini-3.6-flash") == "gemini-3.6-flash"       # no prices at all: nothing can be adopted


def test_an_override_naming_the_default_changes_nothing_and_does_not_need_a_price(monkeypatch):
    monkeypatch.delenv("LLM_PRICES", raising=False)
    monkeypatch.setenv("CHAT_MODEL", "gemini-3.6-flash")
    assert models._job_model("CHAT_MODEL", "gemini-3.6-flash") == "gemini-3.6-flash"


def test_each_job_reads_its_own_setting():
    source = (pathlib.Path(__file__).resolve().parents[1] / "models" / "models.py").read_text(encoding="utf-8")
    for needle in ('model_name=_job_model("CHAT_MODEL", "gemini-3.6-flash")', 'model_name=_job_model("LITE_MODEL", "gemini-3.1-flash-lite")'):
        assert source.count(needle) == 1, needle
    assert source.count('model_name=_job_model("REPLY_MODEL", "gemini-3.6-flash")') == 2      # the reply and the embedded reply


def test_the_warning_never_carries_anything_a_person_wrote():
    source = (pathlib.Path(__file__).resolve().parents[1] / "models" / "models.py").read_text(encoding="utf-8")
    warning = next(line for line in source.splitlines() if "is ignored: that model has no price" in line)
    assert "env_name, wanted, default" in warning        # the setting's name, the model's name and today's model: nothing else
