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
# Cada tono tiene:
# - persona_gramatical: 1ra (yo/me) o 3ra (el Decano / quien suscribe)
# - reglas: bloque de instrucciones específicas, fuerte y diferenciado
# - saludo / despedida: marcadores literales
# - ejemplo_apertura: una frase de muestra para anclar el estilo en GPT-4o
TONOS = {
    "formal": {
        "etiqueta": "Formal",
        "persona_gramatical": "primera persona del singular ('me dirijo', 'tengo el agrado')",
        "reglas": (
            "- Trato de Usted, primera persona del singular.\n"
            "- Vocabulario institucional peruano: 'en atención a', 'en mérito a', "
            "'me dirijo a Usted', 'tengo a bien comunicarle'.\n"
            "- Estructura sobria, sin adornos. Frases medianas, directas pero respetuosas.\n"
            "- NO uses fórmulas excesivamente solemnes (nada de 'alto honor', 'distinguida consideración').\n"
            "- 1 a 3 párrafos. Profesional y limpio."
        ),
        "saludo": "De mi mayor consideración:",
        "despedida": "Sin otro particular, hago propicia la ocasión para expresarle las muestras de mi especial consideración.\n\nAtentamente,",
        "ejemplo_apertura": "Por la presente, me dirijo a Usted con la finalidad de comunicarle...",
    },
    "cordial": {
        "etiqueta": "Cordial",
        "persona_gramatical": "primera persona del singular, con tono cálido",
        "reglas": (
            "- Trato de Usted, pero con CALIDEZ HUMANA evidente.\n"
            "- Incluye una frase explícita de saludo personal: 'aprovecho la ocasión para saludarlo afectuosamente', "
            "'reciba mis más cordiales saludos', 'es grato saludarlo'.\n"
            "- Vocabulario cercano: 'me complace', 'tengo el gusto de', 'con mucho aprecio'.\n"
            "- Reconoce explícitamente a la persona o su gestión cuando aplique.\n"
            "- NO uses fórmulas rígidas ni protocolares ('alto honor', 'mérito', 'digno despacho').\n"
            "- Sigue siendo formal en estructura, pero el lector debe SENTIR cercanía."
        ),
        "saludo": "Reciba usted un cordial saludo:",
        "despedida": "Agradeciendo de antemano su gentil atención y reiterándole mi aprecio personal, me despido cordialmente.\n\nCordialmente,",
        "ejemplo_apertura": "Reciba usted un cordial y afectuoso saludo. Me complace dirigirme a su persona para...",
    },
    "protocolar": {
        "etiqueta": "Protocolar",
        "persona_gramatical": (
            "TERCERA PERSONA institucional ('El Decano del Colegio... tiene el alto honor', "
            "'quien suscribe', 'esta Decanatura'). Evita el 'yo'."
        ),
        "reglas": (
            "- ESTILO DE OFICIO INSTITUCIONAL CEREMONIOSO peruano. Esta es la diferencia clave: "
            "es OBLIGATORIO usar TERCERA PERSONA o fórmulas impersonales solemnes.\n"
            "- Apertura OBLIGATORIA con una de estas fórmulas: "
            "'Tengo el alto honor de dirigirme a Usted', "
            "'Es grato dirigirme a Usted en su digno despacho', "
            "'Quien suscribe, en su calidad de Decano del Colegio..., tiene a bien dirigirse a Usted'.\n"
            "- Tratamiento COMPLETO del destinatario en el cuerpo: 'Su Despacho', 'Su digna autoridad', "
            "'Vuestra Autoridad'.\n"
            "- Fórmulas de cortesía EXTENSAS y reiteradas. Adjetivos solemnes: "
            "'distinguida', 'digna', 'alta', 'esclarecida', 'meritoria'.\n"
            "- Cuerpo más extenso (2-4 párrafos). Vale invertir tiempo en el preámbulo ceremonioso "
            "antes de entrar al asunto.\n"
            "- Cierre OBLIGATORIO con: 'Hago propicia la ocasión para reiterarle los sentimientos "
            "de mi más alta y distinguida consideración' o 'Aprovecho la oportunidad para expresarle "
            "las seguridades de mi más alta y distinguida consideración'.\n"
            "- PROHIBIDO usar lenguaje neutro o sobrio: este tono debe sentirse claramente diferente "
            "del Formal — más solemne, más extenso, más ceremonioso."
        ),
        "saludo": "Tengo el alto honor de dirigirme a Usted:",
        "despedida": "Hago propicia la ocasión para reiterarle los sentimientos de mi más alta y distinguida consideración.\n\nDios guarde a Usted,",
        "ejemplo_apertura": (
            "Tengo el alto honor de dirigirme a Usted, en mi condición de Decano del Colegio "
            "Profesional de [...], a fin de hacer de su conocimiento, con la consideración y el "
            "respeto que merece su digna autoridad, lo siguiente: ..."
        ),
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

================================================================
REGLA #1 — INTERPRETACIÓN, NO COPIA
================================================================
La instrucción que recibes del usuario es coloquial, informal, a veces telegráfica. NUNCA la copies textualmente en la carta. Tu trabajo es ENTENDER la intención y REDACTARLA de nuevo en lenguaje formal peruano apropiado.

NUNCA escribas frases como "en atención a lo siguiente: [texto del usuario]" — eso es copiar, no redactar.

================================================================
REGLA #2 — TONO OBLIGATORIO: {cfg['etiqueta'].upper()}
================================================================
Esta carta debe escribirse en TONO {cfg['etiqueta'].upper()}. Esto NO es decorativo — define la forma completa del texto.

Persona gramatical: {cfg['persona_gramatical']}.

Reglas específicas del tono {cfg['etiqueta']}:
{cfg['reglas']}

Saludo OBLIGATORIO de apertura: "{cfg['saludo']}"
Despedida OBLIGATORIA (debe ir al final del cuerpo, antes de la firma):
\"\"\"{cfg['despedida']}\"\"\"

Ejemplo de cómo debe sentirse la apertura del cuerpo en este tono:
«{cfg['ejemplo_apertura']}»

⚠️ El lector debe poder distinguir CLARAMENTE este tono de los otros dos (Formal, Cordial, Protocolar). Si el resultado se parece a otro tono, has fallado.

================================================================
ESTRUCTURA OBLIGATORIA DE LA CARTA
================================================================
1. Lugar y fecha
2. Línea en blanco
3. Bloque del destinatario: tratamiento + nombre / cargo / institución / "Presente.-"
4. Línea en blanco
5. "Asunto: <resumen breve de máximo 8 palabras>"
6. Línea en blanco
7. Saludo de apertura (el indicado arriba)
8. Cuerpo (extensión según el tono)
9. Despedida formal (la indicada arriba)
10. Firma: nombre del decano / "Decano" / nombre del colegio

================================================================
REGLAS ADICIONALES
================================================================
- Español peruano estándar. Sin tuteo. Sin emojis.
- NUNCA inventes datos que no te dieron (números, fechas concretas, nombres). Si la instrucción no los menciona, redacta sin ellos.
- Si no hay nombre del decano, deja "[Nombre del Decano]" como placeholder.
- Si no hay destinatario, usa "Señor(a):" y "Presente.-".
- Si se te entrega un "Documento de referencia", úsalo SOLO como contexto para entender mejor el caso (números de oficio, fechas, antecedentes). NO lo copies dentro de la carta nueva. Cita en el cuerpo lo que sea relevante en redacción propia.
- Responde EXCLUSIVAMENTE con el texto de la carta, sin explicaciones, sin markdown, sin comillas envolventes."""


def _build_user_prompt(
    texto_entrada: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
    documento_referencia: Optional[str] = None,
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

    # Bloque documento de referencia (opcional). Truncamos a 6000 chars para
    # no inflar el prompt si el archivo es muy largo.
    bloque_ref = ""
    if documento_referencia:
        ref = documento_referencia.strip()
        if len(ref) > 6000:
            ref = ref[:6000] + "\n[...documento truncado...]"
        bloque_ref = (
            "\n=== DOCUMENTO DE REFERENCIA ===\n"
            "(Contexto previo. NO copies este texto literal en la carta nueva. "
            "Úsalo solo para entender antecedentes, números de oficio, fechas, "
            "y referenciar lo que sea relevante con tus propias palabras.)\n\n"
            f"\"\"\"{ref}\"\"\"\n"
        )

    return f"""=== DATOS PARA LA CARTA ===

LUGAR Y FECHA: {ciudad}, {fecha}

DESTINATARIO:
{bloque_dest}

REMITENTE:
- Colegio: {colegio or '[Nombre del Colegio]'}
- Decano: {decano or '[Nombre del Decano]'}
- Ciudad: {ciudad}
{bloque_ref}
=== INSTRUCCIÓN COLOQUIAL DEL USUARIO ===
(Esto es lo que la secretaria escribió en lenguaje informal. NO lo copies textualmente. Interpreta la intención y redacta la carta formal correspondiente.)

\"\"\"{texto_entrada}\"\"\"

=== TAREA ===
Redacta la carta oficial completa siguiendo la estructura y el TONO indicados en las instrucciones del sistema. Devuelve SOLO el texto de la carta."""


def generar_documento(
    texto_entrada: str,
    tono: str = "formal",
    destinatario: Optional[dict] = None,
    remitente: Optional[dict] = None,
    documento_referencia: Optional[str] = None,
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
    user_prompt = _build_user_prompt(
        texto_entrada, destinatario, remitente, documento_referencia
    )

    key = api_key or os.environ.get("OPENAI_API_KEY")
    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.6,
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
