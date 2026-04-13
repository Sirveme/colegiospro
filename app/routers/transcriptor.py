# ══════════════════════════════════════════════════════════
# app/routers/transcriptor.py — Transcriptor de Reuniones
# Rutas bajo /secretaria/transcriptor
# ══════════════════════════════════════════════════════════

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from app.models_secretaria import (
    UsuarioSecretaria,
    TranscripcionReunion,
    AgendaEvento,
    DocumentoSecretaria,
    ConfigOrganizacion,
)
from app.services.auth_service import get_current_user_id
from app.services.transcriptor_service import (
    procesar_audio,
    generar_documento_reunion,
    listar_tipos_doc,
    parse_tiempo,
    formato_tiempo,
    TIPOS_DOC,
)
from app.services.pdf_service import texto_a_pdf_bytes, pdf_disponible


router = APIRouter(prefix="/secretaria/transcriptor", tags=["Transcriptor"])
templates = Jinja2Templates(directory="app/templates")

# Filtro Jinja para formato de tiempo
templates.env.filters["formato_tiempo"] = formato_tiempo


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


def _ctx(usuario, **extra):
    base = {"usuario": usuario, "modo_actual": "transcriptor"}
    base.update(extra)
    return base


# ─── Lista de transcripciones ──────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def transcriptor_lista(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        items = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.secretaria_id == usuario.id
        ).order_by(TranscripcionReunion.creado_en.desc()).limit(100).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/transcriptor/lista.html",
        _ctx(usuario, items=items),
    )


# ─── Nuevo: form ────────────────────────────────────────────────
@router.get("/nuevo", response_class=HTMLResponse)
async def transcriptor_nuevo_form(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        eventos_recientes = db.query(AgendaEvento).filter(
            AgendaEvento.secretaria_id == usuario.id
        ).order_by(AgendaEvento.fecha_inicio.desc()).limit(20).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/transcriptor/nuevo.html",
        _ctx(usuario, eventos_recientes=eventos_recientes),
    )


# ─── Nuevo: submit (procesar audio) ─────────────────────────────
@router.post("/nuevo")
async def transcriptor_nuevo_submit(
    request: Request,
    titulo: str = Form(...),
    agenda_evento_id: Optional[int] = Form(None),
    audio: UploadFile = File(...),
    tramos_json: Optional[str] = Form("[]"),
):
    usuario = _require_user(request)

    if not audio or not audio.filename:
        raise HTTPException(400, "Falta el archivo de audio")

    contenido = await audio.read()
    if len(contenido) > 25 * 1024 * 1024:
        raise HTTPException(400, "Archivo demasiado grande (máx 25 MB)")

    # Parsear tramos excluidos
    tramos = []
    try:
        tramos = json.loads(tramos_json or "[]") or []
    except Exception:
        tramos = []

    # Crear registro inicial en estado "transcribiendo"
    db = _db()
    try:
        tr = TranscripcionReunion(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            agenda_evento_id=agenda_evento_id if agenda_evento_id else None,
            titulo=titulo.strip(),
            audio_nombre=audio.filename,
            tramos_excluidos=tramos,
            estado="transcribiendo",
        )
        db.add(tr)
        db.commit()
        db.refresh(tr)
        tr_id = tr.id
    finally:
        db.close()

    # Procesar (síncrono — Whisper suele tardar pocos segundos)
    resultado = procesar_audio(
        audio_bytes=contenido,
        audio_nombre=audio.filename,
        tramos_excluidos=tramos,
        titulo=titulo,
    )

    # Guardar resultado
    db = _db()
    try:
        tr = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.id == tr_id
        ).first()
        if resultado.get("error"):
            tr.estado = "error"
            tr.texto_transcripcion = f"[ERROR: {resultado['error']}]"
        else:
            tr.estado = "listo"
            tr.texto_transcripcion = resultado.get("texto", "")
            tr.audio_duracion_seg = int(resultado.get("duracion_seg", 0))
        db.commit()
    finally:
        db.close()

    return RedirectResponse(f"/secretaria/transcriptor/{tr_id}", status_code=302)


# ─── Editor de transcripción ────────────────────────────────────
@router.get("/{tr_id}", response_class=HTMLResponse)
async def transcriptor_detalle(tr_id: int, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        tr = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.id == tr_id,
            TranscripcionReunion.secretaria_id == usuario.id,
        ).first()
        if not tr:
            raise HTTPException(404, "Transcripción no encontrada")
    finally:
        db.close()

    texto_actual = tr.texto_editado or tr.texto_transcripcion or ""

    return templates.TemplateResponse(
        request,
        "secretaria/transcriptor/detalle.html",
        _ctx(
            usuario,
            transcripcion=tr,
            texto_actual=texto_actual,
            duracion_str=formato_tiempo(tr.audio_duracion_seg or 0),
            tipos_doc=listar_tipos_doc(),
        ),
    )


@router.post("/{tr_id}/editar")
async def transcriptor_editar(
    tr_id: int,
    request: Request,
    texto_editado: str = Form(...),
):
    usuario = _require_user(request)
    db = _db()
    try:
        tr = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.id == tr_id,
            TranscripcionReunion.secretaria_id == usuario.id,
        ).first()
        if not tr:
            raise HTTPException(404, "Transcripción no encontrada")
        tr.texto_editado = texto_editado
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


@router.post("/{tr_id}/generar")
async def transcriptor_generar(
    tr_id: int,
    request: Request,
    tipo: str = Form(...),
):
    usuario = _require_user(request)
    if tipo not in TIPOS_DOC:
        raise HTTPException(400, "Tipo no válido")

    db = _db()
    try:
        tr = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.id == tr_id,
            TranscripcionReunion.secretaria_id == usuario.id,
        ).first()
        if not tr:
            raise HTTPException(404, "Transcripción no encontrada")

        # Config org
        cfg_org = db.query(ConfigOrganizacion).filter(
            ConfigOrganizacion.secretaria_id == usuario.id
        ).first()
        config_org_dict = {}
        if cfg_org:
            config_org_dict = {
                "nombre_organizacion": cfg_org.nombre_organizacion or "",
                "anno_oficial": cfg_org.anno_oficial or "",
            }

        # Participantes del evento relacionado (si existe)
        participantes = []
        if tr.agenda_evento_id:
            ev = db.query(AgendaEvento).filter(
                AgendaEvento.id == tr.agenda_evento_id
            ).first()
            if ev and ev.participantes:
                participantes = ev.participantes

        texto_base = tr.texto_editado or tr.texto_transcripcion or ""
        texto_doc = generar_documento_reunion(
            texto_transcripcion=texto_base,
            tipo=tipo,
            titulo_reunion=tr.titulo,
            participantes=participantes,
            config_org=config_org_dict,
        )

        # Guardar como DocumentoSecretaria
        doc = DocumentoSecretaria(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            modo="transcriptor",
            texto_entrada=tr.titulo,
            texto_salida=texto_doc,
            tono="formal",
            formato_salida=tipo,
            guardado=False,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

        tr.documento_generado_id = doc.id
        tr.tipo_documento_generado = tipo
        db.commit()

        doc_id = doc.id
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/transcriptor/_resultado.html",
        {
            "texto_salida": texto_doc,
            "documento_id": doc_id,
            "tipo": tipo,
            "tipo_label": TIPOS_DOC[tipo]["label"],
        },
    )


@router.get("/{tr_id}/pdf")
async def transcriptor_pdf(tr_id: int, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        tr = db.query(TranscripcionReunion).filter(
            TranscripcionReunion.id == tr_id,
            TranscripcionReunion.secretaria_id == usuario.id,
        ).first()
        if not tr:
            raise HTTPException(404, "Transcripción no encontrada")
        if not tr.documento_generado_id:
            raise HTTPException(400, "Aún no se ha generado un documento desde esta transcripción")
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == tr.documento_generado_id
        ).first()
        if not doc:
            raise HTTPException(404, "Documento no encontrado")
        texto = doc.texto_salida or ""
    finally:
        db.close()

    contenido = texto_a_pdf_bytes(
        texto,
        titulo=f"Transcripcion_{tr_id}",
        tono="formal",
        tipo_documento=tr.tipo_documento_generado or "acta",
    )
    if pdf_disponible():
        return Response(
            content=contenido,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="reunion_{tr_id}.pdf"'
            },
        )
    return Response(content=contenido, media_type="text/html; charset=utf-8")
