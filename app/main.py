# ══════════════════════════════════════════════════════════
# app/main.py — ColegiosPro
# Rutas: home, landing/demo, admin chat, API, WebSocket
# ══════════════════════════════════════════════════════════

import asyncio
import logging
import os
from datetime import datetime, timezone
from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal, Lead
from app.routers import verificacion, chat, secretaria, agenda, transcriptor, push
# Importar models_secretaria garantiza que las tablas del módulo
# se creen al iniciar la app (Base.metadata.create_all).
from app import models_secretaria  # noqa: F401
# Email Engine — módulo autónomo de envío masivo con tracking
from app.email_engine import models as email_models  # noqa: F401
from app.email_engine import tracking as email_tracking
from app.email_engine import admin as email_admin
from app.email_engine.sender import procesar_cola as _procesar_cola_email

logger = logging.getLogger("colegiospro.main")

app = FastAPI(
    title="ColegiosPro",
    description="Plataforma Digital para Colegios Profesionales del Peru",
    version="1.0.0",
)


# -- HTTPS Redirect Middleware --
@app.middleware("http")
async def redirect_to_https(request: Request, call_next):
    if request.headers.get("x-forwarded-proto") == "http":
        url = request.url.replace(scheme="https")
        return RedirectResponse(url, status_code=301)
    return await call_next(request)


# -- Static files --
app.mount("/static", StaticFiles(directory="static"), name="static")

# -- Templates --
templates = Jinja2Templates(directory="app/templates")

# -- Routers --
app.include_router(verificacion.router)
app.include_router(chat.router)       # ← NUEVO: WebSocket + tracking
app.include_router(secretaria.router) # ← SecretariaPro (módulo /secretaria)
app.include_router(agenda.router)     # ← Agenda Inteligente
app.include_router(transcriptor.router) # ← Transcriptor de Reuniones
app.include_router(push.router)       # ← Push Notifications + panel jefe/público
app.include_router(email_tracking.router) # ← /track/* — pixel, clics, baja, objeción
app.include_router(email_admin.router)    # ← /admin/emails/* — dashboard y gestión


# -- Schemas --

class ContactForm(BaseModel):
    colegio: str
    region: Optional[str] = None
    cantidad: Optional[str] = None
    decano: Optional[str] = None
    admin: Optional[str] = None
    tesoreria: Optional[str] = None
    secretaria: Optional[str] = None


# -- Routes --

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "home.html")


@app.get("/demo", response_class=HTMLResponse)
async def landing_demo(request: Request):
    """Landing page de ventas con videos y chat"""
    return templates.TemplateResponse(request, "landing.html")


@app.get("/admin/chat", response_class=HTMLResponse)
async def admin_chat_panel(request: Request):
    """Panel de chat para admin (Duil)"""
    # TODO: Add authentication middleware
    return templates.TemplateResponse(request, "admin_chat.html")


@app.get("/sandra", response_class=HTMLResponse)
async def sandra_page(request: Request):
    """Página estática /sandra — sin autenticación, sin base de datos."""
    return templates.TemplateResponse(request, "sandra.html")


@app.post("/api/contacto")
async def recibir_contacto(form: ContactForm, request: Request):
    db = SessionLocal()
    try:
        lead = Lead(
            colegio=form.colegio,
            region=form.region,
            cantidad=form.cantidad,
            decano_wsp=form.decano,
            admin_wsp=form.admin,
            tesoreria_wsp=form.tesoreria,
            secretaria_wsp=form.secretaria,
            ip=request.client.host,
        )
        db.add(lead)
        db.commit()
        return {"status": "ok", "message": "Solicitud recibida"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.get("/api/leads")
async def ver_leads():
    db = SessionLocal()
    try:
        leads = db.query(Lead).order_by(Lead.created_at.desc()).all()
        return [
            {
                "id": l.id,
                "colegio": l.colegio,
                "region": l.region,
                "cantidad": l.cantidad,
                "decano": l.decano_wsp,
                "admin": l.admin_wsp,
                "tesoreria": l.tesoreria_wsp,
                "secretaria": l.secretaria_wsp,
                "fecha": l.created_at.isoformat() if l.created_at else None,
            }
            for l in leads
        ]
    finally:
        db.close()


# ─── Revisión pública de documentos (sin login) ──────────────────
@app.get("/ver/{token}", response_class=HTMLResponse)
async def ver_documento_revision(token: str, request: Request):
    """Página pública de revisión. El token identifica una solicitud."""
    from app.models_secretaria import DocumentoRevision, DocumentoSecretaria
    db = SessionLocal()
    try:
        rev = db.query(DocumentoRevision).filter(
            DocumentoRevision.token == token
        ).first()
        if not rev:
            raise HTTPException(404, "Link no encontrado o expirado")
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == rev.documento_id
        ).first()
        if not doc:
            raise HTTPException(404, "Documento no encontrado")
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "revision_publica.html",
        {
            "request": request,
            "rev": rev,
            "doc": doc,
            "ya_respondida": rev.estado != "pendiente",
        },
    )


@app.post("/ver/{token}/responder")
async def responder_revision(
    token: str,
    request: Request,
    estado: str = Form(...),
    feedback: Optional[str] = Form(""),
):
    """Guarda la respuesta del revisor: aprobado o con_correcciones."""
    from app.models_secretaria import DocumentoRevision
    estado_norm = (estado or "").strip().lower()
    if estado_norm not in ("aprobado", "con_correcciones"):
        raise HTTPException(400, "Estado inválido")

    db = SessionLocal()
    try:
        rev = db.query(DocumentoRevision).filter(
            DocumentoRevision.token == token
        ).first()
        if not rev:
            raise HTTPException(404, "Link no encontrado")
        if rev.estado != "pendiente":
            return RedirectResponse(f"/ver/{token}?ok=1", status_code=302)
        rev.estado = estado_norm
        rev.feedback = (feedback or "").strip()[:5000]
        rev.respondido_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return RedirectResponse(f"/ver/{token}?ok=1", status_code=302)


@app.get("/comunicados/{token}", response_class=HTMLResponse)
async def comunicados_publicos(token: str, request: Request):
    """Página pública de comunicados de una organización.
    El token se genera por organización (ConfigOrganizacion.token_publico).
    Muestra sólo los push con es_publico=True, sin login ni sidebar."""
    from app.models_secretaria import (
        ConfigOrganizacion, PushMensaje, UsuarioSecretaria,
    )
    token = (token or "").strip()[:16]
    if not token:
        raise HTTPException(404, "Link inválido")
    db = SessionLocal()
    try:
        cfg = db.query(ConfigOrganizacion).filter(
            ConfigOrganizacion.token_publico == token
        ).first()
        if not cfg:
            raise HTTPException(404, "Organización no encontrada")
        secretaria = db.query(UsuarioSecretaria).filter(
            UsuarioSecretaria.id == cfg.secretaria_id
        ).first()
        secretaria_id = cfg.secretaria_id
        mensajes = (
            db.query(PushMensaje)
            .filter(
                PushMensaje.de_usuario_id == secretaria_id,
                PushMensaje.es_publico == True,  # noqa: E712
            )
            .order_by(PushMensaje.creado_en.desc())
            .limit(50)
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "comunicados_publicos.html",
        {
            "request": request,
            "org": cfg,
            "secretaria": secretaria,
            "mensajes": mensajes,
            "token": token,
        },
    )


@app.get("/offline.html", response_class=HTMLResponse)
async def offline_page(request: Request):
    """Página mostrada por el SW cuando no hay red."""
    return templates.TemplateResponse(request, "offline.html")


@app.get("/health")
async def health():
    return {"status": "ok", "app": "colegiospro"}


@app.get("/debug/db")
async def debug_db():
    import os
    db_url = os.environ.get("DATABASE_URL", "NO EXISTE")
    # Mostrar solo el inicio, sin exponer password completo
    safe = db_url[:30] + "..." if len(db_url) > 30 else db_url
    return {
        "db_url_prefix": safe,
        "starts_with_postgres": db_url.startswith("postgres"),
    }


# SECRETARIA PRO: rutas y lógica para el módulo de secretaria (documentos, plantillas, etc.)
# Se implementa en app/routers/secretaria.py, pero se importa aquí para incluir
@app.get("/secretariapro", response_class=HTMLResponse) 
async def secretariapro(request: Request):
    """Página estática /SecretariaPro — sin autenticación, sin base de datos."""
    return templates.TemplateResponse(request, "secretariapro.html")


# ─── Scheduler en background: heartbeat email cada 5 min ──────
_scheduler = None


async def heartbeat_email_background():
    """Procesa hasta 5 correos pendientes de la cola de email_engine.
    Corre en thread para no bloquear el event loop (procesar_cola es sync)."""
    def _run():
        db = SessionLocal()
        try:
            return _procesar_cola_email(db, max_envios=5)
        finally:
            db.close()
    try:
        resultado = await asyncio.to_thread(_run)
        if (resultado.get("enviados") or 0) > 0 or (resultado.get("fallidos") or 0) > 0:
            logger.info("Heartbeat email: %s", resultado)
    except Exception as e:
        logger.exception("Heartbeat email falló: %s", e)


@app.on_event("startup")
async def startup():
    global _scheduler
    # Solo arrancar en el proceso real, no en imports de tests/scripts.
    # Y permitir desactivar con DISABLE_EMAIL_SCHEDULER=1 (útil en CI).
    if os.environ.get("DISABLE_EMAIL_SCHEDULER") == "1":
        logger.info("Email scheduler deshabilitado por env var.")
        return
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            heartbeat_email_background,
            "interval",
            minutes=5,
            id="email_heartbeat",
            max_instances=1,
            coalesce=True,
            next_run_time=None,
        )
        _scheduler.start()
        logger.info("Email scheduler iniciado: heartbeat cada 5 min.")
    except Exception as e:
        logger.exception("No se pudo iniciar el email scheduler: %s", e)


@app.on_event("shutdown")
async def shutdown():
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
            logger.info("Email scheduler detenido.")
        except Exception as e:
            logger.warning("Error al detener email scheduler: %s", e)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)