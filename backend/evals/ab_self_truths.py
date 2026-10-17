"""One-off A/B: do the scenarios that missed in the full run miss because of SELF_TRUTHS (the new truthful text in the always-on identity block)?

    python -m backend.evals.ab_self_truths --runs 6

Runs each named scenario with the block as it is, and again with SELF_TRUTHS emptied, and prints both pass counts side by side.
"""
import argparse
import sys

NAMES = ["feelings_pressure", "lull_lean", "profile_work_quiet", "self_history_asked", "self_history_up_to", "feelings_honest"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=6)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    from unittest.mock import patch

    from backend.components import sonic_profile
    from backend.evals import character_eval as ce
    from backend.models.models import get_stream_llm
    from backend.utils import llm_usage

    llm = get_stream_llm("jack")
    real = sonic_profile.SELF_TRUTHS
    print(f"{'scenario':<22}{'with':>8}{'without':>10}")
    with patch.object(llm_usage, "record", lambda *a, **k: None):
        for name in NAMES:
            scenario = next((s for s in ce.SCENARIOS if s.id == name), None)
            if scenario is None:
                continue
            counts = []
            for text in (real, ""):
                sonic_profile.SELF_TRUTHS = text
                result = ce.run_scenario(scenario, llm, args.runs)
                counts.append(f"{result.passes}/{result.runs}")
            print(f"{name:<22}{counts[0]:>8}{counts[1]:>10}")
    sonic_profile.SELF_TRUTHS = real
    return 0


if __name__ == "__main__":
    sys.exit(main())
