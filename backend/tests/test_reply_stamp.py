"""The reply stamp (backend/utils/reply_stamp.py): which voice wrote a reply and which voice examples and people notes it was shown, kept on the reply and copied onto feedback, so a thumb can be counted by voice.

Why (Jack, 2026-11-08): he runs a mix of the standard voice and Haiku and rates turns to keep tuning, and a feedback record had no model on it. IDS AND LABELS ONLY: no text. No database is touched."""
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.utils import app_utils, feedback_stats
from backend.utils import reply_stamp as rs
from backend.utils import reply_tier

ROOT = Path(__file__).resolve().parents[2]
STAMP = {"label": "haiku", "tier": "haiku", "reason": "auto_plain", "choice": "auto", "examples": ["big_win_energy"], "notes": ["went_quiet_after_something_vulnerable"]}


def ai(text, stamp=None):
    return AIMessage(content=text, additional_kwargs={rs.KEY: stamp} if stamp else {})


def test_the_stamp_names_the_voice_the_tier_the_reason_the_setting_and_what_it_was_shown_and_nothing_else():
    built = rs.build(reply_tier.HAIKU, "auto_plain", "auto", ["big_win_energy"], ["went_quiet_after_something_vulnerable"])
    assert built == STAMP and set(built) == {"label", "tier", "reason", "choice", "examples", "notes"}      # labels and ids only: no field could hold text
    assert rs.build(reply_tier.STANDARD, "standard_setting", "gemini")["label"] == "flash"
    assert rs.build(reply_tier.STANDARD, "standard_setting", "gpt")["label"] == "gpt"
    assert rs.build(reply_tier.LITE, "lite_setting", "lite")["label"] == "lite"
    assert rs.build(reply_tier.STANDARD, "safety", "haiku")["label"] == "flash"                          # a safety turn is written by the standard voice whatever the setting says


def test_anything_outside_the_fixed_lists_becomes_other_and_ids_are_checked_and_capped():
    odd = rs.build("mystery", "a reason someone invented", "a setting", ["ok_id_here", "Bad Id", "x", 5, "second_id_ok", "third_id_ok"], ["note_id_ok", None])
    assert odd["label"] == "other" and odd["tier"] == "other" and odd["reason"] == "other" and odd["choice"] == "other"
    assert odd["examples"] == ["ok_id_here", "second_id_ok"] and odd["notes"] == ["note_id_ok"]               # bad ids dropped, at most two
    assert rs.build(reply_tier.HAIKU, "auto_plain", "auto", None, None)["examples"] == []
    assert set(reply_tier.REASONS) >= {"auto_plain", "safety", "emotional", "haiku_setting"}


def test_a_stored_stamp_is_made_safe_again_and_a_junk_one_is_dropped():
    assert rs.clean(STAMP) == STAMP
    assert rs.clean({**STAMP, "examples": ["ok_one", "nope!", "ok_two", "ok_three"]})["examples"] == ["ok_one", "ok_two"]
    for junk in (None, "haiku", ["haiku"], {}, {"label": "gemini-3.6-flash"}, {"tier": "haiku"}):
        assert rs.clean(junk) is None, junk


def test_a_reply_keeps_its_stamp_across_a_save_and_a_reload_and_a_junk_stamp_is_not_saved():
    saved = app_utils._serialize_messages([HumanMessage(content="hi"), ai("hello there", STAMP), ai("no stamp here"), ai("junk", {"label": "nonsense"})])
    assert saved[1]["voice"] == STAMP and "voice" not in saved[0] and "voice" not in saved[2] and "voice" not in saved[3]
    loaded = app_utils._deserialize_messages(saved)
    assert loaded[1].additional_kwargs[rs.KEY] == STAMP and rs.KEY not in loaded[0].additional_kwargs and rs.KEY not in loaded[2].additional_kwargs
    assert app_utils._deserialize_messages([{"type": "ai", "content": "x", "voice": {"label": "bad"}}])[0].additional_kwargs.get(rs.KEY) is None


def test_the_rated_reply_is_found_by_its_text_among_the_persons_own_conversations_most_recent_first():
    other = {**STAMP, "label": "flash", "tier": "standard"}
    sessions = {
        "jack::s1": [HumanMessage(content="a"), ai("That is huge! Nice work.", other), HumanMessage(content="b"), ai("That is huge! Nice work.", STAMP)],
        "jack::s2": [ai("Something else entirely", other)],
        "kim::s1": [ai("Kim's private reply", {**STAMP, "label": "lite", "tier": "lite"})],
    }
    assert rs.find(sessions, "jack", "That is huge! Nice work.") == STAMP                                  # the most recent of two identical replies
    assert rs.find(sessions, "jack", "  That is   huge!\nNice work.  <<<FOLLOW_UP: more>>>") == STAMP          # spacing and a follow-up tag do not matter
    assert rs.find(sessions, "jack", "Kim's private reply") is None                                      # another person's conversation is never searched
    assert rs.find(sessions, "kim", "Kim's private reply")["label"] == "lite"
    assert rs.find(sessions, "jack", "A reply that was never sent") is None and rs.find(sessions, "jack", "") is None and rs.find(sessions, "", "x") is None
    assert rs.find({"jack::s1": [ai("old reply without a stamp")]}, "jack", "old reply without a stamp") is None
    assert rs.find({"jack::s1": [HumanMessage(content="That is huge!", additional_kwargs={rs.KEY: STAMP})]}, "jack", "That is huge!") is None          # only a reply, never a message of the person's


def test_a_reply_older_than_the_search_window_is_not_found_so_a_long_thread_costs_a_bounded_scan():
    messages = [ai("the old one", STAMP)] + [ai(f"filler {i}") for i in range(rs.SEARCH_LAST_MESSAGES)]
    assert rs.find({"jack::s1": messages}, "jack", "the old one") is None


class _Collection:
    def __init__(self):
        self.rows = []

    def insert_one(self, doc):
        self.rows.append(doc)


class _DB:
    def __init__(self):
        self.corrections = _Collection()

    def __getitem__(self, name):
        assert name == "corrections"
        return self.corrections


def test_feedback_carries_the_stamp_of_the_rated_reply_and_nothing_when_there_is_none(monkeypatch):
    db = _DB()
    monkeypatch.setattr(app_utils, "get_db", lambda: db)
    monkeypatch.setattr(app_utils, "embed_text", lambda text: [0.1, 0.2])
    app_utils.save_correction("jack", "a question", "a reply", "felt stiff", "tone_off", rating="dislike", voice=STAMP)
    app_utils.save_correction("jack", "a question", "a reply", "", "other", rating="like")
    app_utils.save_correction("jack", "a question", "a reply", "", "other", rating="like", voice={"label": "junk"})
    stamped, plain, junk = db.corrections.rows
    assert stamped["voice"] == STAMP and stamped["rating"] == "negative" and "voice" not in plain and "voice" not in junk
    text_of_the_record = ("a question", "a reply", "felt stiff")
    assert not any(t in str(stamped["voice"]) for t in text_of_the_record)                               # the stamp holds none of the record's text (its `reason` is the routing reason, not the person's)


def test_the_summary_counts_thumbs_by_voice_example_and_note_and_counts_the_unstamped():
    flash = {**STAMP, "label": "flash", "tier": "standard", "examples": [], "notes": []}
    rows = [{"kind": "positive", "voice": STAMP}, {"kind": "positive", "voice": STAMP}, {"kind": "negative", "voice": STAMP}, {"kind": "negative", "voice": flash}, {"kind": "positive", "voice": flash},
            {"kind": "positive", "voice": None}, {"kind": "negative"}, {"kind": "neutral", "voice": STAMP}]
    summary = rs.summarize(rows)
    assert summary["stamped"] == 5 and summary["unstamped"] == 2                                          # the neutral row is not a thumb
    assert summary["labels"] == [{"label": "haiku", "positive": 2, "negative": 1}, {"label": "flash", "positive": 1, "negative": 1}]
    assert summary["examples"] == [{"id": "big_win_energy", "positive": 2, "negative": 1}]
    assert summary["notes"] == [{"id": "went_quiet_after_something_vulnerable", "positive": 2, "negative": 1}]
    assert rs.summarize([]) == {"stamped": 0, "unstamped": 0, "labels": [], "examples": [], "notes": [], "reasons": []}


def test_the_feedback_page_summary_counts_a_persons_thumbs_only_and_asks_for_the_stamp(monkeypatch):
    rows = [
        {"username": "jack", "tag": "other", "rating": "positive", "source": "user", "created_at": "2026-11-08T12:00:00+00:00", "has_embedding": True, "voice": STAMP},
        {"username": "jack", "tag": "tone_off", "rating": "negative", "source": "user", "created_at": "2026-11-08T12:00:00+00:00", "has_embedding": True, "voice": STAMP},
        {"username": "jack", "tag": "hallucination", "rating": "negative", "source": "evaluator", "created_at": "2026-11-08T12:00:00+00:00", "has_embedding": True, "voice": STAMP},
        {"username": "jack", "tag": "other", "rating": "positive", "source": "user", "created_at": "2026-11-01T12:00:00+00:00", "has_embedding": False},
    ]
    summary = feedback_stats.summarize_feedback(rows)
    assert summary["by_voice"]["stamped"] == 2 and summary["by_voice"]["unstamped"] == 1                    # the checker's redo is not a reading of the voice
    assert summary["by_voice"]["labels"] == [{"label": "haiku", "positive": 1, "negative": 1}]
    captured = {}

    class Fake:
        def __getitem__(self, name):
            return self

        def aggregate(self, pipeline):
            captured["pipeline"] = pipeline
            return []

    feedback_stats.fetch_corrections(Fake())
    assert captured["pipeline"][0]["$project"]["voice"] == 1                                              # the stamp is read with the rows, the text is not
    assert "user_prompt" not in captured["pipeline"][0]["$project"] and "bad_response" not in captured["pipeline"][0]["$project"]


def test_the_app_stamps_every_streamed_reply_with_the_tier_that_actually_wrote_it_and_looks_the_stamp_up_when_a_reply_is_rated():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "written_tier = reply_tier.STANDARD if (turn_llm is response_llm or risk_level != RISK_NONE) else tier" in source
    assert source.index("written_tier = reply_tier.STANDARD") < source.index("token_stream = turn_llm.astream(prompt)")        # set once the fallback is known
    assert "ai_message.additional_kwargs[reply_stamp.KEY] = reply_stamp.build(" in source
    assert "written_tier, tier_reason, reply_choice, [e[\"id\"] for e in voice_example_entries], [n[\"id\"] for n in people_note_entries]," in source
    assert source.index("ai_message = stamp(AIMessage(content=full_response))") < source.index("ai_message.additional_kwargs[reply_stamp.KEY]")
    assert "voice = reply_stamp.find(chat_sessions, username, payload.bad_response)" in source and "voice=voice," in source
    assert "from backend.utils import reply_stamp\n" in source
    page = (ROOT / "local" / "src" / "pages" / "SonicHealthFeedback.tsx").read_text(encoding="utf-8")
    assert "feedback.by_voice" in page and "By voice example" in page and "By people note" in page


# ---- the saved-conversation fallback (2026-11-09: six of nine thumbs found no stamp because the server had not loaded the conversation since a restart) -----------------------------------------------------------

class _SavedConversations:
    """Just what find_saved asks of the database: find(query, projection).sort(...).limit(n) over saved conversations, which keep messages as plain dicts."""

    def __init__(self, docs):
        self.docs = docs
        self.calls = []

    def find(self, query, projection=None):
        self.calls.append((query, projection))
        rows = [dict(d) for d in self.docs if d.get("username") == query.get("username")]
        outer = self

        class Cursor:
            def sort(self, key, direction):
                rows.sort(key=lambda d: d.get(key, ""), reverse=direction < 0)
                outer.calls.append(("sort", key, direction))
                return self

            def limit(self, n):
                outer.calls.append(("limit", n))
                del rows[n:]
                return self

            def __iter__(self):
                for d in rows:
                    yield {**d, "messages": d["messages"][projection["messages"]["$slice"]:]}

        return Cursor()


class _Db(dict):
    def __getitem__(self, name):
        assert name == "conversations"
        return dict.__getitem__(self, name)


def saved(*conversations):
    return _Db(conversations=_SavedConversations(list(conversations)))


def saved_ai(text, stamp=None):
    return {"type": "ai", "content": text, **({rs.KEY: stamp} if stamp else {})}


def test_a_rated_reply_the_server_no_longer_holds_is_found_in_the_saved_conversation_newest_first():
    other = {**STAMP, "label": "flash", "tier": "standard"}
    db = saved(
        {"username": "jack", "updated_at": "2026-11-08", "messages": [saved_ai("That is huge! Nice work.", other)]},
        {"username": "jack", "updated_at": "2026-11-09", "messages": [saved_ai("older"), saved_ai("That is huge! Nice work.", STAMP)]},
        {"username": "kim", "updated_at": "2026-11-09", "messages": [saved_ai("Kim's private reply", STAMP)]},
    )
    assert rs.find_saved(db, "jack", "  That is   huge!\nNice work. <<<FOLLOW_UP: more>>>") == STAMP         # the newest conversation, spacing and tag ignored
    assert rs.find_saved(db, "jack", "Kim's private reply") is None                                          # another person's conversation is never read
    assert rs.find_saved(db, "jack", "never sent") is None and rs.find_saved(db, "jack", "") is None and rs.find_saved(db, "", "x") is None and rs.find_saved(None, "jack", "x") is None
    assert rs.find_saved(saved({"username": "jack", "updated_at": "1", "messages": [saved_ai("an old unstamped reply")]}), "jack", "an old unstamped reply") is None
    assert rs.find_saved(saved({"username": "jack", "updated_at": "1", "messages": [{"type": "human", "content": "That is huge!", rs.KEY: STAMP}]}), "jack", "That is huge!") is None   # only a reply


def test_the_saved_search_is_bounded_to_the_newest_few_conversations_and_their_last_messages_and_a_stored_junk_stamp_is_dropped():
    db = saved({"username": "jack", "updated_at": "1", "messages": [saved_ai("x")]})
    rs.find_saved(db, "jack", "x")
    query, projection = db["conversations"].calls[0]
    assert query == {"username": "jack"} and projection["messages"] == {"$slice": -rs.SEARCH_LAST_MESSAGES} and projection["updated_at"] == 1
    assert ("sort", "updated_at", -1) in db["conversations"].calls and ("limit", rs.SEARCH_SAVED_CONVERSATIONS) in db["conversations"].calls
    assert rs.find_saved(saved({"username": "jack", "updated_at": "1", "messages": [saved_ai("a reply", {"label": "junk"})]}), "jack", "a reply") is None


def test_a_failing_database_never_breaks_the_rating():
    class Boom:
        def __getitem__(self, name):
            raise RuntimeError("down")

    assert rs.find_saved(Boom(), "jack", "a reply") is None


def test_the_feedback_endpoint_asks_memory_first_and_the_saved_conversation_second():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    first = source.index("voice = reply_stamp.find(chat_sessions, username, payload.bad_response)")
    second = source.index("voice = await asyncio.to_thread(reply_stamp.find_saved, db, username, payload.bad_response)")
    assert first < second < source.index("save_correction(", second) and "if voice is None:" in source[first:second]


def test_the_page_sends_its_identity_headers_with_a_thumb_so_the_server_files_it_under_the_account_not_the_clerk_id():
    """2026-11-09: the feedback call sent only the login token, so get_current_user (which turns a token into the account only when the X-Principal email header is present) saved 150 thumbs under the raw Clerk id,
    where no conversation, stamp or per-person count could find them."""
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    start = chat.index("await fetch(`${BASE_URL}/api/chat/feedback`")
    headers = chat[start: chat.index("body: JSON.stringify", start)]
    assert "'X-Principal': principal" in headers and "'x-user-id': principal" in headers and "Authorization: `Bearer ${token}`" in headers
    auth = (ROOT / "backend" / "auth" / "isolation_auth.py").read_text(encoding="utf-8")
    assert 'if "@" in normalized_principal:' in auth and 'return {**payload, "sub": verified_email, "email": verified_email}' in auth and "    return payload" in auth     # the header is what maps a token to the account


def test_a_thumbs_down_about_the_voice_itself_is_counted_under_the_voice_that_wrote_the_reply():
    """Jack, 2026-11-11: the rate of "repeated me / nothing new / did not meet me" per voice has to come from real use, not from a pairwise A/B."""
    flash = {**STAMP, "label": "flash", "tier": "standard", "examples": [], "notes": []}
    rows = [
        {"kind": "negative", "voice": STAMP, "tag": "repeated_me"}, {"kind": "negative", "voice": STAMP, "tag": "nothing_new"}, {"kind": "negative", "voice": STAMP, "tag": "nothing_new"},
        {"kind": "negative", "voice": flash, "tag": "didnt_attune"},
        {"kind": "negative", "voice": STAMP, "tag": "formatting"},            # a different reason: counted as a thumbs-down, not here
        {"kind": "positive", "voice": STAMP, "tag": "repeated_me"},          # a thumbs-up never counts as a complaint
        {"kind": "negative", "voice": None, "tag": "repeated_me"},           # no stamp: nothing to attribute it to
    ]
    summary = rs.summarize(rows)
    assert summary["reasons"] == [
        {"label": "haiku", "repeated_me": 1, "nothing_new": 2, "didnt_attune": 0},
        {"label": "flash", "repeated_me": 0, "nothing_new": 0, "didnt_attune": 1},
    ]
    assert rs.VOICE_QUALITY_TAGS == ("repeated_me", "nothing_new", "didnt_attune")


def test_the_page_sends_the_tag_and_the_summary_keeps_it_so_the_reasons_reach_the_health_page():
    rows = [{"username": "jack", "tag": "repeated_me", "rating": "negative", "source": "user", "created_at": "2026-11-08T12:00:00+00:00", "has_embedding": True, "voice": STAMP}]
    summary = feedback_stats.summarize_feedback(rows)
    assert summary["by_voice"]["reasons"] == [{"label": "haiku", "repeated_me": 1, "nothing_new": 0, "didnt_attune": 0}]
    assert {"tag": "repeated_me", "count": 1} in summary["by_tag"]
