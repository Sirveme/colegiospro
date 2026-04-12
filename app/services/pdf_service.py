# ══════════════════════════════════════════════════════════
# app/services/pdf_service.py
# Generación de PDF a partir del texto del documento.
# Usa ReportLab cuando está disponible, con estilo por tono y tipo.
# Cae a HTML imprimible si ninguna lib de PDF está instalada.
# ══════════════════════════════════════════════════════════

from io import BytesIO
from html import escape
from typing import Optional

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
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False


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
) -> bytes:
    """
    Genera el PDF del documento. Aplica estilo según el tono y tipo.
    Si hay membrete en la config del colegio, lo coloca arriba.
    """
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in ("formal", "cordial", "protocolar"):
        tono_norm = "formal"

    if _HAS_REPORTLAB:
        return _generar_pdf_reportlab(
            texto, titulo, tono_norm, config_colegio, tipo_documento, alertas
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


def _generar_pdf_reportlab(
    texto: str,
    titulo: str,
    tono: str,
    config_colegio=None,
    tipo_documento: str = "carta",
    alertas: Optional[list] = None,
) -> bytes:
    estilos = _estilos_tono()
    estilo = estilos.get(tono, estilos["formal"])

    buffer = BytesIO()
    tiene_membrete = (
        config_colegio is not None
        and getattr(config_colegio, "membrete_url", None)
    )
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=2 * cm if tiene_membrete else 3 * cm,
        bottomMargin=2.5 * cm,
        title=titulo,
    )

    story = []

    # Membrete (si existe URL/ruta accesible)
    if tiene_membrete:
        try:
            img = Image(config_colegio.membrete_url, width=16 * cm, height=2.5 * cm)
            story.append(img)
            story.append(Spacer(1, 0.3 * cm))
        except Exception:
            pass

    # Línea decorativa superior
    story.append(
        HRFlowable(
            width="100%",
            thickness=2,
            color=estilo["border_color"],
            spaceAfter=0.5 * cm,
        )
    )

    # Estilos de párrafo
    style_body = ParagraphStyle(
        "body",
        fontName=estilo["font"],
        fontSize=11,
        leading=16,
        textColor=estilo["color_text"],
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )
    style_bold = ParagraphStyle(
        "bold",
        fontName=estilo["font_bold"],
        fontSize=11,
        leading=16,
        textColor=estilo["color_text"],
        alignment=TA_LEFT,
        spaceAfter=8,
        spaceBefore=12,
    )
    style_bullet = ParagraphStyle(
        "bullet",
        fontName=estilo["font"],
        fontSize=11,
        leading=15,
        leftIndent=20,
        bulletIndent=10,
        spaceAfter=4,
        textColor=estilo["color_text"],
    )
    style_center = ParagraphStyle(
        "center",
        fontName=estilo["font"],
        fontSize=10,
        leading=14,
        textColor=estilo["color_text"],
        alignment=TA_CENTER,
        spaceAfter=10,
    )

    # Renderizar texto con detección de tablas y viñetas
    lineas = texto.split("\n")
    tabla_data = []
    i = 0
    while i < len(lineas):
        linea = lineas[i]
        linea_strip = linea.strip()

        # Detectar tablas
        if _es_linea_tabla(linea):
            # Líneas de borde (solo ─-+) se ignoran
            if all(c in '─-+| \t' for c in linea_strip):
                i += 1
                continue
            celdas = [
                c.strip()
                for c in linea_strip.replace('│', '|').split('|')
                if c.strip()
            ]
            if celdas:
                tabla_data.append(celdas)
            i += 1
            continue

        # Si acumulamos tabla, renderizarla
        if tabla_data:
            _flush_tabla(tabla_data, story, estilo)
            tabla_data = []

        if not linea_strip:
            story.append(Spacer(1, 0.25 * cm))
        elif linea_strip.startswith('•') or linea_strip.startswith('- '):
            texto_bullet = linea_strip.lstrip('•- ').strip()
            safe = escape(texto_bullet)
            story.append(
                Paragraph(f"&bull;&nbsp;&nbsp;{safe}", style_bullet)
            )
        elif _es_linea_negrita(linea, tipo_documento):
            safe = escape(linea_strip)
            story.append(Paragraph(safe, style_bold))
        else:
            safe = escape(linea_strip).replace("&amp;amp;", "&amp;")
            story.append(Paragraph(safe, style_body))

        i += 1

    # Flush tabla final si quedó
    if tabla_data:
        _flush_tabla(tabla_data, story, estilo)

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

    doc.build(story)
    return buffer.getvalue()


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
