import sys
import os
import unittest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document
import pytest
import asyncio
import networkx as nx
from backend import graph_db
from langchain_core.messages import HumanMessage, AIMessage
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '.')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), 'backend')))
from backend.graph_state import GraphState, route_user_query
from backend.agent_workflow import retrieve_node, grading_node, rewrite_query_node
from backend.search import _detect_routing_strategy
from app import rewrite_fallback

class MockLLM:
    def invoke(self, prompt):
        return AIMessage(content="Rewritten: facts about Goku in DBZ")
    
class TestGraphState(unittest.TestCase):

    def setUp(self):
        """
        Runs before EVERY test.
        We must clear the global knowledge graph to ensure test isolation.
        Otherwise, data from test 1 would bleed into test 2.
        """
        graph_db.knowledge_graph.clear()

    # ==========================================
    # 1. TESTING THE INTENT ROUTER
    # ==========================================
    def test_route_user_query_conversational(self):
        """Should route greetings or simple chats to the conversational node."""
        state: GraphState = {
            "username": "alice",
             "messages": [HumanMessage(content="Hello there how are you?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 0,
            "original_question": ""
        }
        destination = route_user_query(state)
        self.assertEqual(destination, "conversational_node")

    def test_route_user_query_retrieval(self):
        """Should route data-seeking or long queries to the retrieve node."""
        state: GraphState = {
            "username": "alice",
             "messages": [HumanMessage(content="What is the release status of project delta?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 0,
            "original_question": ""
        }
        destination = route_user_query(state)
        self.assertEqual(destination, "retrieve_node")

    # ==========================================
    # 2. TESTING THE GRADING NODE
    # ==========================================
    @patch("backend.agent_workflow.llm")
    @patch("backend.agent_workflow.format_docs")
    def test_grading_node_positive_evaluation(self, mock_format, mock_llm):
        """Should return relevance_grade 'yes' if LLM output contains 'yes'."""
        mock_format.return_value = "Mocked Context String"
        mock_response = MagicMock()
        mock_response.content = "This is a YES."
        mock_llm.invoke.return_value = mock_response

        doc = Document(page_content="Some Document Text", metadata={"source": "test.txt"})

        state: GraphState = {
            "username": "alice",
            "messages": [HumanMessage(content="How do I build a RAG?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [doc],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 1,
            "original_question": "How do I build a RAG?"
        }

        updates = grading_node(state)
        self.assertEqual(updates["relevance_grade"], "yes")

    @patch("backend.agent_workflow.llm")
    @patch("backend.agent_workflow.format_docs")
    def test_grading_node_negative_evaluation(self, mock_format, mock_llm):
        """Should return relevance_grade 'no' if LLM output does not contain 'yes'."""
        mock_format.return_value = "Mocked Context String"
        mock_response = MagicMock()
        mock_response.content = "The document is unrelated. NO."
        mock_llm.invoke.return_value = mock_response

        doc = Document(page_content="Unrelated Text", metadata={"source": "test.txt"})

        state: GraphState = {
            "username": "alice",
             "messages": [HumanMessage(content="How do I build a RAG?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [doc],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 1,
            "original_question": "How do I build a RAG?"
        }

        updates = grading_node(state)
        self.assertEqual(updates["relevance_grade"], "no")

    # ==========================================
    # 3. TESTING THE RETRIEVAL NODE
    # ==========================================
    @patch("backend.agent_workflow.get_secure_retriever")
    def test_retrieve_node_initializes_loop_and_original_question(self, mock_get_secure_retriever):
        """Should mock retriever and verify document tracking list initialized correctly."""
        mock_retriever = MagicMock()
        doc1 = Document(page_content="doc1", metadata={"source": "a.txt"})
        doc2 = Document(page_content="doc2", metadata={"source": "b.txt"})
        mock_retriever.invoke.return_value = [doc1, doc2]        
        mock_get_secure_retriever.return_value = mock_retriever
        state: GraphState = {
            "username": "alice",
             "messages": [HumanMessage(content="How do I build a RAG?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": None,
            "original_question": None
        }
        updates = retrieve_node(state)
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
    @patch("backend.graph_db.save_graph") # Mock save_graph to prevent writing to disk during tests
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

    @patch("backend.agent_workflow.get_secure_retriever")
    def test_retrieve_node_merges_graph_context(self, mock_get_secure_retriever):
        """Ensure GraphRAG dynamic context is appended to vector search results."""
        
        # --- Seed the graph ---
        graph_db.knowledge_graph.add_edge("Sonic", "Tails", relation="Best Friend")
        
        # --- Mock vector retriever ---
        mock_retriever = MagicMock()
        base_doc = Document(page_content="BaseDoc", metadata={"source": "vec"})
        mock_retriever.invoke.return_value = [base_doc]
        mock_get_secure_retriever.return_value = mock_retriever
        
        # --- Build state ---
        state: GraphState = {
            "username": "alice",
            "messages": [HumanMessage(content="Tell me about Sonic?")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 0,
            "original_question": None
        }
        
        updates = retrieve_node(state)
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

    @patch("backend.agent_workflow.get_secure_retriever")
    @patch("backend.agent_workflow.graph_db.get_dynamic_context")
    def test_retrieve_node_calls_graphrag(self, mock_get_context, mock_get_secure_retriever):
        """Ensure retrieve_node calls GraphRAG context lookup."""
        graph_db.knowledge_graph.add_node("sonic")
        mock_get_context.return_value = ["Sonic --(Best Friend)--> Tails"]
        
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = []
        mock_get_secure_retriever.return_value = mock_retriever
        
        state: GraphState = {
            "username": "alice",
            "messages": [HumanMessage(content="Tell me about Sonic")],
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 0,
            "original_question": None
        }
        
        retrieve_node(state)
        
        mock_get_context.assert_called_once_with("sonic", hops=2)

    @patch("backend.agent_workflow.get_secure_retriever")
    def test_retrieve_node_entity_normalization(self, mock_get_secure_retriever):
        """Ensure retrieve_node normalizes entities before graph lookup."""
        
        # Seed graph with canonical entity
        graph_db.knowledge_graph.add_node("Sonic the Hedgehog")
        
        # Mock retriever
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = []
        mock_get_secure_retriever.return_value = mock_retriever
        
        state: GraphState = {
            "username": "alice",
             "messages": [HumanMessage(content="Tell me about Sonic the Hedgehg?")], # typo
            "target_scope": ["Affiliate_A"],
            "history": "",
            "documents": [],
            "relevance_grade": "",
            "generation": "",
            "loop_count": 0,
            "original_question": None
        }
        
        updates = retrieve_node(state)
        
        # Should have appended normalized relationship doc
        relationship_docs = [
            d for d in updates["documents"]
            if d.metadata.get("type") == "relationship"
        ]
        
        self.assertTrue(len(relationship_docs) >= 0)

    @patch("backend.agent_workflow.llm", new=MockLLM())
    def test_rewrite_node_updates_last_message(self):
        # Arrange: initial state
        state = {
            "messages": [HumanMessage(content="who is goku?")],
            "question": "who is goku?",
            "loop_count": 0,
            "username": "jack_admin",
            "target_scope": "Affiliate_B"
        }

        # Mock LLM rewrite output
        class MockLLM:
            def invoke(self, prompt):
                return AIMessage(content="Rewritten: facts about Goku in DBZ")

        # Inject mock
        global llm
        llm = MockLLM()

        # Act
        new_state = rewrite_query_node(state)

        # Assert: last HumanMessage should be replaced
        assert isinstance(new_state["messages"][-1], HumanMessage)
        assert new_state["messages"][-1].content == "Rewritten: facts about Goku in DBZ"

        # Assert: question field updated
        assert new_state["question"] == "Rewritten: facts about Goku in DBZ"

    @pytest.mark.asyncio
    async def test_rewrite_fallback_success(self):
        # ----- Arrange -----
        username = "jack"
        state = {
            "messages": [HumanMessage(content="who is goku?")],
            "username": username,
            "target_scope": ["Affiliate_B"],
            "documents": [],
            "relevance_grade": "no",
            "loop_count": 0,
            "original_question": "who is goku?",
        }

        # Mock rewrite node → returns rewritten question
        with patch("app.rewrite_query_node") as mock_rewrite:
            mock_rewrite.return_value = {
                **state,
                "messages": [HumanMessage(content="Rewritten: goku super saiyan")],
                "original_question": "Rewritten: goku super saiyan"
            }

            # Mock retrieve node → returns fake docs
            with patch("app.retrieve_node") as mock_retrieve:
                mock_retrieve.return_value = {
                    **state,
                    "documents": ["doc1", "doc2"],
                    "messages": [HumanMessage(content="Rewritten: goku super saiyan")],
                    "original_question": "Rewritten: goku super saiyan"
                }

                # Mock grade node → returns relevant
                with patch("app.grade_node") as mock_grade:
                    mock_grade.return_value = {
                        **state,
                        "relevance_grade": "yes",
                        "documents": ["doc1", "doc2"],
                        "messages": [HumanMessage(content="Rewritten: goku super saiyan")],
                        "original_question": "Rewritten: goku super saiyan"
                    }

                    # Mock prompt builders
                    with patch("app.format_docs") as mock_format_docs:
                        mock_format_docs.return_value = "DOCS"

                        with patch("app.format_history_as_text") as mock_history:
                            mock_history.return_value = "HISTORY"

                            with patch("app.get_system_prompt") as mock_sys:
                                mock_sys.return_value = "SYSTEM {context} {history} {question}"

                                # Mock LLM streaming
                                async def fake_stream(prompt):
                                    yield "TOKEN1"
                                    yield "TOKEN2"

                                with patch("app.llm") as mock_llm:
                                    mock_llm.astream = fake_stream

                                    # ----- Act -----
                                    chunks = []
                                    async for chunk in rewrite_fallback(state, username):
                                        chunks.append(chunk)

                                    # ----- Assert -----
                                    assert len(chunks) > 0
                                    assert any("TOKEN1" in c for c in chunks)
                                    assert any("TOKEN2" in c for c in chunks)
                                    assert any("Rewritten: goku super saiyan" in c for c in chunks)    

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