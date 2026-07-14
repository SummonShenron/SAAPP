from langgraph.graph import StateGraph, START, END
from backend.state.graph_state import GraphState

from backend.services.agent_workflow import data_snapshot_node, activity_classifier_node, pattern_detector_node, trend_analyzer_node, insight_generator_node, formatter_node, generate_node, insight_formatter_node

def create_insight_workflow():
    workflow = StateGraph(GraphState)

    workflow.add_node("snapshot_node", data_snapshot_node)
    workflow.add_node("classifier_node", activity_classifier_node)
    workflow.add_node("pattern_node", pattern_detector_node)
    workflow.add_node("trend_node", trend_analyzer_node)
    workflow.add_node("insight_node", insight_generator_node)
    workflow.add_node("insight_formatter_node", insight_formatter_node)
    

    workflow.add_edge(START, "snapshot_node")
    workflow.add_edge("snapshot_node", "classifier_node")
    workflow.add_edge("classifier_node", "pattern_node")
    workflow.add_edge("pattern_node", "trend_node")
    workflow.add_edge("trend_node", "insight_node")
    workflow.add_edge("insight_node", "insight_formatter_node")
    workflow.add_edge("insight_formatter_node", END)

    return workflow.compile()
