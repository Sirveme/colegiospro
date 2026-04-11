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


# ─── Tipos de documento ────────────────────────────────────────────
TIPOS = {
    "carta": {
        "label": "Carta",
        "icono": "✉️",
        "estructura": "Comunicación entre instituciones o personas. Encabezado formal, cuerpo en párrafos, despedida.",
        "partes": "Lugar y fecha / Destinatario con tratamiento / Asunto / Saludo / Cuerpo / Despedida / Firma",
    },
    "oficio": {
        "label": "Oficio",
        "icono": "📄",
        "estructura": "Documento oficial numerado entre instituciones públicas peruanas.",
        "partes": "Número de oficio (OFICIO N° ___-2026-[SIGLAS]) / Lugar y fecha / Destinatario / Asunto / Referencia (si aplica) / Cuerpo / Atentamente / Firma y sello",
    },
    "memorandum": {
        "label": "Memorándum",
        "icono": "📋",
        "estructura": "Comunicación interna rápida entre áreas o funcionarios.",
        "partes": "MEMORÁNDUM N° / PARA: / DE: / ASUNTO: / FECHA: / Cuerpo breve / Firma",
    },
    "circular": {
        "label": "Circular",
        "icono": "📢",
        "estructura": "Comunicado general dirigido a múltiples destinatarios simultáneamente.",
        "partes": "CIRCULAR N° / Lugar y fecha / A: (destinatarios) / ASUNTO: / Cuerpo / Firma",
    },
    "acta": {
        "label": "Acta de reunión",
        "icono": "📝",
        "estructura": "Registro formal de acuerdos tomados en una reunión o asamblea.",
        "partes": "ACTA N° / Lugar, fecha y hora / Asistentes / Agenda / Desarrollo de puntos / Acuerdos / Hora de cierre / Firmas",
    },
    "invitacion": {
        "label": "Invitación",
        "icono": "🎟️",
        "estructura": "Convocatoria formal a un evento institucional.",
        "partes": "Encabezado institucional / Fórmula de honor (tiene el agrado de invitar) / Detalles del evento / RSVP si aplica / Firma",
    },
    "certificado": {
        "label": "Certificado / Constancia",
        "icono": "🏅",
        "estructura": "Documento que certifica un hecho, cargo o condición.",
        "partes": "EL DECANO / HACE CONSTAR QUE: / Datos del certificado / A solicitud del interesado / Firma y sello",
    },
}


def _tipo_cfg(tipo: str) -> dict:
    return TIPOS.get((tipo or "").strip().lower(), TIPOS["carta"])


def listar_tipos() -> list:
    return [
        {"id": k, "label": v["label"], "icono": v["icono"]}
        for k, v in TIPOS.items()
    ]


# ─── Ajustes post-generación ───────────────────────────────────────
AJUSTES = {
    "mas_corto": {
        "label": "Más corto",
        "icono": "✂️",
        "system": (
            "Eres un editor experto en concisión institucional peruana. "
            "Acorta el documento al mínimo indispensable. CONSERVA la "
            "estructura formal completa (encabezado, destinatario, asunto, "
            "saludo, cuerpo, despedida, firma) y el tono. Elimina "
            "redundancias y fórmulas innecesarias. Devuelve SOLO el "
            "documento acortado, sin explicaciones."
        ),
    },
    "mas_largo": {
        "label": "Más largo",
        "icono": "📝",
        "system": (
            "Eres un redactor oficial peruano. Amplía este documento con "
            "más detalle, fundamento y formalidades apropiadas para el tono. "
            "Mantén la estructura. NO inventes datos que no existan. "
            "Devuelve SOLO el documento ampliado, sin explicaciones."
        ),
    },
    "sugerir_asunto": {
        "label": "Sugerir asunto",
        "icono": "💡",
        "system": (
            "Lee este documento y sugiere EXACTAMENTE 3 opciones de línea "
            "de 'Asunto:' — claras, precisas, formales, máximo 10 palabras "
            "cada una. Formato: '1. ...\\n2. ...\\n3. ...' — solo eso, sin "
            "encabezados ni explicaciones."
        ),
    },
    "version_whatsapp": {
        "label": "Versión WhatsApp",
        "icono": "💬",
        "system": (
            "Convierte este documento formal en un mensaje corto y directo "
            "para WhatsApp. Máximo 3 oraciones. Tono cordial y respetuoso, "
            "pero conversacional. Sin encabezados ni firma. Devuelve SOLO "
            "el mensaje."
        ),
    },
    "traducir_ingles": {
        "label": "Traducir al inglés",
        "icono": "🌐",
        "system": (
            "Traduce este documento al inglés formal manteniendo la "
            "estructura institucional, el tono y todos los datos. Devuelve "
            "SOLO la traducción."
        ),
    },
}


def listar_ajustes() -> list:
    return [
        {"id": k, "label": v["label"], "icono": v["icono"]}
        for k, v in AJUSTES.items()
    ]


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


def _build_system_prompt(tono: str, tipo_documento: str = "carta") -> str:
    cfg = _tono_cfg(tono)
    tipo = _tipo_cfg(tipo_documento)
    return f"""Eres un redactor oficial experto en documentos administrativos peruanos. Trabajas para secretarias de colegios profesionales (CCP, CIP, CMP, CAL, etc.) que reciben instrucciones verbales y necesitan convertirlas en documentos formales perfectos.

================================================================
REGLA FUNDAMENTAL — INTERPRETACIÓN, NO COPIA
================================================================
La instrucción del usuario describe QUÉ comunicar, en lenguaje coloquial, informal, a veces telegráfica. TÚ decides CÓMO decirlo en lenguaje formal peruano. NUNCA copies la instrucción literal.

Ejemplos de transformación correcta:
- Instrucción: "dile al alcalde que no podré ir a la reunión del jueves"
  → Cuerpo: "Por la presente, me dirijo a Usted con la finalidad de comunicarle, con el debido respeto, que por motivos institucionales del Colegio me veré imposibilitado de asistir a la reunión convocada para el día jueves..."
- Instrucción: "agradécele al rector por el préstamo del auditorio"
  → Cuerpo: "Tengo el agrado de dirigirme a Usted para expresarle, en nombre de nuestra institución, nuestro más sincero agradecimiento por la gentil atención de habernos facilitado el auditorio de la universidad..."

⚠️ Si la instrucción dice "dile al Alcalde que no voy", redacta una excusa formal de inasistencia — JAMÁS copies "dile al Alcalde que no voy". NUNCA escribas frases como "en atención a lo siguiente: [texto del usuario]" — eso es copiar, no redactar.

================================================================
TIPO DE DOCUMENTO: {tipo['label'].upper()}
================================================================
{tipo['estructura']}

Estructura obligatoria de partes:
{tipo['partes']}

================================================================
TONO OBLIGATORIO: {cfg['etiqueta'].upper()}
================================================================
Persona gramatical: {cfg['persona_gramatical']}.

Reglas específicas del tono {cfg['etiqueta']}:
{cfg['reglas']}

Saludo OBLIGATORIO de apertura: "{cfg['saludo']}"
Despedida OBLIGATORIA (al final del cuerpo, antes de la firma):
\"\"\"{cfg['despedida']}\"\"\"

Ejemplo de cómo debe sentirse la apertura del cuerpo en este tono:
«{cfg['ejemplo_apertura']}»

⚠️ El lector debe poder distinguir CLARAMENTE este tono de los otros (Formal, Cordial, Protocolar). Si el resultado se parece a otro tono, has fallado.

================================================================
REGLAS ADICIONALES
================================================================
- Español peruano estándar. Sin tuteo. Sin emojis.
- NUNCA inventes datos que no te dieron (números, fechas concretas, nombres). Si no los hay, redacta sin ellos o usa "[ ___ ]".
- Si algún dato del firmante está vacío, deja el espacio con guiones bajos.
- Si no hay destinatario, usa "Señor(a):" y "Presente.-".
- Si se te entrega un "Documento de referencia", úsalo SOLO como contexto (antecedentes, números de oficio, fechas). NO lo copies dentro del documento nuevo. Referéncialo con tus propias palabras.
- Responde EXCLUSIVAMENTE con el texto del documento, sin explicaciones, sin markdown, sin comillas envolventes."""


def _build_user_prompt(
    texto_entrada: str,
    destinatario: Optional[dict],
    remitente: Optional[dict],
    documento_referencia: Optional[str] = None,
    tipo_documento: str = "carta",
) -> str:
    fecha = _fecha_lima_legible()
    tipo = _tipo_cfg(tipo_documento)

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

    # Datos extra del remitente (perfil personalizado)
    nombre_firmante = (rem.get("nombre_firmante") or rem.get("nombre_decano") or "").strip()
    cargo_firmante = (rem.get("cargo_firmante") or "Decano").strip()
    tratamiento_firmante = (rem.get("tratamiento_firmante") or "").strip()

    return f"""=== TIPO DE DOCUMENTO ===
{tipo['label']}

=== INSTRUCCIÓN DEL USUARIO (en lenguaje coloquial — NO copies esto en el documento) ===
\"\"\"{texto_entrada}\"\"\"

Tu tarea:
1. Lee y COMPRENDE qué se necesita comunicar.
2. Redacta el documento completo en español peruano formal.
3. El cuerpo del documento debe expresar el CONTENIDO de la instrucción, NO la instrucción misma.
4. Si la instrucción dice "dile al Alcalde que no voy", redacta una excusa formal de inasistencia — no copies "dile al Alcalde que no voy".

=== DATOS DE LA CARTA ===

LUGAR Y FECHA: {ciudad}, {fecha}

DESTINATARIO:
{bloque_dest}

REMITENTE / FIRMANTE:
- Colegio: {colegio or '[Nombre del Colegio]'}
- Firmante: {(tratamiento_firmante + ' ' + nombre_firmante).strip() or '[Nombre del Firmante]'}
- Cargo: {cargo_firmante}
- Ciudad: {ciudad}
{bloque_ref}
Responde ÚNICAMENTE con el texto del documento. Sin explicaciones, sin markdown."""


def generar_documento(
    texto_entrada: str,
    tono: str = "formal",
    destinatario: Optional[dict] = None,
    remitente: Optional[dict] = None,
    documento_referencia: Optional[str] = None,
    tipo_documento: str = "carta",
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

    tipo_norm = (tipo_documento or "carta").strip().lower()
    if tipo_norm not in TIPOS:
        tipo_norm = "carta"

    system_prompt = _build_system_prompt(tono_norm, tipo_norm)
    user_prompt = _build_user_prompt(
        texto_entrada, destinatario, remitente, documento_referencia, tipo_norm
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
            if texto:
                return texto
        except Exception as e:
            return _fallback_borrador(
                texto_entrada, tono_norm, destinatario, remitente, error=str(e)
            )

    return _fallback_borrador(texto_entrada, tono_norm, destinatario, remitente)


def ajustar_documento(
    texto_actual: str,
    ajuste: str,
    api_key: Optional[str] = None,
    modelo: str = "gpt-4o",
) -> str:
    """
    Aplica un ajuste post-generación al texto: más corto, más largo,
    sugerir asunto, versión WhatsApp, traducir al inglés.
    """
    ajuste_norm = (ajuste or "").strip().lower()
    if ajuste_norm not in AJUSTES:
        return texto_actual

    cfg = AJUSTES[ajuste_norm]
    key = api_key or os.environ.get("OPENAI_API_KEY")

    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model=modelo,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": cfg["system"]},
                    {"role": "user", "content": texto_actual},
                ],
            )
            out = (resp.choices[0].message.content or "").strip()
            if out:
                return out
        except Exception as e:
            return (
                f"[BORRADOR LOCAL — Error al aplicar ajuste '{cfg['label']}': {e}]\n\n"
                f"{texto_actual}"
            )

    return (
        f"[BORRADOR LOCAL — La IA no está conectada. Para aplicar el ajuste "
        f"'{cfg['label']}' configura OPENAI_API_KEY en Railway.]\n\n"
        f"{texto_actual}"
    )


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
