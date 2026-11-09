"""Asking the person to choose between a few approaches, in the same turn (backend/services/choice_cards.py, the ask_choice tool in tool_agent_node)."""
import asyncio
import pathlib
from unittest.mock import AsyncMock

import pytest

from backend.services import agent_workflow as aw
from backend.services import choice_cards as cc
from backend.services import native_tools as nt
from backend.tests.test_local_edits import run_async

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    cc._waiting.clear()
    yield
    cc._waiting.clear()


def _args(**extra):
    base = {"question": "Which greeting do you want?", "options": [{"label": "Formal", "description": "Dear Sir"}, {"label": "Casual", "description": "Hey"}, {"label": "Silly"}]}
    base.update(extra)
    return base


# ---- the card ------------------------------------------------------------------------------------------------------------------------------

def test_a_valid_card_has_an_id_the_question_and_its_options_in_order():
    card, why = cc.validate_card(_args())
    assert why == "" and card["question"] == "Which greeting do you want?" and len(card["choice_id"]) == 32
    assert [o["label"] for o in card["options"]] == ["Formal", "Casual", "Silly"] and not any(o["recommended"] for o in card["options"])
    assert card["options"][0]["description"] == "Dear Sir" and card["options"][2]["description"] == ""


def test_the_recommended_option_is_marked_and_moved_first_by_number_or_by_label():
    card, _ = cc.validate_card(_args(recommended=2))
    assert [o["label"] for o in card["options"]] == ["Casual", "Formal", "Silly"] and [o["recommended"] for o in card["options"]] == [True, False, False]
    by_label, _ = cc.validate_card({"question": "Which?", "options": [{"label": "A"}, {"label": "B (Recommended)"}]})
    assert [o["label"] for o in by_label["options"]] == ["B", "A"] and by_label["options"][0]["recommended"]
    assert not any(o["recommended"] for o in cc.validate_card(_args(recommended=9))[0]["options"])      # a number off the end is ignored, not an error
    assert not any(o["recommended"] for o in cc.validate_card(_args(recommended="soon"))[0]["options"])


def test_a_card_that_could_not_be_drawn_well_is_refused_with_a_reason_the_model_can_act_on():
    refused = {
        "no question": _args(question="  "),
        "long question": _args(question="x" * (cc.MAX_QUESTION_CHARS + 1)),
        "one option": _args(options=[{"label": "Only"}]),
        "five options": _args(options=[{"label": c} for c in "ABCDE"]),
        "not a list": _args(options="Formal, Casual"),
        "no label": _args(options=[{"label": "A"}, {"description": "no name"}]),
        "long label": _args(options=[{"label": "A"}, {"label": "y" * (cc.MAX_LABEL_CHARS + 1)}]),
        "long description": _args(options=[{"label": "A"}, {"label": "B", "description": "z" * (cc.MAX_DESCRIPTION_CHARS + 1)}]),
        "duplicate": _args(options=[{"label": "Same"}, {"label": "same "}]),
        "other option": _args(options=[{"label": "A"}, {"label": "Something else"}]),
        "none of these": _args(options=[{"label": "A"}, {"label": "None of the above"}]),
    }
    for name, args in refused.items():
        card, why = cc.validate_card(args)
        assert card is None and why.endswith((".", "?")) and len(why) > 10, name
    assert cc.validate_card(None)[0] is None and cc.validate_card("which?")[0] is None


def test_plain_string_options_are_accepted_and_whitespace_is_tidied():
    card, why = cc.validate_card({"question": " Which\n one? ", "options": ["  Red ", "Blue"]})
    assert why == "" and card["question"] == "Which one?" and [o["label"] for o in card["options"]] == ["Red", "Blue"]


# ---- the registry --------------------------------------------------------------------------------------------------------------------------

@run_async
async def test_a_pick_wakes_the_waiter_and_is_forgotten_once_answered():
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    waiter = asyncio.create_task(cc.wait_for(card["choice_id"], timeout=5))
    await asyncio.sleep(0)
    assert cc.answer("jack", card["choice_id"], index=1) == "recorded"
    assert await waiter == {"kind": "picked", "index": 1} and cc.waiting_count() == 0


@run_async
async def test_own_words_are_accepted_tidied_and_capped_and_an_option_wins_over_words():
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    waiter = asyncio.create_task(cc.wait_for(card["choice_id"], timeout=5))
    await asyncio.sleep(0)
    assert cc.answer("jack", card["choice_id"], other="  none\nof those " + "w" * 900) == "recorded"
    given = await waiter
    assert given["kind"] == "other" and given["text"].startswith("none of those w") and len(given["text"]) == cc.MAX_OTHER_CHARS
    cc.register("jack", card)
    assert cc.answer("jack", card["choice_id"], index=0, other="words too") == "recorded" and cc._waiting[card["choice_id"]]["answer"] == {"kind": "picked", "index": 0}


def test_only_the_person_it_was_shown_to_can_answer_and_bad_answers_are_invalid_not_recorded():
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    assert cc.answer("mallory", card["choice_id"], index=0) == "no_waiter" and cc.answer("jack", "nope", index=0) == "no_waiter"
    for bad in ({"index": 3}, {"index": -1}, {"index": True}, {"index": "1"}, {"other": "   "}, {}):
        assert cc.answer("jack", card["choice_id"], **bad) == "invalid", bad
    assert cc._waiting[card["choice_id"]]["answer"] is None


@run_async
async def test_an_answer_that_arrives_before_the_wait_starts_is_not_lost():
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    assert cc.answer("jack", card["choice_id"], index=2) == "recorded"
    assert await cc.wait_for(card["choice_id"], timeout=5) == {"kind": "picked", "index": 2}


@run_async
async def test_no_answer_in_time_gives_none_ticks_while_waiting_and_a_late_answer_finds_no_waiter(monkeypatch):
    monkeypatch.setattr(cc, "HEARTBEAT_SECONDS", 0.02)
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    ticks = []

    async def tick(seconds):
        ticks.append(seconds)

    assert await cc.wait_for(card["choice_id"], timeout=0.1, on_tick=tick) is None
    assert ticks and cc.waiting_count() == 0 and cc.answer("jack", card["choice_id"], index=0) == "no_waiter"
    assert await cc.wait_for("never-registered", timeout=0.1) is None


@run_async
async def test_a_cancelled_turn_stops_waiting_and_leaves_nothing_behind():
    card, _ = cc.validate_card(_args())
    cc.register("jack", card)
    waiter = asyncio.create_task(cc.wait_for(card["choice_id"]))
    await asyncio.sleep(0.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert cc.waiting_count() == 0


def test_a_failing_tick_does_not_end_the_wait_and_the_wait_is_ten_minutes_by_default():
    source = (ROOT / "backend" / "services" / "choice_cards.py").read_text(encoding="utf-8")
    assert '"CHOICE_WAIT_SECONDS", "600"' in source and "except Exception:\n                        pass" in source


# ---- what the loop is told -----------------------------------------------------------------------------------------------------------------

def test_the_loop_is_told_what_was_chosen_and_what_to_do_next():
    card, _ = cc.validate_card(_args(recommended=2))
    picked = cc.result_text(card, {"kind": "picked", "index": 0})
    assert 'option 1: "Casual"' in picked and "(Hey)" in picked and "your recommendation" in picked and "Carry on" in picked
    plain = cc.result_text(card, {"kind": "picked", "index": 2})
    assert '"Silly"' in plain and "your recommendation" not in plain and "()" not in plain
    own = cc.result_text(card, {"kind": "other", "text": "the third way"})
    assert "their own words" in own and '"the third way"' in own and "carry on" in own


def test_no_answer_tells_the_loop_not_to_guess_and_to_say_what_it_would_have_asked():
    text = cc.result_text(cc.validate_card(_args())[0], None)
    assert "nothing was decided" in text and "Do not guess" in text and "stop" in text and "next message" in text


# ---- through the tool agent ----------------------------------------------------------------------------------------------------------------

async def _drive(monkeypatch, on_card, *, choice_cards=True, args=None, asks=1, question="rename the helper somewhere sensible"):
    """Runs tool_agent_node on the native engine with a fake loop that calls ask_choice through the real dispatcher while the browser answers via `on_card(card)`.
    Records the tools the loop was offered (seen["tools"]) and each observation (seen["observations"])."""
    from backend.tests.test_tool_agent_node import _setup_github_repo, _state
    from backend.services import repo_checkout as rc

    monkeypatch.setenv("TOOL_AGENT_ENGINE", "native")
    monkeypatch.setattr(aw, "fetch_and_extract_checkout", lambda *a, **k: (_ for _ in ()).throw(rc.RepoCheckoutError("x")))
    _setup_github_repo(monkeypatch)
    emit = AsyncMock()
    monkeypatch.setattr(aw, "safe_emit_event", emit)
    recorded = []
    monkeypatch.setattr("backend.utils.self_counters.record_turn_counts", lambda counts, *a, **k: recorded.append(dict(counts)))
    seen = {"observations": [], "counts": recorded, "emit": emit}

    async def answer_when_shown(already):
        for _ in range(400):
            shown = [c.args[1] for c in emit.call_args_list if c.args[0] == "choice_card"]
            if len(shown) > already:
                on_card(shown[already])
                return
            await asyncio.sleep(0.005)

    async def fake_native_run(**kwargs):
        seen["tools"] = [t["name"] for t in kwargs["tools"]]
        for n in range(asks):
            helper = asyncio.create_task(answer_when_shown(n))
            seen["observations"].append(await kwargs["act"]({"tool_action": "ask_choice", "args": args or _args()}))
            await helper
        return {"final_answer": "done", "attempts": [], "show_work": False}

    monkeypatch.setattr(aw.native_loop, "run", fake_native_run)
    await aw.tool_agent_node({**_state(question, username="jack"), "choice_cards": choice_cards})
    return seen


@run_async
async def test_a_pick_comes_back_to_the_loop_as_the_tool_result_and_the_card_is_streamed(monkeypatch):
    seen = await _drive(monkeypatch, lambda card: cc.answer("jack", card["choice_id"], index=1))
    card = [c.args[1] for c in seen["emit"].call_args_list if c.args[0] == "choice_card"][0]
    assert card["question"] == "Which greeting do you want?" and len(card["options"]) == 3
    assert "ask_choice" in seen["tools"] and 'option 2: "Casual"' in seen["observations"][0] and cc.waiting_count() == 0
    assert {"choice_asked": 1} in seen["counts"] and {"choice_picked": 1} in seen["counts"]


@run_async
async def test_own_words_come_back_and_are_counted_as_other(monkeypatch):
    seen = await _drive(monkeypatch, lambda card: cc.answer("jack", card["choice_id"], other="put it in utils"))
    assert '"put it in utils"' in seen["observations"][0] and {"choice_other": 1} in seen["counts"]


@run_async
async def test_no_answer_in_time_tells_the_loop_to_stop_and_is_counted(monkeypatch):
    monkeypatch.setattr(cc, "WAIT_SECONDS", 0.05)
    seen = await _drive(monkeypatch, lambda card: None)
    assert "nothing was decided" in seen["observations"][0] and {"choice_timeout": 1} in seen["counts"] and cc.waiting_count() == 0


@run_async
async def test_a_card_that_cannot_be_drawn_is_refused_without_showing_anything_and_is_counted(monkeypatch):
    seen = await _drive(monkeypatch, lambda card: None, args=_args(options=[{"label": "Only"}]))
    assert seen["observations"][0].startswith("ERROR: the question was not shown") and not [c for c in seen["emit"].call_args_list if c.args[0] == "choice_card"]
    assert {"choice_refused": 1} in seen["counts"] and cc.waiting_count() == 0


@run_async
async def test_a_third_question_in_one_turn_is_refused_so_it_never_becomes_an_interview(monkeypatch):
    seen = await _drive(monkeypatch, lambda card: cc.answer("jack", card["choice_id"], index=0), asks=3)
    shown = [c for c in seen["emit"].call_args_list if c.args[0] == "choice_card"]
    assert len(shown) == 2 and "Carry on" in seen["observations"][1] and seen["observations"][2].startswith("ERROR: you have already asked 2 times")
    assert {"choice_refused": 1} in seen["counts"]


@run_async
async def test_a_page_that_cannot_show_the_card_is_not_offered_the_tool_and_gets_plain_words_if_it_asks_anyway(monkeypatch):
    seen = await _drive(monkeypatch, lambda card: None, choice_cards=False)
    assert "ask_choice" not in seen["tools"] and seen["observations"][0].startswith("ERROR: this page cannot show a choice")
    assert not [c for c in seen["emit"].call_args_list if c.args[0] == "choice_card"] and cc.waiting_count() == 0


@run_async
async def test_the_switch_turns_the_tool_off_even_for_a_page_that_can_show_it(monkeypatch):
    monkeypatch.setenv("CHOICE_CARDS", "off")
    seen = await _drive(monkeypatch, lambda card: None)
    assert "ask_choice" not in seen["tools"]


# ---- the tool's declaration, the counters, the wiring -----------------------------------------------------------------------------------------

def test_ask_choice_has_a_native_declaration_and_is_offered_only_from_its_menu_line():
    assert "ask_choice" in nt.REGISTRY and "ask_choice" in nt.NATIVE_DESCRIPTIONS
    assert set(nt.REGISTRY["ask_choice"]["required"]) == {"question", "options"}
    declared = nt.declarations_from_menu(["- ask_choice — args: question, options, recommended"])
    assert [d["name"] for d in declared] == ["ask_choice"] and nt.declarations_from_menu(["- read_repo_file — args: path"])[0]["name"] == "read_repo_file"
    assert "do not add an 'other' option" in nt.NATIVE_DESCRIPTIONS["ask_choice"] and "never ask permission" in nt.NATIVE_DESCRIPTIONS["ask_choice"]


def test_every_choice_counter_is_one_the_tally_accepts_and_the_state_declares_the_flag():
    from backend.utils import self_counters
    from backend.state.graph_state import GraphState

    assert set(self_counters.CHOICE_KEYS) == {"choice_asked", "choice_picked", "choice_other", "choice_timeout", "choice_refused"}
    assert set(self_counters.CHOICE_KEYS) <= set(self_counters.KEYS)
    assert "choice_cards" in GraphState.__annotations__
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    for key in self_counters.CHOICE_KEYS:
        assert f'"{key}"' in workflow, f"{key} is never recorded"


def test_the_endpoint_the_request_flag_the_stream_event_and_the_card_are_wired_both_ends():
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    block = app_source[app_source.index('@app.post("/api/chat/choice/{choice_id}")'):]
    block = block[:block.index("\n\n\n")]
    assert "Depends(get_current_user)" in block and "choice_cards.answer(current_user.get(\"sub\")" in block
    assert "choice_cards: bool = False" in app_source and '"choice_cards": bool(request.choice_cards)' in app_source
    assert 'event.get("name") == "choice_card"' in app_source and "'event': 'choice_card'" in app_source
    api_source = (ROOT / "local" / "src" / "api.ts").read_text(encoding="utf-8")
    assert "choice_cards: true" in api_source and "/api/chat/choice/${encodeURIComponent(choiceId)}" in api_source
    chat = (ROOT / "local" / "src" / "pages" / "Chat.tsx").read_text(encoding="utf-8")
    assert "payload.event === 'choice_card'" in chat and "parseChoiceCard(payload)" in chat and "<ChoiceCard" in chat
    card = (ROOT / "local" / "src" / "components" / "ChoiceCard.tsx").read_text(encoding="utf-8")
    assert "api.answerChoice(card.choice_id" in card and "no_waiter" not in card and "reply.status === 'recorded'" in card


def test_the_choice_module_logs_nothing_the_person_wrote():
    """The one log line allowed is how a wait ended: a fixed sentence, the word for how it ended, and the seconds. Never the question, an option or the answer."""
    import ast
    source = (ROOT / "backend" / "services" / "choice_cards.py").read_text(encoding="utf-8")
    logs = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("info", "warning", "error", "exception", "debug")]
    assert len(logs) == 1 and ast.literal_eval(logs[0].args[0]) == "[choice_cards] a wait for a choice ended: %s after about %ds."
    assert len(logs[0].args) == 3                                                    # the sentence, how it ended, the seconds: nothing else is passed in
    workflow = (ROOT / "backend" / "services" / "agent_workflow.py").read_text(encoding="utf-8")
    body = workflow[workflow.index("async def _ask_choice"):workflow.index("async def _run_local_check")]
    assert "logger." not in body
