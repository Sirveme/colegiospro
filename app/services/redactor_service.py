# ══════════════════════════════════════════════════════════
# app/services/redactor_service.py
# Modo 1: Redactor ultrarrápido — llama a GPT-4o
# ══════════════════════════════════════════════════════════

import os
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

# OpenAI SDK (>=1.x). Si no está instalado, devolvemos un fallback útil
# para que el endpoint no se rompa en desarrollo.
try:
    from openai import OpenAI
    _openai_available = True
except Exception:
    OpenAI = None
    _openai_available = False


SYSTEM_PROMPT = """Eres un asistente de redacción oficial para secretarias de colegios profesionales del Perú. Redactas documentos formales en español peruano estándar.

Reglas:
- Fecha actual en Lima, Perú
- Estructura: lugar y fecha / destinatario con tratamiento / asunto / cuerpo / despedida / firma
- Tono {tono}: formal=protocolo estricto, cordial=cálido pero profesional, protocolar=muy ceremonioso
- Si hay destinatario: usa nombre, cargo e institución exactos
- Firma: "[Nombre Decano si existe] / Decano / [Nombre Colegio]" — si no hay datos, deja espacio
- Máximo 3 párrafos salvo que el contenido exija más
- NUNCA inventes datos que no te dieron
- Responde SOLO con el texto del documento, sin explicaciones"""


def _fecha_lima_legible() -> str:
    meses = [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ]
    try:
        ahora = datetime.now(ZoneInfo("America/Lima"))
    except Exception:
        ahora = datetime.now()
    return f"{ahora.day} de {meses[ahora.month - 1]} de {ahora.year}"


def _build_user_prompt(
    texto_entrada: str,
    tono: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
) -> str:
    if destinatario:
        dest_line = (
            f"{destinatario.get('titular_tratamiento') or ''} "
            f"{destinatario.get('titular_nombre') or ''}, "
            f"{destinatario.get('titular_cargo') or ''} de "
            f"{destinatario.get('nombre_institucion') or ''}"
        ).strip()
    else:
        dest_line = "(sin destinatario específico)"

    if remitente:
        rem_line = f"{remitente.get('nombre_colegio') or ''}, {remitente.get('ciudad') or ''}".strip(", ")
    else:
        rem_line = "(remitente no configurado)"

    return (
        f"Instrucción coloquial: {texto_entrada}\n"
        f"Destinatario: {dest_line}\n"
        f"Remitente: {rem_line}\n"
        f"Fecha: {_fecha_lima_legible()}\n"
        f"Tono: {tono}\n"
    )


def generar_documento(
    texto_entrada: str,
    tono: str = "formal",
    destinatario: Optional[dict] = None,
    remitente: Optional[dict] = None,
    api_key: Optional[str] = None,
    modelo: str = "gpt-4o",
) -> str:
    """
    Devuelve el texto del documento generado por GPT-4o.
    Si no hay API key o el SDK no está instalado, devuelve un borrador
    plantilla con los datos disponibles para no romper la UX.
    """
    user_prompt = _build_user_prompt(texto_entrada, tono, destinatario, remitente)
    system_prompt = SYSTEM_PROMPT.replace("{tono}", tono)

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:
            return _fallback_borrador(texto_entrada, tono, destinatario, remitente, error=str(e))

    return _fallback_borrador(texto_entrada, tono, destinatario, remitente)


def _fallback_borrador(
    texto_entrada: str,
    tono: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
    error: Optional[str] = None,
) -> str:
    fecha = _fecha_lima_legible()
    ciudad = (remitente or {}).get("ciudad") or "Lima"
    colegio = (remitente or {}).get("nombre_colegio") or "[Nombre del Colegio]"
    decano = (remitente or {}).get("nombre_decano") or "[Nombre del Decano]"

    if destinatario:
        bloque_dest = (
            f"{destinatario.get('titular_tratamiento') or ''} "
            f"{destinatario.get('titular_nombre') or ''}\n"
            f"{destinatario.get('titular_cargo') or ''}\n"
            f"{destinatario.get('nombre_institucion') or ''}\n"
            f"Presente.-"
        )
    else:
        bloque_dest = "Señor(a):\nPresente.-"

    nota = ""
    if error:
        nota = f"\n\n[Borrador local generado — error API: {error}]"

    return (
        f"{ciudad}, {fecha}\n\n"
        f"{bloque_dest}\n\n"
        f"Asunto: Comunicación oficial\n\n"
        f"De mi mayor consideración:\n\n"
        f"Por medio de la presente, en atención a lo siguiente: {texto_entrada}\n\n"
        f"Hago propicia la ocasión para expresarle los sentimientos de mi especial "
        f"consideración y estima personal.\n\n"
        f"Atentamente,\n\n\n"
        f"_____________________________\n"
        f"{decano}\n"
        f"Decano\n"
        f"{colegio}{nota}"
    )
