# ══════════════════════════════════════════════════════════
# app/services/agenda_service.py
# Lógica de la Agenda Inteligente: sugerencias IA, alertas
# de la semana, plantillas de notificación al jefe.
# ══════════════════════════════════════════════════════════

import os
from datetime import datetime, timedelta
from typing import List, Optional

try:
    from openai import OpenAI
    _openai_available = True
except Exception:
    OpenAI = None
    _openai_available = False


# ─── Plantillas de notificación push al jefe ────────────────────
PLANTILLAS_NOTIF = {
    "recordatorio": {
        "titulo": "Reunión en {minutos} minutos",
        "cuerpo": "{titulo} · {lugar} · {hora}",
    },
    "sugerencia": {
        "titulo": "Antes de tu reunión con {participante}",
        "cuerpo": "{sugerencia_ia}",
    },
    "inicio": {
        "titulo": "Ahora: {titulo}",
        "cuerpo": "Duración estimada: {duracion} · {lugar}",
    },
    "siguiente": {
        "titulo": "Próxima cita: {hora}",
        "cuerpo": "{titulo} · en {minutos_restantes} minutos",
    },
}


# ─── Sugerencia IA pre-evento ──────────────────────────────────
def generar_sugerencia_evento(evento: dict) -> str:
    """
    GPT-4o genera una sugerencia breve (máximo 3 puntos) de qué
    debe preparar el titular antes del compromiso.
    """
    SYSTEM = """Eres un asistente ejecutivo experto. Dado un evento
de agenda, genera una sugerencia breve (máximo 3 puntos) de qué
debe preparar o tener en cuenta el titular antes de este compromiso.

Responde en español peruano formal. Máximo 80 palabras.
Formato: "- Punto 1\n- Punto 2\n- Punto 3"
Sin markdown, sin encabezados."""

    participantes = evento.get("participantes", []) or []
    if isinstance(participantes, list):
        nombres = ", ".join(
            (p.get("nombre", "") + " (" + p.get("cargo", "") + ")").strip(" ()")
            for p in participantes if isinstance(p, dict)
        )
    else:
        nombres = str(participantes)

    fecha = evento.get("fecha_inicio", "")
    if isinstance(fecha, datetime):
        fecha = fecha.strftime("%d/%m/%Y %H:%M")

    USER = f"""Evento: {evento.get('titulo','')}
Tipo: {evento.get('tipo','reunion')}
Participantes: {nombres or 'Sin participantes'}
Notas: {evento.get('descripcion','') or 'Sin notas'}
Hora: {fecha}"""

    key = os.environ.get("OPENAI_API_KEY")
    if not _openai_available or not key:
        return ""

    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            temperature=0.4,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


# ─── Alertas inteligentes de la semana ──────────────────────────
def analizar_semana(eventos: list) -> list:
    """Detecta problemas en la agenda y genera alertas."""
    alertas = []
    por_dia = {}

    for e in eventos:
        if not getattr(e, "fecha_inicio", None):
            continue
        dia = e.fecha_inicio.date()
        por_dia.setdefault(dia, []).append(e)

    dias_es = ["Lunes", "Martes", "Miércoles", "Jueves",
               "Viernes", "Sábado", "Domingo"]

    for dia, evs in por_dia.items():
        nombre_dia = f"{dias_es[dia.weekday()]} {dia.day}"
        reuniones = [e for e in evs if e.tipo == "reunion"]

        # 1) 3+ reuniones seguidas sin descanso
        if len(reuniones) >= 3:
            tiempos = sorted(reuniones, key=lambda x: x.fecha_inicio)
            for i in range(len(tiempos) - 2):
                gap1 = (tiempos[i + 1].fecha_inicio - tiempos[i].fecha_fin).total_seconds() / 60
                gap2 = (tiempos[i + 2].fecha_inicio - tiempos[i + 1].fecha_fin).total_seconds() / 60
                if gap1 < 15 and gap2 < 15:
                    alertas.append({
                        "tipo": "advertencia",
                        "icono": "⚠",
                        "texto": f"{nombre_dia}: 3+ reuniones seguidas sin descanso",
                        "accion": "Agregar buffer entre reuniones",
                    })
                    break

        # 2) Sin bloque de enfoque en días con muchas reuniones
        bloques = [e for e in evs if e.tipo == "bloque_enfoque"]
        if len(reuniones) >= 2 and not bloques:
            alertas.append({
                "tipo": "sugerencia",
                "icono": "💡",
                "texto": f"{nombre_dia}: Sin bloque de enfoque — el jefe no tendrá tiempo para trabajo profundo",
                "accion": "Agregar bloque 8-9am",
            })

        # 3) Reunión sin buffer antes
        for e in reuniones:
            if (e.buffer_antes or 0) == 0:
                alertas.append({
                    "tipo": "info",
                    "icono": "📋",
                    "texto": f"'{e.titulo}' sin tiempo de preparación",
                    "accion": "Agregar 15 min de buffer",
                })

    return alertas


# ─── Construir grilla semanal ───────────────────────────────────
def rango_semana(fecha: datetime) -> tuple:
    """Devuelve (lunes, domingo) de la semana que contiene 'fecha'."""
    lunes = fecha - timedelta(days=fecha.weekday())
    lunes = lunes.replace(hour=0, minute=0, second=0, microsecond=0)
    domingo = lunes + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return lunes, domingo


def agrupar_por_dia(eventos: list, lunes: datetime) -> dict:
    """Agrupa eventos por día de la semana (índice 0-6)."""
    grilla = {i: [] for i in range(7)}
    for e in eventos:
        if not e.fecha_inicio:
            continue
        delta = (e.fecha_inicio.date() - lunes.date()).days
        if 0 <= delta <= 6:
            grilla[delta].append(e)
    for k in grilla:
        grilla[k].sort(key=lambda x: x.fecha_inicio)
    return grilla


# ─── Notificaciones próximas ────────────────────────────────────
def buscar_notificaciones_pendientes(db, ahora: Optional[datetime] = None):
    """Devuelve eventos cuya notificación debe enviarse en los próximos minutos."""
    from app.models_secretaria import AgendaEvento
    if ahora is None:
        ahora = datetime.utcnow()
    return db.query(AgendaEvento).filter(
        AgendaEvento.notif_enviada == False,  # noqa: E712
        AgendaEvento.estado != "cancelado",
        AgendaEvento.fecha_inicio.between(
            ahora + timedelta(minutes=29),
            ahora + timedelta(minutes=31),
        )
    ).all()
