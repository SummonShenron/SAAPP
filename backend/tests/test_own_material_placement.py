"""Sonic's own material placed by code (backend/utils/placement.py, experiences.spoken, relatable place_mode) and the use of offered material measured (backend/utils/material_use.py).
No model, no database: replies and notes are written here in the shapes the app produces."""
import os

from backend.utils import experiences, llm_turns, material_use, placement, relatable_utils, self_counters

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
NOTE = {"on": "2026-10-27", "kind": "failure", "what": "It hallucinated details about a previous discussion because it failed to verify the context before responding."}


def app_source():
    return open(os.path.join(REPO, "app.py"), encoding="utf-8").read()


# ---- placement -----------------------------------------------------------------------------------------------------------------------------------

def test_words_go_after_the_first_paragraph_and_before_the_follow_up_tag():
    reply = "First part.\n\nSecond part.\n\n<<<FOLLOW_UP: what next?>>>"
    assert placement.after_first_paragraph(reply, "My line.") == "First part.\n\nMy line.\n\nSecond part.\n\n<<<FOLLOW_UP: what next?>>>"
    assert placement.after_first_paragraph("Only one paragraph.", "My line.") == "Only one paragraph.\n\nMy line."


def test_nothing_is_placed_twice_into_an_empty_reply_or_from_empty_words():
    assert placement.after_first_paragraph("Already says My line. here", "My line.") == "Already says My line. here"
    assert placement.after_first_paragraph("", "My line.") == "" and placement.after_first_paragraph("Reply.", "  ") == "Reply."


# ---- a record case, said by Sonic ----------------------------------------------------------------------------------------------------------------

def test_a_record_note_is_said_in_the_first_person_as_a_case_in_its_record():
    said = experiences.spoken(NOTE)
    assert said == ("This is close to a case in my record, from October 2026: I hallucinated details about a previous discussion because I failed to verify the context before responding.")
    assert experiences.cited(said)                                                     # it is what the identity check and the cited count read
    assert experiences.spoken(NOTE, "I check the history first now.").endswith("responding. I check the history first now.")


def test_only_the_agent_it_and_its_become_i_and_my():
    assert experiences.first_person("It kept its answer although it was told the file had moved.") == "I kept my answer although I was told the file had moved."
    assert experiences.first_person("It read the file; it seemed fine.") == "I read the file; it seemed fine."          # "it seemed" is not the agent: left as approved
    assert experiences.first_person("The loop ran out.") == "" and experiences.spoken({"what": "The loop ran out."}) == ""


def test_the_modes_default_to_place_and_can_be_turned_back(monkeypatch):
    monkeypatch.delenv("EXPERIENCE_MODE", raising=False)
    monkeypatch.delenv("RELATABLE_MODE", raising=False)
    assert experiences.place_mode() == "place" and relatable_utils.place_mode() == "place"
    monkeypatch.setenv("EXPERIENCE_MODE", "offer")
    monkeypatch.setenv("RELATABLE_MODE", "offer")
    assert experiences.place_mode() == "offer" and relatable_utils.place_mode() == "offer"
    monkeypatch.setenv("RELATABLE_MODE", "nonsense")
    assert relatable_utils.place_mode() == "place"


def test_the_turn_keeps_the_offered_case_for_placement():
    llm_turns.reset()
    turn = llm_turns.begin("jack")
    llm_turns.note_experience(NOTE, "a reflection")
    assert turn.experience_offered and turn.experience_item == {"item": NOTE, "reflection": "a reflection"}
    llm_turns.reset()


def test_the_reply_places_at_most_one_own_item_in_order_and_counts_it():
    app = app_source()
    block = app[app.index("placed_own = bool(step_back_entry)"):][:2600]
    assert block.index("shape_stories_utils.insert_story") < block.index("experiences_utils.spoken") < block.index('relatable_entry["line"]')
    assert "not placed_own and llm_turn.experience_item and experiences_utils.place_mode() == \"place\" and not safety_plan.active" in block
    assert "not experiences_utils.cited(identity_checks_body(full_response))" in block
    assert "not placed_own and relatable_entry and relatable_place_mode() == \"place\" and not safety_plan.active and not mentions_itself(full_response)" in block
    assert 'tally(turn_counts, "experience_placed")' in block and 'tally(turn_counts, "relatable_placed")' in block
    assert block.index("placed_own = True\n                    tally(turn_counts, \"experience_placed\")") > 0
    assert 'llm_turn.experience_item = {"item": dict(chat_character["experience"]), "reflection": str(chat_character.get("reflection") or "")}' in app
    workflow = open(os.path.join(REPO, "backend", "services", "agent_workflow.py"), encoding="utf-8").read()
    assert 'llm_turns.note_experience(found["experience"], found.get("reflection", ""))' in workflow


# ---- was it used? ------------------------------------------------------------------------------------------------------------------------------------

OBSERVATION = "Over the last 30 days 14 of my code changes were applied without a check run first."


def test_a_reply_that_uses_an_observation_counts_and_one_that_shares_little_does_not():
    assert material_use.used_line("In my record, 14 code changes went in without a check run first, so I'd run it now.", OBSERVATION)
    assert material_use.used_line("Fourteen changes were applied without checks lately.", OBSERVATION)            # three distinctive words
    assert not material_use.used_line("Sure, I can help with that change.", OBSERVATION)
    assert not material_use.used_line("Over the last days I thought about it.", OBSERVATION)                       # only common words shared
    assert material_use.used_any("x", ["", OBSERVATION, None]) is False
    assert material_use.used_any("14 of them were applied", [OBSERVATION])                                         # a number and a word


def test_the_counters_are_known_and_the_reply_counts_a_used_observation():
    assert {"observations_used", "experience_placed", "relatable_placed"} <= self_counters.KEYS
    assert 'if self_knowledge_lines and material_use.used_any(full_response, self_knowledge_lines):\n                tally(turn_counts, "observations_used")' in app_source()


def test_a_shared_number_alone_is_not_use():
    assert not material_use.used_line("I counted 14 apples in the basket.", OBSERVATION)
