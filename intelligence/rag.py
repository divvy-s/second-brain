from __future__ import annotations

from dataclasses import dataclass

from intelligence.llm_adapter import LLMAdapter, LLMRequest, LLMUnavailable
from memory.retrieval import HybridRetriever


@dataclass(frozen=True)
class RAGAnswer:
    answer: str
    citations: list[str]
    used_llm: bool


class ContextAwareRAG:
    def __init__(self, retriever: HybridRetriever, llm: LLMAdapter) -> None:
        self.retriever = retriever
        self.llm = llm

    async def answer(self, question: str, limit: int = 8) -> RAGAnswer:
        result = self.retriever.retrieve(question, limit=limit)
        hits = result.hits
        context = self._format_context(result)
        messages = [
            {
                "role": "system",
                "content": (
                    "Answer using only the provided Second Brain context. "
                    "If the context is insufficient, say what is missing. Cite event ids."
                ),
            },
            {"role": "user", "content": f"Question:\n{question}\n\nContext:\n{context}"},
        ]
        try:
            response = await self.llm.complete(LLMRequest(messages=messages, temperature=0.2, max_tokens=900))
            return RAGAnswer(response.content, [hit.event.id for hit in hits], True)
        except LLMUnavailable:
            if not hits:
                return RAGAnswer("I do not have enough stored context to answer that yet.", [], False)
            summary = " ".join(f"[{hit.event.id}] {hit.event.title}: {hit.event.body}" for hit in hits[:3])
            return RAGAnswer(summary, [hit.event.id for hit in hits], False)

    def _format_context(self, result) -> str:
        lines = []
        for hit in result.hits:
            event = hit.event
            lines.append(
                f"event_id={event.id}; score={hit.score:.3f}; priority={hit.priority_score:.3f}; source={event.source}; "
                f"occurred_at={event.occurred_at.isoformat()}; title={event.title}; body={event.body}"
            )
        structured = result.structured.to_dict()
        if any(structured.values()):
            lines.append(f"structured_context={structured}")
        return "\n".join(lines)

