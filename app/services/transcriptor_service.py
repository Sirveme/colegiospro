# ══════════════════════════════════════════════════════════
# app/services/transcriptor_service.py
# Audio → texto (Whisper) → documentos (acta/resumen/etc.)
# ══════════════════════════════════════════════════════════

import os
from io import BytesIO
from typing import Optional

try:
    from openai import OpenAI
    _openai_available = True
except Exception:
    OpenAI = None
    _openai_available = False

try:
    from pydub import AudioSegment
    _has_pydub = True
except Exception:
    AudioSegment = None
    _has_pydub = False


# ─── Procesar audio: cortar tramos + Whisper ──────────────────
def procesar_audio(
    audio_bytes: bytes,
    audio_nombre: str,
    tramos_excluidos: list,
    titulo: str = "",
) -> dict:
    """
    1. Corta tramos sensibles si pydub está disponible
    2. Envía a Whisper
    3. Devuelve dict con texto, segmentos y duración
    """
    ext = (audio_nombre.rsplit('.', 1)[-1] or "mp3").lower()

    duracion_seg = 0
    audio_para_whisper = None
    audio_filename = audio_nombre

    if _has_pydub:
        try:
            audio = AudioSegment.from_file(BytesIO(audio_bytes), format=ext)
            if tramos_excluidos:
                audio = _excluir_tramos(audio, tramos_excluidos)
            duracion_seg = int(audio.duration_seconds)
            buffer = BytesIO()
            audio.export(buffer, format="mp3")
            buffer.seek(0)
            audio_para_whisper = buffer
            audio_filename = "audio_recortado.mp3"
        except Exception as e:
            return {
                "texto": "",
                "segmentos": [],
                "duracion_seg": 0,
                "error": f"Error procesando audio: {e}",
            }
    else:
        # Sin pydub no podemos cortar — enviamos el audio crudo
        audio_para_whisper = BytesIO(audio_bytes)

    key = os.environ.get("OPENAI_API_KEY")
    if not _openai_available or not key:
        return {
            "texto": "",
            "segmentos": [],
            "duracion_seg": duracion_seg,
            "error": "OPENAI_API_KEY no configurada o SDK no disponible",
        }

    try:
        client = OpenAI(api_key=key)
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=(audio_filename, audio_para_whisper, "audio/mpeg"),
            language="es",
            response_format="verbose_json",
            timestamp_granularities=["segment"],
        )
        segmentos = []
        for s in getattr(transcript, "segments", []) or []:
            segmentos.append({
                "inicio": getattr(s, "start", 0),
                "fin": getattr(s, "end", 0),
                "texto": getattr(s, "text", ""),
            })
        return {
            "texto": getattr(transcript, "text", ""),
            "segmentos": segmentos,
            "duracion_seg": duracion_seg,
            "error": None,
        }
    except Exception as e:
        return {
            "texto": "",
            "segmentos": [],
            "duracion_seg": duracion_seg,
            "error": f"Error en Whisper: {e}",
        }


def _excluir_tramos(audio, tramos: list):
    """Reconstruye el audio saltando los tramos excluidos."""
    if not _has_pydub:
        return audio
    resultado = AudioSegment.empty()
    pos_actual = 0
    tramos_sorted = sorted(tramos, key=lambda x: x.get("inicio_seg", 0))
    for tramo in tramos_sorted:
        inicio_ms = int(tramo.get("inicio_seg", 0)) * 1000
        fin_ms = int(tramo.get("fin_seg", 0)) * 1000
        if pos_actual < inicio_ms:
            resultado += audio[pos_actual:inicio_ms]
        pos_actual = fin_ms
    if pos_actual < len(audio):
        resultado += audio[pos_actual:]
    return resultado


# ─── Tipos de documento generables desde transcripción ────────
TIPOS_DOC = {
    "acta": {
        "label": "Acta de Reunión",
        "instruccion": """Redacta un ACTA FORMAL de reunión con:
- Encabezado: número de acta, fecha, hora, lugar
- Lista de asistentes con cargo
- Agenda tratada (puntos numerados)
- Desarrollo de cada punto
- Acuerdos tomados (numerados, con responsable y fecha límite)
- Hora de cierre
- Espacios para firmas""",
    },
    "resumen": {
        "label": "Resumen Ejecutivo",
        "instruccion": """Redacta un RESUMEN EJECUTIVO de máximo 1 página con:
- Título, fecha y participantes clave
- Objetivo de la reunión
- Puntos principales discutidos (3-5 bullets)
- Decisiones tomadas
- Próximos pasos""",
    },
    "acuerdos": {
        "label": "Lista de Acuerdos",
        "instruccion": """Extrae ÚNICAMENTE los acuerdos y compromisos de la reunión en formato:
ACUERDO N° 1: [descripción]
Responsable: [nombre]
Fecha límite: [fecha o "Por definir"]
Estado: Pendiente

(repetir para cada acuerdo)""",
    },
    "informe": {
        "label": "Informe de Directorio",
        "instruccion": """Redacta un INFORME FORMAL para el Directorio con:
- Resumen ejecutivo
- Antecedentes
- Temas tratados con análisis
- Conclusiones y recomendaciones
- Acuerdos adoptados
Tono: formal institucional peruano""",
    },
}


def listar_tipos_doc():
    return [
        {"id": k, "label": v["label"]}
        for k, v in TIPOS_DOC.items()
    ]


# ─── Generar documento desde transcripción ────────────────────
def generar_documento_reunion(
    texto_transcripcion: str,
    tipo: str,
    titulo_reunion: str,
    participantes: list,
    config_org: dict,
) -> str:
    """GPT-4o convierte la transcripción en el documento solicitado."""
    tipo_cfg = TIPOS_DOC.get(tipo, TIPOS_DOC["acta"])
    anno = config_org.get(
        "anno_oficial",
        "Año del Bicentenario de la Integración Latinoamericana y Caribeña",
    )

    SYSTEM = f"""Eres un redactor experto en documentos institucionales peruanos.
Recibirás la transcripción de una reunión y debes generar el documento solicitado.

REGLAS:
- Año oficial: "{anno}"
- Institución: {config_org.get('nombre_organizacion', '')}
- Usa lenguaje formal peruano
- Si algo no quedó claro en la transcripción, usa [POR CONFIRMAR]
- NO inventes datos que no estén en la transcripción

TIPO DE DOCUMENTO: {tipo_cfg['label']}
{tipo_cfg['instruccion']}

Responde SOLO con el texto del documento."""

    nombres = ", ".join(
        (p.get("nombre", "") if isinstance(p, dict) else str(p))
        for p in (participantes or [])
    )
    USER = f"""REUNIÓN: {titulo_reunion}
PARTICIPANTES: {nombres or 'No especificados'}

TRANSCRIPCIÓN:
{texto_transcripcion[:8000]}"""

    key = os.environ.get("OPENAI_API_KEY")
    if not _openai_available or not key:
        return f"[BORRADOR LOCAL — La IA no está conectada]\n\nReunión: {titulo_reunion}\n\n{texto_transcripcion[:2000]}"

    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.2,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return f"[Error generando {tipo_cfg['label']}: {e}]\n\n{texto_transcripcion[:2000]}"


# ─── Helper: parsear tiempo "MM:SS" a segundos ───────────────
def parse_tiempo(s: str) -> int:
    """Convierte 'MM:SS' o 'HH:MM:SS' a segundos."""
    if not s:
        return 0
    partes = [int(x) for x in s.strip().split(":") if x.isdigit()]
    if len(partes) == 2:
        return partes[0] * 60 + partes[1]
    if len(partes) == 3:
        return partes[0] * 3600 + partes[1] * 60 + partes[2]
    return 0


def formato_tiempo(seg: float) -> str:
    """Convierte segundos a 'MM:SS'."""
    seg = int(seg or 0)
    return f"{seg // 60:02d}:{seg % 60:02d}"
