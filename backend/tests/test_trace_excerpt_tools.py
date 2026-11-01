"""Trace excerpts for web, library and memory steps. The inputs here have the shapes the tools REALLY return (checked against the code, 2026-11-01): a web search reaches the loop as one line of JSON, a library
search prints "[n] (file, library, page)" above each passage, the file list is "- name (library x, n passages)", and a memory search is a header, group titles and numbered lines (or one "Nothing about that" line)."""
import json

from backend.services import tool_agent_memory as tam
from backend.utils.agent_utils import TRACE_EXCERPT_MAX_CHARS, build_tool_excerpt


def test_a_fetched_page_is_a_read_excerpt_labelled_with_its_address_and_capped_like_a_file_read():
    body = "Welcome to the page. " * 200
    excerpt = build_tool_excerpt("read_url", {"url": "https://example.com/a"}, body)
    assert excerpt["kind"] == "read" and excerpt["path"] == "https://example.com/a"
    assert excerpt["text"] == body[:TRACE_EXCERPT_MAX_CHARS] and excerpt["truncated"] is True


def test_a_page_excerpt_is_redacted_like_any_other_read():
    excerpt = build_tool_excerpt("read_url", {"url": "https://example.com"}, "set API_KEY=abcd1234efgh5678ijkl\nhello")
    assert "abcd1234efgh5678ijkl" not in excerpt["text"] and "hello" in excerpt["text"]


def test_a_web_search_shows_title_and_address_for_the_first_five_results_from_the_real_json_shape():
    rows = [{"title": f"Result {n}", "snippet": f"a long snippet {n}", "link": f"https://example.com/{n}"} for n in range(1, 9)]
    excerpt = build_tool_excerpt("web_search", {"query": "x"}, json.dumps(rows))
    assert excerpt["kind"] == "list" and excerpt["tool"] == "web_search"
    assert excerpt["lines"][0] == "Result 1 - https://example.com/1" and len(excerpt["lines"]) == 5
    assert excerpt["total_lines"] == 8 and excerpt["truncated"] is True
    assert "snippet" not in repr(excerpt)


def test_a_web_search_that_found_nothing_or_is_not_json_gives_no_excerpt():
    assert build_tool_excerpt("web_search", {"query": "x"}, "[]") is None
    assert build_tool_excerpt("web_search", {"query": "x"}, "not json") is None
    assert build_tool_excerpt("web_search", {"query": "x"}, json.dumps({"error": "down"})) is None


def test_a_library_search_shows_where_the_hits_are_and_not_the_passages():
    result = ("[1] (week-3.md, library weekly, page 2)\nthe passage text about the budget\n\n[2] (week-4.md, library weekly)\nanother passage\n\n"
              "This search returned passages from 2 file(s): week-3.md, week-4.md. Nothing is known here about any other file or period.")
    excerpt = build_tool_excerpt("search_library", {"query": "budget"}, result)
    assert excerpt["lines"] == ["[1] (week-3.md, library weekly, page 2)", "[2] (week-4.md, library weekly)"] and excerpt["truncated"] is False
    assert "passage text" not in repr(excerpt)


def test_the_library_file_list_shows_the_file_lines_and_a_read_shows_the_start_of_the_file():
    listing = "2 file(s) in the person's libraries (pass a name exactly as written to read_library_file):\n- week-1.md (library weekly, 3 passages)\n- notes.txt (library own, 1 passage)"
    excerpt = build_tool_excerpt("list_library_files", {}, listing)
    assert excerpt["lines"] == ["- week-1.md (library weekly, 3 passages)", "- notes.txt (library own, 1 passage)"]
    read = build_tool_excerpt("read_library_file", {"name": "notes.txt"}, "first line of notes")
    assert read == {"kind": "read", "path": "notes.txt", "text": "first line of notes", "truncated": False}


def test_the_memory_excerpt_counts_the_numbered_items_and_holds_no_remembered_text():
    result = (tam.SEARCH_HEADER + "\n\nSaved facts:\n1. User prefers SECRET_MEMORY_TEXT_XYZ\n2. User uses Postgres\n\nFrom earlier conversations:\n1. They planned a trip")
    excerpt = build_tool_excerpt("search_memory", {"query": "usual setup"}, result)
    assert excerpt == {"kind": "count", "tool": "search_memory", "count": 3}                 # the header and the group titles are not items
    assert "SECRET_MEMORY_TEXT_XYZ" not in repr(excerpt)


def test_a_memory_search_that_found_nothing_gives_no_excerpt_not_a_count_of_one():
    assert build_tool_excerpt("search_memory", {"query": "x"}, tam.NO_RESULT) is None


def test_an_error_or_an_unknown_tool_gives_no_excerpt():
    assert build_tool_excerpt("some_unknown_tool", {"query": "x"}, "some result") is None
    assert build_tool_excerpt("read_url", {"url": "x"}, "ERROR: fetch failed") is None
