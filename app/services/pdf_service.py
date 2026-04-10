# ══════════════════════════════════════════════════════════
# app/services/pdf_service.py
# Generación de PDF a partir del texto del documento.
# Intenta WeasyPrint → ReportLab → fallback HTML imprimible.
# ══════════════════════════════════════════════════════════

from io import BytesIO
from html import escape

# WeasyPrint (preferido por estilo CSS)
try:
    from weasyprint import HTML  # type: ignore
    _HAS_WEASY = True
except Exception:
    _HAS_WEASY = False

# ReportLab (fallback)
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    _HAS_REPORTLAB = True
except Exception:
    _HAS_REPORTLAB = False


def _texto_a_html(texto: str, titulo: str = "Documento") -> str:
    parrafos = "".join(
        f"<p>{escape(linea) if linea.strip() else '&nbsp;'}</p>"
        for linea in texto.split("\n")
    )
    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{escape(titulo)}</title>
<style>
@page {{ size: A4; margin: 2.5cm 2cm; }}
body {{ font-family: 'Times New Roman', Georgia, serif; font-size: 12pt; line-height: 1.5; color: #111; }}
p {{ margin: 0 0 .6em 0; white-space: pre-wrap; }}
</style>
</head>
<body>
{parrafos}
</body>
</html>"""


def texto_a_pdf_bytes(texto: str, titulo: str = "Documento") -> bytes:
    """
    Devuelve los bytes del PDF generado a partir de `texto`.
    Si no hay librerías PDF instaladas, devuelve HTML imprimible
    (el endpoint puede servirlo como text/html en ese caso).
    """
    if _HAS_WEASY:
        html = _texto_a_html(texto, titulo)
        return HTML(string=html).write_pdf()

    if _HAS_REPORTLAB:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            leftMargin=2 * cm, rightMargin=2 * cm,
            topMargin=2.5 * cm, bottomMargin=2.5 * cm,
            title=titulo,
        )
        styles = getSampleStyleSheet()
        estilo = ParagraphStyle(
            "doc",
            parent=styles["Normal"],
            fontName="Times-Roman",
            fontSize=12,
            leading=18,
        )
        story = []
        for linea in texto.split("\n"):
            if linea.strip():
                story.append(Paragraph(escape(linea).replace(" ", "&nbsp;", 0), estilo))
            else:
                story.append(Spacer(1, 10))
        doc.build(story)
        return buffer.getvalue()

    # Último recurso: devolver HTML como bytes (el caller decide content-type)
    return _texto_a_html(texto, titulo).encode("utf-8")


def pdf_disponible() -> bool:
    return _HAS_WEASY or _HAS_REPORTLAB
