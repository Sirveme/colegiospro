# ══════════════════════════════════════════════════════════
# app/ws_manager.py — WebSocket Connection Manager
# Handles visitor ↔ admin real-time communication
# ══════════════════════════════════════════════════════════

import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
from dataclasses import dataclass, field
from fastapi import WebSocket


@dataclass
class VisitorConnection:
    """Represents a connected visitor"""
    websocket: WebSocket
    session_id: str
    ref: str = "directo"
    nombre: str = ""
    cargo: str = ""
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_message_at: Optional[datetime] = None


class ConnectionManager:
    """
    Manages WebSocket connections for:
    - Multiple visitors (identified by session_id)
    - One or more admin connections (Duil)
    """

    def __init__(self):
        # visitor session_id → VisitorConnection
        self.visitors: Dict[str, VisitorConnection] = {}
        # admin connections (Duil can have multiple tabs open)
        self.admins: list[WebSocket] = []

    # ─── VISITOR CONNECTIONS ───

    async def connect_visitor(self, websocket: WebSocket, session_id: str,
                               ref: str = "", nombre: str = "", cargo: str = ""):
        await websocket.accept()
        conn = VisitorConnection(
            websocket=websocket,
            session_id=session_id,
            ref=ref,
            nombre=nombre,
            cargo=cargo,
        )
        self.visitors[session_id] = conn

        # Notify all admin tabs
        await self.broadcast_to_admins({
            "type": "visitor_connected",
            "session_id": session_id,
            "ref": ref,
            "nombre": nombre,
            "cargo": cargo,
            "connected_at": conn.connected_at.isoformat(),
            "total_visitors": len(self.visitors),
        })

    def disconnect_visitor(self, session_id: str):
        if session_id in self.visitors:
            del self.visitors[session_id]
            # Fire-and-forget notification to admins
            asyncio.ensure_future(self.broadcast_to_admins({
                "type": "visitor_disconnected",
                "session_id": session_id,
                "total_visitors": len(self.visitors),
            }))

    async def send_to_visitor(self, session_id: str, data: dict):
        """Send a message to a specific visitor"""
        conn = self.visitors.get(session_id)
        if conn:
            try:
                await conn.websocket.send_json(data)
            except Exception:
                self.disconnect_visitor(session_id)

    # ─── ADMIN CONNECTIONS ───

    async def connect_admin(self, websocket: WebSocket):
        await websocket.accept()
        self.admins.append(websocket)

        # Send current visitor list
        visitors_list = [
            {
                "session_id": sid,
                "ref": v.ref,
                "nombre": v.nombre,
                "cargo": v.cargo,
                "connected_at": v.connected_at.isoformat(),
                "last_message_at": v.last_message_at.isoformat() if v.last_message_at else None,
            }
            for sid, v in self.visitors.items()
        ]
        await websocket.send_json({
            "type": "visitor_list",
            "visitors": visitors_list,
            "total": len(visitors_list),
        })

    def disconnect_admin(self, websocket: WebSocket):
        if websocket in self.admins:
            self.admins.remove(websocket)

    async def broadcast_to_admins(self, data: dict):
        """Send to all admin connections"""
        dead = []
        for ws in self.admins:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.admins.remove(ws)

    # ─── MESSAGE ROUTING ───

    async def visitor_message(self, session_id: str, text: str):
        """Visitor sends a message → forward to all admins"""
        conn = self.visitors.get(session_id)
        if conn:
            conn.last_message_at = datetime.now(timezone.utc)

        await self.broadcast_to_admins({
            "type": "visitor_message",
            "session_id": session_id,
            "ref": conn.ref if conn else "",
            "nombre": conn.nombre if conn else "",
            "cargo": conn.cargo if conn else "",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def admin_message(self, session_id: str, text: str):
        """Admin sends a message → forward to specific visitor"""
        await self.send_to_visitor(session_id, {
            "type": "message",
            "text": text,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def admin_typing(self, session_id: str):
        """Admin is typing → show indicator to visitor"""
        await self.send_to_visitor(session_id, {"type": "typing"})

    # ─── STATUS ───

    def get_stats(self) -> dict:
        return {
            "visitors_online": len(self.visitors),
            "admins_online": len(self.admins),
            "visitors": [
                {
                    "session_id": sid,
                    "ref": v.ref,
                    "nombre": v.nombre,
                    "cargo": v.cargo,
                    "connected_at": v.connected_at.isoformat(),
                }
                for sid, v in self.visitors.items()
            ],
        }


# Singleton instance
manager = ConnectionManager()