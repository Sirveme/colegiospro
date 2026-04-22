"""
Servicio: PDF de Vista Previa de Boleta/Factura (antes de emitir a SUNAT)
app/services/pdf_preview_boleta.py

Genera un PDF local de aproximación para que el cajero revise ítems, cliente
y totales antes de confirmar la emisión. NO reemplaza al PDF oficial emitido
por facturalo.pro — lleva marca de agua "VISTA PREVIA".
"""

import io
from datetime import datetime, timezone, timedelta

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

TZ_PERU = timezone(timedelta(hours=-5))

AZUL = HexColor("#1e293b")
AZUL_MEDIO = HexColor("#334155")
GRIS = HexColor("#f1f5f9")
BORDE = HexColor("#cbd5e1")
NARANJA = HexColor("#d97706")


def _f(v):
    return f"{float(v or 0):,.2f}"


def _watermark(canvas_obj, doc):
    canvas_obj.saveState()
    canvas_obj.setFont("Helvetica-Bold", 72)
    canvas_obj.setFillColor(HexColor("#d97706"))
    canvas_obj.setFillAlpha(0.12)
    canvas_obj.translate(A4[0] / 2, A4[1] / 2)
    canvas_obj.rotate(35)
    canvas_obj.drawCentredString(0, 0, "VISTA PREVIA")
    canvas_obj.drawCentredString(0, -80, "NO VÁLIDA · NO EMITIDA")
    canvas_obj.restoreState()


def generar_pdf_preview(
    *,
    tipo_comprobante: str,
    serie: str,
    cliente: dict,
    items: list,
    subtotal: float,
    igv: float,
    total: float,
    forma_pago: str = "contado",
    metodo_pago: str = "Efectivo",
    org_nombre: str = "Colegio de Contadores Públicos de Lambayeque",
    org_ruc: str = "",
    org_direccion: str = "",
    matricula: str = None,
) -> bytes:
    """Retorna bytes del PDF preview."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    estilo_h1 = ParagraphStyle('h1', parent=styles['Heading1'],
                                fontName='Helvetica-Bold', fontSize=14,
                                textColor=AZUL, alignment=TA_CENTER, spaceAfter=2)
    estilo_sub = ParagraphStyle('sub', parent=styles['Normal'],
                                 fontSize=9, textColor=AZUL_MEDIO,
                                 alignment=TA_CENTER, spaceAfter=8)
    estilo_label = ParagraphStyle('lbl', parent=styles['Normal'],
                                   fontSize=9, textColor=AZUL_MEDIO)
    estilo_valor = ParagraphStyle('val', parent=styles['Normal'],
                                   fontName='Helvetica-Bold', fontSize=10, textColor=AZUL)
    estilo_item = ParagraphStyle('item', parent=styles['Normal'],
                                  fontSize=8.5, leading=11)
    estilo_warn = ParagraphStyle('warn', parent=styles['Normal'],
                                  fontName='Helvetica-Bold', fontSize=10,
                                  textColor=NARANJA, alignment=TA_CENTER, spaceAfter=6)

    ahora = datetime.now(TZ_PERU)
    tipo_txt = {
        "01": "FACTURA ELECTRÓNICA",
        "03": "BOLETA DE VENTA ELECTRÓNICA",
    }.get(tipo_comprobante, "COMPROBANTE")

    story = []

    story.append(Paragraph(
        "⚠️ VISTA PREVIA — Este documento NO es un comprobante oficial y NO ha sido emitido a SUNAT",
        estilo_warn
    ))
    story.append(HRFlowable(width="100%", thickness=1, color=NARANJA, spaceAfter=6))

    story.append(Paragraph(org_nombre, estilo_h1))
    subtitulo_partes = []
    if org_ruc:
        subtitulo_partes.append(f"RUC: {org_ruc}")
    if org_direccion:
        subtitulo_partes.append(org_direccion)
    if subtitulo_partes:
        story.append(Paragraph(" · ".join(subtitulo_partes), estilo_sub))

    story.append(Paragraph(tipo_txt, ParagraphStyle(
        'tipo', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=12, textColor=AZUL, alignment=TA_CENTER, spaceAfter=2,
    )))
    story.append(Paragraph(f"{serie} - PREVIEW", ParagraphStyle(
        'serie', parent=styles['Normal'], fontSize=10,
        textColor=AZUL_MEDIO, alignment=TA_CENTER, spaceAfter=10,
    )))

    tipo_doc_map = {"1": "DNI", "6": "RUC", "4": "CE", "7": "Pasaporte", "0": "S/D"}
    tipo_doc_txt = tipo_doc_map.get(str(cliente.get("tipo_doc") or "0"), "DOC")
    num_doc = cliente.get("num_doc") or "—"
    nombre = cliente.get("nombre") or "—"
    direccion = cliente.get("direccion") or ""
    matr = matricula or cliente.get("matricula") or ""

    filas_cliente = [
        [Paragraph("Cliente:", estilo_label), Paragraph(nombre, estilo_valor)],
        [Paragraph(f"{tipo_doc_txt}:", estilo_label), Paragraph(num_doc, estilo_valor)],
    ]
    if matr:
        filas_cliente.append([Paragraph("Cód. Matrícula:", estilo_label), Paragraph(matr, estilo_valor)])
    if direccion:
        filas_cliente.append([Paragraph("Dirección:", estilo_label), Paragraph(direccion, estilo_valor)])
    filas_cliente.append([Paragraph("Fecha emisión (preview):", estilo_label),
                          Paragraph(ahora.strftime("%d/%m/%Y %H:%M"), estilo_valor)])
    filas_cliente.append([Paragraph("Forma de pago:", estilo_label),
                          Paragraph(f"{metodo_pago} · {forma_pago.capitalize()}", estilo_valor)])

    tbl_cliente = Table(filas_cliente, colWidths=[40 * mm, 140 * mm])
    tbl_cliente.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), GRIS),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LINEBELOW', (0, 0), (-1, -2), 0.3, BORDE),
    ]))
    story.append(tbl_cliente)
    story.append(Spacer(1, 8))

    encabezado = [
        Paragraph("<b>Descripción</b>", estilo_item),
        Paragraph("<b>Cant.</b>", ParagraphStyle('x', parent=estilo_item, alignment=TA_CENTER)),
        Paragraph("<b>P. Unit.</b>", ParagraphStyle('x', parent=estilo_item, alignment=TA_RIGHT)),
        Paragraph("<b>Importe</b>", ParagraphStyle('x', parent=estilo_item, alignment=TA_RIGHT)),
    ]
    filas_items = [encabezado]
    for it in items:
        desc_html = (it.get("descripcion", "") or "").replace("\n", "<br/>")
        filas_items.append([
            Paragraph(desc_html, estilo_item),
            Paragraph(str(it.get("cantidad", 1)),
                      ParagraphStyle('x', parent=estilo_item, alignment=TA_CENTER)),
            Paragraph(f"S/ {_f(it.get('precio_unitario'))}",
                      ParagraphStyle('x', parent=estilo_item, alignment=TA_RIGHT)),
            Paragraph(f"S/ {_f(it.get('valor_venta'))}",
                      ParagraphStyle('x', parent=estilo_item, alignment=TA_RIGHT)),
        ])

    tbl_items = Table(filas_items, colWidths=[115 * mm, 15 * mm, 25 * mm, 25 * mm], repeatRows=1)
    tbl_items.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), AZUL),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('GRID', (0, 0), (-1, -1), 0.3, BORDE),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl_items)
    story.append(Spacer(1, 8))

    filas_totales = [
        ["Subtotal", f"S/ {_f(subtotal)}"],
        ["IGV", f"S/ {_f(igv)}"],
        ["TOTAL", f"S/ {_f(total)}"],
    ]
    tbl_tot = Table(filas_totales, colWidths=[40 * mm, 30 * mm], hAlign='RIGHT')
    tbl_tot.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -2), 10),
        ('FONTSIZE', (0, -1), (-1, -1), 12),
        ('TEXTCOLOR', (0, -1), (-1, -1), AZUL),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('LINEABOVE', (0, -1), (-1, -1), 1, AZUL),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl_tot)
    story.append(Spacer(1, 20))

    story.append(HRFlowable(width="100%", thickness=0.5, color=BORDE, spaceAfter=4))
    story.append(Paragraph(
        "Al confirmar se emitirá el comprobante real: se asignará correlativo, se enviará a SUNAT vía facturalo.pro y se generará el PDF oficial.",
        ParagraphStyle('foot', parent=styles['Normal'], fontSize=8,
                       textColor=AZUL_MEDIO, alignment=TA_CENTER)
    ))

    doc.build(story, onFirstPage=_watermark, onLaterPages=_watermark)
    buf.seek(0)
    return buf.getvalue()
