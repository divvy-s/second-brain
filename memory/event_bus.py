from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from connectors.base import ContextEvent


class EventBus:
    def __init__(self, redis_url: str, stream_name: str = "second_brain.events") -> None:
        self.redis_url = redis_url
        self.stream_name = stream_name
        self._memory_streams: dict[str, list[tuple[str, dict[str, str]]]] = defaultdict(list)
        self._redis = self._connect_redis(redis_url)

    def _connect_redis(self, redis_url: str) -> Any | None:
        try:
            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            client.ping()
            return client
        except Exception:
            return None

    @property
    def is_redis_backed(self) -> bool:
        return self._redis is not None

    def publish(self, event: ContextEvent, stream_name: str | None = None) -> str:
        stream = stream_name or self.stream_name
        payload = {"event": json.dumps(event.to_dict(), separators=(",", ":"))}
        if self._redis is not None:
            return str(self._redis.xadd(stream, payload))
        event_id = f"{len(self._memory_streams[stream]) + 1}-0"
        self._memory_streams[stream].append((event_id, payload))
        return event_id

    def read_entries(
        self,
        last_id: str = "0-0",
        count: int = 100,
        stream_name: str | None = None,
        block_ms: int | None = None,
    ) -> list[tuple[str, ContextEvent]]:
        stream = stream_name or self.stream_name
        messages: list[tuple[str, dict[str, str]]]
        if self._redis is not None:
            raw = self._redis.xread({stream: last_id}, count=count, block=block_ms)
            messages = []
            for _, entries in raw:
                messages.extend(entries)
        else:
            messages = [entry for entry in self._memory_streams.get(stream, []) if entry[0] > last_id][:count]
        return [
            (message_id, ContextEvent.from_dict(json.loads(payload["event"])))
            for message_id, payload in messages
        ]

    def read(
        self,
        last_id: str = "0-0",
        count: int = 100,
        stream_name: str | None = None,
        block_ms: int | None = None,
    ) -> list[ContextEvent]:
        return [event for _, event in self.read_entries(last_id, count, stream_name, block_ms)]

