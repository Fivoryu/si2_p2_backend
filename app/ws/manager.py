import asyncio
import json
from collections import defaultdict

import redis.asyncio as aioredis
from fastapi import WebSocket

from ..core.config import settings


def channel(tenant_id: str, incident_id: str) -> str:
    return f"ws:tenant:{tenant_id}:incident:{incident_id}"


class WSManager:
    def __init__(self):
        self.local: dict[str, set[WebSocket]] = defaultdict(set)
        self.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        self.pubsub = self.redis.pubsub()
        self._listener_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, tenant_id: str, incident_id: str):
        await ws.accept()
        ch = channel(tenant_id, incident_id)
        async with self._lock:
            if not self.local[ch]:
                await self.pubsub.subscribe(ch)
                if self._listener_task is None:
                    self._listener_task = asyncio.create_task(self._listen())
            self.local[ch].add(ws)

    async def disconnect(self, ws: WebSocket, tenant_id: str, incident_id: str):
        ch = channel(tenant_id, incident_id)
        async with self._lock:
            self.local[ch].discard(ws)
            if not self.local[ch]:
                try:
                    await self.pubsub.unsubscribe(ch)
                except Exception:
                    pass

    async def publish(self, tenant_id: str, incident_id: str, event: dict):
        await self.redis.publish(
            channel(tenant_id, incident_id), json.dumps(event, default=str)
        )

    async def _listen(self):
        async for msg in self.pubsub.listen():
            if msg["type"] != "message":
                continue
            ch, payload = msg["channel"], msg["data"]
            dead = []
            for ws in list(self.local.get(ch, [])):
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.local[ch].discard(ws)


manager = WSManager()
