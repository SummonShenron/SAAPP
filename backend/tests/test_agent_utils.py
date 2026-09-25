from backend.utils.agent_utils import (
    parse_definition_index_from_observation,
    find_mismatched_start_line_note,
)


TRUNCATED_OBSERVATION = (
    "URL: https://github.com/SummonShenron/SAAPP/blob/main/backend/services/agent_workflow.py\n"
    "from __future__ import annotations\nimport ast\n...\n\n"
    "... [truncated — this file has 4849 lines total, too long to show in full. "
    "Top-level definitions found in it:\n"
    "  line 183: def _build_definition_index\n"
    "  line 3306: def tool_agent_node\n"
    "Call read_repo_file again with start_line set to the one you actually need — do not "
    "assume the file's contents past this point from general knowledge of what a file like "
    "this usually contains.]"
)


def test_parse_definition_index_from_observation_extracts_real_line_numbers():
    index = parse_definition_index_from_observation(TRUNCATED_OBSERVATION)
    assert index == {"_build_definition_index": 183, "tool_agent_node": 3306}


def test_parse_definition_index_from_observation_empty_for_non_truncated_read():
    plain = "URL: https://github.com/x/y/blob/main/foo.py\ndef foo():\n    return 1\n"
    assert parse_definition_index_from_observation(plain) == {}


def test_parse_definition_index_from_observation_empty_for_windowed_read():
    windowed = (
        "URL: https://github.com/x/y/blob/main/foo.py\nLines 400-599 of 4849 total:\n"
        "def is_valid_pending_pr(pending_action):\n    ...\n"
        "\n... [523 more lines below — re-call with a higher start_line to keep reading]"
    )
    assert parse_definition_index_from_observation(windowed) == {}


def test_find_mismatched_start_line_note_flags_a_real_production_shape():
    """Reproduces the exact real trace: purpose names 'tool_agent_node', an earlier index
    placed it at line 3306, but the chosen start_line=400/window=200 doesn't reach it."""
    index = {"_build_definition_index": 183, "tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        400, 200, 150, index,
    )
    assert note is not None
    assert "tool_agent_node" in note
    assert "line 3306" in note
    assert "start_line=3306" in note


def test_find_mismatched_start_line_note_negative_when_window_actually_reaches_it():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        3300, 150, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_purpose_names_no_indexed_symbol():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read the top of the file.", "backend/services/agent_workflow.py",
        1, 150, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_no_index_for_this_path():
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        400, 200, 150, {},
    )
    assert note is None


def test_find_mismatched_start_line_note_negative_when_no_start_line_given():
    index = {"tool_agent_node": 3306}
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        None, None, 150, index,
    )
    assert note is None


def test_find_mismatched_start_line_note_uses_default_window_when_line_count_missing():
    index = {"tool_agent_node": 3306}
    # default_window=150, start_line=3200 -> covers lines 3200-3349, which includes 3306
    note = find_mismatched_start_line_note(
        "Read tool_agent_node implementation.", "backend/services/agent_workflow.py",
        3200, None, 150, index,
    )
    assert note is None
