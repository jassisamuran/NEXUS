# app/streaming/websocket.py
import json
import asyncio
import redis.asyncio as aioredis
from fastapi import WebSocket
from typing import Dict
from app.config import settings

class WebSocketManager:
    """
    Manages WebSocket connections and streams agent events via Redis pub/sub.
    Each task gets its own Redis channel.
    Architecture: Agent → Redis publish → WebSocket → Browser
    """

    def __init__(self):
        self.connections: Dict[str, list] = {} 

    async def connect(self, websocket: WebSocket, task_id: str):
        await websocket.accept()
        if task_id not in self.connections:
            self.connections[task_id] = []
        self.connections[task_id].append(websocket)

    def disconnect(self, websocket: WebSocket, task_id: str):
        if task_id in self.connections:
            try:
                self.connections[task_id].remove(websocket)
            except ValueError:
                pass

    async def broadcast(self, task_id: str, message: dict):
        """Send message to all WebSocket clients watching this task."""
        if task_id not in self.connections:
            return
        disconnected = []
        for ws in self.connections[task_id]:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws, task_id)

    async def listen_and_forward(self, task_id: str, websocket: WebSocket):
        """
        Subscribe to Redis channel for this task and forward messages to WebSocket.
        This runs as a background coroutine while the WebSocket is connected.
        """
        redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        channel = f"task:{task_id}:events"
        await pubsub.subscribe(channel)

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        await websocket.send_json(data)

                        if data.get("event_type") in ("COMPLETE", "FAILED"):
                            await asyncio.sleep(0.2)
                            # await websocket.close()
                            break
                    except Exception:
                        break
        finally:
            await pubsub.unsubscribe(channel)
            await redis.aclose()

ws_manager = WebSocketManager()

def publish_event(task_id: str, event_type: str, agent: str, message: str, data: dict = None):
    """
    Synchronous function called from Celery worker to publish events.
    This is the bridge between AutoGen (sync) and WebSocket (async).
    """
    import redis as sync_redis
    import json
    from datetime import datetime

    r = sync_redis.from_url(settings.REDIS_URL, decode_responses=True)
    channel = f"task:{task_id}:events"

    payload = json.dumps({
        "event_type": event_type,
        "agent": agent,
        "message": message,
        "timestamp": datetime.utcnow().isoformat(),
        "data": data or {},
    })

    r.publish(channel, payload)
    r.close()