# ══════════════════════════════════════════════════════════
# app/routers/push.py — Web Push + panel del jefe + panel público
# ══════════════════════════════════════════════════════════

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import pathlib
import secrets as _secrets

from fastapi import UploadFile, File

from app.database import SessionLocal
from app.models_secretaria import (
    UsuarioSecretaria,
    PushSuscriptor,
    PushMensaje,
    PushMediaBiblioteca,
    DocumentoSecretaria,
)
from app.services.auth_service import get_current_user_id
from app.services.push_service import (
    enviar_push_multi,
    vapid_public_key,
    push_habilitado,
)


UPLOADS_MEDIA_DIR = pathlib.Path("static/img/push-uploads")
UPLOADS_MEDIA_DIR.mkdir(parents=True, exist_ok=True)
EXT_PERMITIDAS = {"jpg", "jpeg", "png", "gif", "webp", "mp3", "ogg", "wav", "m4a"}


router = APIRouter(prefix="/push", tags=["Push"])
templates = Jinja2Templates(directory="app/templates")


def _db():
    return SessionLocal()


def _user(request: Request) -> Optional[UsuarioSecretaria]:
    uid = get_current_user_id(request)
    if not uid:
        return None
    db = _db()
    try:
        return db.query(UsuarioSecretaria).filter(UsuarioSecretaria.id == uid).first()
    finally:
        db.close()


# ─── Suscripción push ───
@router.post("/suscribir")
async def push_suscribir(request: Request):
    """Registra un PushSubscription del navegador. Acepta dos formatos:
    - El nativo: {endpoint, keys:{p256dh, auth}}
    - Con metadatos: {endpoint, keys:{...}, nombre, cargo}
    """
    data = await request.json()
    endpoint = (data.get("endpoint") or "").strip()
    keys = data.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    nombre = (data.get("nombre") or "").strip()[:150]
    cargo = (data.get("cargo") or "").strip()[:100]

    if not endpoint:
        return JSONResponse({"ok": False, "error": "Falta endpoint"}, status_code=400)

    uid = get_current_user_id(request)

    db = _db()
    try:
        existe = db.query(PushSuscriptor).filter(
            PushSuscriptor.endpoint == endpoint
        ).first()
        if existe:
            existe.p256dh = p256dh or existe.p256dh
            existe.auth = auth or existe.auth
            if uid and not existe.secretaria_id:
                existe.secretaria_id = uid
            if nombre:
                existe.nombre = nombre
            if cargo:
                existe.cargo = cargo
            existe.activo = True
            db.commit()
            return JSONResponse({"ok": True, "id": existe.id, "nuevo": False})

        s = PushSuscriptor(
            secretaria_id=uid,
            nombre=nombre,
            cargo=cargo,
            endpoint=endpoint,
            p256dh=p256dh,
            auth=auth,
            activo=True,
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        return JSONResponse({"ok": True, "id": s.id, "nuevo": True})
    finally:
        db.close()


# ─── Enviar push ───
@router.post("/enviar")
async def push_enviar(request: Request):
    """Envía un push a uno o varios suscriptores.
    Body JSON: { titulo, cuerpo, url_destino?, urgente?, a_ids?: [int], a_todos?: bool }
    Si a_ids/a_todos están vacíos, envía a los suscriptores de la secretaria logueada.
    """
    uid = get_current_user_id(request)
    if not uid:
        raise HTTPException(401, "No autenticado")

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not data:
        form = await request.form()
        data = dict(form)

    titulo = (data.get("titulo") or "").strip()[:200]
    cuerpo = (data.get("cuerpo") or "").strip()[:2000]
    url_destino = (data.get("url_destino") or "").strip()[:500]
    urgente = bool(data.get("urgente"))
    a_ids = data.get("a_ids") or []
    a_todos = bool(data.get("a_todos"))

    if not titulo or not cuerpo:
        return JSONResponse(
            {"ok": False, "error": "Título y cuerpo son obligatorios"},
            status_code=400,
        )

    db = _db()
    try:
        q = db.query(PushSuscriptor).filter(PushSuscriptor.activo == True)  # noqa: E712
        if a_ids and not a_todos:
            q = q.filter(PushSuscriptor.id.in_([int(i) for i in a_ids]))
        elif not a_todos:
            # Default: mis suscriptores (los de mi organización)
            q = q.filter(PushSuscriptor.secretaria_id == uid)
        suscriptores = q.all()

        payload = {
            "titulo": titulo,
            "cuerpo": cuerpo,
            "url": url_destino or "/secretaria/",
            "urgente": urgente,
        }

        resultado = enviar_push_multi(suscriptores, payload)

        # Registrar en historial
        msg = PushMensaje(
            de_usuario_id=uid,
            titulo=titulo,
            cuerpo=cuerpo,
            url_destino=url_destino,
            urgente=urgente,
            enviado_a=[s.id for s in suscriptores],
        )
        db.add(msg)
        # Actualizar suscripciones marcadas inactivas en el sender
        for s in suscriptores:
            pass  # ya se mutó en el service
        db.commit()

        return JSONResponse({
            "ok": True,
            "resultado": resultado,
            "total_suscriptores": len(suscriptores),
            "push_habilitado": push_habilitado(),
        })
    finally:
        db.close()


# ─── Panel público simple para servidores externos ───
@router.get("/panel", response_class=HTMLResponse)
async def push_panel_publico(request: Request):
    """Panel ultra-simple, sin login: permite al colaborador/jefe externo
    suscribirse a notificaciones y enviar un mensaje corto a la secretaria."""
    return templates.TemplateResponse(
        request,
        "push_panel.html",
        {
            "vapid_public_key": vapid_public_key(),
        },
    )


@router.post("/panel/enviar-mensaje")
async def push_panel_enviar(request: Request):
    """Recibe mensajes del panel público (colaboradores externos)
    y los envía como push a todas las secretarias registradas."""
    data = await request.json()
    nombre = (data.get("nombre") or "").strip()[:150]
    cargo = (data.get("cargo") or "").strip()[:100]
    texto = (data.get("texto") or "").strip()[:200]

    if not texto:
        return JSONResponse({"ok": False, "error": "Texto vacío"}, status_code=400)

    titulo = f"Mensaje de {nombre or 'un colaborador'}"
    cuerpo = texto + (f" — {cargo}" if cargo else "")

    db = _db()
    try:
        # Destinatarios: todos los usuarios (secretarias) con suscripción activa
        suscriptores = db.query(PushSuscriptor).filter(
            PushSuscriptor.activo == True,  # noqa: E712
            PushSuscriptor.secretaria_id.isnot(None),
        ).all()

        payload = {
            "titulo": titulo,
            "cuerpo": cuerpo,
            "url": "/secretaria/",
            "urgente": False,
        }
        resultado = enviar_push_multi(suscriptores, payload)

        msg = PushMensaje(
            de_usuario_id=None,
            titulo=titulo,
            cuerpo=cuerpo,
            url_destino="/secretaria/",
            urgente=False,
            enviado_a=[s.id for s in suscriptores],
        )
        db.add(msg)
        db.commit()

        return JSONResponse({
            "ok": True,
            "resultado": resultado,
            "total_suscriptores": len(suscriptores),
        })
    finally:
        db.close()


# Los endpoints del panel jefe (/secretaria/jefe y /secretaria/jefe/solicitud)
# se movieron a app/routers/secretaria.py para usar su prefix /secretaria.


# ─── Biblioteca de medios para Push ───
@router.get("/media/biblioteca")
async def push_media_biblioteca(request: Request, categoria: Optional[str] = None):
    """Lista los medios disponibles. Incluye plantillas del sistema +
    medios propios de la secretaria logueada."""
    uid = get_current_user_id(request)
    db = _db()
    try:
        q = db.query(PushMediaBiblioteca)
        if categoria:
            q = q.filter(PushMediaBiblioteca.categoria == categoria)
        # Plantillas del sistema (sin dueño) + mis propios
        from sqlalchemy import or_
        if uid:
            q = q.filter(or_(
                PushMediaBiblioteca.es_plantilla == True,  # noqa: E712
                PushMediaBiblioteca.secretaria_id == uid,
            ))
        else:
            q = q.filter(PushMediaBiblioteca.es_plantilla == True)  # noqa: E712
        items = q.order_by(PushMediaBiblioteca.es_plantilla.desc(), PushMediaBiblioteca.creado_en.desc()).all()
        return JSONResponse({
            "ok": True,
            "items": [
                {
                    "id": i.id,
                    "categoria": i.categoria,
                    "nombre": i.nombre,
                    "tipo": i.tipo,
                    "url": i.url,
                    "es_plantilla": bool(i.es_plantilla),
                }
                for i in items
            ],
        })
    finally:
        db.close()


@router.post("/media/subir")
async def push_media_subir(
    request: Request,
    archivo: UploadFile = File(...),
    categoria: str = "general",
    nombre: str = "",
):
    """Sube un archivo (imagen/gif/audio) y lo guarda en la biblioteca."""
    uid = get_current_user_id(request)
    if not uid:
        raise HTTPException(401, "No autenticado")

    fname = (archivo.filename or "").strip()
    if not fname:
        return JSONResponse({"ok": False, "error": "Sin archivo"}, status_code=400)
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
    if ext not in EXT_PERMITIDAS:
        return JSONResponse(
            {"ok": False, "error": f"Extensión no permitida: .{ext}"},
            status_code=400,
        )

    tipo = "imagen"
    if ext in ("gif",):
        tipo = "gif"
    elif ext in ("mp3", "ogg", "wav", "m4a"):
        tipo = "audio"

    # Guardar con nombre aleatorio
    random_name = _secrets.token_urlsafe(12) + "." + ext
    dest = UPLOADS_MEDIA_DIR / random_name
    contenido = await archivo.read()
    if len(contenido) > 10 * 1024 * 1024:  # 10 MB
        return JSONResponse(
            {"ok": False, "error": "Archivo demasiado grande (máx 10 MB)"},
            status_code=413,
        )
    dest.write_bytes(contenido)
    url = f"/static/img/push-uploads/{random_name}"

    db = _db()
    try:
        m = PushMediaBiblioteca(
            secretaria_id=uid,
            categoria=(categoria or "general").strip()[:30],
            nombre=(nombre or fname)[:100],
            tipo=tipo,
            url=url,
            es_plantilla=False,
        )
        db.add(m)
        db.commit()
        db.refresh(m)
        return JSONResponse({
            "ok": True,
            "id": m.id, "url": url, "tipo": tipo,
            "nombre": m.nombre, "categoria": m.categoria,
        })
    finally:
        db.close()
