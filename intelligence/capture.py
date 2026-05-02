from __future__ import annotations

from connectors.base import ContextEvent
from intelligence.priority import PriorityScorer
from memory.ner import EntityExtractor


class BrainDumpCapture:
    def __init__(self, extractor: EntityExtractor, scorer: PriorityScorer) -> None:
        self.extractor = extractor
        self.scorer = scorer

    def capture(self, text: str, source: str = "system", title: str | None = None) -> ContextEvent:
        cleaned = text.strip()
        event = ContextEvent(
            source=source,
            kind="brain_dump",
            title=title or self._title(cleaned),
            body=cleaned,
            entities=[entity.to_dict() for entity in self.extractor.extract(cleaned)],
            metadata={"captured_by": "brain_dump"},
        )
        event.importance = self.scorer.score(event)
        return event

    def _title(self, text: str) -> str:
        first_line = text.splitlines()[0] if text else "Untitled capture"
        return first_line[:90]

