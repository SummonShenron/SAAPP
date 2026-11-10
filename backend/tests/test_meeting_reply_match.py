import unittest
from datetime import datetime, timezone

from backend.utils import meetings as mt

AGENDA = ["Get back to enterprise AI sales", "Review the job search and interview prep", "Plan the family Trello board"]
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)


class Msg:
    def __init__(self, kind, content):
        self.type = kind
        self.content = content


class MatchAskedItemTests(unittest.TestCase):
    def test_matches_the_item_the_question_was_about(self):
        asked = "How is the job search going, and what has been getting in the way of interview prep?"
        self.assertEqual(mt.match_asked_item(AGENDA, [], asked), 1)

    def test_skips_covered_items(self):
        asked = "How is the job search going, and what has been getting in the way of interview prep?"
        self.assertIsNone(mt.match_asked_item(AGENDA, [1], asked))

    def test_one_shared_word_is_not_enough(self):
        self.assertIsNone(mt.match_asked_item(AGENDA, [], "Anything new this week?"))

    def test_empty_agenda_matches_nothing(self):
        self.assertIsNone(mt.match_asked_item([], [], "job search interview prep"))


class RecordReplyTests(unittest.TestCase):
    def test_no_reply_records_nothing(self):
        self.assertIsNone(mt.record_reply_from_history("u", "s", [Msg("ai", "job search?")], "   ", db=object()))

    def test_no_ai_question_records_nothing(self):
        self.assertIsNone(mt.record_reply_from_history("u", "s", [Msg("human", "hi")], "yes", db=object()))


class CoveredPromptTests(unittest.TestCase):
    def test_covered_items_are_listed_as_already_covered(self):
        meeting = {"name": "Check-in", "agenda": AGENDA}
        text = mt.build_meeting_text(meeting, [], {"goals": [], "task_findings": [], "progress_notes": []}, "America/Chicago", NOW, [1])
        self.assertIn("ALREADY COVERED", text)
        self.assertIn("2. Review the job search", text)

    def test_context_passes_covered_through(self):
        meeting = {"name": "Check-in", "agenda": AGENDA}
        text = mt.build_meeting_context(meeting, [], {"goals": [], "task_findings": [], "progress_notes": []}, "America/Chicago", NOW, covered=[0])
        self.assertIn("1. Get back to enterprise AI sales", text)
        self.assertIn("ALREADY COVERED", text)


if __name__ == "__main__":
    unittest.main()
