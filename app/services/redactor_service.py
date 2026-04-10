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


# ─── Instrucciones por tono ──────────────────────────────────────────
TONOS = {
    "formal": {
        "etiqueta": "Formal",
        "descripcion": (
            "Lenguaje protocolar peruano estándar. Trato de Usted. "
            "Vocabulario institucional ('en atención a', 'en mérito a', "
            "'me dirijo a Usted'). Frases cerradas y respetuosas. Sin coloquialismos."
        ),
        "saludo": "De mi mayor consideración:",
        "despedida": "Sin otro particular, hago propicia la ocasión para expresarle las muestras de mi especial consideración.\n\nAtentamente,",
    },
    "cordial": {
        "etiqueta": "Cordial",
        "descripcion": (
            "Cálido pero profesional. Trato de Usted, pero con frases más humanas y cercanas. "
            "Reconoce a la persona ('aprovecho la ocasión para saludarlo cordialmente'). "
            "Evita la rigidez del protocolo estricto, pero sigue siendo formal en estructura."
        ),
        "saludo": "Reciba usted un cordial saludo:",
        "despedida": "Agradeciendo de antemano su gentil atención, me despido cordialmente.\n\nCordialmente,",
    },
    "protocolar": {
        "etiqueta": "Protocolar",
        "descripcion": (
            "Muy ceremonioso. Estilo de oficio institucional peruano. "
            "Usa fórmulas solemnes: 'Tengo el alto honor de dirigirme a Usted', "
            "'es grato dirigirme a Usted en su digno despacho'. "
            "Tratamiento completo del cargo y de la institución del destinatario."
        ),
        "saludo": "Tengo el alto honor de dirigirme a Usted:",
        "despedida": "Hago propicia la ocasión para reiterarle los sentimientos de mi más alta y distinguida consideración.\n\nDios guarde a Usted,",
    },
}


def _tono_cfg(tono: str) -> dict:
    return TONOS.get((tono or "").strip().lower(), TONOS["formal"])


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


def _build_system_prompt(tono: str) -> str:
    cfg = _tono_cfg(tono)
    return f"""Eres un redactor oficial experto en correspondencia institucional del Perú. Trabajas para secretarias de colegios profesionales (CCP, CIP, CMP, CAL, etc.) que reciben instrucciones verbales y necesitan convertirlas en cartas oficiales bien redactadas.

REGLA #1 — INTERPRETACIÓN, NO COPIA:
La instrucción que recibes del usuario es coloquial, informal, a veces telegráfica. NUNCA la copies textualmente en la carta. Tu trabajo es ENTENDER la intención y REDACTARLA de nuevo en lenguaje formal peruano apropiado.

Ejemplos de transformación correcta:
- Instrucción: "dile al alcalde que no podré ir a la reunión del jueves"
  → Cuerpo: "Por la presente, me dirijo a Usted con la finalidad de comunicarle, con el debido respeto, que por motivos institucionales del Colegio me veré imposibilitado de asistir a la reunión convocada para el día jueves..."
- Instrucción: "agradécele al rector por el préstamo del auditorio"
  → Cuerpo: "Tengo el agrado de dirigirme a Usted para expresarle, en nombre de nuestra institución, nuestro más sincero agradecimiento por la gentil atención de habernos facilitado el auditorio de la universidad..."

NUNCA escribas frases como "en atención a lo siguiente: [texto del usuario]" — eso es copiar, no redactar.

ESTRUCTURA OBLIGATORIA:
1. Lugar y fecha (alineado a la derecha conceptualmente; sólo texto plano)
2. Línea en blanco
3. Bloque del destinatario: tratamiento + nombre / cargo / institución / "Presente.-"
4. Línea en blanco
5. "Asunto: <resumen breve de máximo 8 palabras>"
6. Línea en blanco
7. Saludo de apertura
8. Cuerpo (1 a 3 párrafos, según lo amerite)
9. Despedida formal
10. Firma: nombre del decano / "Decano" / nombre del colegio

TONO REQUERIDO PARA ESTA CARTA — {cfg['etiqueta'].upper()}:
{cfg['descripcion']}
- Saludo sugerido: "{cfg['saludo']}"
- Despedida sugerida: "{cfg['despedida'].splitlines()[0]}"

REGLAS ADICIONALES:
- Español peruano estándar. Sin tuteo. Sin emojis.
- NUNCA inventes datos que no te dieron (números, fechas concretas, nombres). Si la instrucción no los menciona, redacta sin ellos.
- Si no hay nombre del decano, deja "[Nombre del Decano]" como placeholder.
- Si no hay destinatario, usa "Señor(a):" y "Presente.-".
- Responde EXCLUSIVAMENTE con el texto de la carta, sin explicaciones, sin markdown, sin comillas envolventes."""


def _build_user_prompt(
    texto_entrada: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
) -> str:
    fecha = _fecha_lima_legible()

    # Bloque destinatario
    if destinatario:
        dest_partes = []
        trat = (destinatario.get("titular_tratamiento") or "").strip()
        nom = (destinatario.get("titular_nombre") or "").strip()
        cargo = (destinatario.get("titular_cargo") or "").strip()
        inst = (destinatario.get("nombre_institucion") or "").strip()
        if trat or nom:
            dest_partes.append(f"{trat} {nom}".strip())
        if cargo:
            dest_partes.append(cargo)
        if inst:
            dest_partes.append(inst)
        bloque_dest = "\n".join(dest_partes) if dest_partes else "(sin destinatario específico)"
    else:
        bloque_dest = "(sin destinatario específico)"

    # Bloque remitente
    rem = remitente or {}
    colegio = (rem.get("nombre_colegio") or "").strip()
    decano = (rem.get("nombre_decano") or "").strip()
    ciudad = (rem.get("ciudad") or "Lima").strip()

    return f"""=== DATOS PARA LA CARTA ===

LUGAR Y FECHA: {ciudad}, {fecha}

DESTINATARIO:
{bloque_dest}

REMITENTE:
- Colegio: {colegio or '[Nombre del Colegio]'}
- Decano: {decano or '[Nombre del Decano]'}
- Ciudad: {ciudad}

=== INSTRUCCIÓN COLOQUIAL DEL USUARIO ===
(Esto es lo que la secretaria escribió en lenguaje informal. NO lo copies textualmente. Interpreta la intención y redacta la carta formal correspondiente.)

\"\"\"{texto_entrada}\"\"\"

=== TAREA ===
Redacta la carta oficial completa siguiendo la estructura y el tono indicados en las instrucciones del sistema. Devuelve SOLO el texto de la carta."""


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
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in TONOS:
        tono_norm = "formal"

    system_prompt = _build_system_prompt(tono_norm)
    user_prompt = _build_user_prompt(texto_entrada, destinatario, remitente)

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.5,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            texto = (resp.choices[0].message.content or "").strip()
            # Si por alguna razón la API devolvió vacío, caer al borrador
            if texto:
                return texto
        except Exception as e:
            return _fallback_borrador(
                texto_entrada, tono_norm, destinatario, remitente, error=str(e)
            )

    return _fallback_borrador(texto_entrada, tono_norm, destinatario, remitente)


def _fallback_borrador(
    texto_entrada: str,
    tono: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
    error: Optional[str] = None,
) -> str:
    """
    Borrador local cuando no hay OPENAI_API_KEY o falla la API.
    Respeta el tono y NO inserta el texto coloquial literal en el cuerpo.
    """
    cfg = _tono_cfg(tono)
    fecha = _fecha_lima_legible()
    rem = remitente or {}
    ciudad = (rem.get("ciudad") or "Lima").strip()
    colegio = (rem.get("nombre_colegio") or "[Nombre del Colegio]").strip()
    decano = (rem.get("nombre_decano") or "[Nombre del Decano]").strip()

    if destinatario:
        partes = []
        trat = (destinatario.get("titular_tratamiento") or "").strip()
        nom = (destinatario.get("titular_nombre") or "").strip()
        cargo = (destinatario.get("titular_cargo") or "").strip()
        inst = (destinatario.get("nombre_institucion") or "").strip()
        if trat or nom:
            partes.append(f"{trat} {nom}".strip())
        if cargo:
            partes.append(cargo)
        if inst:
            partes.append(inst)
        partes.append("Presente.-")
        bloque_dest = "\n".join(partes)
    else:
        bloque_dest = "Señor(a):\nPresente.-"

    aviso = (
        "[BORRADOR LOCAL — La IA no está conectada (configura OPENAI_API_KEY "
        "en Railway para activar la redacción automática con GPT-4o).]"
    )
    if error:
        aviso = f"[BORRADOR LOCAL — Error al llamar a GPT-4o: {error}]"

    cuerpo_placeholder = (
        "[Este es un borrador automático. La IA generaría aquí la versión "
        "formal de la siguiente intención de la secretaria:\n\n"
        f"  «{texto_entrada}»\n\n"
        "Redacte el cuerpo de la carta interpretando esta instrucción.]"
    )

    return (
        f"{aviso}\n\n"
        f"{ciudad}, {fecha}\n\n"
        f"{bloque_dest}\n\n"
        f"Asunto: Comunicación oficial\n\n"
        f"{cfg['saludo']}\n\n"
        f"{cuerpo_placeholder}\n\n"
        f"{cfg['despedida']}\n\n\n"
        f"_____________________________\n"
        f"{decano}\n"
        f"Decano\n"
        f"{colegio}"
    )
