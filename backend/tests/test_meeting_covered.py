import unittest
from datetime import datetime, timezone

from backend.utils import meetings as mt

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
SPEC = {"name": "Weekly Check-in", "purpose": "Progress.", "agenda": ["Work this week", "Applications sent", "Side project"]}
PREP = {"goals": [], "goal_ages": [], "task_findings": [], "progress_notes": []}


class CoveredIndexesTest(unittest.TestCase):
    def test_none_and_empty(self):
        self.assertEqual(mt.covered_indexes(None), [])
        self.assertEqual(mt.covered_indexes({}), [])

    def test_keeps_order_drops_repeats_and_junk(self):
        run = {"covered": [2, 0, 2, "x", None, "1"]}
        self.assertEqual(mt.covered_indexes(run), [2, 0, 1])


class CoveredPromptTest(unittest.TestCase):
    def test_no_covered_line_when_nothing_covered(self):
        text = mt.build_meeting_text(SPEC, [], PREP, "UTC", NOW)
        self.assertNotIn("ALREADY COVERED", text)

    def test_covered_items_are_listed_by_agenda_number(self):
        text = mt.build_meeting_text(SPEC, [], PREP, "UTC", NOW, covered=[0, 2])
        self.assertIn("ALREADY COVERED in this meeting, do not ask these again: 1. Work this week; 3. Side project", text)

    def test_out_of_range_index_is_ignored(self):
        text = mt.build_meeting_text(SPEC, [], PREP, "UTC", NOW, covered=[7])
        self.assertNotIn("ALREADY COVERED", text)


class MarkCoveredTest(unittest.TestCase):
    def test_marks_only_an_active_run(self):
        class Result:
            def __init__(self, n):
                self.matched_count = n

        class Coll:
            def __init__(self, n):
                self.n = n
                self.calls = []

            def update_one(self, flt, update):
                self.calls.append((flt, update))
                return Result(self.n)

        active = Coll(1)
        self.assertTrue(mt.mark_covered({mt.RUNS_COLLECTION: active}, "jack", "r1", 2))
        self.assertEqual(active.calls[0][1], {"$addToSet": {"covered": 2}})
        self.assertEqual(active.calls[0][0]["status"], mt.ACTIVE)
        self.assertFalse(mt.mark_covered({mt.RUNS_COLLECTION: Coll(0)}, "jack", "r1", 2))


if __name__ == "__main__":
    unittest.main()
