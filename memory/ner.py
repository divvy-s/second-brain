from __future__ import annotations

import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Entity:
    label: str
    value: str
    normalized_value: str
    confidence: float = 1.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class EntityExtractor:
    def __init__(self, model_name: str = "en_core_web_sm") -> None:
        self.model_name = model_name
        self.nlp = self._load_spacy(model_name)

    def _load_spacy(self, model_name: str):
        try:
            import spacy

            return spacy.load(model_name)
        except Exception:
            return None

    def extract(self, text: str) -> list[Entity]:
        seen: set[tuple[str, str]] = set()
        entities: list[Entity] = []
        if self.nlp is not None:
            doc = self.nlp(text)
            for ent in doc.ents:
                normalized = ent.text.strip().lower()
                key = (ent.label_, normalized)
                if key not in seen:
                    seen.add(key)
                    entities.append(Entity(ent.label_, ent.text.strip(), normalized, 0.95))
        for entity in self._regex_entities(text):
            key = (entity.label, entity.normalized_value)
            if key not in seen:
                seen.add(key)
                entities.append(entity)
        return entities

    def _regex_entities(self, text: str) -> list[Entity]:
        entities: list[Entity] = []
        for match in re.finditer(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text):
            value = match.group(0)
            entities.append(Entity("EMAIL", value, value.lower(), 0.98))
        for match in re.finditer(r"https?://[^\s)]+", text):
            value = match.group(0)
            entities.append(Entity("URL", value, value.lower(), 0.98))
        for match in re.finditer(r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)day\b|\b(?:today|tomorrow|yesterday)\b", text, re.I):
            value = match.group(0)
            entities.append(Entity("DATE_REF", value, value.lower(), 0.75))
        for match in re.finditer(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b", text):
            value = match.group(0)
            sentence_start = match.start() == 0 or text[max(0, match.start() - 2):match.start()].endswith((". ", "! ", "? ", "\n"))
            is_single_word = " " not in value
            if sentence_start and is_single_word:
                continue
            if value.lower() not in {"the", "and", "for"}:
                entities.append(Entity("PROPER_NOUN", value, value.lower(), 0.65))
        return entities

