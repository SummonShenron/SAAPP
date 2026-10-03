"""Graph state is re-saved in every checkpoint, so its size multiplies. Observed: memory_recall_node
put every fact's embedding (~12-15KB each) into state["memory_facts"], making one thread's
checkpoints ~3.3MB each — 353 of them filled the Atlas free-tier quota in a day. Nothing reads
state["memory_facts"] back, so the embeddings must stay out of it."""
from langchain_core.messages import HumanMessage

from backend.services import agent_workflow as aw
from backend.utils.memory_utils import UserFact, fact_for_state

EMBEDDING = [0.001] * 3072


def _fact(i=1):
    return UserFact(
        id=f"f{i}", username="u", category="preference", fact=f"Likes thing {i}",
        created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00", embedding=EMBEDDING,
    )


def test_fact_for_state_drops_the_embedding_and_keeps_everything_else():
    state_fact = fact_for_state(_fact())

    assert "embedding" not in state_fact
    assert state_fact["id"] == "f1"
    assert state_fact["category"] == "preference"
    assert state_fact["fact"] == "Likes thing 1"


def test_memory_recall_node_keeps_embeddings_out_of_graph_state(monkeypatch):
    facts = [_fact(i) for i in range(5)]
    monkeypatch.setattr(aw, "load_user_facts", lambda username: facts)
    monkeypatch.setattr(aw, "retrieve_user_memory", lambda *a, **k: [])

    state = aw.memory_recall_node({"messages": [HumanMessage(content="what do you know about me")], "username": "u"})

    assert len(state["memory_facts"]) == 5
    assert all("embedding" not in f for f in state["memory_facts"])
    # And the saved state is small: five facts with embeddings would be ~75KB+ as JSON.
    import json
    assert len(json.dumps(state["memory_facts"])) < 5_000
