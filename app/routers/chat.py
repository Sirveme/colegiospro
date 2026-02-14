# ══════════════════════════════════════════════════════════
# app/routers/chat.py — WebSocket + Tracking endpoints
# ══════════════════════════════════════════════════════════

import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal, Visit, ChatMessage
from app.ws_manager import manager

router = APIRouter()


# ─── SCHEMAS ───

class TrackEvent(BaseModel):
    action: str                    # page_view, chat_opened, pwa_installed, message_sent
    ref: Optional[str] = "directo"
    nombre: Optional[str] = ""
    cargo: Optional[str] = ""
    timestamp: Optional[str] = None
    userAgent: Optional[str] = None
    referrer: Optional[str] = None


class AdminReply(BaseModel):
    session_id: str
    text: str


# ─── TRACKING ENDPOINT ───

@router.post("/api/track")
async def track_visit(event: TrackEvent, request: Request):
    """Log visitor actions: page_view, chat_opened, pwa_installed"""
    db = SessionLocal()
    try:
        visit = Visit(
            ref=event.ref or "directo",
            nombre=event.nombre or "",
            cargo=event.cargo or "",
            action=event.action,
            ip=request.client.host if request.client else "",
            user_agent=event.userAgent or "",
            referrer=event.referrer or "",
        )
        db.add(visit)
        db.commit()

        # If it's a notable event, notify admin
        if event.action in ("chat_opened", "pwa_installed"):
            await manager.broadcast_to_admins({
                "type": "track_event",
                "action": event.action,
                "ref": event.ref,
                "nombre": event.nombre,
                "cargo": event.cargo,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}
    finally:
        db.close()


# ─── VISITOR WEBSOCKET ───

@router.websocket("/ws/chat")
async def ws_visitor(websocket: WebSocket):
    """
    WebSocket for landing page visitors.
    Query params: ?ref=CIP-LORETO&nombre=Ing.+Garcia&cargo=Decano
    """
    params = websocket.query_params
    ref = params.get("ref", "directo")
    nombre = params.get("nombre", "")
    cargo = params.get("cargo", "")
    session_id = f"{ref}-{uuid.uuid4().hex[:8]}"

    await manager.connect_visitor(websocket, session_id, ref, nombre, cargo)

    # Log connection
    db = SessionLocal()
    try:
        db.add(Visit(ref=ref, nombre=nombre, cargo=cargo, action="ws_connected",
                      ip=websocket.client.host if websocket.client else ""))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                text = msg.get("text", "")
            except json.JSONDecodeError:
                text = data

            if text.strip():
                # Save to DB
                db = SessionLocal()
                try:
                    db.add(ChatMessage(
                        session_id=session_id,
                        ref=ref,
                        nombre=nombre,
                        cargo=cargo,
                        sender="visitor",
                        content=text,
                    ))
                    db.commit()
                except Exception:
                    db.rollback()
                finally:
                    db.close()

                # Forward to admin
                await manager.visitor_message(session_id, text)

    except WebSocketDisconnect:
        manager.disconnect_visitor(session_id)


# ─── ADMIN WEBSOCKET ───

@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    """
    WebSocket for admin (Duil).
    Receives visitor list, messages, and connection events.
    Sends messages to specific visitors.

    TODO: Add authentication (API key or token in query params)
    """
    # Simple auth check — replace with proper auth later
    params = websocket.query_params
    admin_key = params.get("key", "")
    # TODO: Use environment variable for this
    expected_key = "duilio-admin-2026"  # CAMBIAR en producción
    if admin_key != expected_key:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    await manager.connect_admin(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "message":
                # Admin sends message to visitor
                session_id = msg.get("session_id", "")
                text = msg.get("text", "")
                if session_id and text:
                    # Save to DB
                    db = SessionLocal()
                    try:
                        db.add(ChatMessage(
                            session_id=session_id,
                            ref=msg.get("ref", ""),
                            nombre=msg.get("nombre", ""),
                            cargo="",
                            sender="admin",
                            content=text,
                        ))
                        db.commit()
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()

                    # Send to visitor
                    await manager.admin_message(session_id, text)

            elif msg_type == "typing":
                session_id = msg.get("session_id", "")
                if session_id:
                    await manager.admin_typing(session_id)

    except WebSocketDisconnect:
        manager.disconnect_admin(websocket)


# ─── REST ENDPOINTS FOR ADMIN ───

@router.get("/api/chat/stats")
async def chat_stats():
    """Current connection stats"""
    return manager.get_stats()


@router.get("/api/chat/history/{session_id}")
async def chat_history(session_id: str):
    """Get message history for a session"""
    db = SessionLocal()
    try:
        messages = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.created_at).all()
        return [
            {
                "id": m.id,
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
    finally:
        db.close()


@router.get("/api/chat/sessions")
async def chat_sessions():
    """List all chat sessions with last message"""
    db = SessionLocal()
    try:
        from sqlalchemy import func, desc
        # Get unique sessions with their latest message
        sessions = db.query(
            ChatMessage.session_id,
            ChatMessage.ref,
            ChatMessage.nombre,
            ChatMessage.cargo,
            func.max(ChatMessage.created_at).label("last_at"),
            func.count(ChatMessage.id).label("msg_count"),
        ).group_by(
            ChatMessage.session_id
        ).order_by(
            desc("last_at")
        ).all()

        return [
            {
                "session_id": s.session_id,
                "ref": s.ref,
                "nombre": s.nombre,
                "cargo": s.cargo,
                "last_at": s.last_at.isoformat() if s.last_at else None,
                "msg_count": s.msg_count,
                "is_online": s.session_id in manager.visitors,
            }
            for s in sessions
        ]
    finally:
        db.close()


@router.get("/api/visits")
async def get_visits(limit: int = 50):
    """Recent visitor tracking events"""
    db = SessionLocal()
    try:
        visits = db.query(Visit).order_by(Visit.created_at.desc()).limit(limit).all()
        return [
            {
                "id": v.id,
                "ref": v.ref,
                "nombre": v.nombre,
                "cargo": v.cargo,
                "action": v.action,
                "ip": v.ip,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in visits
        ]
    finally:
        db.close()