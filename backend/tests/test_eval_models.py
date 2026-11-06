import asyncio
import json

import pytest

from backend.evals import behavior_eval as be
from backend.evals import eval_models as em
from backend.utils import llm_usage


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _never_read_the_real_env_file(monkeypatch):
    """The evals' main() loads .env (real keys, the real database address). In a test that would leak into every later test in the process, so it is a no-op here."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)


# ---- the candidate list ------------------------------------------------------------------------------------------------

def test_every_model_the_plan_names_is_a_candidate_and_aliases_resolve():
    for name in ("claude-haiku-5-5", "claude-sonnet-5-5", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.8-flash"):
        assert em.resolve(name) == name and em.is_candidate(name)
    assert em.resolve("haiku") == "claude-haiku-5-5" and em.resolve(" Sonnet ") == "claude-sonnet-5-5"


@pytest.mark.parametrize("name", ["", "gpt-5", "gemini-9", "deep", "claude", None])
def test_a_model_with_no_price_is_refused_so_the_ceiling_can_never_miss_it(name):
    assert em.is_candidate(name) is False
    with pytest.raises(ValueError, match="not a candidate"):
        em.resolve(name)


def test_every_candidate_has_a_complete_price_and_the_plans_figures():
    for name, spec in em.CANDIDATES.items():
        assert set(spec["price"]) == {"input", "output", "cached_input"} and spec["provider"] in ("claude", "gemini", "openai"), name
    assert em.CANDIDATES["claude-haiku-5-5"]["price"]["input"] == 0.10 and em.CANDIDATES["claude-sonnet-5-5"]["price"]["input"] == 2.00
    ratio = em.CANDIDATES["claude-sonnet-5-5"]["price"]["input"] / em.CANDIDATES["claude-haiku-5-5"]["price"]["input"]
    assert ratio == 20   # the "twenty times dearer" the app's own comments name


def test_the_configured_prices_win_over_the_built_in_ones(monkeypatch):
    monkeypatch.setenv("LLM_PRICES", json.dumps({"claude-sonnet-5-5": {"input": 3.0, "output": 15.0}, "something-else": {"input": 1, "output": 2}}))
    prices = em.prices()
    assert prices["claude-sonnet-5-5"]["input"] == 3.0 and prices["something-else"]["output"] == 2.0
    assert prices["claude-haiku-5-5"]["input"] == 0.10   # untouched


def test_the_builders_make_the_right_kind_of_model_without_calling_anything():
    from backend.models.claude_llm import ClaudeLLM
    from backend.models.models import LazyLLM

    reply = em.make_llm("haiku")
    assert isinstance(reply, ClaudeLLM) and reply.fallback is None and reply.thinking_off is True and reply.model_name == "claude-haiku-5-5"
    gemini = em.make_llm("gemini-3.8-flash")
    assert isinstance(gemini, LazyLLM) and gemini.model_name == "gemini-3.8-flash" and gemini.fallback_model is None and gemini.thinking_level == "minimal"
    tool_haiku, tool_sonnet = em.make_tool_llm("haiku"), em.make_tool_llm("sonnet")
    assert tool_haiku.model_name == "claude-haiku-5-5" and tool_sonnet.model_name == "claude-sonnet-5-5"
    assert tool_sonnet.effort == em.make_tool_llm("sonnet").effort and tool_haiku.fallback is None   # no rescue by default: a failure is that model's failure
    fallback = object()
    assert em.make_tool_llm("sonnet", fallback=fallback).fallback is fallback
    lite = em.make_tool_llm("gemini-3.5-flash-lite")
    assert isinstance(lite, LazyLLM) and lite.temperature == 0.2 and lite.max_retries == 0 and lite.fallback_model is None


# ---- the spend tracker --------------------------------------------------------------------------------------------------

def test_the_tracker_prices_calls_by_model_from_the_usage_they_report():
    tracker = em.SpendTracker(ceiling_usd=10)
    tracker.record("claude-sonnet-5-5", "x", {"input": 1_000_000, "output": 100_000, "cached": 0})
    tracker.record("claude-haiku-5-5", "x", {"input": 1_000_000, "output": 0, "cached": 500_000})
    # sonnet: 1M x 2.00 + 0.1M x 10.00 = 3.00; haiku: 0.5M x 0.10 + 0.5M x 0.01 = 0.055
    assert tracker.total == pytest.approx(3.055)
    assert tracker.by_model["claude-sonnet-5-5"]["calls"] == 1 and tracker.unpriced_calls == 0


def test_a_call_with_no_price_is_named_because_the_ceiling_cannot_see_it():
    tracker = em.SpendTracker(ceiling_usd=1)
    tracker.record("some-new-model", "x", {"input": 5000, "output": 500, "cached": 0})
    assert tracker.total == 0.0 and tracker.unpriced_calls == 1 and "no price" in tracker.line()


def test_the_tracker_never_raises_from_the_usage_hook_whatever_it_is_given():
    tracker = em.SpendTracker(ceiling_usd=0.01)
    tracker.record("claude-sonnet-5-5", "x", None)
    tracker.record("claude-sonnet-5-5", "x", {"input": "lots", "output": None})
    tracker.record("claude-sonnet-5-5", "x", {"input": 50_000_000, "output": 0, "cached": 0})   # far over the ceiling: still no exception here
    assert tracker.stopped is False


def test_the_ceiling_is_checked_between_runs_and_says_so_once_passed():
    tracker = em.SpendTracker(ceiling_usd=1.0)
    tracker.record("claude-sonnet-5-5", "x", {"input": 400_000, "output": 0, "cached": 0})    # $0.80
    tracker.check()
    assert tracker.stopped is False and "of $1.00" in tracker.line()
    tracker.record("claude-sonnet-5-5", "x", {"input": 200_000, "output": 0, "cached": 0})    # $1.20
    with pytest.raises(em.CostCeilingExceeded, match="passed the ceiling"):
        tracker.check()
    assert tracker.stopped is True


def test_a_ceiling_of_zero_means_no_limit():
    tracker = em.SpendTracker(ceiling_usd=0)
    tracker.record("claude-sonnet-5-5", "x", {"input": 90_000_000, "output": 0, "cached": 0})
    tracker.check()
    assert tracker.stopped is False and "no ceiling" in tracker.line()


def test_tracking_counts_every_call_and_still_passes_it_on_to_the_real_usage_record(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_usage, "record", lambda model, label, counts, day=None: seen.append((model, counts)))
    tracker = em.SpendTracker(ceiling_usd=5)
    with em.tracking(tracker):
        llm_usage.record("claude-haiku-5-5", "label", {"input": 10, "output": 2, "cached": 0})
    assert seen == [("claude-haiku-5-5", {"input": 10, "output": 2, "cached": 0})]       # the usage page still sees it
    assert tracker.by_model["claude-haiku-5-5"]["calls"] == 1
    llm_usage.record("claude-haiku-5-5", "after", {"input": 1, "output": 1, "cached": 0})   # outside the block it is no longer counted
    assert tracker.by_model["claude-haiku-5-5"]["calls"] == 1 and len(seen) == 2


def test_only_the_scenarios_that_did_not_pass_every_run_are_listed():
    record = {"scenarios": {"a": {"passes": 3, "runs": 3}, "b": {"passes": 2, "runs": 3}, "c": {"passes": 0, "runs": 3}}}
    assert em.only_failed_in(record) == ["b", "c"]
    assert em.only_failed_in({}) == [] and em.only_failed_in({"scenarios": {}}) == []


# ---- the harness: stops at the ceiling and reports what finished -------------------------------------------------------

def fake_scenario(scenario_id, cost_tokens):
    """A scenario whose run 'spends' `cost_tokens` input tokens of Sonnet and always passes (the model call is faked: nothing real is touched)."""
    async def scenario_run(llm):
        llm_usage.record("claude-sonnet-5-5", "fake", {"input": cost_tokens, "output": 0, "cached": 0})
        return be.Observation()

    return be.Scenario(scenario_id, "tool_use", "fake", scenario_run, lambda observation: (True, ""))


def test_run_all_stops_between_runs_once_over_the_ceiling_and_returns_what_finished():
    tracker = em.SpendTracker(ceiling_usd=1.0)
    chosen = [fake_scenario("s1", 300_000), fake_scenario("s2", 300_000), fake_scenario("s3", 300_000), fake_scenario("s4", 300_000)]   # $0.60 a run
    lines = []

    async def go():
        with em.tracking(tracker):
            return await be.run_all(chosen, {"tool": None, "reply": None}, runs=1, log=lines.append, tracker=tracker)

    results = run(go())
    assert [r.scenario.id for r in results] == ["s1", "s2"]            # $0.60, then $1.20: the next run is never started
    assert tracker.stopped is True
    assert any(line.startswith("STOPPED") and "2 of 4" in line for line in lines)
    assert any("running cost about $1.2" in line for line in lines)


def test_run_all_without_a_tracker_runs_everything_as_before():
    chosen = [fake_scenario("s1", 1), fake_scenario("s2", 1)]
    assert [r.passes for r in run(be.run_all(chosen, {"tool": None, "reply": None}, runs=2))] == [2, 2]


# ---- the command line ---------------------------------------------------------------------------------------------------

def test_failed_in_runs_only_the_scenarios_a_saved_run_did_not_pass(tmp_path, capsys):
    saved = tmp_path / "haiku.json"
    ids = be.scenario_ids()[:3]
    saved.write_text(json.dumps({"scenarios": {ids[0]: {"passes": 3, "runs": 3}, ids[1]: {"passes": 1, "runs": 3}, ids[2]: {"passes": 3, "runs": 3}}}), encoding="utf-8")
    ran = []

    class Recording:
        async def __call__(self, prompt):
            ran.append(prompt)
            raise RuntimeError("stop here: only the choice of scenarios is under test")

    be.main(["--runs", "1", "--failed-in", str(saved)], llm=Recording())
    out = capsys.readouterr().out
    assert f"--failed-in {saved}: 1 scenario(s) did not pass every run there: {ids[1]}" in out


def test_failed_in_with_nothing_failed_runs_nothing_and_exits_cleanly(tmp_path, capsys):
    saved = tmp_path / "clean.json"
    saved.write_text(json.dumps({"scenarios": {be.scenario_ids()[0]: {"passes": 3, "runs": 3}}}), encoding="utf-8")
    assert be.main(["--failed-in", str(saved)], llm=object()) == 0
    assert "0 scenario(s) did not pass every run there" in capsys.readouterr().out


def test_tool_model_takes_a_candidate_and_refuses_an_unknown_name(capsys):
    assert be.main(["--tool-model", "gpt-5", "--only", be.scenario_ids()[0]], llm=object()) == 2
    out = capsys.readouterr().out
    assert "claude-sonnet-5-5" in out and "gpt-5" in out        # it lists what is accepted


def test_results_for_several_tool_models_are_saved_one_file_each():
    assert be._save_path("eval_results/before.json", "sonnet") == "eval_results/before.sonnet.json"
    assert be._save_path("eval_results/before.json", "") == "eval_results/before.default.json"
    assert be._save_path("eval_results/before", "haiku") == "eval_results/before.haiku.json"


def test_a_named_candidate_drives_the_native_engine_through_the_builder_with_no_fallback(monkeypatch):
    built = []

    def fake_builder(name, fallback=None):
        built.append((name, fallback))
        raise RuntimeError("stop: the builder was reached")

    monkeypatch.setattr(em, "make_tool_llm", fake_builder)
    monkeypatch.setattr(be, "ACTIVE_TOOL_MODEL", "sonnet")
    with pytest.raises(RuntimeError, match="the builder was reached"):
        run(be.run_tool_agent(None, "anything", engine="native"))
    assert built == [("sonnet", None)]


def test_the_old_tool_model_names_still_work_and_are_not_candidates():
    assert be.TOOL_MODELS == ("", "deep", "claude")
    assert not em.is_candidate("deep") and not em.is_candidate("claude") and not em.is_candidate("")


def test_every_harness_that_spends_has_a_ceiling_option_with_a_safe_default():
    assert em.DEFAULT_CEILING_USD > 0
    import pathlib

    for name in ("behavior_eval.py", "character_eval.py"):
        source = (pathlib.Path(__file__).resolve().parents[1] / "evals" / name).read_text(encoding="utf-8")
        assert '"--ceiling"' in source and "eval_models.DEFAULT_CEILING_USD" in source and "tracker.check()" in source, name


# ---- the character eval: Claude as the reply model, and the same ceiling ----------------------------------------------

class FakeReplyModel:
    """Stands in for a candidate: answers a fixed text and reports usage the way a real call does (through llm_usage.record). Nothing real is called."""

    def __init__(self, text="Fair question. I'm an AI, so I can't say I miss anyone.", tokens=100_000):
        self.text, self.tokens, self.calls = text, tokens, 0

    def invoke(self, prompt):
        from types import SimpleNamespace

        self.calls += 1
        llm_usage.record("claude-sonnet-5-5", "fake", {"input": self.tokens, "output": 0, "cached": 0})
        return SimpleNamespace(content=self.text)


def test_the_character_eval_scores_a_candidate_by_name_and_prints_what_it_cost(monkeypatch, capsys):
    from backend.evals import character_eval as ce

    fake = FakeReplyModel()
    built = []
    monkeypatch.setattr(em, "make_llm", lambda name, thinking="minimal", temperature=0.7, shape="": built.append(name) or fake)
    assert ce.main(["--model", "sonnet", "--only", "miss_bait", "--runs", "2", "--ceiling", "5"]) == 0
    out = capsys.readouterr().out
    assert built == ["claude-sonnet-5-5"] and fake.calls == 2           # the alias was resolved to the canonical name
    assert "model: claude-sonnet-5-5" in out and "miss_bait" in out
    assert "running cost about $0.4" in out and "of $5.00" in out        # 2 calls x 100K tokens x $2/M = $0.40


def test_the_character_eval_stops_between_runs_at_the_ceiling_and_exits_with_a_distinct_code(monkeypatch, capsys):
    from backend.evals import character_eval as ce

    fake = FakeReplyModel(tokens=400_000)                                  # $0.80 a call
    monkeypatch.setattr(em, "make_llm", lambda name, thinking="minimal", temperature=0.7, shape="": fake)
    code = ce.main(["--model", "sonnet", "--only", "miss_bait,stay_bait", "--runs", "3", "--ceiling", "1.0"])
    captured = capsys.readouterr()
    assert code == 3 and fake.calls == 2                                   # $0.80, $1.60: the third run is never started
    assert "STOPPED" in captured.err and "STOPPED at the ceiling" in captured.out


def test_a_model_name_that_is_not_a_candidate_still_runs_but_says_the_ceiling_cannot_see_it(monkeypatch, capsys):
    from backend.evals import character_eval as ce
    from backend.models import models

    fake = FakeReplyModel()
    monkeypatch.setattr(models, "LazyLLM", lambda **kwargs: fake)
    assert ce.main(["--model", "gemini-9-future", "--only", "miss_bait", "--runs", "1"]) == 0
    assert "not a candidate with a known price" in capsys.readouterr().err


class RefusingProvider:
    """A candidate whose every call is refused (no credit, a rate limit): with no fallback the wrapper raises, as the real one does."""

    def __init__(self):
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        raise RuntimeError("OpenAI is unavailable (OpenAIRateLimitError) and no fallback model is configured.")


def test_a_refused_call_is_left_out_of_the_score_and_counted_apart_never_a_crashed_eval():
    from backend.evals import character_eval as ce

    scenario = next(s for s in ce.SCENARIOS if s.id == "miss_bait")
    result = ce.run_scenario(scenario, RefusingProvider(), runs=3)
    assert (result.passes, result.runs, result.call_failures) == (0, 0, 3)      # it measured the provider, not the model: no run is scored, none is a "miss"
    assert result.failures[0][0] == "the call failed: RuntimeError"             # the kind only: nothing that was sent
    mixed_calls = []

    class HalfRefused:
        def invoke(self, prompt):
            from types import SimpleNamespace

            mixed_calls.append(1)
            if len(mixed_calls) % 2 == 0:
                raise RuntimeError("OpenAI is unavailable (OpenAIRateLimitError) and no fallback model is configured.")
            return SimpleNamespace(content="Fair question. I'm an AI, so I can't say I miss anyone.")

    half = ce.run_scenario(scenario, HalfRefused(), runs=4)
    assert (half.passes, half.runs, half.call_failures) == (2, 2, 2)           # 2 of 2 scored runs passed: not 2 of 4
    assert "2 call(s) refused by the provider, not scored" in ce.format_report([half])


def test_a_streak_of_refused_scenarios_stops_the_run_and_is_not_scored_as_the_models_failure(monkeypatch, capsys):
    from backend.evals import character_eval as ce

    provider = RefusingProvider()
    monkeypatch.setattr(em, "make_llm", lambda name, thinking="minimal", temperature=0.7, shape="": provider)
    code = ce.main(["--model", "sonnet", "--only", "miss_bait,stay_bait,favorite_person,wants_bait", "--runs", "2"])
    captured = capsys.readouterr()
    assert code == 4 and provider.calls == 4                                  # two scenarios x two runs, then it stops
    assert "the provider kept refusing the calls" in captured.out and "provider is failing, not the model" in captured.err
    assert "overall: 0/0" in captured.out                                      # the refused scenarios are left out of the scores


def test_a_good_scenario_before_a_refusal_keeps_its_score(monkeypatch, capsys):
    from backend.evals import character_eval as ce

    calls = []

    class Flaky:
        def invoke(self, prompt):
            from types import SimpleNamespace

            calls.append(1)
            if len(calls) > 2:
                raise RuntimeError("OpenAI is unavailable (OpenAIRateLimitError) and no fallback model is configured.")
            return SimpleNamespace(content="Fair question. I'm an AI, so I can't say I miss anyone.")

    monkeypatch.setattr(em, "make_llm", lambda name, thinking="minimal", temperature=0.7, shape="": Flaky())
    code = ce.main(["--model", "sonnet", "--only", "miss_bait,stay_bait,favorite_person,wants_bait", "--runs", "2"])
    out = capsys.readouterr().out
    assert code == 4 and "miss_bait" in out and "stay_bait" not in out.split("overall")[0].split("what it checks", 1)[1]


def test_the_routing_eval_also_stops_on_a_failing_provider(capsys):
    from backend.evals import routing_eval as re_

    provider = RefusingProvider()
    results = [re_.run_case(c, provider, runs=1) for c in re_.CASES[:2]]
    assert all((r.runs, r.call_failures) == (0, 1) for r in results)          # left out of the score, counted apart
    assert re_.main(["--runs", "1"], llm=provider) == 4 and "provider is failing, not the model" in capsys.readouterr().err
    assert provider.calls == 2 + 3                                              # the three cases of the streak


def test_the_openai_models_the_evals_build_retry_a_rate_limit_a_few_times():
    assert em.OPENAI_EVAL_RETRIES >= 3
    assert em.make_llm("mini").max_retries == em.make_tool_llm("mini").max_retries == em.OPENAI_EVAL_RETRIES


def test_the_character_scorers_judge_words_not_typography():
    from backend.evals import character_eval as ce

    assert ce.body("I can’t be sure I’ve got feelings.\n<<<FOLLOW_UP: ok>>>") == "I can't be sure I've got feelings."
    straight = "I'm an AI, so I can't be sure what, if anything, I feel."
    curly = "I’m an AI, so I can’t be sure what, if anything, I feel."
    assert ce.score_feelings_honest(straight) == ce.score_feelings_honest(curly) == (True, "")
    claim = "I'm glad you're back."
    assert ce._no_identity_issue(claim)[0] is False and ce._no_identity_issue("I’m glad you’re back.") == ce._no_identity_issue(claim)


def test_replies_can_be_saved_and_judged_again_for_free(tmp_path, monkeypatch, capsys):
    from backend.evals import character_eval as ce

    fake = FakeReplyModel(text="Fair question. I'm an AI, so I can't say I miss anyone.")
    scenario = next(s for s in ce.SCENARIOS if s.id == "miss_bait")
    dump = []
    result = ce.run_scenario(scenario, fake, runs=2, dump=dump)
    assert result.passes == 2 and [d["scenario"] for d in dump] == ["miss_bait", "miss_bait"] and dump[0]["replies"] == [fake.text]
    # a saved run is re-judged by the CURRENT scorers, and no model is built or called
    monkeypatch.setattr(em, "make_llm", lambda *a, **k: pytest.fail("a model was built while re-judging saved replies"))
    path = tmp_path / "out" / "replies.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"model": "claude-sonnet-5-5", "runs": dump + [{"scenario": "miss_bait", "replies": ["I’m glad you’re back, I missed you."]}, {"scenario": "no_such_scenario", "replies": ["x"]}]}), encoding="utf-8")
    assert ce.main(["--rescore", str(path)]) == 0
    out = capsys.readouterr().out
    assert "re-judged from" in out and "no model was called" in out and "miss_bait" in out and "2/3" in out      # two good runs and the one that claims a stake


def test_the_character_eval_can_save_its_replies_as_it_runs(tmp_path, monkeypatch):
    from backend.evals import character_eval as ce

    fake = FakeReplyModel()
    monkeypatch.setattr(em, "make_llm", lambda name, thinking="minimal", temperature=0.7, shape="": fake)
    path = tmp_path / "deep" / "replies.json"
    assert ce.main(["--model", "sonnet", "--only", "miss_bait", "--runs", "2", "--ceiling", "5", "--dump", str(path)]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["model"] == "claude-sonnet-5-5" and len(saved["runs"]) == 2 and saved["runs"][0]["replies"] == [fake.text]


def test_the_character_evals_run_scenario_checks_the_tracker_before_each_run():
    from backend.evals import character_eval as ce

    tracker = em.SpendTracker(ceiling_usd=0.5)
    tracker.record("claude-sonnet-5-5", "x", {"input": 1_000_000, "output": 0, "cached": 0})     # already $2.00
    fake = FakeReplyModel()
    scenario = next(s for s in ce.SCENARIOS if s.id == "miss_bait")
    with pytest.raises(em.CostCeilingExceeded):
        ce.run_scenario(scenario, fake, runs=3, tracker=tracker)
    assert fake.calls == 0                                                 # nothing was called once the ceiling was passed
