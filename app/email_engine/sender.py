# ══════════════════════════════════════════════════════════
# app/email_engine/sender.py
# Envío SMTP + construcción de HTML con pixel y links rastreables
# ══════════════════════════════════════════════════════════

import logging
import os
import smtplib
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("email_engine.sender")

try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except Exception:
    Fernet = None
    _HAS_FERNET = False

from jinja2 import Template
from sqlalchemy.orm import Session

from .models import EmailConfig, EmailCampana, EmailContacto, EmailEnvio


BASE_URL = os.environ.get("BASE_URL", "https://colegiospro.org.pe")
FERNET_KEY = os.environ.get("FERNET_KEY", "")


def descifrar(valor: str) -> str:
    """Descifra valor con Fernet. Si no hay key, devuelve el valor como está."""
    if not valor:
        return ""
    if not FERNET_KEY or not _HAS_FERNET:
        return valor
    try:
        return Fernet(FERNET_KEY.encode()).decrypt(valor.encode()).decode()
    except Exception:
        return valor


def cifrar(valor: str) -> str:
    """Cifra valor con Fernet. Si no hay key, devuelve el valor como está."""
    if not valor:
        return ""
    if not FERNET_KEY or not _HAS_FERNET:
        return valor
    try:
        return Fernet(FERNET_KEY.encode()).encrypt(valor.encode()).decode()
    except Exception:
        return valor


def generar_token() -> str:
    """UUID4 hex — 32 caracteres."""
    return uuid.uuid4().hex


def puede_enviar(config: EmailConfig) -> bool:
    """Verifica límites de día y hora antes de enviar."""
    ahora = datetime.utcnow()
    # Reset diario
    if not config.dia_reset or ahora.date() > config.dia_reset.date():
        config.enviados_hoy = 0
        config.dia_reset = ahora
    # Reset horario
    if not config.hora_reset or (ahora - config.hora_reset).total_seconds() > 3600:
        config.enviados_hora_actual = 0
        config.hora_reset = ahora
    return (
        (config.enviados_hoy or 0) < (config.limite_dia or 0)
        and (config.enviados_hora_actual or 0) < (config.limite_hora or 0)
    )


def construir_html(template_html: str, contacto: EmailContacto, token: str) -> str:
    """Aplica variables y agrega pixel + links rastreables."""
    pixel = (
        f'<img src="{BASE_URL}/track/open/{token}.gif" '
        f'width="1" height="1" style="display:none" alt="">'
    )

    link_guia = f"{BASE_URL}/track/click/{token}?url=/guia&tipo=pdf_download"
    link_registro = f"{BASE_URL}/track/click/{token}?url=/secretaria/registro&tipo=registro"
    link_demo = f"{BASE_URL}/track/click/{token}?url=https://wa.me/51967317946&tipo=demo"
    link_baja = f"{BASE_URL}/track/baja/{token}"
    link_objecion = f"{BASE_URL}/track/objecion/{token}"

    return Template(template_html).render(
        municipalidad=contacto.municipalidad or "",
        departamento=contacto.departamento or "",
        provincia=contacto.provincia or "",
        alcalde=contacto.alcalde or "",
        nombre=contacto.nombre or "",
        correo=contacto.correo or "",
        link_guia=link_guia,
        link_registro=link_registro,
        link_demo=link_demo,
        link_baja=link_baja,
        link_objecion=link_objecion,
        pixel=pixel,
        base_url=BASE_URL,
    )


def enviar_un_correo(
    config: EmailConfig,
    envio: EmailEnvio,
    contacto: EmailContacto,
    campana: EmailCampana,
    db: Session,
) -> bool:
    """Envía un solo correo. Retorna True si tuvo éxito."""
    try:
        html = construir_html(campana.html_template, contacto, envio.token)
        asunto = Template(campana.asunto_template or campana.asunto or "").render(
            municipalidad=contacto.municipalidad or "",
            departamento=contacto.departamento or "",
            provincia=contacto.provincia or "",
            alcalde=contacto.alcalde or "",
            nombre=contacto.nombre or "",
        )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"] = f"{config.from_name} <{config.from_email}>"
        msg["To"] = contacto.correo
        msg["List-Unsubscribe"] = f"<{BASE_URL}/track/baja/{envio.token}>"
        msg.attach(MIMEText(html, "html", "utf-8"))

        password = descifrar(config.smtp_pass_enc)
        with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as s:
            s.ehlo()
            s.starttls()
            s.login(config.smtp_user, password)
            s.sendmail(config.from_email, [contacto.correo], msg.as_string())

        envio.estado = "enviado"
        envio.enviado_en = datetime.utcnow()
        envio.asunto_final = asunto[:300]
        envio.intentos = (envio.intentos or 0) + 1
        config.enviados_hoy = (config.enviados_hoy or 0) + 1
        config.enviados_hora_actual = (config.enviados_hora_actual or 0) + 1
        campana.total_enviados = (campana.total_enviados or 0) + 1
        db.commit()
        return True

    except smtplib.SMTPRecipientsRefused:
        envio.estado = "rebotado"
        envio.rebote_mensaje = "Dirección rechazada"
        envio.intentos = (envio.intentos or 0) + 1
        campana.total_rebotes = (campana.total_rebotes or 0) + 1
        db.commit()
        return False
    except Exception as e:
        envio.rebote_mensaje = str(e)[:200]
        envio.intentos = (envio.intentos or 0) + 1
        db.commit()
        return False


def procesar_cola(db: Session, max_envios: int = 5) -> dict:
    """
    Procesa hasta max_envios correos pendientes.
    Llamar desde heartbeat cada 5 minutos.
    """
    pendientes = (
        db.query(EmailEnvio)
        .join(EmailCampana, EmailEnvio.campana_id == EmailCampana.id)
        .join(EmailContacto, EmailEnvio.contacto_id == EmailContacto.id)
        .filter(
            EmailEnvio.estado == "pendiente",
            EmailEnvio.intentos < 3,
            EmailCampana.estado == "activa",
            EmailContacto.baja == False,  # noqa: E712
            EmailContacto.activo == True,  # noqa: E712
        )
        .limit(max_envios)
        .all()
    )

    resultados = {"enviados": 0, "fallidos": 0, "omitidos": 0}
    for envio in pendientes:
        campana = db.get(EmailCampana, envio.campana_id)
        contacto = db.get(EmailContacto, envio.contacto_id)
        config = db.get(EmailConfig, campana.config_id) if campana else None
        if not config or not contacto or not campana:
            resultados["omitidos"] += 1
            continue
        if not puede_enviar(config):
            resultados["omitidos"] += 1
            continue
        ok = enviar_un_correo(config, envio, contacto, campana, db)
        resultados["enviados" if ok else "fallidos"] += 1
    return resultados


def _render_asunto(campana: EmailCampana, contacto: EmailContacto) -> str:
    """Renderiza el asunto con Jinja usando el contexto del contacto."""
    tpl = (campana.asunto_template or campana.asunto or "").strip()
    if not tpl:
        return ""
    try:
        return Template(tpl).render(
            municipalidad=contacto.municipalidad or "",
            departamento=contacto.departamento or "",
            provincia=contacto.provincia or "",
            alcalde=contacto.alcalde or "",
            nombre=contacto.nombre or "",
            correo=contacto.correo or "",
        )[:300]
    except Exception as e:
        logger.warning("Error renderizando asunto: %s", e)
        return tpl[:300]


def _palabra_clave_segmento(seg: str) -> str:
    """Extrae la palabra clave de un segmento de campaña.

    Ejemplos:
      "A_secretaria"  -> "secretaria"
      "B_alcalde"     -> "alcalde"
      "secretaria"    -> "secretaria"
      "A — Secretaria"-> "secretaria"
    """
    if not seg:
        return ""
    s = seg.strip().lower()
    for sep in ("_", "—", "-", " "):
        if sep in s:
            partes = [p for p in s.split(sep) if p.strip()]
            if partes:
                s = partes[-1].strip()
    return s


def crear_envios_para_campana(db: Session, campana: EmailCampana) -> int:
    """Crea un EmailEnvio por cada contacto del segmento de la campaña.

    Match flexible: extrae la palabra clave del segmento de la campaña
    (ej. "A_secretaria" -> "secretaria") y filtra contactos cuyo segmento
    contenga esa palabra (ilike '%palabra%'). Esto tolera variantes como
    "A_secretaria", "A — Secretaria", "Secretaria municipal", etc.
    """
    from sqlalchemy import func

    seg_camp = (campana.segmento or "").strip()
    palabra = _palabra_clave_segmento(seg_camp)
    patron = f"%{palabra}%" if palabra else f"%{seg_camp}%"

    total_contactos_db = db.query(func.count(EmailContacto.id)).scalar() or 0
    total_segmento = db.query(func.count(EmailContacto.id)).filter(
        EmailContacto.segmento.ilike(patron),
    ).scalar() or 0

    contactos = db.query(EmailContacto).filter(
        EmailContacto.segmento.ilike(patron),
        EmailContacto.activo == True,  # noqa: E712
        EmailContacto.baja == False,  # noqa: E712
    ).all()

    logger.info(
        "crear_envios_para_campana(campana_id=%s, segmento=%r, patron=%r): "
        "total_contactos_db=%s, total_en_segmento=%s, "
        "elegibles_activos_no_baja=%s",
        campana.id, seg_camp, patron, total_contactos_db,
        total_segmento, len(contactos),
    )

    creados = 0
    for c in contactos:
        existe = db.query(EmailEnvio).filter_by(
            campana_id=campana.id, contacto_id=c.id
        ).first()
        if existe:
            continue
        db.add(EmailEnvio(
            campana_id=campana.id,
            contacto_id=c.id,
            token=generar_token(),
            estado="pendiente",
            asunto_final=_render_asunto(campana, c),
        ))
        creados += 1
    campana.total_contactos = (campana.total_contactos or 0) + creados
    db.commit()
    logger.info("crear_envios_para_campana: creados=%s", creados)
    return creados
