"""The strict second look at proposed fact links (backend/utils/fact_links_backfill.py)."""
from backend.tests.test_fact_links_backfill import STORE, fact
from backend.utils import fact_links as fl
from backend.utils import fact_links_backfill as bf


def test_the_second_look_keeps_only_a_clear_yes_and_drops_a_model_that_fails_or_is_unsure():
    facts = [fact("a", "Works at Principal.", 1, "career"), fact("b", "Is on the Principal redesign.", 2), fact("c", "Feels anxious at night.", 3, "trait")]
    proposals = [{"new": "b", "other": "a", "thing": "the job"}, {"new": "c", "other": "b", "thing": "coping"}, {"new": "c", "other": "a", "thing": "x"}, {"new": "zz", "other": "a", "thing": "gone"}]

    def invoke(prompt):
        if "anxious" in prompt and "redesign" in prompt:
            return '{"same": false}'
        if "anxious" in prompt:
            raise RuntimeError("model down")
        return '{"same": true}'

    result = bf.verify_links(facts, proposals, invoke)
    assert [(p["new"], p["other"]) for p in result["kept"]] == [("b", "a")]
    assert len(result["dropped"]) == 3


def test_an_unclear_answer_is_not_a_yes():
    facts = [fact("a", "Works at Principal.", 1), fact("b", "The redesign.", 2)]
    item = [{"new": "b", "other": "a", "thing": "t"}]
    for raw in ('{"same": "true"}', "yes", "{}", '{"same": null}', '```json\n{"same": false}\n```'):
        assert bf.verify_links(facts, item, lambda prompt, raw=raw: raw)["kept"] == []
    assert bf.verify_links(facts, item, lambda prompt: '```json\n{"same": true}\n```')["kept"] == item


def test_the_prompt_refuses_themes_and_feelings_pulled_into_other_topics_and_says_when_unsure_say_no():
    for rule in ("broad theme", "feelings must never be pulled into a discussion of something unrelated", "If you are not sure, answer no", "addiction"):
        assert rule in fl.VERIFY_PROMPT


def test_the_report_lists_what_the_second_look_dropped_apart_from_what_is_kept():
    text = bf.report(STORE, [{"new": "b", "other": "a", "thing": "the job"}], [{"new": "c", "other": "a", "thing": "theme"}])
    assert text.index("# Proposed fact links (1)") < text.index("# Dropped by the second look (1)") and "theme" in text.split("# Dropped")[1]
