# ══════════════════════════════════════════════════════════
# app/services/redactor_service.py
# Modo 1: Redactor ultrarrápido — llama a GPT-4o
# ══════════════════════════════════════════════════════════

import json
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
    "memorandum_multiple": {
        "label": "Memorándum Múltiple",
        "icono": "📋",
        "estructura": "Comunicación interna simultánea a varios destinatarios.",
        "partes": "MEMORÁNDUM MÚLTIPLE N° / PARA: [lista de destinatarios] / DE: / ASUNTO: / FECHA: / Cuerpo / Firma",
        "persona": "primera_plural",
        "audiencia": "interno",
        "permite_lista_destinatarios": True,
    },
    "oficio_multiple": {
        "label": "Oficio Múltiple",
        "icono": "📨",
        "estructura": "Comunicación oficial enviada simultáneamente a varias instituciones externas.",
        "partes": "OFICIO MÚLTIPLE N° / Lugar y fecha / A: [lista de instituciones] / ASUNTO: / Cuerpo / Firma y sello",
        "persona": "primera_plural",
        "audiencia": "externo_multiple",
        "permite_lista_destinatarios": True,
    },
    "resolucion": {
        "label": "Resolución",
        "icono": "⚖️",
        "estructura": "Acto administrativo formal con estructura legal obligatoria.",
        "partes": "RESOLUCIÓN N° / VISTOS: / CONSIDERANDO: / SE RESUELVE: / REGÍSTRESE COMUNÍQUESE Y ARCHÍVESE. / Firma",
        "persona": "tercera",
        "audiencia": "formal_legal",
        "partes_obligatorias": ["VISTOS:", "CONSIDERANDO:", "SE RESUELVE:"],
    },
    "orden_pedido": {
        "label": "Orden de Pedido",
        "icono": "🛒",
        "estructura": "Documento comercial para solicitar bienes o servicios a un proveedor.",
        "partes": "ORDEN DE PEDIDO N° / Fecha / Proveedor / Tabla: ítem-descripción-cantidad-precio unitario-total / Condiciones / Firma",
        "persona": "institucional",
        "audiencia": "proveedor",
        "requiere_tabla": True,
    },
    "comunicado_general": {
        "label": "Comunicado",
        "icono": "📢",
        "estructura": "Anuncio institucional sin destinatario específico, para difusión general.",
        "partes": "COMUNICADO / Cuerpo directo y claro / Firma institucional",
        "persona": "institucional",
        "audiencia": "publico_general",
        "sin_saludo": True,
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
    "version_email": {
        "label": "Versión Email",
        "icono": "📧",
        "system": (
            "Convierte este documento en un correo electrónico profesional. "
            "Sin membrete, sin numeración oficial. Incluye: (1) saludo "
            "adecuado con el nombre del destinatario si está disponible, "
            "(2) cuerpo claro y conciso, (3) despedida cordial con firma "
            "simple de nombre y cargo. Tono amable pero profesional, "
            "apropiado para correo electrónico institucional. Devuelve "
            "SOLO el texto del correo."
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


# ─── Género en remitentes ─────────────────────────────────────────
TRATAMIENTOS_GENERO = {
    "Dr.":   {"M": "Dr.",    "F": "Dra."},
    "Mg.":   {"M": "Mg.",    "F": "Mg."},
    "CPC.":  {"M": "CPC.",   "F": "CPC."},
    "CPC":   {"M": "CPC.",   "F": "CPC."},
    "Abog.": {"M": "Abog.",  "F": "Abog."},
    "Ing.":  {"M": "Ing.",   "F": "Ing."},
    "Lic.":  {"M": "Lic.",   "F": "Lic."},
    "Sr.":   {"M": "Sr.",    "F": "Sra."},
    "Bach.": {"M": "Bach.",  "F": "Bach."},
}

CARGOS_GENERO = {
    "Decano":        {"M": "Decano",        "F": "Decana"},
    "Director":      {"M": "Director",      "F": "Directora"},
    "Presidente":    {"M": "Presidente",    "F": "Presidenta"},
    "Secretario":    {"M": "Secretario",    "F": "Secretaria"},
    "Tesorero":      {"M": "Tesorero",      "F": "Tesorera"},
    "Vocal":         {"M": "Vocal",         "F": "Vocal"},
    "Administrador": {"M": "Administrador", "F": "Administradora"},
}


def resolver_genero(perfil: dict) -> dict:
    """Aplica género correcto a tratamiento y cargo."""
    sexo = perfil.get("sexo", "M")
    trat = perfil.get("tratamiento", "Sr.")
    cargo = perfil.get("cargo", "")

    perfil["tratamiento_resuelto"] = TRATAMIENTOS_GENERO.get(
        trat, {"M": trat, "F": trat}
    ).get(sexo, trat)
    perfil["cargo_resuelto"] = CARGOS_GENERO.get(
        cargo, {"M": cargo, "F": cargo}
    ).get(sexo, cargo)
    perfil["articulo"] = "El" if sexo == "M" else "La"
    return perfil


# ─── Agente clasificador ──────────────────────────────────────────
TIPOS_HABILITADOS_DEFAULT = list(TIPOS.keys())


def clasificar_instruccion(
    texto: str,
    tipos_habilitados: list = None,
    remitentes: list = None,
    texto_referencia: str = "",
) -> dict:
    """Llama a GPT-4o para clasificar la instrucción y proponer parámetros."""
    if tipos_habilitados is None:
        tipos_habilitados = TIPOS_HABILITADOS_DEFAULT
    if remitentes is None:
        remitentes = []

    prompt_system = """Eres un asistente administrativo experto en documentos
oficiales peruanos. Tu tarea es ANALIZAR una instrucción coloquial y proponer
los parámetros correctos para redactar el documento.

Responde ÚNICAMENTE con un JSON válido con esta estructura:
{
  "tipo_sugerido": "circular",
  "asunto_sugerido": "Obligaciones que generan multa pecuniaria",
  "tono_sugerido": "formal",
  "destinatario_tipo": "masivo",
  "remitente_sugerido": "Decano",
  "necesita_preguntas": true,
  "preguntas": [
    "¿Deseas incluir los montos específicos de cada multa?",
    "¿El documento debe incluir fecha límite de pago?"
  ],
  "razon": "La instrucción solicita informar obligaciones económicas a todos los colegiados — corresponde una Circular formal."
}

REGLAS:
- preguntas: máximo 2, solo si la instrucción es ambigua
- Si la instrucción es clara, necesita_preguntas = false y preguntas = []
- tipo_sugerido debe ser uno de: """ + str(tipos_habilitados) + """
- tono_sugerido debe ser: formal, cordial o protocolar
- Responde SOLO con el JSON, sin explicaciones ni markdown."""

    prompt_user = f"""
INSTRUCCIÓN: {texto}

{"DOCUMENTO DE REFERENCIA (resumen):" + texto_referencia[:2000] if texto_referencia else ""}

REMITENTES DISPONIBLES: {[r.get("nombre","") + " - " + r.get("cargo","") for r in remitentes]}

Analiza y responde con el JSON.
"""

    key = os.environ.get("OPENAI_API_KEY")
    if _openai_available and key:
        try:
            client = OpenAI(api_key=key)
            resp = client.chat.completions.create(
                model="gpt-4o",
                temperature=0.1,
                messages=[
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": prompt_user},
                ],
            )
            raw = (resp.choices[0].message.content or "").strip()
            # Limpiar markdown si viene envuelto
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1]
                if raw.endswith("```"):
                    raw = raw[:-3]
            return json.loads(raw)
        except Exception:
            pass

    # Fallback: respuesta básica sin IA
    return {
        "tipo_sugerido": "carta",
        "asunto_sugerido": "",
        "tono_sugerido": "formal",
        "destinatario_tipo": "especifico",
        "remitente_sugerido": "",
        "necesita_preguntas": False,
        "preguntas": [],
        "razon": "Análisis automático no disponible — se usarán los valores por defecto.",
    }


# ─── Numeración correlativa ──────────────────────────────────────
def obtener_siguiente_correlativo(
    tipo_documento: str,
    secretaria_id: int,
    db,
) -> int:
    """Obtiene y incrementa el número correlativo para el tipo de documento y año."""
    from app.models_secretaria import CorrelatividadDocumento

    anno_actual = datetime.now().year
    registro = db.query(CorrelatividadDocumento).filter_by(
        secretaria_id=secretaria_id,
        tipo_documento=tipo_documento,
        anno=anno_actual,
    ).first()

    if not registro:
        registro = CorrelatividadDocumento(
            secretaria_id=secretaria_id,
            tipo_documento=tipo_documento,
            anno=anno_actual,
            ultimo_numero=0,
        )
        db.add(registro)

    registro.ultimo_numero += 1
    db.commit()
    return registro.ultimo_numero


# ─── Prompts negativos ────────────────────────────────────────────
NEGATIVOS_BASE = [
    "NUNCA uses frases de relleno vacías que no aportan contenido",
    "NUNCA repitas el asunto en el primer párrafo del cuerpo",
    "NUNCA inventes montos, fechas o nombres que no estén en los datos",
    "NUNCA copies textualmente la instrucción del usuario",
]

NEGATIVOS_POR_TONO = {
    "cordial": [
        "NUNCA uses 'De mi mayor consideración' (es tono Formal, no Cordial)",
        "NUNCA uses 'Hago propicia la ocasión'",
        "NUNCA uses lenguaje ceremonioso excesivo",
    ],
    "formal": [
        "NUNCA uses 'Estimado/a' (no corresponde a documentos formales peruanos)",
        "NUNCA uses lenguaje coloquial",
    ],
    "protocolar": [
        "NUNCA omitas 'Dios guarde a Usted' en la despedida",
        "NUNCA uses primera persona singular en el cuerpo (usa tercera persona)",
    ],
}

NEGATIVOS_POR_TIPO = {
    "resolucion": [
        "NUNCA omitas las secciones VISTOS, CONSIDERANDO y SE RESUELVE",
        "NUNCA uses formato de carta en una Resolución",
        "NUNCA olvides 'REGÍSTRESE, COMUNÍQUESE Y ARCHÍVESE'",
    ],
    "memorandum": [
        "NUNCA uses la estructura de carta en un Memorándum",
        "NUNCA incluyas fórmulas de cortesía extensas",
    ],
    "memorandum_multiple": [
        "NUNCA uses la estructura de carta en un Memorándum",
        "NUNCA incluyas fórmulas de cortesía extensas",
    ],
    "comunicado_general": [
        "NUNCA incluyas destinatario específico",
        "NUNCA uses saludo inicial",
    ],
    "orden_pedido": [
        "NUNCA omitas la tabla de ítems con cantidades y precios",
        "NUNCA uses lenguaje formal ceremonioso en una Orden de Pedido",
    ],
}

NEGATIVOS_OPCIONALES = {
    "sin_relleno": {
        "label": "Evitar frases de relleno (recomendado)",
        "regla": "NUNCA uses frases de protocolo vacías que no aporten contenido concreto",
        "default": True,
    },
    "sin_gerundios": {
        "label": "Evitar gerundios en exceso",
        "regla": "Minimiza el uso de gerundios. Prefiere construcciones directas.",
        "default": False,
    },
    "sin_repeticion": {
        "label": "Sin repetición de ideas",
        "regla": "NUNCA repitas la misma idea en párrafos distintos",
        "default": True,
    },
    "citar_normas": {
        "label": "Citar referencias normativas cuando existan",
        "regla": "Cuando menciones obligaciones o derechos, cita el artículo o norma correspondiente",
        "default": False,
    },
    "modo_estricto": {
        "label": "Modo estricto: no generar si faltan datos críticos",
        "regla": "Si faltan datos críticos para el documento, NO generes — lista lo que necesitas",
        "default": False,
    },
}


def _construir_prompts_negativos(
    tono: str, tipo: str, preferencias: Optional[dict] = None
) -> str:
    prefs = preferencias or {}
    negativos = list(NEGATIVOS_BASE)
    negativos.extend(NEGATIVOS_POR_TONO.get(tono, []))
    negativos.extend(NEGATIVOS_POR_TIPO.get(tipo, []))
    for key, cfg in NEGATIVOS_OPCIONALES.items():
        if prefs.get(key, cfg["default"]):
            negativos.append(cfg["regla"])
    return "\n".join(f"- {n}" for n in negativos)


def _construir_estructura_forzada(tipo_cfg: dict) -> str:
    """Instrucciones de estructura obligatoria según propiedades del tipo."""
    partes = []
    if tipo_cfg.get("partes_obligatorias"):
        partes.append(
            "Secciones OBLIGATORIAS que deben aparecer como encabezados: "
            + ", ".join(tipo_cfg["partes_obligatorias"])
        )
    if tipo_cfg.get("requiere_tabla"):
        partes.append(
            "Este documento REQUIERE una tabla con columnas: "
            "ítem | descripción | cantidad | precio unitario | total. "
            "Usa caracteres │ y ─ para la tabla."
        )
    if tipo_cfg.get("sin_saludo"):
        partes.append("NO incluyas saludo formal ni destinatario específico.")
    if tipo_cfg.get("permite_lista_destinatarios"):
        partes.append(
            "El campo PARA: / A: debe ser una LISTA de destinatarios, no uno solo."
        )
    return "\n".join(partes)


# ─── Extracción en dos fases ─────────────────────────────────────
def _llamar_gpt4o(system: str, user: str, temperature: float = 0.3) -> str:
    """Helper: llama a GPT-4o y devuelve texto o string vacío."""
    key = os.environ.get("OPENAI_API_KEY")
    if not _openai_available or not key:
        return ""
    try:
        client = OpenAI(api_key=key)
        resp = client.chat.completions.create(
            model="gpt-4o",
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def extraer_datos_relevantes(
    texto_instruccion: str,
    texto_documento: str,
) -> dict:
    """
    Fase 1: GPT-4o extrae datos específicos del documento de referencia
    relevantes para la instrucción dada.
    """
    SYSTEM = """Eres un extractor de información de documentos oficiales.
Tu tarea es leer un documento y extraer ÚNICAMENTE la información
relevante para la instrucción dada.

Responde SOLO con JSON válido:
{
  "datos_encontrados": [
    {"concepto": "Multa por inasistencia", "valor": "S/ 150", "referencia": "Art. 45"}
  ],
  "datos_faltantes": [
    "Fecha límite de pago"
  ],
  "resumen_contexto": "El Estatuto establece 6 causales de multa..."
}

Si el documento no contiene información relevante para la instrucción,
datos_encontrados = [] y explica en datos_faltantes qué se necesitaría."""

    USER = f"INSTRUCCIÓN: {texto_instruccion}\n\nDOCUMENTO:\n{texto_documento[:6000]}"

    raw = _llamar_gpt4o(SYSTEM, USER, temperature=0.1)
    if not raw:
        return {"datos_encontrados": [], "datos_faltantes": [], "resumen_contexto": ""}
    try:
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1]
            if raw.endswith("```"):
                raw = raw[:-3]
        return json.loads(raw)
    except Exception:
        return {"datos_encontrados": [], "datos_faltantes": [], "resumen_contexto": raw[:500]}


# ─── Alertas de completitud ──────────────────────────────────────
import re as _re


def _detectar_alertas_completitud(texto: str, tipo_documento: str) -> list:
    """Detecta marcadores [COMPLETAR: ...] y datos típicamente faltantes."""
    alertas = []

    marcadores = _re.findall(r'\[COMPLETAR: ([^\]]+)\]', texto)
    alertas.extend(marcadores)

    patrones_criticos = {
        "resolucion": ["VISTOS:", "CONSIDERANDO:", "SE RESUELVE:"],
        "orden_pedido": ["total", "cantidad"],
    }
    for patron in patrones_criticos.get(tipo_documento, []):
        if patron.lower() not in texto.lower():
            alertas.append(f"Falta sección o dato: '{patron}'")

    if "N° ___" in texto or "N° [" in texto:
        alertas.append("Número de documento no asignado")

    return alertas


def generar_documento(
    texto_entrada: str,
    tono: str = "formal",
    destinatario: Optional[dict] = None,
    remitente: Optional[dict] = None,
    documento_referencia: Optional[str] = None,
    tipo_documento: str = "carta",
    api_key: Optional[str] = None,
    modelo: str = "gpt-4o",
    config_org: Optional[dict] = None,
    asunto_confirmado: str = "",
    respuestas_agente: Optional[dict] = None,
    num_correlativo: Optional[int] = None,
    preferencias_prompt: Optional[dict] = None,
) -> tuple:
    """
    Devuelve (texto_documento, lista_alertas).
    Si no hay API key o el SDK no está instalado, devuelve un borrador
    plantilla con los datos disponibles para no romper la UX.
    """
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in TONOS:
        tono_norm = "formal"

    tipo_norm = (tipo_documento or "carta").strip().lower()
    if tipo_norm not in TIPOS:
        tipo_norm = "carta"

    tipo_cfg = TIPOS[tipo_norm]
    org = config_org or {}
    anno_oficial = org.get("anno_oficial", "")
    siglas = org.get("siglas", "")
    ciudad_org = org.get("ciudad", "")
    alertas = []

    # Resolver género del remitente
    perfil = resolver_genero(remitente or {})
    if perfil and ciudad_org and not perfil.get("ciudad"):
        perfil["ciudad"] = ciudad_org

    # FASE 1: Extracción de datos si hay documento de referencia
    modo_extraccion = bool(documento_referencia and documento_referencia.strip())
    datos_extraidos = {}
    if modo_extraccion:
        datos_extraidos = extraer_datos_relevantes(texto_entrada, documento_referencia)
        if datos_extraidos.get("datos_faltantes"):
            alertas.extend(datos_extraidos["datos_faltantes"])

    # FASE 2: Generar documento
    system_prompt = _build_system_prompt(tono_norm, tipo_norm)

    # Prompts negativos
    negativos = _construir_prompts_negativos(tono_norm, tipo_norm, preferencias_prompt)
    estructura_forzada = _construir_estructura_forzada(tipo_cfg)

    # Datos organizacionales
    num_display = ""
    if num_correlativo:
        num_display = f"{num_correlativo:03d}-{datetime.now().year}"
        if siglas:
            num_display += f"-{siglas}"

    org_block = f"""

================================================================
DATOS DE LA ORGANIZACIÓN
================================================================
- Año oficial: "{anno_oficial}"
- Numeración: {tipo_cfg['label'].upper()} N° {num_display if num_display else '[___]'}
- {perfil.get('articulo', 'El')} {perfil.get('cargo_resuelto', perfil.get('cargo', 'Decano'))}: {perfil.get('tratamiento_resuelto', perfil.get('tratamiento', ''))} {perfil.get('nombre', perfil.get('nombre_firmante', ''))}
- Organización: {org.get('nombre_organizacion', '')}

Incluye el año oficial SIEMPRE en la primera línea del documento como encabezado centrado.
{estructura_forzada}

=== INSTRUCCIONES DE FORMATO ===
- Usa tablas (con │ y ─) cuando el contenido sea una lista de ítems con 2+ atributos
- Usa viñetas (•) cuando sean 3+ elementos de una lista sin atributos adicionales
- Marca datos faltantes como [COMPLETAR: descripción del dato]
- NUNCA uses markdown (**, ##, ---)

=== PROHIBICIONES ===
{negativos}"""

    if modo_extraccion:
        org_block += """

=== MODO EXTRACCIÓN ACTIVO ===
El usuario subió un documento de referencia. Los datos extraídos están abajo.
USA estos datos reales. NO inventes. Si falta algo, marca [COMPLETAR: ...]."""

    if asunto_confirmado:
        org_block += f"\n\nASUNTO CONFIRMADO: {asunto_confirmado}"
    if respuestas_agente:
        org_block += f"\n\nRESPUESTAS DEL USUARIO: {respuestas_agente}"

    system_prompt += org_block

    # Construir user prompt con datos extraídos
    user_prompt = _build_user_prompt(
        texto_entrada, destinatario, perfil, documento_referencia, tipo_norm
    )

    # Insertar datos extraídos si existen
    if datos_extraidos.get("datos_encontrados"):
        datos_str = "\n=== DATOS EXTRAÍDOS DEL DOCUMENTO DE REFERENCIA ===\n"
        for d in datos_extraidos["datos_encontrados"]:
            datos_str += f"- {d.get('concepto','')}: {d.get('valor','')} ({d.get('referencia','')})\n"
        datos_str += f"\nContexto: {datos_extraidos.get('resumen_contexto', '')}\n"
        user_prompt += datos_str

    user_prompt += "\nGenera el documento completo. Marca con [COMPLETAR: ...] cualquier dato faltante."

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
                alertas.extend(_detectar_alertas_completitud(texto, tipo_norm))
                return texto, alertas
        except Exception as e:
            return _fallback_borrador(
                texto_entrada, tono_norm, destinatario, perfil, error=str(e)
            ), alertas

    return _fallback_borrador(texto_entrada, tono_norm, destinatario, perfil), alertas


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
