# ══════════════════════════════════════════════════════════
# app/services/pdf_service.py
# Generación de PDF a partir del texto del documento.
# Usa ReportLab cuando está disponible, con estilo por tono y tipo.
# Cae a HTML imprimible si ninguna lib de PDF está instalada.
# ══════════════════════════════════════════════════════════

import re
from io import BytesIO
from html import escape
from typing import Optional
from datetime import datetime

try:
    from bs4 import BeautifulSoup, NavigableString
    _HAS_BS4 = True
except Exception:
    _HAS_BS4 = False


_HTML_HINT_RE = re.compile(r'<\s*(p|div|table|ul|ol|li|tr|td|th|strong|b|em|i|br|h[1-6])\b', re.I)


def es_contenido_html(texto: str) -> bool:
    """Heurística: el cuerpo es HTML (vino del editor Quill) y no Markdown."""
    if not texto:
        return False
    return bool(_HTML_HINT_RE.search(texto))


def _limpiar_delimitadores(texto: str) -> str:
    """Quita delimitadores ''' o ``` que a veces quedan al inicio/fin del texto."""
    texto = (texto or "").strip()
    for delim in ("'''", '"""', "```"):
        if texto.startswith(delim):
            texto = texto[len(delim):]
        if texto.endswith(delim):
            texto = texto[:-len(delim)]
    return texto.strip()


def construir_nombre_archivo(
    tipo_doc: str,
    siglas: str,
    numero_correlativo: str,
    ext: str,
    fecha: Optional[datetime] = None,
) -> str:
    """Nombre estándar: TIPO_SIGLAS_CORRELATIVO_FECHA.ext
    Ej: Carta_MDTMC_001_20260418.pdf"""
    fecha = fecha or datetime.now()
    tipo_limpio = (tipo_doc or "documento").replace("_", " ").title().replace(" ", "") or "Documento"
    siglas_limpias = (siglas or "DOC").replace(" ", "").upper() or "DOC"
    correlativo = (numero_correlativo or "").strip()
    if "°" in correlativo:
        correlativo = correlativo.split("°")[-1].strip()
    correlativo = correlativo.split("-")[0] if correlativo else "001"
    correlativo = "".join(c for c in correlativo if c.isdigit())[:6] or "001"
    correlativo = correlativo.zfill(3)
    fecha_str = fecha.strftime("%Y%m%d")
    ext_clean = ext.lstrip(".")
    return f"{tipo_limpio}_{siglas_limpias}_{correlativo}_{fecha_str}.{ext_clean}"

# WeasyPrint (preferido por estilo CSS) — opcional
try:
    from weasyprint import HTML  # type: ignore
    _HAS_WEASY = True
except Exception:
    _HAS_WEASY = False

# ReportLab (preferido para estilizado controlado) — opcional
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        HRFlowable,
        Image,
        Table,
        TableStyle,
    )
    from reportlab.pdfgen.canvas import Canvas  # noqa: F401
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False


# ─── Márgenes por tipo de documento ───
# Oficio y resolución necesitan margen izquierdo mayor para empaste oficial.
def _estilos_por_tipo():
    if not _HAS_REPORTLAB:
        return {}
    return {
        "oficio":             {"margen_izq": 3.5 * cm, "margen_der": 2.5 * cm},
        "oficio_multiple":    {"margen_izq": 3.5 * cm, "margen_der": 2.5 * cm},
        "carta":              {"margen_izq": 3.0 * cm, "margen_der": 2.5 * cm},
        "memorandum":         {"margen_izq": 2.5 * cm, "margen_der": 2.5 * cm},
        "memorandum_multiple":{"margen_izq": 2.5 * cm, "margen_der": 2.5 * cm},
        "resolucion":         {"margen_izq": 3.0 * cm, "margen_der": 2.5 * cm},
        "acta":               {"margen_izq": 2.5 * cm, "margen_der": 2.5 * cm},
        "circular":           {"margen_izq": 3.0 * cm, "margen_der": 2.5 * cm},
        "comunicado_general": {"margen_izq": 2.5 * cm, "margen_der": 2.5 * cm},
        "orden_pedido":       {"margen_izq": 2.5 * cm, "margen_der": 2.5 * cm},
    }


# ─── Estilos por tono (solo se usan si reportlab está disponible) ───
def _estilos_tono():
    if not _HAS_REPORTLAB:
        return {}
    return {
        "formal": {
            "font": "Times-Roman",
            "font_bold": "Times-Bold",
            "color_header": colors.HexColor("#1c3f8f"),
            "color_text": colors.HexColor("#0d1c3d"),
            "border_color": colors.HexColor("#1c3f8f"),
        },
        "cordial": {
            "font": "Helvetica",
            "font_bold": "Helvetica-Bold",
            "color_header": colors.HexColor("#2d8a48"),
            "color_text": colors.HexColor("#1d3a26"),
            "border_color": colors.HexColor("#2d8a48"),
        },
        "protocolar": {
            "font": "Times-Roman",
            "font_bold": "Times-Bold",
            "color_header": colors.HexColor("#b8860b"),
            "color_text": colors.HexColor("#4a3500"),
            "border_color": colors.HexColor("#b8860b"),
        },
    }


# ─── Estilos específicos por tipo de documento ───
ESTILOS_TIPO = {
    "resolucion": {
        "partes_negrita": ["VISTOS:", "CONSIDERANDO:", "SE RESUELVE:",
                           "REGÍSTRESE", "COMUNÍQUESE", "ARCHÍVESE"],
        "sangria_cuerpo": True,
    },
    "memorandum": {
        "encabezado_tabla": True,
        "sin_saludo_formal": True,
    },
    "memorandum_multiple": {
        "encabezado_tabla": True,
        "sin_saludo_formal": True,
    },
    "orden_pedido": {
        "tabla_items": True,
    },
    "circular": {
        "destinatario_general": True,
    },
    "comunicado_general": {
        "destinatario_general": True,
    },
}


def _texto_a_html(texto: str, titulo: str = "Documento", tono: str = "formal") -> str:
    parrafos = "".join(
        f"<p>{escape(linea) if linea.strip() else '&nbsp;'}</p>"
        for linea in texto.split("\n")
    )
    colores = {
        "formal":     {"text": "#0d1c3d", "border": "#1c3f8f", "font": "'Times New Roman', Georgia, serif"},
        "cordial":    {"text": "#1d3a26", "border": "#2d8a48", "font": "Georgia, 'Cambria', serif"},
        "protocolar": {"text": "#4a3500", "border": "#b8860b", "font": "'Garamond', Georgia, serif"},
    }
    c = colores.get(tono, colores["formal"])
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{escape(titulo)}</title>
<style>
@page {{ size: A4; margin: 2.5cm 2cm; }}
body {{ font-family: {c['font']}; font-size: 12pt; line-height: 1.55; color: {c['text']}; }}
hr {{ border: 0; border-top: 2px solid {c['border']}; margin: .5em 0 1em; }}
p {{ margin: 0 0 .6em 0; white-space: pre-wrap; }}
</style>
</head>
<body>
<hr>
{parrafos}
<hr>
</body>
</html>"""


def texto_a_pdf_bytes(
    texto: str,
    titulo: str = "Documento",
    tono: str = "formal",
    config_colegio=None,
    tipo_documento: str = "carta",
    alertas: Optional[list] = None,
    config_organizacion=None,
    numero_documento: str = "",
) -> bytes:
    """
    Genera el PDF del documento. Aplica estilo según el tono y tipo.
    Si hay membrete en la config del colegio, lo coloca arriba.

    config_organizacion: objeto ConfigOrganizacion con nombre_organizacion,
        siglas, ciudad, anno_oficial. Se usa para pintar el membrete.
    numero_documento: por ej. "045-2026-SIGLAS" — va en el encabezado.
    """
    texto = _limpiar_delimitadores(texto)
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in ("formal", "cordial", "protocolar"):
        tono_norm = "formal"

    if _HAS_REPORTLAB:
        return _generar_pdf_reportlab(
            texto, titulo, tono_norm, config_colegio, tipo_documento,
            alertas, config_organizacion, numero_documento,
        )

    if _HAS_WEASY:
        return HTML(string=_texto_a_html(texto, titulo, tono_norm)).write_pdf()

    # Último recurso: HTML como bytes (caller decide content-type)
    return _texto_a_html(texto, titulo, tono_norm).encode("utf-8")


def _es_linea_tabla(linea: str) -> bool:
    """Detecta si una línea es parte de una tabla (contiene │ o |)."""
    stripped = linea.strip()
    if not stripped:
        return False
    # Líneas con separadores de tabla
    if '│' in stripped or (stripped.count('|') >= 2):
        return True
    # Líneas de borde de tabla (solo ─, +, -)
    if all(c in '─-+| \t' for c in stripped) and len(stripped) > 3:
        return True
    return False


def _es_linea_negrita(linea: str, tipo_documento: str) -> bool:
    """Determina si una línea debe ir en negrita según el tipo de documento."""
    tipo_cfg = ESTILOS_TIPO.get(tipo_documento, {})
    partes_negrita = tipo_cfg.get("partes_negrita", [])
    stripped = linea.strip().upper()
    for parte in partes_negrita:
        if parte.upper() in stripped:
            return True
    # Encabezados en mayúsculas (tipo MEMORÁNDUM N°, CIRCULAR N°, etc.)
    if stripped and stripped == linea.strip() and linea.strip().isupper() and len(linea.strip()) < 80:
        return True
    return False


def _inline_html_a_reportlab(nodo) -> str:
    """Convierte el contenido inline de un nodo BeautifulSoup a markup ReportLab."""
    if not _HAS_BS4:
        return ""
    out = []
    for hijo in getattr(nodo, "children", []):
        if isinstance(hijo, NavigableString):
            out.append(escape(str(hijo), quote=False))
            continue
        nm = (hijo.name or "").lower()
        inner = _inline_html_a_reportlab(hijo)
        if nm in ("strong", "b"):
            out.append(f"<b>{inner}</b>")
        elif nm in ("em", "i"):
            out.append(f"<i>{inner}</i>")
        elif nm == "u":
            out.append(f"<u>{inner}</u>")
        elif nm == "br":
            out.append("<br/>")
        elif nm == "a":
            href = hijo.get("href", "")
            if href:
                out.append(f'<a href="{escape(href, quote=True)}" color="#0563C1">{inner}</a>')
            else:
                out.append(inner)
        elif nm == "code":
            out.append(f'<font face="Courier">{inner}</font>')
        else:
            out.append(inner)
    return "".join(out)


def html_a_elementos_reportlab(html: str, estilo: dict) -> list:
    """Convierte HTML del editor Quill a Flowables ReportLab."""
    if not _HAS_REPORTLAB:
        return []
    if not html or not _HAS_BS4:
        return []

    style_body = ParagraphStyle(
        "html_body", fontName=estilo["font"], fontSize=11, leading=15,
        textColor=estilo["color_text"], alignment=TA_JUSTIFY, spaceAfter=4,
    )
    style_bold = ParagraphStyle(
        "html_bold", fontName=estilo["font_bold"], fontSize=11, leading=15,
        textColor=estilo["color_text"], spaceAfter=4,
    )
    style_bullet = ParagraphStyle(
        "html_bullet", fontName=estilo["font"], fontSize=11, leading=15,
        textColor=estilo["color_text"], leftIndent=18, bulletIndent=6, spaceAfter=2,
    )

    soup = BeautifulSoup(html, "html.parser")
    elementos = []

    def render_lista(elem, ordenada: bool):
        items = elem.find_all("li", recursive=False)
        for idx, li in enumerate(items, start=1):
            inner = _inline_html_a_reportlab(li)
            marca = f"{idx}." if ordenada else "&bull;"
            elementos.append(Paragraph(f"{marca}&nbsp;&nbsp;{inner}", style_bullet))

    def render_tabla(tbl):
        filas = []
        for tr in tbl.find_all("tr"):
            celdas = tr.find_all(["th", "td"])
            if not celdas:
                continue
            filas.append([
                Paragraph(_inline_html_a_reportlab(c), style_body) for c in celdas
            ])
        if not filas:
            return
        cols = max(len(f) for f in filas)
        for f in filas:
            while len(f) < cols:
                f.append(Paragraph("", style_body))
        es_header = bool(tbl.find("th"))
        t = Table(filas, repeatRows=1 if es_header else 0)
        estilo_tbl = [
            ('FONTSIZE',   (0, 0), (-1, -1), 10),
            ('GRID',       (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
            ('ROWBACKGROUNDS', (0, 1 if es_header else 0), (-1, -1),
             [colors.white, colors.HexColor('#f5f5f5')]),
            ('PADDING',    (0, 0), (-1, -1), 6),
            ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ]
        if es_header:
            estilo_tbl = [
                ('BACKGROUND', (0, 0), (-1, 0), estilo["color_header"]),
                ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
                ('FONTNAME',   (0, 0), (-1, 0), estilo["font_bold"]),
                ('FONTNAME',   (0, 1), (-1, -1), estilo["font"]),
            ] + estilo_tbl
        else:
            estilo_tbl = [('FONTNAME', (0, 0), (-1, -1), estilo["font"])] + estilo_tbl
        t.setStyle(TableStyle(estilo_tbl))
        elementos.append(t)
        elementos.append(Spacer(1, 0.3 * cm))

    def walk(nodes):
        for elem in nodes:
            if isinstance(elem, NavigableString):
                txt = str(elem).strip()
                if txt:
                    elementos.append(Paragraph(escape(txt, quote=False), style_body))
                continue
            nm = (elem.name or "").lower()
            if nm in ("p", "div"):
                inner = _inline_html_a_reportlab(elem).strip()
                if inner:
                    elementos.append(Paragraph(inner, style_body))
                else:
                    elementos.append(Spacer(1, 0.2 * cm))
            elif nm in ("h1", "h2", "h3", "h4", "h5", "h6"):
                inner = _inline_html_a_reportlab(elem).strip()
                if inner:
                    elementos.append(Paragraph(f"<b>{inner}</b>", style_bold))
            elif nm == "ul":
                render_lista(elem, ordenada=False)
            elif nm == "ol":
                render_lista(elem, ordenada=True)
            elif nm == "table":
                render_tabla(elem)
            elif nm == "br":
                elementos.append(Spacer(1, 0.15 * cm))
            elif nm == "hr":
                elementos.append(Spacer(1, 0.3 * cm))
            else:
                # Elemento raíz no reconocido: tratarlo como párrafo
                inner = _inline_html_a_reportlab(elem).strip()
                if inner:
                    elementos.append(Paragraph(inner, style_body))

    raiz = soup.body or soup
    walk(list(raiz.children))
    return elementos


_MD_BOLD_RE   = re.compile(r'\*\*(.+?)\*\*')
_MD_ITALIC_RE = re.compile(r'(?<!\w)_([^_\n]+)_(?!\w)')
_MD_LINK_RE   = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_MD_CODE_RE   = re.compile(r'`([^`\n]+)`')
_MD_OL_RE     = re.compile(r'^\s*\d+\.\s+')
_MD_TBL_SEP_RE = re.compile(r'^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$')


def _limpiar_markdown_inline(texto: str) -> str:
    """Convierte negrita/itálica/links/code Markdown a tags ReportLab."""
    if not texto:
        return ""
    s = escape(texto, quote=False)
    s = _MD_BOLD_RE.sub(r'<b>\1</b>', s)
    s = _MD_ITALIC_RE.sub(r'<i>\1</i>', s)
    s = _MD_CODE_RE.sub(r'<font face="Courier">\1</font>', s)
    s = _MD_LINK_RE.sub(r'\1', s)
    return s


def _es_separador_md(linea: str) -> bool:
    return bool(_MD_TBL_SEP_RE.match(linea or ''))


def _parsear_celdas_md(linea: str) -> list:
    s = (linea or '').strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    return [c.strip() for c in s.split('|')]


def markdown_a_elementos_reportlab(texto: str, estilo: dict) -> list:
    """Convierte Markdown del documento a lista de Flowables ReportLab."""
    if not _HAS_REPORTLAB:
        return []

    style_body = ParagraphStyle(
        "md_body", fontName=estilo["font"], fontSize=11, leading=15,
        textColor=estilo["color_text"], alignment=TA_JUSTIFY, spaceAfter=4,
    )
    style_bold = ParagraphStyle(
        "md_bold", fontName=estilo["font_bold"], fontSize=11, leading=15,
        textColor=estilo["color_text"], spaceAfter=4,
    )
    style_bullet = ParagraphStyle(
        "md_bullet", fontName=estilo["font"], fontSize=11, leading=15,
        textColor=estilo["color_text"], leftIndent=18, bulletIndent=6, spaceAfter=2,
    )

    elementos = []
    lineas = (texto or '').split('\n')
    i = 0
    n = len(lineas)
    while i < n:
        linea = lineas[i]
        stripped = linea.strip()

        # Tabla Markdown: cabecera | --- | + filas
        if stripped.startswith('|') and i + 1 < n and _es_separador_md(lineas[i+1]):
            cabecera = _parsear_celdas_md(lineas[i])
            i += 2  # saltar cabecera + separador
            filas = [cabecera]
            while i < n and lineas[i].strip().startswith('|'):
                filas.append(_parsear_celdas_md(lineas[i]))
                i += 1
            max_cols = max(len(f) for f in filas)
            for f in filas:
                while len(f) < max_cols:
                    f.append('')
            datos = [[Paragraph(_limpiar_markdown_inline(c), style_body) for c in f]
                     for f in filas]
            t = Table(datos, repeatRows=1)
            t.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), estilo["color_header"]),
                ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
                ('FONTNAME',   (0, 0), (-1, 0), estilo["font_bold"]),
                ('FONTNAME',   (0, 1), (-1, -1), estilo["font"]),
                ('FONTSIZE',   (0, 0), (-1, -1), 10),
                ('GRID',       (0, 0), (-1, -1), 0.5, colors.HexColor('#cccccc')),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1),
                 [colors.white, colors.HexColor('#f5f5f5')]),
                ('PADDING',    (0, 0), (-1, -1), 6),
                ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
            ]))
            elementos.append(t)
            elementos.append(Spacer(1, 0.3 * cm))
            continue

        # Lista numerada
        if _MD_OL_RE.match(linea):
            cuerpo = _MD_OL_RE.sub('', linea, count=1)
            elementos.append(Paragraph(
                f"&bull;&nbsp;&nbsp;{_limpiar_markdown_inline(cuerpo)}",
                style_bullet,
            ))
            i += 1
            continue

        # Lista con viñetas
        if stripped.startswith('- ') or stripped.startswith('* '):
            cuerpo = stripped[2:]
            elementos.append(Paragraph(
                f"&bull;&nbsp;&nbsp;{_limpiar_markdown_inline(cuerpo)}",
                style_bullet,
            ))
            i += 1
            continue

        # Línea vacía
        if not stripped:
            elementos.append(Spacer(1, 0.2 * cm))
            i += 1
            continue

        # Línea totalmente en mayúsculas (títulos como MEMORÁNDUM N° ...)
        if stripped == linea.strip() and stripped.isupper() and len(stripped) < 80:
            elementos.append(Paragraph(_limpiar_markdown_inline(stripped), style_bold))
        else:
            elementos.append(Paragraph(_limpiar_markdown_inline(linea), style_body))
        i += 1

    return elementos


def _generar_pdf_reportlab(
    texto: str,
    titulo: str,
    tono: str,
    config_colegio=None,
    tipo_documento: str = "carta",
    alertas: Optional[list] = None,
    config_organizacion=None,
    numero_documento: str = "",
) -> bytes:
    estilos = _estilos_tono()
    estilo = estilos.get(tono, estilos["formal"])

    # Márgenes por tipo de documento
    margenes = _estilos_por_tipo()
    margen_cfg = margenes.get(
        (tipo_documento or "carta").lower(),
        {"margen_izq": 3.0 * cm, "margen_der": 2.5 * cm},
    )

    # Datos de organización para membrete
    org = config_organizacion
    org_nombre = (getattr(org, "nombre_organizacion", "") or "").strip() if org else ""
    org_siglas = (getattr(org, "siglas", "") or "").strip() if org else ""
    org_ciudad = (getattr(org, "ciudad", "") or "").strip() if org else ""
    org_anno = (getattr(org, "anno_oficial", "") or "").strip() if org else ""

    logo_url = ""
    if org and getattr(org, "logo_url", None):
        logo_url = org.logo_url
    elif config_colegio and getattr(config_colegio, "membrete_url", None):
        logo_url = config_colegio.membrete_url

    tiene_datos_membrete = bool(org_nombre or logo_url)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=margen_cfg["margen_izq"],
        rightMargin=margen_cfg["margen_der"],
        topMargin=2 * cm if tiene_datos_membrete else 3 * cm,
        bottomMargin=2.8 * cm,
        title=titulo,
    )

    story = []

    # Membrete institucional
    if tiene_datos_membrete:
        _agregar_membrete(
            story, estilo, logo_url,
            org_nombre, org_siglas, org_ciudad, org_anno,
            tipo_documento, numero_documento,
        )
    else:
        # Línea decorativa superior mínima
        story.append(
            HRFlowable(
                width="100%",
                thickness=2,
                color=estilo["border_color"],
                spaceAfter=0.5 * cm,
            )
        )

    # Renderizar cuerpo: HTML del editor Quill o Markdown de la IA
    if es_contenido_html(texto):
        story.extend(html_a_elementos_reportlab(texto, estilo))
    else:
        story.extend(markdown_a_elementos_reportlab(texto, estilo))

    # Alertas de completitud al final del PDF
    if alertas:
        story.append(Spacer(1, 0.8 * cm))
        story.append(
            HRFlowable(
                width="100%",
                thickness=1,
                color=colors.HexColor("#ffc107"),
                spaceBefore=0.2 * cm,
            )
        )
        style_alerta_h = ParagraphStyle(
            "alerta_header",
            fontName=estilo["font_bold"],
            fontSize=10,
            textColor=colors.HexColor("#b8860b"),
            spaceBefore=8,
            spaceAfter=4,
        )
        style_alerta = ParagraphStyle(
            "alerta_item",
            fontName=estilo["font"],
            fontSize=9,
            textColor=colors.HexColor("#856404"),
            leftIndent=10,
            spaceAfter=3,
        )
        story.append(Paragraph("PENDIENTE DE COMPLETAR:", style_alerta_h))
        for alerta in alertas:
            story.append(Paragraph(f"\u25a1 {escape(alerta)}", style_alerta))

    # Línea decorativa inferior
    story.append(Spacer(1, 0.5 * cm))
    story.append(
        HRFlowable(
            width="100%",
            thickness=1,
            color=estilo["border_color"],
            spaceBefore=0.3 * cm,
        )
    )

    # Configuración de marca de agua
    marca_cfg = _leer_marca_agua(org, org_nombre)

    # Callback de pie de página + marca de agua
    def _on_page(canvas, _doc):
        _dibujar_pie(canvas, _doc, estilo, org_nombre, org_ciudad, org_siglas)
        if marca_cfg["activa"] and marca_cfg["texto"]:
            _dibujar_marca_agua(canvas, _doc, marca_cfg)

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    return buffer.getvalue()


_COLORES_MARCA = {
    "gris":  (0.40, 0.40, 0.40),
    "azul":  (0.15, 0.25, 0.55),
    "rojo":  (0.70, 0.15, 0.15),
    "verde": (0.15, 0.45, 0.25),
    "negro": (0.10, 0.10, 0.10),
}


def _leer_marca_agua(org, org_nombre: str) -> dict:
    """Lee la config de marca de agua desde ConfigOrganizacion (con defaults)."""
    if org is None:
        return {
            "activa": bool(org_nombre),
            "texto": org_nombre or "",
            "tamano": 48,
            "opacidad": 0.08,
            "angulo": 45,
            "color": "gris",
        }
    activa = getattr(org, "marca_agua_activa", None)
    if activa is None:
        activa = True  # default activo si no existe la columna
    texto = (getattr(org, "marca_agua_texto", None) or org_nombre or "").strip()
    tamano = getattr(org, "marca_agua_tamano", None) or 48
    opacidad = getattr(org, "marca_agua_opacidad", None)
    if opacidad is None:
        opacidad = 0.08
    angulo = getattr(org, "marca_agua_angulo", None)
    if angulo is None:
        angulo = 45
    color = (getattr(org, "marca_agua_color", None) or "gris").lower()
    return {
        "activa": bool(activa),
        "texto": texto,
        "tamano": int(tamano),
        "opacidad": float(opacidad),
        "angulo": int(angulo),
        "color": color,
    }


# ─── Membrete / pie / marca de agua ─────────────────────────────
def _agregar_membrete(
    story, estilo, logo_url: str,
    org_nombre: str, org_siglas: str, org_ciudad: str, org_anno: str,
    tipo_documento: str, numero_documento: str,
):
    """Construye el membrete institucional arriba del cuerpo del documento."""
    # Estilos para el bloque de texto del membrete
    style_nombre = ParagraphStyle(
        "org_nombre",
        fontName=estilo["font_bold"],
        fontSize=13,
        leading=15,
        textColor=estilo["color_header"],
        alignment=TA_LEFT,
    )
    style_sub = ParagraphStyle(
        "org_sub",
        fontName=estilo["font"],
        fontSize=9,
        leading=11,
        textColor=estilo["color_text"],
        alignment=TA_LEFT,
    )
    style_anno = ParagraphStyle(
        "org_anno",
        fontName=estilo["font"],
        fontSize=8,
        leading=10,
        textColor=estilo["color_header"],
        alignment=TA_CENTER,
        spaceAfter=4,
    )
    style_numero = ParagraphStyle(
        "org_numero",
        fontName=estilo["font_bold"],
        fontSize=10,
        leading=12,
        textColor=estilo["color_text"],
        alignment=TA_LEFT,
        spaceBefore=6,
    )

    # Celda logo: Imagen si hay URL usable, si no franja de color con siglas/nombre
    celda_logo = _celda_logo(logo_url, org_siglas, org_nombre, estilo)

    # Celda texto institucional
    nombre_safe = escape(org_nombre) if org_nombre else ""
    sub_parts = []
    if org_siglas:
        sub_parts.append(escape(org_siglas))
    if org_ciudad:
        sub_parts.append(escape(org_ciudad))
    sub_line = " — ".join(sub_parts)

    celda_texto = []
    if nombre_safe:
        celda_texto.append(Paragraph(nombre_safe, style_nombre))
    if sub_line:
        celda_texto.append(Paragraph(sub_line, style_sub))

    tabla = Table(
        [[celda_logo, celda_texto]],
        colWidths=[3.5 * cm, None],
    )
    tabla.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(tabla)
    story.append(Spacer(1, 0.2 * cm))

    # Año oficial centrado
    if org_anno:
        story.append(Paragraph(f'"{escape(org_anno)}"', style_anno))

    # Línea separadora entre membrete y cuerpo
    story.append(
        HRFlowable(
            width="100%",
            thickness=1.5,
            color=estilo["border_color"],
            spaceAfter=0.3 * cm,
        )
    )

    # Número del documento (ej: OFICIO N° 045-2026-SIGLAS)
    if numero_documento:
        tipo_label = (tipo_documento or "documento").replace("_", " ").upper()
        texto_num = f"{tipo_label} N° {escape(numero_documento)}"
        story.append(Paragraph(texto_num, style_numero))
        story.append(Spacer(1, 0.2 * cm))


def _celda_logo(logo_url: str, org_siglas: str, org_nombre: str, estilo: dict):
    """Devuelve una celda con Image si el logo es cargable; si no, una franja de
    color con siglas o iniciales del nombre en texto blanco."""
    if logo_url:
        try:
            return Image(logo_url, width=3 * cm, height=2.2 * cm)
        except Exception:
            pass

    # Placeholder: franja de color con siglas/iniciales en blanco
    iniciales = (org_siglas or "").strip()
    if not iniciales and org_nombre:
        iniciales = "".join(
            p[0] for p in org_nombre.split() if p and p[0].isalpha()
        )[:4].upper()
    if not iniciales:
        iniciales = "ORG"

    style_placeholder = ParagraphStyle(
        "ph_logo",
        fontName=estilo["font_bold"],
        fontSize=16,
        leading=18,
        textColor=colors.white,
        alignment=TA_CENTER,
    )
    tabla = Table(
        [[Paragraph(escape(iniciales), style_placeholder)]],
        colWidths=[3 * cm],
        rowHeights=[2.2 * cm],
    )
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), estilo["color_header"]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tabla


def _dibujar_pie(canvas, doc, estilo: dict, org_nombre: str, org_ciudad: str, org_siglas: str):
    """Dibuja el pie de página: nombre org, ciudad y numeración de página."""
    canvas.saveState()
    ancho, _ = A4

    # Línea superior del pie
    canvas.setStrokeColor(estilo["border_color"])
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, 1.8 * cm, ancho - 2 * cm, 1.8 * cm)

    # Texto del pie
    canvas.setFont(estilo["font"], 8)
    canvas.setFillColor(estilo["color_text"])

    izq_parts = []
    if org_nombre:
        izq_parts.append(org_nombre)
    if org_ciudad:
        izq_parts.append(org_ciudad)
    izq = " — ".join(izq_parts)
    if izq:
        canvas.drawString(2 * cm, 1.4 * cm, izq[:90])

    # Página N en la derecha
    pagina_txt = f"Página {doc.page}"
    canvas.drawRightString(ancho - 2 * cm, 1.4 * cm, pagina_txt)

    canvas.restoreState()


def _dibujar_marca_agua(canvas, _doc, cfg: dict):
    """Marca de agua configurable: texto, tamaño, opacidad, ángulo, color."""
    canvas.saveState()
    ancho, alto = A4
    r, g, b = _COLORES_MARCA.get(cfg["color"], _COLORES_MARCA["gris"])
    try:
        canvas.setFillColorRGB(r, g, b, alpha=cfg["opacidad"])
    except TypeError:
        canvas.restoreState()
        return
    canvas.setFont("Helvetica-Bold", cfg["tamano"])
    canvas.translate(ancho / 2, alto / 2)
    canvas.rotate(cfg["angulo"])
    canvas.drawCentredString(0, 0, cfg["texto"][:60])
    canvas.restoreState()


def _flush_tabla(tabla_data: list, story: list, estilo: dict):
    """Renderiza una tabla acumulada y la agrega al story."""
    if not tabla_data or not _HAS_REPORTLAB:
        return

    # Normalizar anchos (asegurar que todas las filas tengan mismo # de celdas)
    max_cols = max(len(row) for row in tabla_data)
    for row in tabla_data:
        while len(row) < max_cols:
            row.append("")

    t = Table(tabla_data, repeatRows=1)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), estilo["color_header"]),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), estilo["font_bold"]),
        ('FONTNAME', (0, 1), (-1, -1), estilo["font"]),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1),
         [colors.white, colors.HexColor("#f8f9fa")]),
        ('PADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3 * cm))
    tabla_data.clear()


def pdf_disponible() -> bool:
    return _HAS_WEASY or _HAS_REPORTLAB
