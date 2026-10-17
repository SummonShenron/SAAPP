"""Does the model that links facts link the right ones and leave the rest alone?  (backend/utils/fact_links.py)

Each case is a new fact, a short list of saved facts it might belong with, which of them it really belongs with, and which extra links are
defensible and so not held against it. A wrong link puts unrelated context into a reply, so precision matters at least as much as recall.

    python -m backend.evals.fact_links_eval --runs 3

It makes real model calls (small ones: a case is about 300 tokens) and records nothing in the usage meter.
"""
import argparse
import sys
from dataclasses import dataclass, field
from typing import List, Set

POOL = {
    "A": "Works at Principal.",
    "B": "Is a software engineer.",
    "C": "Prefers dark mode UI.",
    "D": "Has a dog named Max.",
    "E": "Has a sister named Priya who lives in Denver.",
    "F": "Is training for a marathon in April.",
    "G": "Is learning Spanish for a trip to Mexico City in March.",
    "H": "Lives in Des Moines, Iowa.",
    "I": "Is working on a side project, a recipe app.",
    "J": "Has a coworker named Dana who leads the design team.",
    "K": "Drinks oat milk lattes.",
    "L": "Has a sore left knee.",
}


@dataclass
class Case:
    id: str
    new_fact: str
    candidates: str                       # keys of POOL, in the order shown to the model
    expected: str = ""                    # keys that must be linked
    acceptable: str = ""                  # keys that may be linked without penalty
    notes: str = ""
    _expected: Set[str] = field(default_factory=set, repr=False)


CASES: List[Case] = [
    Case("employer_project", "Is working on the PAG UI redesign at work.", "ABCDI", expected="A", notes="the project is at the employer, though it never says Principal"),
    Case("trip_to_sisters_city", "Is planning a trip to Denver in November to visit family.", "EFGHD", expected="E"),
    Case("training_and_knee", "Is slowing down marathon training because of a sore left knee.", "FLDK", expected="FL"),
    Case("pet", "Max has an appointment at the vet on Friday.", "DEAK", expected="D"),
    Case("general_preference", "Prefers tabs over spaces.", "BIAC", notes="a general coding preference belongs with nothing"),
    Case("coworker", "Dana asked the user to present the redesign to leadership.", "JABI", expected="J", acceptable="A"),
    Case("project_with_coworker_present", "Is working on the PAG UI redesign at work.", "AJI", expected="A", acceptable="J"),
    Case("food_preference", "Likes spicy food.", "KDAF", notes="a taste belongs with nothing here"),
    Case("work_location", "Works from the Des Moines office three days a week.", "AHBJ", expected="A", acceptable="HJ"),
    Case("class_and_trip", "Started a Spanish class on Tuesdays.", "GKFH", expected="G"),
    Case("half_marathon", "Is thinking about running a half marathon before the big one.", "FLIG", expected="F", acceptable="L"),
    Case("side_project_not_job", "Is building a recipe app with React Native.", "IBAC", expected="I", notes="an engineer fact is general, not the same thing"),
    Case("sister_calls", "Gets coffee with Priya every Sunday over video.", "EKDJ", expected="E"),
    Case("diet", "Is a vegetarian.", "KDAE", acceptable="K", notes="dietary: the lattes are defensible, the dog and the job are not"),
    Case("employer_news", "Principal is rolling out a new design system this quarter.", "AIJB", expected="A", acceptable="J"),
    Case("trap_employer_missing", "Is working on the PAG UI redesign at work.", "ICFG", acceptable="C",
         notes="the employer is not among the candidates, and a side project must not be linked to it. A 'dark mode UI' preference is defensible context for a UI project, so it is not held against it"),
]
for _case in CASES:
    _case._expected = set(_case.expected)


IDENTITY = {"B"}          # "a software engineer" is an identity fact in the real store: always shown, so never offered as a candidate


class Fact:
    """Just enough of a saved fact for the judge."""
    def __init__(self, key: str, text: str):
        self.id, self.fact, self.related, self.active = key, text, [], True
        self.category = "identity" if key in IDENTITY else "career"


def judge(case: Case, invoke) -> Set[str]:
    from backend.utils import fact_links

    # the same filter the app applies when it shortlists (fact_links.candidate_facts)
    candidates = [fact for fact in (Fact(key, POOL[key]) for key in case.candidates) if fact.category != "identity"]
    return {fact.id for fact in fact_links.judge_links(case.new_fact, candidates, invoke)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args(argv)

    from dotenv import load_dotenv

    load_dotenv()
    from unittest.mock import patch

    from backend.models.models import lite_llm
    from backend.utils import llm_usage

    expected_total = found_total = made_total = wrong_total = 0
    misses: List[str] = []
    with patch.object(llm_usage, "record", lambda *a, **k: None):
        for case in CASES:
            for _ in range(args.runs):
                linked = judge(case, lite_llm.invoke)
                wrong = linked - case._expected - set(case.acceptable)
                missed = case._expected - linked
                expected_total += len(case._expected)
                found_total += len(case._expected & linked)
                made_total += len(linked - set(case.acceptable))
                wrong_total += len(wrong)
                if wrong or missed:
                    misses.append(f"  [{case.id}] linked {sorted(linked)}; wanted {sorted(case._expected)}" + (f" (also fine: {sorted(case.acceptable)})" if case.acceptable else ""))
    recall = found_total / expected_total if expected_total else 1.0
    precision = (made_total - wrong_total) / made_total if made_total else 1.0
    print(f"cases: {len(CASES)} x {args.runs} runs | recall {found_total}/{expected_total} = {recall:.0%} | precision {made_total - wrong_total}/{made_total} = {precision:.0%}")
    print("misses:" if misses else "no misses")
    seen = set()
    for line in misses:
        if line not in seen:
            seen.add(line)
            print(line)
    return 0 if recall >= 0.9 and precision >= 0.95 else 1


if __name__ == "__main__":
    sys.exit(main())
