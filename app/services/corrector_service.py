# ══════════════════════════════════════════════════════════
# app/services/corrector_service.py
# Modo 2: Corrector — recibe un texto y aplica una de las
# 5 acciones (ortografía, redacción, tono formal, acortar, formalizar).
# ══════════════════════════════════════════════════════════

import os
from typing import Optional

try:
    from openai import OpenAI  # type: ignore
    _openai_available = True
except Exception:
    OpenAI = None
    _openai_available = False


# ─── Acciones disponibles ──────────────────────────────────────────
ACCIONES = {
    "ortografia": {
        "etiqueta": "Corregir ortografía",
        "icono": "✓",
        "system": (
            "Eres un corrector ortográfico y gramatical experto en español "
            "peruano. Corrige ÚNICAMENTE errores ortográficos, de tildes, "
            "puntuación y gramática. NO cambies el estilo, ni el orden de "
            "las ideas, ni el vocabulario. Mantén EXACTAMENTE el mismo tono "
            "y longitud. Devuelve SOLO el texto corregido, sin explicaciones."
        ),
    },
    "redaccion": {
        "etiqueta": "Mejorar redacción",
        "icono": "✎",
        "system": (
            "Eres un editor experto en redacción institucional peruana. "
            "Mejora la redacción del texto manteniendo el significado original. "
            "Hazlo más claro, fluido y profesional. Puedes reordenar frases, "
            "elegir sinónimos mejores y eliminar redundancias, pero NO cambies "
            "la intención ni agregues información nueva. Devuelve SOLO el texto "
            "mejorado, sin explicaciones."
        ),
    },
    "tono_formal": {
        "etiqueta": "Cambiar a tono formal",
        "icono": "🎩",
        "system": (
            "Eres un redactor oficial peruano. Convierte el texto recibido al "
            "tono formal institucional peruano. Trato de Usted, vocabulario "
            "institucional ('me dirijo a Usted', 'en atención a', 'tengo a "
            "bien comunicarle'). Mantén el mensaje original, solo cambia el "
            "registro. Devuelve SOLO el texto en tono formal, sin explicaciones."
        ),
    },
    "acortar": {
        "etiqueta": "Acortar",
        "icono": "✂",
        "system": (
            "Eres un editor experto en concisión. Acorta el texto al mínimo "
            "necesario sin perder el mensaje principal. Elimina rellenos, "
            "frases redundantes y formalidades excesivas. Conserva los datos "
            "concretos (números, fechas, nombres). Devuelve SOLO el texto "
            "acortado, sin explicaciones."
        ),
    },
    "formalizar": {
        "etiqueta": "Formalizar (coloquial → oficio)",
        "icono": "📜",
        "system": (
            "Eres un redactor oficial peruano. Convierte este texto informal "
            "o coloquial en un documento formal oficial completo. Aplica "
            "estructura de carta institucional peruana: lugar/fecha, "
            "destinatario, asunto, cuerpo formal, despedida. Vocabulario "
            "institucional. NO inventes datos. Devuelve SOLO el documento "
            "formal completo, sin explicaciones ni markdown."
        ),
    },
}


def listar_acciones() -> list:
    """Devuelve la lista ordenada para el template."""
    orden = ["ortografia", "redaccion", "tono_formal", "acortar", "formalizar"]
    return [
        {"id": k, "etiqueta": ACCIONES[k]["etiqueta"], "icono": ACCIONES[k]["icono"]}
        for k in orden
    ]


def corregir_texto(
    texto: str,
    accion: str,
    api_key: Optional[str] = None,
    modelo: str = "gpt-4o",
) -> str:
    """
    Aplica `accion` sobre `texto` y devuelve el resultado.
    Si no hay API key o falla, devuelve un fallback con el texto original
    marcado para que el usuario sepa que no hubo procesamiento real.
    """
    accion = (accion or "").strip().lower()
    if accion not in ACCIONES:
        accion = "ortografia"

    cfg = ACCIONES[accion]
    key = api_key or os.environ.get("OPENAI_API_KEY")

    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.3,
                messages=[
                    {"role": "system", "content": cfg["system"]},
                    {"role": "user", "content": texto},
                ],
            )
            out = (resp.choices[0].message.content or "").strip()
            if out:
                return out
        except Exception as e:
            return _fallback(texto, accion, error=str(e))

    return _fallback(texto, accion)


def _fallback(texto: str, accion: str, error: Optional[str] = None) -> str:
    aviso = (
        f"[BORRADOR LOCAL — La IA no está conectada. Para activar la acción "
        f"'{ACCIONES[accion]['etiqueta']}' configura OPENAI_API_KEY en Railway.]"
    )
    if error:
        aviso = f"[BORRADOR LOCAL — Error al llamar a GPT-4o: {error}]"
    return f"{aviso}\n\n{texto}"
