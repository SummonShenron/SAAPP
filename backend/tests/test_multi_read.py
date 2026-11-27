"""Several places in one read_repo_file call (backend/utils/multi_read.py, `ranges`), and the counters and read log that follow reads (reading_waste.py, read_log.py)."""
from backend.services import agent_workflow as aw
from backend.services import native_tools
from backend.tests.test_local_edits import _connect_folder, _drive_turn, run_async
from backend.utils import multi_read as mr
from backend.utils import read_log as rl
from backend.utils import self_counters
from backend.utils.reading_waste import ReadingLog, counts_from

NUMBERED = "\n".join(f"line {i}" for i in range(1, 301))
OTHER = "\n".join(f"other {i}" for i in range(1, 51))
BIG = "x = 1\n" * 1000          # 6000 characters: bigger than a read from the top may show


def _fetch(files):
    calls = []

    def fetch(path):
        calls.append(path)
        return (files[path], None) if path in files else (None, f"ERROR: {path} not found")

    return fetch, calls


def _read(path, ranges, files=None, cap=3500):
    spans, problems = mr.parse(path, ranges)
    fetch, calls = _fetch(files or {"a.py": NUMBERED, "b.py": OTHER, "big.py": BIG})
    return mr.render(spans, problems, fetch, cap), calls


# ---- reading ----------------------------------------------------------------------------------------------------------------------------------

def test_several_ranges_of_one_file_and_another_file_come_back_in_one_result_each_file_fetched_once():
    text, calls = _read("a.py", ["10-12", "200-201", "b.py:5-6", "40-41"])
    assert "== a.py lines 10-12 of 300 ==\nline 10\nline 11\nline 12" in text
    assert "== a.py lines 40-41 of 300 ==" in text and "== a.py lines 200-201 of 300 ==" in text
    assert "== b.py lines 5-6 of 50 ==\nother 5\nother 6" in text
    assert calls == ["a.py", "b.py"]
    assert text.index("a.py lines 10") < text.index("a.py lines 40") < text.index("a.py lines 200") < text.index("b.py lines 5")


def test_overlapping_and_touching_ranges_are_merged():
    assert mr.parse("a.py", ["10-20", "15-30", "31-35", "50-60"])[0] == [("a.py", 10, 35), ("a.py", 50, 60)]


def test_a_single_line_reads_around_it_and_an_open_range_reads_a_default_span():
    assert mr.parse("a.py", ["100"])[0] == [("a.py", 80, 120)]
    assert mr.parse("a.py", ["5"])[0] == [("a.py", 1, 25)]
    assert mr.parse("a.py", ["100-"])[0] == [("a.py", 100, 100 + mr.DEFAULT_SPAN - 1)]


def test_a_range_past_the_end_is_cut_and_one_that_starts_past_it_says_so():
    text, _ = _read("b.py", ["45-60", "70-80"])
    assert "== b.py lines 45-50 of 50 ==" in text and "only has 50 lines, so line 70" in text
    just_past, _ = _read("b.py", ["51-55"])
    assert "only has 50 lines, so line 51 is past the end" in just_past


def test_what_was_left_is_named_within_the_file():
    long = "\n".join(f"line {i}" for i in range(1, 601))
    text, _ = _read("a.py", ["1-390", "395-900"], {"a.py": long}, cap=10 ** 6)
    assert "a.py:405-600" in text


def test_bad_ranges_are_named_and_the_good_ones_still_read():
    text, _ = _read("a.py", ["10-11", "twelve", "30-20"])
    assert "line 10" in text and "'twelve' is not a range" in text and "'30-20' ends before it starts" in text


def test_a_missing_file_is_an_error_for_that_file_only():
    text, _ = _read("a.py", ["1-2", "nope.py:1-5"])
    assert "line 1" in text and "ERROR: nope.py not found" in text


def test_at_most_six_ranges():
    spans, problems = mr.parse("a.py", [f"{i * 20}-{i * 20 + 1}" for i in range(1, 10)])
    assert len(spans) == mr.MAX_RANGES and any("only the first 6" in p for p in problems)


def test_the_line_limit_stops_the_read_and_names_what_was_left():
    long = "\n".join(f"line {i}" for i in range(1, 601))
    text, _ = _read("a.py", ["1-350", "b.py:1-50", "400-460"], {"a.py": long, "b.py": OTHER}, cap=10 ** 6)
    assert sum(1 for line in text.splitlines() if line.startswith(("line ", "other "))) == mr.MAX_TOTAL_LINES
    assert "== a.py lines 400-449 of 600 ==" in text and "not read (the 400-line" in text and "a.py:450-460" in text and "b.py:1-50" in text


def test_the_character_limit_stops_the_read_and_names_what_was_left():
    wide = "\n".join("y" * 200 for _ in range(300))
    text, _ = _read("w.py", ["1-300"], {"w.py": wide}, cap=10 ** 6)
    body = [line for line in text.splitlines() if line.startswith("y")]
    assert len(body) * 201 <= mr.MAX_CHARS + 201 and len(body) < 300 and f"w.py:{len(body) + 1}-300" in text


def test_the_top_of_a_big_file_is_not_a_way_around_the_top_read_rule():
    text, _ = _read("big.py", ["1-300", "500-505"])
    assert "NOT READ: this is the top of a big file" in text and "== big.py lines 500-505 of 1000 ==" in text
    small, _ = _read("a.py", ["1-300"], cap=10 ** 6)
    assert "line 1\n" in small                                 # a small file can be read from the top


def test_a_short_window_at_the_top_of_a_big_file_is_allowed():
    text, _ = _read("big.py", ["1-20"])
    assert "== big.py lines 1-20 of 1000 ==" in text


# ---- through the dispatcher -------------------------------------------------------------------------------------------------------------------

@run_async
async def test_the_tool_reads_ranges_of_the_connected_folder(monkeypatch):
    _connect_folder()
    observed, _, _ = await _drive_turn(monkeypatch, [{"tool_action": "read_repo_file", "args": {"path": "src/app.py", "ranges": ["3-4", "src/data.json:1"]}}])
    assert "== src/app.py lines 3-4 of 7 ==\ndef greet(name):\n    return 'hi ' + name" in observed[0]
    assert '== src/data.json lines 1-1 of 1 ==\n{"a": 1}' in observed[0]


def test_the_schema_and_the_menu_offer_ranges_and_the_loop_says_to_use_them():
    assert native_tools.REGISTRY["read_repo_file"]["properties"]["ranges"] == {"type": "array", "items": {"type": "string"}, "description": native_tools.REGISTRY["read_repo_file"]["properties"]["ranges"]["description"]}
    assert "ranges" not in native_tools.REGISTRY["read_repo_file"]["required"]
    import inspect
    assert "ranges (optional: a list such as" in inspect.getsource(aw.tool_agent_node)
    from backend.services import native_loop
    assert "in ONE read_repo_file call with `ranges`" in inspect.getsource(native_loop)


# ---- a plain window read is cut to whole lines, and says which line it stopped at (2026-11-27) ---------------------------------------------------

def test_a_window_is_fitted_to_whole_lines_within_the_line_and_character_limits():
    assert mr.fit(["a", "b"]) == ["a", "b"] and mr.fit([]) == []
    assert len(mr.fit(["x"] * (mr.MAX_TOTAL_LINES + 50))) == mr.MAX_TOTAL_LINES
    wide = ["y" * 999] * 100                                  # 1,000 characters a line with its newline
    kept = mr.fit(wide)
    assert len(kept) == mr.MAX_CHARS // 1000 and all(line == "y" * 999 for line in kept)      # whole lines only, never a cut one
    assert mr.fit(["z" * (mr.MAX_CHARS * 2), "next"]) == ["z" * (mr.MAX_CHARS * 2)]            # one huge line is still shown: a read is never empty


@run_async
async def test_a_window_read_names_the_last_line_it_really_showed(monkeypatch):
    import backend.services.local_workspace as lw
    wide = "\n".join(f"row {i} " + "w" * 190 for i in range(1, 501))            # 500 lines of about 200 characters: 300 of them are more than one read may show
    lw.apply_sync("jack", "local-rag", [{"path": "src/wide.py", "content": wide}], [], True)
    observed, _, _ = await _drive_turn(monkeypatch, [{"tool_action": "read_repo_file", "args": {"path": "src/wide.py", "start_line": 101, "line_count": 300}},
                                                     {"tool_action": "read_repo_file", "args": {"path": "src/wide.py", "start_line": 101, "line_count": 20}}])
    shown = [line for line in observed[0].splitlines() if line.startswith("row ")]
    last = 100 + len(shown)
    assert 0 < len(shown) < 300 and len("\n".join(shown)) <= mr.MAX_CHARS and shown[-1].startswith(f"row {last} ") and shown[-1].endswith("w" * 190)
    assert f"Lines 101-{last} of 500 total:" in observed[0] and "Lines 101-400" not in observed[0]
    assert f"stopped at line {last}" in observed[0] and f"start_line={last + 1}" in observed[0]
    assert "Lines 101-120 of 500 total:" in observed[1] and "stopped at line" not in observed[1] and "380 more lines below" in observed[1]      # a read that fits is as it was


def test_the_read_log_keeps_the_lines_that_were_shown_not_the_lines_asked_for():
    cut = "URL: u\nLines 101-216 of 500 total:\nrow 101\n... [stopped at line 216: ...]"
    assert rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 101, "line_count": 300}, cut)["range"] == "101-216"
    assert rl.entry_for("read_repo_file", {"path": "a.py", "start_line": 101}, cut)["range"] == "101-216"                     # no count given: the default window, as shown
    several = "== a.py lines 40-60 of 300 ==\nx\n\n== b.py lines 5-7 of 50 ==\ny\n== c.py lines 1-9 of 9 ==\nNOTE: not read (...): b.py:8-9."
    entry = rl.entry_for("read_repo_file", {"path": "a.py", "ranges": ["40-60", "b.py:5-9"]}, several)
    assert entry["range"] == "40-60; b.py:5-7"                # b.py was cut at 7, and a header-shaped line naming a file nobody asked for (c.py: it is inside a file's text) is not taken


# ---- counting and the read log ----------------------------------------------------------------------------------------------------------------

def test_a_multi_range_read_counts_every_file_once_and_as_one_window_read():
    log = ReadingLog()
    log.note("read_repo_file", {"path": "a.py", "ranges": ["1-5", "b.py:2-3", "40-50"]}, "text")
    summary = log.summary()
    assert summary["files_read"] == 2 and summary["rereads"] == 0 and summary["window_reads"] == 1 and summary["top_reads"] == 0 and summary["multi_reads"] == 1
    assert counts_from(summary)["reading_multi_reads"] == 1 and "reading_multi_reads" in self_counters.READING_KEYS
    log.note("read_repo_file", {"path": "a.py", "start_line": 3, "line_count": 10}, "text")
    assert log.summary()["multi_reads"] == 1 and log.summary()["rereads"] == 1


def test_a_failed_multi_range_read_counts_nothing():
    log = ReadingLog()
    log.note("read_repo_file", {"path": "a.py", "ranges": ["1-5"]}, "ERROR: a.py not found")
    assert log.summary()["files_read"] == 0 and log.summary()["multi_reads"] == 0


def test_the_read_log_keeps_each_range_with_its_file():
    entry = rl.entry_for("read_repo_file", {"path": "a.py", "ranges": ["40-60", "b.py:5-9"]}, "text")
    assert entry["target"] == "a.py" and entry["range"] == "40-60; b.py:5-9"
