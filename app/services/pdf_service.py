# ══════════════════════════════════════════════════════════
# app/services/pdf_service.py
# Generación de PDF a partir del texto del documento.
# Usa ReportLab cuando está disponible, con estilo por tono.
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
) -> bytes:
    """
    Genera el PDF del documento. Aplica estilo según el tono.
    Si hay membrete en la config del colegio, lo coloca arriba.
    """
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in ("formal", "cordial", "protocolar"):
        tono_norm = "formal"

    if _HAS_REPORTLAB:
        return _generar_pdf_reportlab(texto, titulo, tono_norm, config_colegio)

    if _HAS_WEASY:
        return HTML(string=_texto_a_html(texto, titulo, tono_norm)).write_pdf()

    # Último recurso: HTML como bytes (caller decide content-type)
    return _texto_a_html(texto, titulo, tono_norm).encode("utf-8")


def _generar_pdf_reportlab(
    texto: str,
    titulo: str,
    tono: str,
    config_colegio=None,
) -> bytes:
    estilos = _estilos_tono()
    estilo = estilos[tono]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=2.5 * cm,
        leftMargin=2.5 * cm,
        topMargin=3 * cm,
        bottomMargin=2.5 * cm,
        title=titulo,
    )

    story = []

    # Membrete (si existe URL/ruta accesible)
    if config_colegio is not None:
        membrete = getattr(config_colegio, "membrete_url", None)
        if membrete:
            try:
                img = Image(membrete, width=16 * cm, height=3 * cm)
                story.append(img)
                story.append(Spacer(1, 0.4 * cm))
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

    # Cuerpo del texto — párrafo por párrafo
    style_body = ParagraphStyle(
        "body",
        fontName=estilo["font"],
        fontSize=11,
        leading=16,
        textColor=estilo["color_text"],
        alignment=TA_JUSTIFY,
        spaceAfter=10,
    )

    for linea in texto.split("\n"):
        linea_strip = linea.strip()
        if linea_strip:
            # Escapar caracteres especiales para reportlab
            safe = (
                escape(linea_strip)
                .replace("&amp;amp;", "&amp;")
            )
            story.append(Paragraph(safe, style_body))
        else:
            story.append(Spacer(1, 0.3 * cm))

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


def pdf_disponible() -> bool:
    return _HAS_WEASY or _HAS_REPORTLAB
