# ══════════════════════════════════════════════════════════
# app/routers/chat.py — WebSocket + Tracking endpoints
# FIX: Auto-reply when no admin is online
# FIX: SW and manifest served from root
# ══════════════════════════════════════════════════════════

import json
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal, Visit, ChatMessage
from app.ws_manager import manager

router = APIRouter()


# ─── SCHEMAS ───

class TrackEvent(BaseModel):
    action: str
    ref: Optional[str] = "directo"
    nombre: Optional[str] = ""
    cargo: Optional[str] = ""
    timestamp: Optional[str] = None
    userAgent: Optional[str] = None
    referrer: Optional[str] = None


# ─── PWA: Serve manifest.json and sw.js from ROOT ───

@router.get("/manifest.json")
async def serve_manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@router.get("/sw.js")
async def serve_sw():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


# ─── TRACKING ENDPOINT ───

@router.post("/api/track")
async def track_visit(event: TrackEvent, request: Request):
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


# ─── AUTO-REPLY MESSAGES ───

AUTO_REPLIES = [
    {
        "keywords": ["precio", "costo", "cuanto", "cuánto", "cobran", "tarifa"],
        "reply": "El precio depende del tamaño de su colegio. Generalmente es S/ 0.50 por colegiado activo al mes, con un mínimo de S/ 300. Le puedo dar una cotización exacta si me indica cuántos colegiados tienen. 😊",
    },
    {
        "keywords": ["demo", "demostración", "mostrar", "ver", "probar"],
        "reply": "¡Claro! Puede ver los videos demo aquí mismo en esta página, y si desea una demostración en vivo personalizada, podemos coordinar una reunión virtual. ¿Qué día le conviene?",
    },
    {
        "keywords": ["factura", "boleta", "sunat", "comprobante", "electrónic"],
        "reply": "Sí, ColegiosPro incluye facturación electrónica completa: boletas, facturas, notas de crédito, todo validado por SUNAT en tiempo real. Ya funciona en producción con el Colegio de Contadores de Loreto.",
    },
    {
        "keywords": ["tiempo", "demora", "cuándo", "cuando", "plazo", "implementar"],
        "reply": "La implementación básica toma entre 1-2 semanas. Incluye configuración, migración de datos del padrón de colegiados, capacitación al personal, y puesta en marcha de facturación electrónica.",
    },
]

FALLBACK_REPLY = "Gracias por su mensaje. En este momento no estoy conectado, pero le responderé a la brevedad. También puede escribirme a duilio@perusistemas.com o al WhatsApp. 📱"
FIRST_AUTO_REPLY = "Gracias por escribir. Duilio no está conectado en este momento, pero recibirá su mensaje y le responderá pronto. Mientras tanto, puede explorar los videos demo en esta página. 👇"


def get_auto_reply(text: str, message_count: int) -> str:
    """Generate contextual auto-reply based on keywords"""
    text_lower = text.lower()

    # Check keyword matches
    for rule in AUTO_REPLIES:
        for kw in rule["keywords"]:
            if kw in text_lower:
                return rule["reply"]

    # First message gets a specific reply, subsequent get generic
    if message_count <= 1:
        return FIRST_AUTO_REPLY
    return FALLBACK_REPLY


# ─── VISITOR WEBSOCKET ───

@router.websocket("/ws/chat")
async def ws_visitor(websocket: WebSocket):
    params = websocket.query_params
    ref = params.get("ref", "directo")
    nombre = params.get("nombre", "")
    cargo = params.get("cargo", "")
    session_id = f"{ref}-{uuid.uuid4().hex[:8]}"

    await manager.connect_visitor(websocket, session_id, ref, nombre, cargo)

    # Track connection
    db = SessionLocal()
    try:
        db.add(Visit(ref=ref, nombre=nombre, cargo=cargo, action="ws_connected",
                      ip=websocket.client.host if websocket.client else ""))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    # Count messages for this session (for auto-reply logic)
    visitor_msg_count = 0

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                text = msg.get("text", "")
            except json.JSONDecodeError:
                text = data

            if text.strip():
                visitor_msg_count += 1

                # Save to DB
                db = SessionLocal()
                try:
                    db.add(ChatMessage(
                        session_id=session_id,
                        ref=ref, nombre=nombre, cargo=cargo,
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

                # ══════ AUTO-REPLY if no admin is connected ══════
                if len(manager.admins) == 0:
                    import asyncio
                    # Simulate typing delay
                    await manager.send_to_visitor(session_id, {"type": "typing"})
                    await asyncio.sleep(1.5)

                    reply = get_auto_reply(text, visitor_msg_count)

                    # Save auto-reply to DB
                    db = SessionLocal()
                    try:
                        db.add(ChatMessage(
                            session_id=session_id,
                            ref=ref, nombre=nombre, cargo=cargo,
                            sender="admin",
                            content=reply,
                        ))
                        db.commit()
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()

                    # Send to visitor
                    await manager.send_to_visitor(session_id, {
                        "type": "message",
                        "text": reply,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })

    except WebSocketDisconnect:
        manager.disconnect_visitor(session_id)


# ─── ADMIN WEBSOCKET ───

@router.websocket("/ws/admin")
async def ws_admin(websocket: WebSocket):
    params = websocket.query_params
    admin_key = params.get("key", "")
    # TODO: Use environment variable: os.environ.get("ADMIN_CHAT_KEY")
    expected_key = "duilio-admin-2026"
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
                session_id = msg.get("session_id", "")
                text = msg.get("text", "")
                if session_id and text:
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

                    await manager.admin_message(session_id, text)

            elif msg_type == "typing":
                session_id = msg.get("session_id", "")
                if session_id:
                    await manager.admin_typing(session_id)

    except WebSocketDisconnect:
        manager.disconnect_admin(websocket)


# ─── REST ENDPOINTS ───

@router.get("/api/chat/stats")
async def chat_stats():
    return manager.get_stats()


@router.get("/api/chat/history/{session_id}")
async def chat_history(session_id: str):
    db = SessionLocal()
    try:
        msgs = db.query(ChatMessage).filter(
            ChatMessage.session_id == session_id
        ).order_by(ChatMessage.created_at).all()
        return [
            {"id": m.id, "sender": m.sender, "content": m.content,
             "created_at": m.created_at.isoformat() if m.created_at else None}
            for m in msgs
        ]
    finally:
        db.close()


@router.get("/api/chat/sessions")
async def chat_sessions():
    db = SessionLocal()
    try:
        from sqlalchemy import func, desc
        sessions = db.query(
            ChatMessage.session_id,
            ChatMessage.ref,
            ChatMessage.nombre,
            ChatMessage.cargo,
            func.max(ChatMessage.created_at).label("last_at"),
            func.count(ChatMessage.id).label("msg_count"),
        ).group_by(
            ChatMessage.session_id
        ).order_by(desc("last_at")).all()

        return [
            {"session_id": s.session_id, "ref": s.ref, "nombre": s.nombre,
             "cargo": s.cargo, "last_at": s.last_at.isoformat() if s.last_at else None,
             "msg_count": s.msg_count, "is_online": s.session_id in manager.visitors}
            for s in sessions
        ]
    finally:
        db.close()


@router.get("/api/visits")
async def get_visits(limit: int = 50):
    db = SessionLocal()
    try:
        visits = db.query(Visit).order_by(Visit.created_at.desc()).limit(limit).all()
        return [
            {"id": v.id, "ref": v.ref, "nombre": v.nombre, "cargo": v.cargo,
             "action": v.action, "ip": v.ip,
             "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in visits
        ]
    finally:
        db.close()