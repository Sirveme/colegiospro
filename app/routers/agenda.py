# ══════════════════════════════════════════════════════════
# app/routers/agenda.py — Agenda Inteligente
# Rutas bajo /secretaria/agenda
# ══════════════════════════════════════════════════════════

import os
from datetime import datetime, timedelta, date as date_cls
from typing import Optional, List

from fastapi import (
    APIRouter, Request, Form, HTTPException, UploadFile, File
)
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from app.models_secretaria import (
    UsuarioSecretaria,
    AgendaEvento,
    AgendaAcceso,
    AgendaConfig,
    DocumentoSecretaria,
)
from app.services.auth_service import get_current_user_id
from app.services.agenda_service import (
    generar_sugerencia_evento,
    analizar_semana,
    rango_semana,
    agrupar_por_dia,
)


router = APIRouter(prefix="/secretaria/agenda", tags=["Agenda"])
templates = Jinja2Templates(directory="app/templates")


# ─── Helpers ────────────────────────────────────────────────────
def _db():
    return SessionLocal()


def _require_user(request: Request) -> UsuarioSecretaria:
    uid = get_current_user_id(request)
    if not uid:
        raise HTTPException(status_code=401, detail="No autenticado")
    db = _db()
    try:
        u = db.query(UsuarioSecretaria).filter(UsuarioSecretaria.id == uid).first()
        if not u:
            raise HTTPException(status_code=401, detail="Sesión inválida")
        return u
    finally:
        db.close()


def _user_or_redirect(request: Request):
    uid = get_current_user_id(request)
    if not uid:
        return None
    db = _db()
    try:
        return db.query(UsuarioSecretaria).filter(UsuarioSecretaria.id == uid).first()
    finally:
        db.close()


def _get_o_crear_config(db, secretaria_id: int) -> AgendaConfig:
    cfg = db.query(AgendaConfig).filter(
        AgendaConfig.secretaria_id == secretaria_id
    ).first()
    if not cfg:
        cfg = AgendaConfig(secretaria_id=secretaria_id)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _ctx(usuario, **extra):
    base = {"usuario": usuario, "modo_actual": "agenda"}
    base.update(extra)
    return base


# ─── Vista semana (default) ─────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def agenda_semana(request: Request, fecha: Optional[str] = None):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    if fecha:
        try:
            base_fecha = datetime.fromisoformat(fecha)
        except Exception:
            base_fecha = datetime.utcnow()
    else:
        base_fecha = datetime.utcnow()

    lunes, domingo = rango_semana(base_fecha)

    db = _db()
    try:
        eventos = db.query(AgendaEvento).filter(
            AgendaEvento.secretaria_id == usuario.id,
            AgendaEvento.fecha_inicio >= lunes,
            AgendaEvento.fecha_inicio <= domingo,
            AgendaEvento.estado != "cancelado",
        ).order_by(AgendaEvento.fecha_inicio.asc()).all()

        cfg = _get_o_crear_config(db, usuario.id)
    finally:
        db.close()

    grilla = agrupar_por_dia(eventos, lunes)
    alertas = analizar_semana(eventos)

    semana_anterior = (lunes - timedelta(days=7)).date().isoformat()
    semana_siguiente = (lunes + timedelta(days=7)).date().isoformat()

    dias_es = ["LUN", "MAR", "MIE", "JUE", "VIE", "SAB", "DOM"]
    dias_data = []
    for i in range(7):
        d = lunes + timedelta(days=i)
        dias_data.append({
            "nombre": dias_es[i],
            "numero": d.day,
            "fecha_iso": d.date().isoformat(),
            "es_hoy": d.date() == datetime.utcnow().date(),
            "eventos": grilla[i],
        })

    return templates.TemplateResponse(
        request,
        "secretaria/agenda/semana.html",
        _ctx(
            usuario,
            dias=dias_data,
            alertas=alertas,
            semana_inicio=lunes,
            semana_fin=domingo,
            semana_anterior=semana_anterior,
            semana_siguiente=semana_siguiente,
            hoy_iso=datetime.utcnow().date().isoformat(),
            cfg=cfg,
        ),
    )


# ─── Vista mes (simplificada) ───────────────────────────────────
@router.get("/mes", response_class=HTMLResponse)
async def agenda_mes(request: Request, anio: Optional[int] = None, mes: Optional[int] = None):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    hoy = datetime.utcnow()
    anio = anio or hoy.year
    mes = mes or hoy.month

    primero = datetime(anio, mes, 1)
    if mes == 12:
        siguiente = datetime(anio + 1, 1, 1)
    else:
        siguiente = datetime(anio, mes + 1, 1)
    ultimo = siguiente - timedelta(seconds=1)

    db = _db()
    try:
        eventos = db.query(AgendaEvento).filter(
            AgendaEvento.secretaria_id == usuario.id,
            AgendaEvento.fecha_inicio >= primero,
            AgendaEvento.fecha_inicio <= ultimo,
            AgendaEvento.estado != "cancelado",
        ).order_by(AgendaEvento.fecha_inicio.asc()).all()
    finally:
        db.close()

    # Agrupar por día del mes
    por_dia = {}
    for e in eventos:
        d = e.fecha_inicio.day
        por_dia.setdefault(d, []).append(e)

    return templates.TemplateResponse(
        request,
        "secretaria/agenda/mes.html",
        _ctx(
            usuario,
            anio=anio,
            mes=mes,
            por_dia=por_dia,
            ultimo_dia=ultimo.day,
        ),
    )


# ─── Vista día ──────────────────────────────────────────────────
@router.get("/dia/{fecha}", response_class=HTMLResponse)
async def agenda_dia(fecha: str, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    try:
        d = datetime.fromisoformat(fecha)
    except Exception:
        return RedirectResponse("/secretaria/agenda/", status_code=302)

    inicio = d.replace(hour=0, minute=0, second=0, microsecond=0)
    fin = inicio + timedelta(days=1)

    db = _db()
    try:
        eventos = db.query(AgendaEvento).filter(
            AgendaEvento.secretaria_id == usuario.id,
            AgendaEvento.fecha_inicio >= inicio,
            AgendaEvento.fecha_inicio < fin,
            AgendaEvento.estado != "cancelado",
        ).order_by(AgendaEvento.fecha_inicio.asc()).all()
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/agenda/dia.html",
        _ctx(usuario, fecha=inicio, eventos=eventos),
    )


# ─── Nuevo evento (form + submit) ───────────────────────────────
@router.get("/nuevo", response_class=HTMLResponse)
async def agenda_nuevo_form(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    db = _db()
    try:
        documentos = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.secretaria_id == usuario.id,
            DocumentoSecretaria.guardado == True,  # noqa: E712
        ).order_by(DocumentoSecretaria.creado_en.desc()).limit(50).all()
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/agenda/nuevo.html",
        _ctx(usuario, documentos=documentos),
    )


@router.post("/nuevo")
async def agenda_nuevo_submit(
    request: Request,
    titulo: str = Form(...),
    fecha: str = Form(...),
    hora_inicio: str = Form(...),
    duracion_min: int = Form(60),
    tipo: str = Form("reunion"),
    lugar: Optional[str] = Form(None),
    modalidad: str = Form("presencial"),
    descripcion: Optional[str] = Form(None),
    buffer_antes: int = Form(15),
    documento_id: Optional[int] = Form(None),
    notif_jefe: Optional[str] = Form(None),
    incluir_sugerencia: Optional[str] = Form(None),
    participantes_json: Optional[str] = Form(None),
    archivo_adjunto: Optional[UploadFile] = File(None),
):
    usuario = _require_user(request)

    try:
        f_inicio = datetime.fromisoformat(f"{fecha}T{hora_inicio}")
    except Exception:
        raise HTTPException(400, "Fecha u hora inválida")
    f_fin = f_inicio + timedelta(minutes=int(duracion_min or 60))

    # Participantes
    participantes = []
    if participantes_json:
        try:
            import json
            participantes = json.loads(participantes_json) or []
        except Exception:
            participantes = []

    # Adjunto (guardar en static/uploads/)
    adj_url = ""
    adj_nombre = ""
    if archivo_adjunto and archivo_adjunto.filename:
        contenido = await archivo_adjunto.read()
        carpeta = "static/uploads/agenda"
        os.makedirs(carpeta, exist_ok=True)
        safe_name = f"{usuario.id}_{int(datetime.utcnow().timestamp())}_{archivo_adjunto.filename}"
        ruta = os.path.join(carpeta, safe_name)
        with open(ruta, "wb") as f:
            f.write(contenido)
        adj_url = f"/static/uploads/agenda/{safe_name}"
        adj_nombre = archivo_adjunto.filename

    # Sugerencia IA opcional
    sugerencia = ""
    if incluir_sugerencia:
        sugerencia = generar_sugerencia_evento({
            "titulo": titulo,
            "tipo": tipo,
            "participantes": participantes,
            "descripcion": descripcion or "",
            "fecha_inicio": f_inicio,
        })

    db = _db()
    try:
        ev = AgendaEvento(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            titulo=titulo.strip(),
            descripcion=(descripcion or "").strip(),
            fecha_inicio=f_inicio,
            fecha_fin=f_fin,
            tipo=tipo,
            lugar=(lugar or "").strip(),
            modalidad=modalidad,
            participantes=participantes,
            documento_id=documento_id if documento_id else None,
            archivo_adjunto_url=adj_url,
            archivo_adjunto_nombre=adj_nombre,
            buffer_antes=int(buffer_antes or 0),
            sugerencia_ia=sugerencia,
            estado="confirmado",
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)
    finally:
        db.close()

    return RedirectResponse("/secretaria/agenda/", status_code=302)


# ─── Detalle / editar / cancelar / notificar ────────────────────
# Restringimos evento_id a dígitos con regex para que rutas estáticas
# como /config, /heartbeat, /google/callback NO colisionen con este
# catch-all (sino FastAPI devolvía 422 al intentar convertir "config" a int).
@router.get("/{evento_id:int}", response_class=HTMLResponse)
async def agenda_detalle(evento_id: int, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    db = _db()
    try:
        ev = db.query(AgendaEvento).filter(
            AgendaEvento.id == evento_id,
            AgendaEvento.secretaria_id == usuario.id,
        ).first()
        if not ev:
            raise HTTPException(404, "Evento no encontrado")
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/agenda/detalle.html",
        _ctx(usuario, evento=ev),
    )


@router.post("/{evento_id:int}/editar")
async def agenda_editar(
    evento_id: int,
    request: Request,
    titulo: Optional[str] = Form(None),
    descripcion: Optional[str] = Form(None),
    lugar: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    db = _db()
    try:
        ev = db.query(AgendaEvento).filter(
            AgendaEvento.id == evento_id,
            AgendaEvento.secretaria_id == usuario.id,
        ).first()
        if not ev:
            raise HTTPException(404, "Evento no encontrado")
        if titulo is not None:
            ev.titulo = titulo.strip()
        if descripcion is not None:
            ev.descripcion = descripcion.strip()
        if lugar is not None:
            ev.lugar = lugar.strip()
        ev.actualizado_en = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    return RedirectResponse(f"/secretaria/agenda/{evento_id}", status_code=302)


@router.post("/{evento_id:int}/cancelar")
async def agenda_cancelar(evento_id: int, request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        ev = db.query(AgendaEvento).filter(
            AgendaEvento.id == evento_id,
            AgendaEvento.secretaria_id == usuario.id,
        ).first()
        if not ev:
            raise HTTPException(404, "Evento no encontrado")
        ev.estado = "cancelado"
        ev.actualizado_en = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


@router.post("/{evento_id:int}/notificar")
async def agenda_notificar_ahora(evento_id: int, request: Request):
    """Marca el evento como notificado (placeholder de push real)."""
    usuario = _require_user(request)
    db = _db()
    try:
        ev = db.query(AgendaEvento).filter(
            AgendaEvento.id == evento_id,
            AgendaEvento.secretaria_id == usuario.id,
        ).first()
        if not ev:
            raise HTTPException(404, "Evento no encontrado")
        ev.notif_enviada = True
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "mensaje": "Notificación enviada"})


# ─── Configuración ─────────────────────────────────────────────
@router.get("/config", response_class=HTMLResponse)
async def agenda_config_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        cfg = _get_o_crear_config(db, usuario.id)
        accesos = db.query(AgendaAcceso).filter(
            AgendaAcceso.propietario_id == usuario.id,
            AgendaAcceso.activo == True,  # noqa: E712
        ).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/agenda/config.html",
        _ctx(usuario, cfg=cfg, accesos=accesos),
    )


@router.post("/config")
async def agenda_config_guardar(
    request: Request,
    hora_inicio: str = Form("08:00"),
    hora_fin: str = Form("17:00"),
    buffer_default: int = Form(15),
    notif_minutos_antes: int = Form(30),
    notif_jefe_activa: Optional[str] = Form(None),
    duracion_bloque_enfoque: int = Form(90),
):
    usuario = _require_user(request)
    db = _db()
    try:
        cfg = _get_o_crear_config(db, usuario.id)
        cfg.hora_inicio = hora_inicio
        cfg.hora_fin = hora_fin
        cfg.buffer_default = int(buffer_default or 15)
        cfg.notif_minutos_antes = int(notif_minutos_antes or 30)
        cfg.notif_jefe_activa = bool(notif_jefe_activa)
        cfg.duracion_bloque_enfoque = int(duracion_bloque_enfoque or 90)
        cfg.actualizado_en = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/agenda/config", status_code=302)


# ─── Acceso a directivos ────────────────────────────────────────
@router.post("/acceso/nuevo")
async def agenda_acceso_nuevo(
    request: Request,
    correo_autorizado: str = Form(...),
    nivel: str = Form("lectura"),
):
    usuario = _require_user(request)
    db = _db()
    try:
        autorizado = db.query(UsuarioSecretaria).filter(
            UsuarioSecretaria.correo == correo_autorizado.strip().lower()
        ).first()
        if not autorizado:
            raise HTTPException(404, "Usuario no encontrado")
        existe = db.query(AgendaAcceso).filter(
            AgendaAcceso.propietario_id == usuario.id,
            AgendaAcceso.autorizado_id == autorizado.id,
        ).first()
        if existe:
            existe.nivel = nivel
            existe.activo = True
        else:
            ac = AgendaAcceso(
                propietario_id=usuario.id,
                autorizado_id=autorizado.id,
                nivel=nivel if nivel in ("lectura", "edicion") else "lectura",
                activo=True,
            )
            db.add(ac)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/agenda/config", status_code=302)


# ─── Google Calendar (placeholder) ──────────────────────────────
@router.post("/google/conectar")
async def agenda_google_conectar(request: Request):
    """Placeholder: en producción inicia OAuth flow."""
    _ = _require_user(request)
    return JSONResponse({
        "ok": False,
        "error": "Integración con Google Calendar próximamente. Usa la agenda interna por ahora.",
    })


@router.get("/google/callback")
async def agenda_google_callback(request: Request):
    return RedirectResponse("/secretaria/agenda/config", status_code=302)


@router.post("/google/sincronizar")
async def agenda_google_sincronizar(request: Request):
    _ = _require_user(request)
    return JSONResponse({"ok": False, "error": "No conectado a Google Calendar"})


@router.post("/google/desconectar")
async def agenda_google_desconectar(request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        cfg = _get_o_crear_config(db, usuario.id)
        cfg.google_calendar_id = ""
        cfg.google_refresh_token_enc = ""
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/agenda/config", status_code=302)


# ─── Heartbeat para notificaciones ──────────────────────────────
@router.get("/heartbeat")
async def agenda_heartbeat(request: Request):
    """Endpoint a llamar cada 5 min desde el cliente o cron."""
    from app.services.agenda_service import buscar_notificaciones_pendientes
    usuario = _user_or_redirect(request)
    if not usuario:
        return JSONResponse({"ok": False}, status_code=401)

    db = _db()
    try:
        proximos = buscar_notificaciones_pendientes(db)
        # Filtrar a los del usuario actual
        proximos = [e for e in proximos if e.secretaria_id == usuario.id]
        for ev in proximos:
            ev.notif_enviada = True
        db.commit()
        return JSONResponse({
            "ok": True,
            "enviadas": [{"id": e.id, "titulo": e.titulo} for e in proximos],
        })
    finally:
        db.close()
