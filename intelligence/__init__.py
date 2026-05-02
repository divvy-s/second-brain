from intelligence.capture import BrainDumpCapture
from intelligence.goal_decomposer import GoalDecomposer
from intelligence.intent_classifier import IntentClassifier
from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMResponse, LLMUnavailable
from intelligence.priority import PriorityBreakdown, PriorityScorer
from intelligence.recommendations import RecommendationEngine, RecommendationItem

__all__ = [
    "BrainDumpCapture",
    "ContextAwareRAG",
    "GoalDecomposer",
    "IntentClassifier",
    "LLMAdapter",
    "LLMRequest",
    "LLMResponse",
    "LLMUnavailable",
    "PriorityBreakdown",
    "PriorityScorer",
    "RecommendationEngine",
    "RecommendationItem",
]


def __getattr__(name: str):
    if name == "ContextAwareRAG":
        from intelligence.rag import ContextAwareRAG

        return ContextAwareRAG
    raise AttributeError(name)

