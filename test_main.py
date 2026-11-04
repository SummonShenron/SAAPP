"""Tests for the document-answer path: the GitHub PR overview, the graph database helpers, the retrieve / grade / rewrite nodes and the rewrite fallback.

Nothing here reaches a real service. Each test that runs a node replaces the three things it reads through (the library vector search, the session search and the scoped knowledge-graph lookup, which reads
MongoDB) and the model it calls. This file is outside backend/tests, so the suite's guards against real databases, embeddings and models (backend/tests/conftest.py) do not apply to it: it must keep mocking
for itself. Run it with `GOOGLE_API_KEY=test-not-real python -m pytest test_main.py`.
"""
import asyncio
import json
import os
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))
from backend.state import graph_db
from backend.state.graph_state import GraphState
from backend.services.agent_workflow import retrieve_node, grading_node, rewrite_query_node
from backend.services.search import _detect_routing_strategy
from backend.services.github_service import process_pr_summary
from backend.utils.fallback_utils import rewrite_fallback      # not `from app import ...`: importing the app connects to the real database


class MockLLM:
    def invoke(self, prompt):
        return AIMessage(content="Rewritten: facts about Goku in DBZ")


def _state(question, **extra) -> GraphState:
    state: GraphState = {
        "username": "alice",
        "messages": [HumanMessage(content=question)],
        "target_scope": ["Affiliate_A"],
        "history": "",
        "documents": [],
        "relevance_grade": "",
        "generation": "",
        "loop_count": 0,
        "original_question": None,
    }
    state.update(extra)
    return state


class TestGitHubRepoBehavior(unittest.TestCase):

    @patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"})
    @patch("backend.services.github_service._call_llm_with_retry", new=lambda prompt: SimpleNamespace(content="An overview of the change."))     # the overview is written by a model: not here
    @patch("backend.services.github_service.requests.post")
    @patch("backend.services.github_service.requests.get")
    def test_process_pr_summary_posts_to_target_repo(self, mock_get, mock_post):
        mock_get.return_value.status_code = 200
        mock_get.return_value.json.return_value = [
            {
                "filename": "app.py",
                "status": "modified",
                "patch": "+ print('hi')"
            }
        ]

        mock_post.return_value.status_code = 201
        mock_post.return_value.json.return_value = {"html_url": "https://github.com/Acme/OtherRepo/pull/7"}

        process_pr_summary("Acme/OtherRepo", 7)

        get_call = mock_get.call_args_list[0]
        self.assertIn("/repos/Acme/OtherRepo/pulls/7/files", get_call.args[0])

        post_call = mock_post.call_args_list[0]
        self.assertIn("/repos/Acme/OtherRepo/issues/7/comments", post_call.args[0])
        self.assertIn("An overview of the change.", json.dumps(post_call.kwargs.get("json") or {}))


class TestGraphState(unittest.TestCase):

    def setUp(self):
        """
        Runs before EVERY test.
        We must clear the global knowledge graph to ensure test isolation.
        Otherwise, data from test 1 would bleed into test 2.
        """
        graph_db.knowledge_graph.clear()

    def _sources(self, vector=(), graph=(), session=()):
        """Replaces what retrieve_node reads through (the library vector search, the person's session files and the scoped knowledge graph, which is MongoDB) and returns the three mocks."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        return SimpleNamespace(
            vector=stack.enter_context(patch("backend.services.agent_workflow.library_search.vector_docs", return_value=list(vector))),
            session=stack.enter_context(patch("backend.services.agent_workflow.retrieve_from_session", return_value=list(session))),
            graph=stack.enter_context(patch("backend.services.agent_workflow.graph_db.scoped_context", return_value=list(graph))),
        )

    # ==========================================
    # 2. TESTING THE GRADING NODE
    # ==========================================
    @patch("backend.services.agent_workflow.lite_llm")
    @patch("backend.services.agent_workflow.format_docs")
    def test_grading_node_positive_evaluation(self, mock_format, mock_llm):
        """Should return relevance_grade 'yes' if LLM output contains 'yes'."""
        mock_format.return_value = "Mocked Context String"
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="This is a YES."))

        doc = Document(page_content="Some Document Text", metadata={"source": "test.txt"})
        state = _state("How do I build a RAG?", documents=[doc], loop_count=1, original_question="How do I build a RAG?")

        updates = asyncio.run(grading_node(state))
        self.assertEqual(updates["relevance_grade"], "yes")

    @patch("backend.services.agent_workflow.lite_llm")
    @patch("backend.services.agent_workflow.format_docs")
    def test_grading_node_negative_evaluation(self, mock_format, mock_llm):
        """Should return relevance_grade 'no' if LLM output does not contain 'yes'."""
        mock_format.return_value = "Mocked Context String"
        mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="The document is unrelated. NO."))

        doc = Document(page_content="Unrelated Text", metadata={"source": "test.txt"})
        state = _state("How do I build a RAG?", documents=[doc], loop_count=1, original_question="How do I build a RAG?")

        updates = asyncio.run(grading_node(state))
        self.assertEqual(updates["relevance_grade"], "no")

    @patch("backend.services.agent_workflow.lite_llm")
    def test_grading_node_with_no_documents_grades_no_without_asking_the_model(self, mock_llm):
        mock_llm.ainvoke = AsyncMock()
        updates = asyncio.run(grading_node(_state("Anything?")))
        self.assertEqual(updates["relevance_grade"], "no")
        mock_llm.ainvoke.assert_not_called()

    # ==========================================
    # 3. TESTING THE RETRIEVAL NODE
    # ==========================================
    def test_retrieve_node_initializes_loop_and_original_question(self):
        """The library's chunks come back as the documents, and the loop counter starts at one."""
        doc1 = Document(page_content="doc1", metadata={"source": "a.txt"})
        doc2 = Document(page_content="doc2", metadata={"source": "b.txt"})
        self._sources(vector=[doc1, doc2])
        state = _state("How do I build a RAG?", loop_count=None)

        updates = asyncio.run(retrieve_node(state, MagicMock()))      # the vector_store argument is not read: the search goes through library_search
        self.assertEqual(updates["documents"], [doc1, doc2])
        self.assertEqual(updates["loop_count"], 1)

    def test_normalize_entity_exact_and_new_matches(self):
        """Should return existing entity if fuzzy matched, or the new string if no match."""
        # Pre-seed the graph with a known entity
        graph_db.knowledge_graph.add_node("Sonic the Hedgehog")

        # Test 1: Exact Match
        result_exact = graph_db.normalize_entity("Sonic the Hedgehog")
        self.assertEqual(result_exact, "Sonic the Hedgehog")

        # Test 2: Fuzzy Match (Typos or partials based on your 0.85 threshold)
        # Note: difflib might need a lower threshold for "Sonic" to match "Sonic the Hedgehog",
        # but let's test a clear typo
        result_typo = graph_db.normalize_entity("Sonic the Hedgehg", threshold=0.8)
        self.assertEqual(result_typo, "Sonic the Hedgehog")

        # Test 3: Completely New Entity (No match should return original)
        result_new = graph_db.normalize_entity("Miles Tails Prower")
        self.assertEqual(result_new, "Miles Tails Prower")

    # ==========================================
    # 4. TESTING EDGES
    # ==========================================
    @patch("backend.state.graph_db.save_graph") # Mock save_graph to prevent writing to disk during tests
    def test_update_graph_adds_edges_and_normalizes(self, mock_save):
        """Should add relationships and trigger a save operation."""
        # Provide a raw relationship from our LLM extraction pipeline
        relationships = [
            {"s": "Goku", "t": "Vegeta", "relationship": "Rival"}
        ]

        graph_db.update_graph(relationships)

        # 1. Verify the nodes and edge were added to the NetworkX graph
        self.assertTrue(graph_db.knowledge_graph.has_edge("Goku", "Vegeta"))

        # 2. Verify the relationship data was stored correctly
        edge_data = graph_db.knowledge_graph.get_edge_data("Goku", "Vegeta")
        self.assertEqual(edge_data["relation"], "Rival")

        # 3. Verify that save_graph() was called at the end of the update
        mock_save.assert_called_once()

    def test_get_dynamic_context_traversal(self):
        """Should retrieve 1-hop and 2-hop connected string contexts."""
        # Pre-build a mini graph: Sonic -> Tails -> Tornado (Airplane)
        graph_db.knowledge_graph.add_edge("Sonic", "Tails", relation="Best Friend")
        graph_db.knowledge_graph.add_edge("Tails", "Tornado", relation="Pilots")

        # --- TEST 1: 1-Hop Context ---
        # Asking about Sonic should only reveal Tails (1 hop away)
        context_1_hop = graph_db.get_dynamic_context("Sonic", hops=1)

        self.assertEqual(len(context_1_hop), 1)
        self.assertIn("Sonic --(Best Friend)--> Tails", context_1_hop[0])
        # It should NOT know about the Tornado yet!
        self.assertFalse(any("Tornado" in c for c in context_1_hop))

        # --- TEST 2: 2-Hop Context ---
        # Asking about Sonic with 2 hops should reveal Tails AND the Tornado
        context_2_hop = graph_db.get_dynamic_context("Sonic", hops=2)

        # Should return both edges
        self.assertEqual(len(context_2_hop), 2)

        # We use a boolean flag check because sets/lists might return in different orders
        has_sonic_tails = any("Sonic --(Best Friend)--> Tails" in c for c in context_2_hop)
        has_tails_tornado = any("Tails --(Pilots)--> Tornado" in c for c in context_2_hop)

        self.assertTrue(has_sonic_tails)
        self.assertTrue(has_tails_tornado)

    def test_retrieve_node_merges_graph_context(self):
        """Ensure GraphRAG dynamic context is appended to vector search results."""
        base_doc = Document(page_content="BaseDoc", metadata={"source": "vec"})
        self._sources(vector=[base_doc], graph=["Sonic --(Best Friend)--> Tails"])
        state = _state("Tell me about Sonic?")

        updates = asyncio.run(retrieve_node(state, MagicMock()))
        docs = updates["documents"]

        # Should contain the base vector doc
        self.assertIn(base_doc, docs)

        # Should contain GraphRAG relationship doc
        graph_docs = [d for d in docs if d.metadata.get("type") == "relationship"]
        self.assertEqual(len(graph_docs), 1)

        # Validate relationship content
        self.assertIn("Connection:", graph_docs[0].page_content)
        self.assertIn("Sonic", graph_docs[0].page_content)
        self.assertIn("Tails", graph_docs[0].page_content)

    def test_retrieve_node_calls_graphrag(self):
        """Ensure retrieve_node asks the scoped knowledge graph about the question, two hops out."""
        sources = self._sources(graph=["Sonic --(Best Friend)--> Tails"])
        state = _state("Tell me about Sonic")

        asyncio.run(retrieve_node(state, MagicMock()))

        sources.graph.assert_called_once_with("Tell me about Sonic", ["Affiliate_A"], hops=2)

    def test_retrieve_node_reads_only_the_callers_knowledge_bases(self):
        """The isolation rule: both searches get the person's scope and nothing wider. (Finding an entity named with a typo is the graph's job now: see backend/tests/test_graph_scoped.py.)"""
        sources = self._sources()
        asyncio.run(retrieve_node(_state("Tell me about Sonic the Hedgehg?", target_scope=["Affiliate_A", "Affiliate_B"]), MagicMock()))
        sources.vector.assert_called_once_with("Tell me about Sonic the Hedgehg?", ["Affiliate_A", "Affiliate_B"], top_k=3)
        sources.graph.assert_called_once_with("Tell me about Sonic the Hedgehg?", ["Affiliate_A", "Affiliate_B"], hops=2)

    def test_retrieve_node_with_no_scope_asks_the_graph_about_no_knowledge_base(self):
        sources = self._sources()
        asyncio.run(retrieve_node(_state("Tell me about Sonic", target_scope=None), MagicMock()))
        sources.graph.assert_called_once_with("Tell me about Sonic", [], hops=2)

    @patch("backend.services.agent_workflow.lite_llm", new=MockLLM())
    def test_rewrite_node_updates_last_message(self):
        # Arrange: initial state
        state = {
            "messages": [HumanMessage(content="who is goku?")],
            "question": "who is goku?",
            "loop_count": 0,
            "username": "jack_admin",
            "target_scope": "Affiliate_B"
        }

        # Act
        new_state = rewrite_query_node(state)

        # Assert: last HumanMessage should be replaced
        assert isinstance(new_state["messages"][-1], HumanMessage)
        assert new_state["messages"][-1].content == "Rewritten: facts about Goku in DBZ"

        # Assert: question field updated
        assert new_state["question"] == "Rewritten: facts about Goku in DBZ"

    # ==========================================
    # 5. TESTING THE REWRITE FALLBACK (backend/utils/fallback_utils.py)
    # ==========================================
    def _run_fallback(self, grade, chat_sessions, saved):
        """rewrite_fallback as the chat endpoint runs it, with the three nodes and the model stream replaced. Returns the SSE lines it yielded and the mock stream model."""
        state = {"messages": [HumanMessage(content="who is goku?")], "username": "jack", "target_scope": ["Affiliate_B"], "documents": [], "relevance_grade": "no", "loop_count": 0}

        async def fake_stream(prompt):
            yield "TOKEN1"
            yield "TOKEN2"

        stream_llm = MagicMock()
        stream_llm.astream = MagicMock(side_effect=fake_stream)
        patches = {
            "rewrite_query_node": MagicMock(return_value={"question": "Rewritten: goku super saiyan", "messages": [HumanMessage(content="Rewritten: goku super saiyan")]}),
            "retrieve_node": AsyncMock(return_value={"documents": ["doc1", "doc2"]}),
            "grading_node": AsyncMock(return_value={"relevance_grade": grade}),
            "format_docs": MagicMock(return_value="DOCS"),
            "format_history_as_text": MagicMock(return_value="HISTORY"),
            "get_system_prompt": MagicMock(return_value="SYSTEM {context} {history} {question}"),
            "get_stream_llm": MagicMock(return_value=stream_llm),
        }

        async def collect():
            return [chunk async for chunk in rewrite_fallback(MagicMock(), state, "jack", "s1", chat_sessions, saved)]

        with ExitStack() as stack:
            for name, mock in patches.items():
                stack.enter_context(patch(f"backend.utils.fallback_utils.{name}", mock))
            chunks = asyncio.run(collect())
        return chunks, stream_llm

    @staticmethod
    def _events(chunks):
        return [json.loads(chunk[len("data: "):]) for chunk in chunks]

    def test_rewrite_fallback_streams_the_answer_and_keeps_the_turn(self):
        chat_sessions = {"jack::s1": [HumanMessage(content="who is goku?")]}
        saved = MagicMock()

        chunks, stream_llm = self._run_fallback("yes", chat_sessions, saved)
        events = self._events(chunks)

        self.assertEqual([e["text"] for e in events if e["event"] == "token"], ["TOKEN1", "TOKEN2"])
        self.assertEqual(events[-1], {"event": "final_generation", "text": "TOKEN1TOKEN2"})
        self.assertIn("Rewritten: goku super saiyan", stream_llm.astream.call_args.args[0])          # the question the model answers is the rewritten one
        history = chat_sessions["jack::s1"]
        self.assertEqual([m.content for m in history[-2:]], ["Rewritten: goku super saiyan", "TOKEN1TOKEN2"])
        saved.assert_called_once()

    def test_rewrite_fallback_asks_before_searching_the_web_when_the_documents_do_not_answer(self):
        chat_sessions = {"jack::s1": [HumanMessage(content="who is goku?")]}
        saved = MagicMock()

        chunks, stream_llm = self._run_fallback("no", chat_sessions, saved)
        events = self._events(chunks)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "final_generation")
        self.assertIn("break RAG restrictions", events[0]["text"])
        stream_llm.astream.assert_not_called()                                                       # nothing is answered from outside the knowledge base without a yes
        self.assertIsInstance(chat_sessions["jack::s1"][-1], AIMessage)
        saved.assert_called_once()                                                                   # the question for the person is saved BEFORE it is streamed

    def test_rewrite_fallback_without_a_conversation_says_so(self):
        saved = MagicMock()
        chunks, stream_llm = self._run_fallback("yes", {}, saved)
        self.assertEqual(self._events(chunks), [{"event": "final_generation", "text": "No conversation context available."}])
        stream_llm.astream.assert_not_called()
        saved.assert_not_called()

    def test_detect_strategy_lexical(self):
        assert _detect_routing_strategy("latest release date") == "lexical"

    def test_detect_strategy_hybrid(self):
        assert _detect_routing_strategy("compare sonic vs shadow") == "hybrid"

    def test_detect_strategy_hybrid_long_query(self):
        assert _detect_routing_strategy("Who is Sonic and how does his relationship with Shadow progress the narrative in Sonic Adventure 2?") == "hybrid"

    def test_detect_strategy_vector(self):
        assert _detect_routing_strategy("tell me about sonic") == "vector"



if __name__ == "__main__":
    unittest.main()
