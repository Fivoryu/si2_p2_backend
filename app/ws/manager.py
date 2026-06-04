import asyncio
import json
import uuid
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
        self.instance_id = str(uuid.uuid4())

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
        ch = channel(tenant_id, incident_id)
        await self._broadcast_local(ch, event)
        payload = dict(event)
        payload["_origin"] = self.instance_id
        await self.redis.publish(ch, json.dumps(payload, default=str))

    async def _broadcast_local(self, ch: str, event: dict):
        dead = []
        payload = json.dumps(event, default=str)
        for ws in list(self.local.get(ch, [])):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.local[ch].discard(ws)

    async def broadcast_state_snapshot(
        self, tenant_id: str, incident_id: str, incident_data: dict
    ):
        await self.publish(tenant_id, incident_id, {
            "type": "STATE_SNAPSHOT",
            "incident_id": incident_id,
            "data": incident_data,
        })

    async def broadcast_status_changed(
        self,
        tenant_id: str,
        incident_id: str,
        estado_anterior: str,
        estado_nuevo: str,
        comentario: str | None = None,
    ):
        await self.publish(tenant_id, incident_id, {
            "type": "STATUS_CHANGED",
            "incident_id": incident_id,
            "data": {
                "estado_anterior": estado_anterior,
                "estado_nuevo": estado_nuevo,
                "comentario": comentario,
            },
        })

    async def broadcast_tech_location(
        self,
        tenant_id: str,
        incident_id: str,
        lat: float,
        lng: float,
        tecnico_id: str | None = None,
    ):
        await self.publish(tenant_id, incident_id, {
            "type": "TECH_LOCATION",
            "incident_id": incident_id,
            "data": {"lat": lat, "lng": lng, "tecnico_id": tecnico_id},
        })

    async def broadcast_tech_arrived(
        self, tenant_id: str, incident_id: str, lat: float, lng: float
    ):
        await self.publish(tenant_id, incident_id, {
            "type": "TECH_ARRIVED",
            "incident_id": incident_id,
            "data": {"lat": lat, "lng": lng},
        })

    async def _listen(self):
        async for msg in self.pubsub.listen():
            if msg["type"] != "message":
                continue
            ch, payload = msg["channel"], msg["data"]
            event = json.loads(payload)
            if event.pop("_origin", None) == self.instance_id:
                continue
            await self._broadcast_local(ch, event)


manager = WSManager()
