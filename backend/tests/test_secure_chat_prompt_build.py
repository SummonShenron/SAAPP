"""Regression guard for app.py's collapsed prompt-selection branch (secure_chat). Rather than
spin up the SSE endpoint, this replicates the exact selection logic app.py runs on
final_state["voice_payload"] and confirms the right persona + grounding sentence come out for
every source_type app.py can encounter."""
from langchain_core.documents import Document

from backend.components.constraints import (
    build_voice_prompt,
    GROUNDING_BLOCKS,
    KB_STRICT_GROUNDING,
    get_affiliate_override,
    format_docs,
)
from backend.utils.app_utils import collect_kb_images


def _build_prompt_like_app_py(voice_payload, documents, requested_affiliate="All"):
    source_type = voice_payload.get("source_type", "kb_strict")
    documents_sorted = sorted(documents, key=lambda d: d.metadata.get("priority", False), reverse=True)
    data = voice_payload.get("data") or format_docs(documents_sorted)
    kb_images = collect_kb_images(documents_sorted) if source_type in ("kb_strict", "kb_open") else []
    prompt = build_voice_prompt(
        grounding_block=GROUNDING_BLOCKS.get(source_type, KB_STRICT_GROUNDING),
        data=data,
        history="",
        question="does this work?",
        affiliate_override=get_affiliate_override(requested_affiliate),
        insight=voice_payload.get("insight") or "",
    )
    return prompt, kb_images


def test_kb_strict_source_type_builds_correct_prompt():
    docs = [Document(page_content="Some KB text.", metadata={"source": "manual.pdf"})]
    prompt, _ = _build_prompt_like_app_py({"source_type": "kb_strict"}, docs)
    assert "Sonic Assistant" in prompt
    assert "I cannot find the answer in the provided knowledge base." in prompt
    assert "Some KB text." in prompt


def test_tool_output_source_type_uses_content_to_format_not_documents():
    prompt, _ = _build_prompt_like_app_py({"source_type": "tool_output", "data": "15 commits, 4 files changed"}, documents=[])
    assert "15 commits, 4 files changed" in prompt
    assert "inherently ground truth" in prompt
    assert "I cannot find the answer in the provided knowledge base." not in prompt


def test_conversational_source_type_with_insight():
    prompt, _ = _build_prompt_like_app_py(
        {"source_type": "conversational", "insight": "Just cancelled the pending action."}, documents=[]
    )
    assert "Just cancelled the pending action." in prompt
    assert "WHAT YOU REMEMBER" in prompt


def test_kb_images_only_collected_for_kb_source_types():
    docs_with_image = [Document(page_content="desc", metadata={"doc_type": "standalone_image", "gridfs_id": "abc123", "source": "logo.png"})]

    _, kb_images_kb_strict = _build_prompt_like_app_py({"source_type": "kb_strict"}, docs_with_image)
    assert kb_images_kb_strict == [{"filename": "logo.png", "fileId": "abc123"}]

    _, kb_images_tool_output = _build_prompt_like_app_py({"source_type": "tool_output", "data": "x"}, docs_with_image)
    assert kb_images_tool_output == []


def test_affiliate_override_flows_through_app_py_glue():
    prompt, _ = _build_prompt_like_app_py({"source_type": "kb_strict"}, documents=[], requested_affiliate="Affiliate_B")
    assert "sarcastic" in prompt
