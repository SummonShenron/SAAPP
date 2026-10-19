import json

import pytest

from local_function_app import json_chunks as jc

WEEK = {
    "week_start": "2026-09-28",
    "week_end": "2026-10-04",
    "generated_at": "2026-10-02T20:28:04.756194+00:00",
    "standup_entries": [
        {"id": "2465a070-66f6-4e2d-8e59-dde93a74f83a", "author": "jack harper", "date": "2026-09-28", "yesterday": "",
         "today": "- discussing lambda schema changes\n- updating the python parser pipeline", "blockers": "- schema disagreements\n- waiting for reference table access",
         "created_at": "2026-09-28T15:17:19.881192+00:00"},
        {"id": "b7f47cd8-cdec-4937-9a2b-29a573fbb752", "author": "jack harper", "date": "2026-09-29", "yesterday": "", "today": "- frontend practice", "blockers": "",
         "created_at": "2026-09-29T17:32:43.741905+00:00"},
    ],
    "wins": [],
    "notes": [{"id": "801198dc-7bc9-451b-8a65-3f1798775fda", "kind": "term", "term": "NSK", "body": "Next Start Kit", "answer": "", "resolved": False}],
    "skills": [], "upcoming": [], "contacts": [],
}


def chunks_of(data, name="data.json"):
    return jc.chunk_texts(data, name)


def texts_of(data, name="data.json"):
    return [text for text, _ in chunks_of(data, name)]


# ---------------------------------------------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------------------------------------------

def test_a_json_document_and_one_with_a_byte_order_mark_both_read():
    assert jc.parse(b'{"a": 1}') == {"a": 1}
    assert jc.parse(b'\xef\xbb\xbf{"a": 1}') == {"a": 1}


def test_json_lines_become_a_list_of_records():
    assert jc.parse(b'{"a": 1}\n{"a": 2}\n\n{"a": 3}\n') == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_nothing_usable_is_a_clear_error_that_does_not_echo_the_file():
    for raw in (b"", b"   \n  ", b"\xff\xfe\x00bad"):
        with pytest.raises(jc.JsonIngestError):
            jc.parse(raw)
    with pytest.raises(jc.JsonIngestError) as caught:
        jc.parse(b'{"secret_note": "do not repeat this", oops}')
    assert "do not repeat this" not in str(caught.value) and "not valid JSON" in str(caught.value) and "line 1" in str(caught.value)


def test_a_file_over_the_size_limit_is_refused_before_it_is_read(monkeypatch):
    monkeypatch.setattr(jc, "MAX_BYTES", 100)
    with pytest.raises(jc.JsonIngestError) as caught:
        jc.parse(b"[" + b"1," * 200 + b"1]")
    assert "limit" in str(caught.value)


# ---------------------------------------------------------------------------------------------------------------
# A file with record lists (the weekly export)
# ---------------------------------------------------------------------------------------------------------------

def test_each_record_of_each_list_is_a_chunk_headed_with_the_file_and_its_own_top_level_values():
    chunks = chunks_of(WEEK, "week-2026-09-28.json")
    assert [path for _, path in chunks] == ["standup_entries", "standup_entries", "notes"]
    first = chunks[0][0]
    assert first.startswith("week-2026-09-28.json | week_start: 2026-09-28, week_end: 2026-10-04\nsection: standup_entries (entry 1 of 2)\n")
    assert "author: jack harper" in first and "date: 2026-09-28" in first
    assert "section: notes (entry 1 of 1)" in chunks[2][0] and "term: NSK" in chunks[2][0]


def test_ids_empty_values_empty_lists_and_bookkeeping_are_left_out():
    joined = "\n".join(texts_of(WEEK))
    assert "2465a070" not in joined and "801198dc" not in joined, "uuids carry no meaning for a search"
    assert "generated_at" not in joined and "yesterday" not in joined and "answer:" not in joined
    assert "wins" not in joined and "contacts" not in joined


def test_a_value_with_several_lines_keeps_them_as_lines():
    first = texts_of(WEEK)[0]
    assert "today: - discussing lambda schema changes\n- updating the python parser pipeline\nblockers: - schema disagreements" in first


def test_timestamps_lose_the_seconds_fractions_and_zone():
    assert "created_at: 2026-09-28 15:17" in texts_of(WEEK)[0] and "T15:17" not in texts_of(WEEK)[0]


def test_false_and_zero_are_real_values_not_empty_ones():
    text = texts_of({"items": [{"resolved": False, "count": 0, "name": "x"}]})[0]
    assert "resolved: false" in text and "count: 0" in text


# ---------------------------------------------------------------------------------------------------------------
# Other shapes
# ---------------------------------------------------------------------------------------------------------------

def test_a_list_of_objects_is_one_chunk_per_record_under_the_file_name():
    chunks = chunks_of([{"name": "Dana", "role": "pilot"}, {"name": "Sam", "role": "cook"}], "crew.json")
    assert len(chunks) == 2 and chunks[0][0].startswith("crew.json\nsection: items (entry 1 of 2)\nname: Dana")


def test_json_lines_chunk_like_a_list():
    assert len(chunks_of(jc.parse(b'{"a": "one"}\n{"a": "two"}'))) == 2


def test_an_object_with_no_lists_is_key_value_lines_with_dotted_paths():
    text = texts_of({"server": {"port": 8080, "tls": {"enabled": True}}, "tags": ["a", "b"], "name": "svc"}, "cfg.json")[0]
    assert text.startswith("cfg.json\n")
    for line in ("server.port: 8080", "server.tls.enabled: true", "tags: a, b", "name: svc"):
        assert line in text


def test_lists_nested_deeper_inside_an_object_are_still_found():
    chunks = chunks_of({"data": {"users": [{"name": "Dana"}, {"name": "Sam"}]}, "owner": "jack"}, "x.json")
    assert [path for _, path in chunks] == ["data.users", "data.users"] and "owner: jack" in chunks[0][0]


def test_a_list_inside_a_record_is_written_inline_with_numbered_keys():
    text = texts_of([{"order": "A1", "lines": [{"sku": "x", "qty": 2}, {"sku": "y", "qty": 1}]}])[0]
    assert "lines[1].sku: x" in text and "lines[2].qty: 1" in text


def test_a_bare_value_or_a_list_of_plain_values_is_one_chunk():
    assert "value: hello" in texts_of("hello")[0]
    assert "values: 1, 2, 3" in texts_of([1, 2, 3])[0]


def test_a_mixed_list_does_not_lose_its_plain_values():
    text = "\n".join(texts_of([{"a": "xyz"}, "loose"]))
    assert "xyz" in text and "loose" in text


def test_a_file_with_nothing_in_it_is_refused():
    for data in ({}, [], {"a": "", "b": [], "c": None}, [{"id": "2465a070-66f6-4e2d-8e59-dde93a74f83a"}]):
        with pytest.raises(jc.JsonIngestError):
            chunks_of(data)


# ---------------------------------------------------------------------------------------------------------------
# Masking
# ---------------------------------------------------------------------------------------------------------------

def test_values_of_keys_named_like_secrets_are_masked():
    data = {"password": "hunter2hunter2", "apiKey": "abcdefgh12345", "client_secret": "xxxxxxxxxx", "accessToken": "tok_12345678", "auth_token": "zzzzzzzzzz", "name": "svc"}
    text = texts_of(data)[0]
    for secret in ("hunter2hunter2", "abcdefgh12345", "xxxxxxxxxx", "tok_12345678", "zzzzzzzzzz"):
        assert secret not in text
    assert text.count("[redacted]") == 5 and "name: svc" in text


def test_ordinary_keys_that_only_contain_those_letters_are_not_masked():
    text = texts_of({"author": "jack harper", "token_count": 120, "tokens": "a few words here", "authority": "city council"})[0]
    assert "author: jack harper" in text and "token_count: 120" in text and "authority: city council" in text


def test_known_key_formats_are_masked_wherever_they_appear_in_a_string():
    text = texts_of({"note": "use AKIAABCDEFGHIJKLMNOP and ghp_abcdefghijklmnopqrstuvwx and sk-abcdefghijklmnopqrstuv then eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc123def456"})[0]
    for secret in ("AKIAABCDEFGHIJKLMNOP", "ghp_abcdefghijklmnopqrstuvwx", "sk-abcdefghijklmnopqrstuv", "eyJhbGciOiJIUzI1NiJ9"):
        assert secret not in text
    assert text.count("[redacted]") == 4


def test_a_private_key_block_is_masked():
    text = texts_of({"cert": "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"})[0]
    assert "MIIEowIBAAKCAQEA" not in text and "[redacted]" in text


# ---------------------------------------------------------------------------------------------------------------
# Size
# ---------------------------------------------------------------------------------------------------------------

def test_every_chunk_fits_under_the_splitters_limit():
    big = {"items": [{"name": f"n{i}", "notes": "word " * 400} for i in range(5)]}
    for text, _ in chunks_of(big):
        assert len(text) <= 1000


def test_a_long_record_is_split_and_every_piece_keeps_the_header_and_says_which_part_it_is():
    record = {"title": "report", **{f"field{i}": "value " * 30 for i in range(20)}}
    pieces = texts_of({"owner": "jack", "rows": [record]}, "big.json")
    assert len(pieces) > 1
    for number, piece in enumerate(pieces, 1):
        assert piece.startswith("big.json | owner: jack\nsection: rows (entry 1 of 1, part ") and f"part {number} of {len(pieces)})" in piece


def test_one_very_long_value_is_cut_at_spaces_and_continued():
    pieces = texts_of([{"body": "alpha beta gamma " * 200}])
    assert len(pieces) > 1 and "body (continued): " in "\n".join(pieces)


def test_many_small_records_are_packed_together_to_keep_the_cost_sensible():
    data = [{"n": f"record number {i}"} for i in range(jc.PACK_ABOVE_RECORDS + 100)]
    chunks = chunks_of(data)
    assert len(chunks) < len(data) // 5 and all(len(text) <= 1000 for text, _ in chunks)
    assert "entries 1 to " in chunks[0][0] and f"of {len(data)})" in chunks[0][0]
    joined = "\n".join(text for text, _ in chunks)
    assert "record number 0" in joined and f"record number {len(data) - 1}" in joined, "packing must not lose records"


def test_too_many_chunks_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(jc, "MAX_CHUNKS", 3)
    with pytest.raises(jc.JsonIngestError) as caught:
        chunks_of([{"a": i} for i in range(10)])
    assert "Split it into smaller files" in str(caught.value)


def test_a_deeply_nested_file_does_not_blow_the_stack():
    data = current = {}
    for _ in range(500):
        current["x"] = {}
        current = current["x"]
    current["leaf"] = "bottom"
    chunks_of({"a": 1, "nest": data})      # must finish without a RecursionError


# ---------------------------------------------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------------------------------------------

class Doc:
    def __init__(self, page_content, metadata):
        self.page_content, self.metadata = page_content, metadata


def test_documents_carry_what_a_pdf_page_carries_plus_their_section_and_upload_marker():
    docs = jc.build_documents(json.dumps(WEEK).encode(), "week.json", "personal_jack", Doc, "abc123")
    assert [d.metadata["page"] for d in docs] == [1, 2, 3]
    assert docs[0].metadata == {"affiliate": "personal_jack", "source": "week.json", "page": 1, "doc_type": "json", "json_section": "standup_entries", "ingest_id": "abc123"}
    assert docs[2].metadata["json_section"] == "notes"
