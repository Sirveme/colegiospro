# ══════════════════════════════════════════════════════════
# app/email_engine/admin.py
# Dashboard admin y rutas de gestión de campañas, contactos, config
# Bajo /admin/emails
# ══════════════════════════════════════════════════════════

import csv
import io
import logging
import smtplib
import traceback
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from fastapi import APIRouter, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from .models import (
    EmailConfig, EmailCampana, EmailContacto, EmailEnvio,
    EmailEvento, EmailObjecion,
)
from .sender import (
    cifrar, descifrar, procesar_cola, crear_envios_para_campana,
)
from .templates_html import PLANTILLAS


router = APIRouter(prefix="/admin/emails", tags=["admin-emails"])
templates = Jinja2Templates(directory="app/templates")
logger = logging.getLogger("email_engine.admin")


def _db():
    return SessionLocal()


# ─── Dashboard principal ───────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = _db()
    try:
        hoy = datetime.utcnow().date()
        inicio_hoy = datetime.combine(hoy, datetime.min.time())
        inicio_semana = inicio_hoy - timedelta(days=hoy.weekday())

        total_envios = db.query(EmailEnvio).count()
        enviados_hoy = db.query(EmailEnvio).filter(
            EmailEnvio.enviado_en >= inicio_hoy
        ).count()
        enviados_semana = db.query(EmailEnvio).filter(
            EmailEnvio.enviado_en >= inicio_semana
        ).count()

        total_enviados = db.query(EmailEnvio).filter(
            EmailEnvio.estado.in_(["enviado", "abierto"])
        ).count()
        total_abiertos = db.query(EmailEnvio).filter(
            EmailEnvio.estado == "abierto"
        ).count()

        total_clics = db.query(EmailEnvio).filter(
            EmailEnvio.primer_clic_en.isnot(None)
        ).count()
        total_descargas = db.query(EmailEvento).filter(
            EmailEvento.tipo == "pdf_download"
        ).count()

        tasa_apertura = (total_abiertos / total_enviados * 100) if total_enviados else 0
        tasa_clic = (total_clics / total_enviados * 100) if total_enviados else 0
        tasa_descarga = (total_descargas / total_enviados * 100) if total_enviados else 0

        ultimas_aperturas = db.query(EmailEvento).filter(
            EmailEvento.tipo == "open"
        ).order_by(EmailEvento.creado_en.desc()).limit(20).all()

        aperturas_data = []
        for ev in ultimas_aperturas:
            envio = db.get(EmailEnvio, ev.envio_id)
            contacto = db.get(EmailContacto, envio.contacto_id) if envio else None
            aperturas_data.append({
                "fecha": ev.creado_en,
                "correo": contacto.correo if contacto else "—",
                "municipalidad": contacto.municipalidad if contacto else "",
            })

        hace_24h = datetime.utcnow() - timedelta(hours=24)
        clics_24h = db.query(EmailEnvio).filter(
            EmailEnvio.primer_clic_en >= hace_24h
        ).order_by(EmailEnvio.primer_clic_en.desc()).limit(30).all()

        clics_data = []
        for en in clics_24h:
            c = db.get(EmailContacto, en.contacto_id)
            if c:
                clics_data.append({
                    "correo": c.correo,
                    "municipalidad": c.municipalidad,
                    "telefono": c.telefono or c.whatsapp,
                    "cuando": en.primer_clic_en,
                })

        objeciones_total = db.query(EmailObjecion).count()
        objeciones_por_tipo = {}
        for obj in db.query(EmailObjecion).all():
            key = obj.objecion or "otro"
            objeciones_por_tipo[key] = objeciones_por_tipo.get(key, 0) + 1
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "email_engine/dashboard.html",
        {
            "request": request,
            "total_envios": total_envios,
            "enviados_hoy": enviados_hoy,
            "enviados_semana": enviados_semana,
            "total_enviados": total_enviados,
            "total_abiertos": total_abiertos,
            "total_clics": total_clics,
            "total_descargas": total_descargas,
            "tasa_apertura": round(tasa_apertura, 1),
            "tasa_clic": round(tasa_clic, 1),
            "tasa_descarga": round(tasa_descarga, 1),
            "ultimas_aperturas": aperturas_data,
            "clics_24h": clics_data,
            "objeciones_total": objeciones_total,
            "objeciones_por_tipo": objeciones_por_tipo,
        },
    )


# ─── Campañas ──────────────────────────────────────────────────
@router.get("/campanas", response_class=HTMLResponse)
async def lista_campanas(request: Request):
    db = _db()
    try:
        campanas = db.query(EmailCampana).order_by(
            EmailCampana.creado_en.desc()
        ).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/campanas.html",
        {"request": request, "campanas": campanas},
    )


@router.get("/campana/nueva", response_class=HTMLResponse)
async def nueva_campana_form(request: Request):
    db = _db()
    try:
        configs = db.query(EmailConfig).filter(
            EmailConfig.activo == True  # noqa: E712
        ).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/campana_nueva.html",
        {"request": request, "configs": configs, "plantillas": PLANTILLAS},
    )


@router.post("/campana/nueva")
async def nueva_campana_submit(
    request: Request,
    nombre: str = Form(...),
    asunto_template: str = Form(...),
    segmento: str = Form(...),
    config_id: int = Form(...),
    plantilla: str = Form("A_secretaria"),
    html_custom: Optional[str] = Form(None),
):
    html = (html_custom or "").strip()
    if not html and plantilla in PLANTILLAS:
        html = PLANTILLAS[plantilla]["html"]

    db = _db()
    try:
        c = EmailCampana(
            nombre=nombre.strip(),
            asunto=asunto_template.strip()[:200],
            asunto_template=asunto_template.strip(),
            html_template=html,
            config_id=config_id,
            segmento=segmento.strip(),
            estado="borrador",
        )
        db.add(c)
        db.commit()
        db.refresh(c)
        cid = c.id
    finally:
        db.close()
    return RedirectResponse(f"/admin/emails/campana/{cid}", status_code=302)


@router.get("/campana/{campana_id}", response_class=HTMLResponse)
async def detalle_campana(campana_id: int, request: Request):
    db = _db()
    try:
        c = db.get(EmailCampana, campana_id)
        if not c:
            raise HTTPException(404, "Campaña no encontrada")
        envios = db.query(EmailEnvio).filter(
            EmailEnvio.campana_id == campana_id
        ).order_by(EmailEnvio.id.desc()).limit(100).all()
        # Decorar con contacto
        envios_data = []
        for e in envios:
            ct = db.get(EmailContacto, e.contacto_id)
            envios_data.append({"envio": e, "contacto": ct})
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/campana_detalle.html",
        {"request": request, "c": c, "envios": envios_data},
    )


@router.post("/campana/{campana_id}/activar")
async def activar_campana(campana_id: int, request: Request):
    db = _db()
    try:
        c = db.get(EmailCampana, campana_id)
        if not c:
            raise HTTPException(404, "Campaña no encontrada")
        # Crear envíos para los contactos del segmento
        creados = crear_envios_para_campana(db, c)
        c.estado = "activa"
        if not c.iniciado_en:
            c.iniciado_en = datetime.utcnow()
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "envios_creados": creados})


@router.post("/campana/{campana_id}/pausar")
async def pausar_campana(campana_id: int, request: Request):
    db = _db()
    try:
        c = db.get(EmailCampana, campana_id)
        if not c:
            raise HTTPException(404, "Campaña no encontrada")
        c.estado = "pausada"
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


# ─── Contactos ─────────────────────────────────────────────────
@router.get("/contactos", response_class=HTMLResponse)
async def lista_contactos(request: Request, segmento: Optional[str] = None):
    db = _db()
    try:
        q = db.query(EmailContacto)
        if segmento:
            q = q.filter(EmailContacto.segmento == segmento)
        contactos = q.order_by(EmailContacto.id.desc()).limit(500).all()
        total = q.count()
        # Segmentos disponibles
        segmentos_data = {}
        for c in db.query(EmailContacto).all():
            segmentos_data[c.segmento or "(sin)"] = segmentos_data.get(c.segmento or "(sin)", 0) + 1
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/contactos.html",
        {
            "request": request,
            "contactos": contactos,
            "total": total,
            "segmento_filtro": segmento or "",
            "segmentos_data": segmentos_data,
        },
    )


@router.post("/contactos/importar")
async def importar_contactos(request: Request, archivo: UploadFile = File(...)):
    """Acepta CSV con columnas (case-insensitive, con o sin tildes):
    Correo, Nombre, Municipalidad, Departamento, Provincia, Alcalde,
    Telefono, WhatsApp, Tipo, Campana (segmento)."""
    insertados = 0
    duplicados = 0
    errores = 0

    try:
        if not archivo or not archivo.filename:
            return JSONResponse(
                {"ok": False, "error": "Falta el archivo",
                 "insertados": 0, "duplicados": 0, "errores": 0},
                status_code=400,
            )

        contenido = await archivo.read()
        try:
            texto = contenido.decode("utf-8-sig")
        except Exception:
            try:
                texto = contenido.decode("utf-8", errors="ignore")
            except Exception:
                texto = contenido.decode("latin-1", errors="ignore")

        # Detectar delimitador (coma, punto y coma, tab)
        muestra = texto[:4096]
        try:
            dialect = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
        except Exception:
            dialect = csv.excel
        reader = csv.DictReader(io.StringIO(texto), dialect=dialect)

        def _norm(k: str) -> str:
            if not k:
                return ""
            s = k.strip().lower()
            # quitar tildes comunes
            for a, b in (("á", "a"), ("é", "e"), ("í", "i"),
                         ("ó", "o"), ("ú", "u"), ("ñ", "n")):
                s = s.replace(a, b)
            return s

        vistos_en_csv = set()
        db = _db()
        try:
            for row in reader:
                try:
                    d = {_norm(k): (v or "").strip()
                         for k, v in (row or {}).items() if k}
                    correo = (d.get("correo") or d.get("email") or "").lower()
                    if not correo or "@" not in correo:
                        errores += 1
                        continue
                    if correo in vistos_en_csv:
                        duplicados += 1
                        continue
                    vistos_en_csv.add(correo)

                    existe = db.query(EmailContacto).filter_by(correo=correo).first()
                    if existe:
                        duplicados += 1
                        continue

                    c = EmailContacto(
                        correo=correo,
                        nombre=d.get("nombre", ""),
                        municipalidad=d.get("municipalidad", ""),
                        provincia=d.get("provincia", ""),
                        departamento=d.get("departamento", ""),
                        alcalde=d.get("alcalde", ""),
                        telefono=d.get("telefono", ""),
                        whatsapp=d.get("whatsapp", ""),
                        tipo_correo=d.get("tipo") or d.get("tipo_correo") or "",
                        segmento=d.get("campana") or d.get("segmento") or "",
                        activo=True,
                        baja=False,
                    )
                    db.add(c)
                    db.commit()
                    insertados += 1
                except Exception as row_err:
                    db.rollback()
                    errores += 1
                    logger.warning("Fila con error en importación CSV: %s", row_err)
        finally:
            db.close()

        return JSONResponse({
            "ok": True,
            "insertados": insertados,
            "duplicados": duplicados,
            "errores": errores,
        })

    except Exception as e:
        tb = traceback.format_exc()
        logger.error("Error global en importar_contactos: %s\n%s", e, tb)
        return JSONResponse(
            {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "insertados": insertados,
                "duplicados": duplicados,
                "errores": errores,
            },
            status_code=500,
        )


# ─── Objeciones ────────────────────────────────────────────────
@router.get("/objeciones", response_class=HTMLResponse)
async def lista_objeciones(request: Request):
    db = _db()
    try:
        items = db.query(EmailObjecion).order_by(
            EmailObjecion.creado_en.desc()
        ).limit(200).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/objeciones.html",
        {"request": request, "items": items},
    )


# ─── Configuración SMTP ───────────────────────────────────────
@router.get("/config", response_class=HTMLResponse)
async def config_view(request: Request):
    db = _db()
    try:
        configs = db.query(EmailConfig).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "email_engine/config.html",
        {"request": request, "configs": configs},
    )


@router.post("/config")
async def config_guardar(
    request: Request,
    nombre: str = Form(...),
    smtp_host: str = Form("smtp.gmail.com"),
    smtp_port: int = Form(587),
    smtp_user: str = Form(...),
    smtp_pass: Optional[str] = Form(None),
    from_name: str = Form(...),
    from_email: str = Form(...),
    limite_dia: int = Form(50),
    limite_hora: int = Form(10),
    config_id: Optional[int] = Form(None),
):
    db = _db()
    try:
        if config_id:
            cfg = db.get(EmailConfig, config_id)
            if not cfg:
                raise HTTPException(404, "Config no encontrada")
        else:
            cfg = EmailConfig(nombre=nombre)
            db.add(cfg)

        cfg.nombre = nombre
        cfg.smtp_host = smtp_host
        cfg.smtp_port = smtp_port
        cfg.smtp_user = smtp_user
        if smtp_pass:
            cfg.smtp_pass_enc = cifrar(smtp_pass)
        cfg.from_name = from_name
        cfg.from_email = from_email
        cfg.limite_dia = limite_dia
        cfg.limite_hora = limite_hora
        cfg.activo = True
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/admin/emails/config", status_code=302)


@router.post("/config/{config_id}/test")
async def config_test(config_id: int):
    """Envía un correo de prueba a la misma dirección del remitente."""
    db = _db()
    try:
        cfg = db.get(EmailConfig, config_id)
        if not cfg:
            raise HTTPException(404, "Config no encontrada")
        destino = cfg.from_email
        password = descifrar(cfg.smtp_pass_enc)
        if not password:
            return JSONResponse(
                {"ok": False, "error": "No hay contraseña SMTP guardada para esta cuenta."},
                status_code=400,
            )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Prueba SMTP] {cfg.nombre} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        msg["From"] = f"{cfg.from_name} <{cfg.from_email}>"
        msg["To"] = destino
        cuerpo = (
            f"<p>Este es un correo de prueba enviado desde la configuración SMTP "
            f"<strong>{cfg.nombre}</strong>.</p>"
            f"<p>Servidor: {cfg.smtp_host}:{cfg.smtp_port}<br>"
            f"Usuario: {cfg.smtp_user}<br>"
            f"Remitente: {cfg.from_email}</p>"
            f"<p>Si recibes este mensaje, la conexión SMTP funciona correctamente.</p>"
        )
        msg.attach(MIMEText(cuerpo, "html", "utf-8"))

        try:
            with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
                s.ehlo()
                s.starttls()
                s.login(cfg.smtp_user, password)
                s.sendmail(cfg.from_email, [destino], msg.as_string())
        except smtplib.SMTPAuthenticationError as e:
            return JSONResponse(
                {"ok": False, "error": f"Autenticación SMTP fallida: {e.smtp_code} {e.smtp_error.decode(errors='ignore') if isinstance(e.smtp_error, bytes) else e.smtp_error}"},
                status_code=400,
            )
        except smtplib.SMTPException as e:
            return JSONResponse(
                {"ok": False, "error": f"Error SMTP: {type(e).__name__}: {e}"},
                status_code=400,
            )
        except Exception as e:
            return JSONResponse(
                {"ok": False, "error": f"{type(e).__name__}: {e}"},
                status_code=400,
            )
    finally:
        db.close()

    return JSONResponse({"ok": True, "destino": destino})


# ─── Heartbeat ─────────────────────────────────────────────────
@router.get("/heartbeat")
async def heartbeat():
    """Procesa hasta 5 correos de la cola. Llamar cada 5 min desde el cliente
    o desde un cron externo (Railway cron job)."""
    db = _db()
    try:
        resultado = procesar_cola(db, max_envios=5)
    finally:
        db.close()
    return JSONResponse(resultado)
