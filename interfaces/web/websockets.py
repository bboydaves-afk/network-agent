"""WebSocket handlers for real-time data streaming and AI chat."""

import asyncio
import json
import logging
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections grouped by channel."""

    def __init__(self):
        self.active_connections: dict[str, Set[WebSocket]] = {
            "metrics": set(),
            "alerts": set(),
            "logs": set(),
            "automation": set(),
            "syslog": set(),
        }

    async def connect(self, websocket: WebSocket, channel: str):
        """Accept a WebSocket and register it under *channel*."""
        await websocket.accept()
        if channel not in self.active_connections:
            self.active_connections[channel] = set()
        self.active_connections[channel].add(websocket)
        logger.info("WebSocket connected to channel '%s' (%d total)", channel, len(self.active_connections[channel]))

    def disconnect(self, websocket: WebSocket, channel: str):
        """Remove a WebSocket from its channel."""
        self.active_connections.get(channel, set()).discard(websocket)
        logger.info("WebSocket disconnected from channel '%s'", channel)

    async def broadcast(self, channel: str, data: dict):
        """Send *data* as JSON to every connection on *channel*.

        Dead connections are silently removed.
        """
        dead: Set[WebSocket] = set()
        for ws in self.active_connections.get(channel, set()):
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        if dead:
            self.active_connections[channel] -= dead

    async def send_personal(self, websocket: WebSocket, data: dict):
        """Send a JSON message to a single WebSocket."""
        try:
            await websocket.send_json(data)
        except Exception:
            pass

    @property
    def connection_counts(self) -> dict:
        """Return the number of active connections per channel."""
        return {ch: len(conns) for ch, conns in self.active_connections.items()}


manager = ConnectionManager()


def setup_websockets(app: FastAPI):
    """Register all WebSocket endpoints on the FastAPI *app*."""

    # ------------------------------------------------------------------
    # Metrics stream
    # ------------------------------------------------------------------
    @app.websocket("/ws/metrics")
    async def metrics_ws(websocket: WebSocket):
        await manager.connect(websocket, "metrics")
        try:
            while True:
                # Keep-alive: client can send pings or we just wait
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            manager.disconnect(websocket, "metrics")
        except Exception:
            manager.disconnect(websocket, "metrics")

    # ------------------------------------------------------------------
    # Alerts stream
    # ------------------------------------------------------------------
    @app.websocket("/ws/alerts")
    async def alerts_ws(websocket: WebSocket):
        await manager.connect(websocket, "alerts")
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            manager.disconnect(websocket, "alerts")
        except Exception:
            manager.disconnect(websocket, "alerts")

    # ------------------------------------------------------------------
    # Log stream
    # ------------------------------------------------------------------
    @app.websocket("/ws/logs")
    async def logs_ws(websocket: WebSocket):
        await manager.connect(websocket, "logs")
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            manager.disconnect(websocket, "logs")
        except Exception:
            manager.disconnect(websocket, "logs")

    # ------------------------------------------------------------------
    # Syslog stream
    # ------------------------------------------------------------------
    @app.websocket("/ws/syslog")
    async def syslog_ws(websocket: WebSocket):
        await manager.connect(websocket, "syslog")
        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            manager.disconnect(websocket, "syslog")
        except Exception:
            manager.disconnect(websocket, "syslog")

    # ------------------------------------------------------------------
    # AI Chat
    # ------------------------------------------------------------------
    @app.websocket("/ws/chat")
    async def chat_ws(websocket: WebSocket):
        await websocket.accept()
        # Lazy-import to avoid circular dependency at module scope
        try:
            from interfaces.ai_agent.agent import NetworkAIAgent
            from interfaces.web.app import ctx

            import os
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set in environment")
            agent = NetworkAIAgent(
                api_key=api_key,
                db=ctx.db,
                credential_manager=ctx.cred_manager,
                config_manager=ctx.config_manager,
                monitor=ctx.monitor,
                discovery=ctx.discovery,
                troubleshooter=ctx.troubleshooter,
            )
        except Exception as init_err:
            await websocket.send_json({
                "role": "system",
                "content": f"AI agent initialisation failed: {init_err}",
            })
            await websocket.close()
            return

        try:
            while True:
                data = await websocket.receive_json()
                user_message = data.get("content", data.get("message", ""))
                if not user_message:
                    continue

                # Send "thinking" indicator
                await websocket.send_json({
                    "role": "assistant",
                    "content": "",
                    "status": "thinking",
                })

                try:
                    response = await agent.chat(user_message)
                    assistant_content = (
                        response.get("message", str(response))
                        if isinstance(response, dict)
                        else str(response)
                    )
                except Exception as agent_err:
                    assistant_content = f"Error processing request: {agent_err}"

                await websocket.send_json({
                    "role": "assistant",
                    "content": assistant_content,
                    "status": "complete",
                })

        except WebSocketDisconnect:
            logger.info("Chat WebSocket disconnected")
        except Exception as exc:
            logger.exception("Chat WebSocket error: %s", exc)
