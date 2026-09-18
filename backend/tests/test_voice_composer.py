from backend.components.constraints import (
    build_voice_prompt,
    GROUNDING_BLOCKS,
    SONIC_ASSISTANT_PERSONA,
    FOLLOW_UP_CONSTRAINT,
    get_affiliate_override,
)


def test_persona_present_in_every_source_type():
    for source_type, block in GROUNDING_BLOCKS.items():
        prompt = build_voice_prompt(grounding_block=block, data="some data", history="", question="hi?")
        assert "Sonic Assistant" in prompt, source_type
        assert "personal AI agent" in prompt, source_type


def test_follow_up_constraint_present_in_every_source_type():
    # Regression: today, web_search/code_interpreter/github_search/pr_summary never got a
    # follow-up button because they used templates that never carried FOLLOW_UP_CONSTRAINT.
    for source_type, block in GROUNDING_BLOCKS.items():
        prompt = build_voice_prompt(grounding_block=block, data="some data", history="", question="hi?")
        assert "<<<FOLLOW_UP:" in prompt, source_type


def test_kb_strict_refusal_string_only_appears_for_kb_strict():
    refusal = "I cannot find the answer in the provided knowledge base."
    for source_type, block in GROUNDING_BLOCKS.items():
        prompt = build_voice_prompt(grounding_block=block, data="some data", history="", question="hi?")
        if source_type == "kb_strict":
            assert refusal in prompt
        else:
            assert refusal not in prompt, source_type


def test_kb_open_allows_general_knowledge_only_for_kb_open():
    for source_type, block in GROUNDING_BLOCKS.items():
        prompt = build_voice_prompt(grounding_block=block, data="some data", history="", question="hi?")
        normalized = " ".join(prompt.split())
        if source_type == "kb_open":
            assert "general knowledge" in normalized
        else:
            assert "general knowledge" not in normalized, source_type


def test_web_grounding_mentions_citing_urls_only_for_web():
    for source_type, block in GROUNDING_BLOCKS.items():
        prompt = build_voice_prompt(grounding_block=block, data="some data", history="", question="hi?")
        if source_type == "web":
            assert "Cite the source URLs" in prompt
        else:
            assert "Cite the source URLs" not in prompt, source_type


def test_tool_output_grounding_forbids_kb_refusal_language():
    prompt = build_voice_prompt(grounding_block=GROUNDING_BLOCKS["tool_output"], data="15 commits", history="", question="what changed?")
    assert "inherently ground truth" in prompt
    assert "I cannot find the answer in the provided knowledge base." not in prompt


def test_data_slot_reflects_empty_vs_populated():
    empty_prompt = build_voice_prompt(grounding_block=GROUNDING_BLOCKS["conversational"], data="", history="", question="hi")
    assert "(none for this turn)" in empty_prompt

    filled_prompt = build_voice_prompt(grounding_block=GROUNDING_BLOCKS["conversational"], data="user likes dark mode", history="", question="hi")
    assert "user likes dark mode" in filled_prompt
    assert "(none for this turn)" not in filled_prompt


def test_insight_only_appears_when_provided():
    no_insight = build_voice_prompt(grounding_block=GROUNDING_BLOCKS["conversational"], data="", history="", question="hi")
    assert "WHAT YOU REMEMBER" not in no_insight

    with_insight = build_voice_prompt(
        grounding_block=GROUNDING_BLOCKS["conversational"], data="", history="", question="hi",
        insight="Just saved a memory fact for this user.",
    )
    assert "WHAT YOU REMEMBER" in with_insight
    assert "Just saved a memory fact for this user." in with_insight


def test_affiliate_override_appears_only_when_provided():
    # Note: the persona itself always references "an AFFILIATE OVERRIDE section" so the model
    # knows to defer to one if present — check for the actual override content instead.
    no_override = build_voice_prompt(grounding_block=GROUNDING_BLOCKS["kb_strict"], data="", history="", question="hi")
    assert "sarcastic" not in no_override

    with_override = build_voice_prompt(
        grounding_block=GROUNDING_BLOCKS["kb_strict"], data="", history="", question="hi",
        affiliate_override=get_affiliate_override("Affiliate_B"),
    )
    assert "sarcastic" in with_override


def test_get_affiliate_override_bty_fitness_identity_swap():
    override = get_affiliate_override("Affiliate_D")
    assert "BTY Fitness" in override
    assert "Madison Spear" in override


def test_get_affiliate_override_empty_for_unknown_affiliate():
    assert get_affiliate_override("All") == ""
    assert get_affiliate_override("Affiliate_Z") == ""
