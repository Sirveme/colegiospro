"""
Módulo: Caja - Cobros Presenciales
app/routers/caja.py

Pantalla de caja para el personal del CCPL.
Flujo: Buscar colegiado → Ver deudas → Cobrar → Emitir comprobante

Requiere rol: cajero, tesorero o admin
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from decimal import Decimal
import logging
import json

from fastapi import Request
from fastapi.templating import Jinja2Templates
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, and_, text
from pydantic import BaseModel, Field

import io
from fastapi.responses import StreamingResponse
from app.utils.templates import templates
#from app.services.pdf_cierre_caja import generar_pdf_cierre

from app.database import get_db
from app.models import (
    Colegiado, Payment, Comprobante, ConceptoCobro,
    UsuarioAdmin, CentroCosto, Organization,
    ConfiguracionFacturacion
)
from app.models_debt_management import Debt

from starlette.responses import StreamingResponse
import httpx

from app.routers.dashboard import get_current_member
from app.models import Member

from app.services.facturacion import FacturacionService

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/caja", tags=["Caja"])

# Router para la página HTML (sin prefix)
page_router = APIRouter(tags=["Caja"])


PERU_TZ = timezone(timedelta(hours=-5))

def _inicio_dia_peru_utc(fecha=None):
    """
    Retorna medianoche Perú convertida a UTC naive (para comparar con created_at).
    fecha: date object o None para hoy.
    Medianoche Perú (00:00 UTC-5) = 05:00 UTC
    """
    if fecha is None:
        ahora_peru = datetime.now(PERU_TZ)
        fecha = ahora_peru.date()
    # Medianoche Perú en UTC = fecha 05:00:00 UTC
    return datetime(fecha.year, fecha.month, fecha.day, 5, 0, 0)

def _fin_dia_peru_utc(fecha=None):
    """Fin del día Perú (23:59:59) convertido a UTC naive."""
    inicio = _inicio_dia_peru_utc(fecha)
    return inicio + timedelta(days=1)


@page_router.get("/caja")
async def pagina_caja(request: Request, member: Member = Depends(get_current_member)):
    return templates.TemplateResponse("pages/caja.html", {"request": request})


PERU_TZ = timezone(timedelta(hours=-5))


# ============================================================
# SCHEMAS
# ============================================================

class BuscarColegiadoResponse(BaseModel):
    id: int
    dni: str
    codigo_matricula: Optional[str] = None
    apellidos_nombres: str
    email: Optional[str] = None
    telefono: Optional[str] = None
    habilitado: bool = False
    total_deuda: float = 0
    deudas_pendientes: int = 0

    class Config:
        from_attributes = True


class DeudaResponse(BaseModel):
    id: int
    concepto: Optional[str] = None
    periodo: Optional[str] = None
    monto: float
    monto_pagado: float = 0
    saldo: float = 0
    fecha_vencimiento: Optional[str] = None
    estado: str

    class Config:
        from_attributes = True


class ItemCobro(BaseModel):
    """Item individual a cobrar"""
    tipo: str = "deuda"
    deuda_id: Optional[int] = None
    concepto_id: Optional[int] = None
    descripcion: str = ""
    cantidad: int = 1
    monto_unitario: float = 0
    monto_total: float = 0


class RegistrarCobroRequest(BaseModel):
    """Request para registrar un cobro"""
    colegiado_id: Optional[int] = None
    items: List[ItemCobro]
    total: float
    metodo_pago: str = "efectivo"
    referencia_pago: Optional[str] = None
    observaciones: Optional[str] = None
    tipo_comprobante: str = "03"
    cliente_ruc: Optional[str] = None
    cliente_razon_social: Optional[str] = None
    cliente_direccion: Optional[str] = None
    forma_pago: str = "contado"


class CobroResponse(BaseModel):
    success: bool
    mensaje: str
    payment_id: Optional[int] = None
    total: float = 0
    comprobante_emitido: Optional[bool] = None
    comprobante_numero: Optional[str] = None
    comprobante_pdf: Optional[str] = None
    comprobante_estado: Optional[str] = None
    comprobante_mensaje: Optional[str] = None


# ============================================================
# SCHEMAS EGRESOS
# ============================================================

class EgresoRequest(BaseModel):
    monto: float
    concepto: str
    responsable: str
    detalle: Optional[str] = None
    tipo: str = "gasto"

class LiquidarEgresoRequest(BaseModel):
    monto_factura: float
    numero_documento: Optional[str] = None
    observaciones: Optional[str] = None


# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/buscar-colegiado")
async def buscar_colegiado(
    q: str = Query(..., min_length=2, description="DNI, matrícula o nombre"),
    db: Session = Depends(get_db),
):
    """
    Busca colegiados por DNI, código de matrícula o nombre.
    Retorna lista con resumen de deudas.
    """
    q = q.strip()
    query = db.query(Colegiado)

    if q.isdigit() and len(q) >= 7:
        query = query.filter(Colegiado.dni == q)
    elif "-" in q:
        query = query.filter(Colegiado.codigo_matricula == q)
    else:
        query = query.filter(
            or_(
                Colegiado.apellidos_nombres.ilike(f"%{q}%"),
                Colegiado.dni.contains(q),
                Colegiado.codigo_matricula.contains(q),
            )
        )

    colegiados = query.limit(20).all()

    resultados = []
    for col in colegiados:
        deudas_info = db.query(
            func.count(Debt.id).label("cantidad"),
            func.coalesce(func.sum(Debt.amount), 0).label("total"),
        ).filter(
            Debt.colegiado_id == col.id,
            Debt.status.in_(["pending", "partial"]),
        ).first()

        resultados.append(BuscarColegiadoResponse(
            id=col.id,
            dni=col.dni or "",
            codigo_matricula=col.codigo_matricula or "",
            apellidos_nombres=col.apellidos_nombres or "",
            email=col.email,
            telefono=col.telefono,
            habilitado=(col.condicion in ('habil', 'vitalicio')),
            total_deuda=float(deudas_info.total or 0),
            deudas_pendientes=int(deudas_info.cantidad or 0),
        ))

    return resultados


@router.get("/deudas/{colegiado_id}")
async def obtener_deudas(
    colegiado_id: int,
    db: Session = Depends(get_db),
):
    """Obtiene las deudas pendientes de un colegiado."""
    colegiado = db.query(Colegiado).filter(Colegiado.id == colegiado_id).first()
    if not colegiado:
        raise HTTPException(404, detail="Colegiado no encontrado")

    deudas = db.query(Debt).filter(
        Debt.colegiado_id == colegiado_id,
        Debt.status.in_(["pending", "partial"]),
    ).order_by(Debt.periodo.asc()).all()

    resultado = []
    for d in deudas:
        monto = float(d.amount or 0)
        saldo = float(d.balance or 0)
        resultado.append(DeudaResponse(
            id=d.id,
            concepto=d.concept or "Cuota",
            periodo=str(d.periodo) if d.periodo else None,
            monto=monto,
            monto_pagado=monto - saldo,
            saldo=saldo,
            fecha_vencimiento=d.due_date.strftime("%d/%m/%Y") if d.due_date else None,
            estado=d.status,
        ))

    return {
        "colegiado": {
            "id": colegiado.id,
            "dni": colegiado.dni,
            "codigo_matricula": colegiado.codigo_matricula,
            "apellidos_nombres": colegiado.apellidos_nombres,
            "habilitado": (colegiado.condicion in ('habil', 'vitalicio')),
        },
        "deudas": resultado,
        "total_deuda": sum(d.saldo for d in resultado),
    }


@router.get("/conceptos")
async def listar_conceptos(
    categoria: Optional[str] = None,
    solo_publico: bool = False,
    db: Session = Depends(get_db),
):
    """Lista conceptos de cobro disponibles para la caja."""
    query = db.query(ConceptoCobro).filter(ConceptoCobro.activo == True)

    if categoria:
        query = query.filter(ConceptoCobro.categoria == categoria)
    if solo_publico:
        query = query.filter(ConceptoCobro.aplica_a_publico == True)

    conceptos = query.order_by(ConceptoCobro.orden, ConceptoCobro.nombre).all()

    return [{
        "id": c.id,
        "codigo": c.codigo,
        "nombre": c.nombre,
        "nombre_corto": c.nombre_corto,
        "categoria": c.categoria,
        "monto_base": c.monto_base,
        "permite_monto_libre": c.permite_monto_libre,
        "afecto_igv": c.afecto_igv,
        "requiere_colegiado": c.requiere_colegiado,
        "maneja_stock": c.maneja_stock,
        "stock_actual": c.stock_actual if c.maneja_stock else None,
    } for c in conceptos]


@router.get("/categorias")
async def listar_categorias(db: Session = Depends(get_db)):
    """Lista las categorías de conceptos que tienen conceptos activos"""
    categorias = db.query(
        ConceptoCobro.categoria,
        func.count(ConceptoCobro.id).label("total")
    ).filter(
        ConceptoCobro.activo == True
    ).group_by(ConceptoCobro.categoria).order_by(ConceptoCobro.categoria).all()

    NOMBRES = {
        "cuotas": "Cuotas", "constancias": "Constancias", "derechos": "Derechos",
        "capacitacion": "Capacitación", "alquileres": "Alquileres",
        "recreacion": "Recreación", "mercaderia": "Mercadería",
        "multas": "Multas", "eventos": "Eventos", "otros": "Otros",
    }

    return [{
        "codigo": cat,
        "nombre": NOMBRES.get(cat, cat.title()),
        "total": total,
    } for cat, total in categorias]


# ============================================================
# COBRAR — Endpoint principal
# ============================================================

@router.post("/cobrar", response_model=CobroResponse)
async def registrar_cobro(
    cobro: RegistrarCobroRequest,
    db: Session = Depends(get_db),
):
    """
    Registra un cobro presencial.
    1. Valida items  2. Crea Payment  3. Marca deudas pagadas
    4. Actualiza stock  5. Emite comprobante vía facturalo.pro
    """
    ahora = datetime.now(PERU_TZ)

    org = db.query(Organization).first()
    if not org:
        raise HTTPException(500, detail="Sin organización configurada")

    colegiado = None
    if cobro.colegiado_id:
        colegiado = db.query(Colegiado).filter(
            Colegiado.id == cobro.colegiado_id
        ).first()
        if not colegiado:
            raise HTTPException(404, detail="Colegiado no encontrado")

    # ── Validar y procesar items ──
    total_calculado = 0
    items_procesados = []
    deudas_a_pagar = []

    for item in cobro.items:
        if item.tipo == "deuda" and item.deuda_id:
            deuda = db.query(Debt).filter(
                Debt.id == item.deuda_id,
                Debt.status.in_(["pending", "partial"]),
            ).first()
            if not deuda:
                raise HTTPException(400, detail=f"Deuda {item.deuda_id} no encontrada o ya pagada")

            saldo = float(deuda.balance or 0)
            items_procesados.append({
                "tipo": "deuda",
                "deuda_id": deuda.id,
                "descripcion": f"{deuda.concept or 'Cuota'} {deuda.periodo or ''}".strip(),
                "monto": saldo,
            })
            deudas_a_pagar.append(deuda)
            total_calculado += saldo

        elif item.tipo == "concepto" and item.concepto_id:
            concepto = db.query(ConceptoCobro).filter(
                ConceptoCobro.id == item.concepto_id,
                ConceptoCobro.activo == True,
            ).first()
            if not concepto:
                raise HTTPException(400, detail=f"Concepto {item.concepto_id} no encontrado")

            if concepto.permite_monto_libre:
                monto = item.monto_unitario if item.monto_unitario > 0 else concepto.monto_base
            else:
                monto = concepto.monto_base

            if monto <= 0:
                raise HTTPException(400, detail=f"Monto inválido para {concepto.nombre}")

            if concepto.maneja_stock:
                if concepto.stock_actual < item.cantidad:
                    raise HTTPException(400,
                        detail=f"Stock insuficiente de {concepto.nombre}: disponible {concepto.stock_actual}")

            monto_total = monto * item.cantidad
            items_procesados.append({
                "tipo": "concepto",
                "concepto_id": concepto.id,
                "codigo": concepto.codigo,
                "descripcion": concepto.nombre,
                "cantidad": item.cantidad,
                "monto_unitario": monto,
                "monto_total": monto_total,
                "afecto_igv": concepto.afecto_igv,
            })
            total_calculado += monto_total

            if concepto.maneja_stock:
                concepto.stock_actual -= item.cantidad

        else:
            if item.monto_total <= 0:
                raise HTTPException(400, detail="Item sin monto")
            items_procesados.append({
                "tipo": "libre",
                "descripcion": item.descripcion or "Otros",
                "cantidad": item.cantidad,
                "monto_total": item.monto_total,
            })
            total_calculado += item.monto_total

    if not items_procesados:
        raise HTTPException(400, detail="No hay items para cobrar")

    if abs(total_calculado - cobro.total) > 0.02:
        raise HTTPException(400,
            detail=f"Total no coincide: calculado={total_calculado:.2f}, enviado={cobro.total:.2f}")

    # ── CREAR PAYMENT ──
    descripciones = [i["descripcion"] for i in items_procesados]
    descripcion_pago = "; ".join(descripciones[:5])
    if len(descripciones) > 5:
        descripcion_pago += f" (+{len(descripciones) - 5} más)"

    # Incluir IDs de deudas en notes para reconstruir en facturación
    ids_deudas = [str(d.id) for d in deudas_a_pagar]
    ids_str = ",".join(ids_deudas)
    payment = Payment(
        organization_id=org.id,
        colegiado_id=cobro.colegiado_id,
        amount=Decimal(str(cobro.total)),
        payment_method=cobro.metodo_pago,
        operation_code=cobro.referencia_pago,
        notes=f"[CAJA] {descripcion_pago} [DEBT_IDS:{ids_str}]",
        status="approved",
        reviewed_at=ahora,
    )

    if cobro.tipo_comprobante == "01" and cobro.cliente_ruc:
        payment.pagador_tipo = "empresa"
        payment.pagador_documento = cobro.cliente_ruc
        payment.pagador_nombre = cobro.cliente_razon_social

    db.add(payment)
    db.flush()

    # ── MARCAR DEUDAS COMO PAGADAS ──
    for deuda in deudas_a_pagar:
        deuda.status = "paid"
        deuda.balance = 0

    # ── GENERAR DEUDAS para conceptos que genera_deuda ──
    for item in items_procesados:
        if item["tipo"] == "concepto":
            concepto = db.query(ConceptoCobro).filter(
                ConceptoCobro.id == item["concepto_id"]
            ).first()
            if concepto and concepto.genera_deuda and cobro.colegiado_id:
                nueva_deuda = Debt(
                    organization_id=org.id,
                    colegiado_id=cobro.colegiado_id,
                    concept=concepto.nombre,
                    amount=Decimal(str(item["monto_total"])),
                    balance=0,
                    status="paid",
                )
                db.add(nueva_deuda)

    db.commit()

    # ═══ EMITIR COMPROBANTE ELECTRÓNICO ═══
    comprobante_info = {}
    try:
        service = FacturacionService(db, org.id)

        if service.esta_configurado():
            tipo = cobro.tipo_comprobante or "03"

            forzar_cliente = None
            if tipo == "01" and cobro.cliente_ruc:
                forzar_cliente = {
                    "tipo_doc": "6",
                    "num_doc": cobro.cliente_ruc,
                    "nombre": cobro.cliente_razon_social or "",
                    "direccion": cobro.cliente_direccion or "",
                    "email": "",
                }

            resultado = await service.emitir_comprobante_por_pago(
                payment_id=payment.id,
                tipo=tipo,
                forzar_datos_cliente=forzar_cliente,
                sede_id="1",
                forma_pago=cobro.forma_pago,
            )
            logger.info(f"FACTURALO RESULTADO: {resultado}")

            comprobante_info = {
                "comprobante_emitido": resultado.get("success", False),
                "comprobante_numero": resultado.get("numero_formato"),
                "comprobante_pdf": resultado.get("pdf_url"),
                "comprobante_estado": "aceptado" if resultado.get("success") else "error",
                "comprobante_mensaje": resultado.get("error"),
            }
        else:
            comprobante_info = {
                "comprobante_emitido": False,
                "comprobante_mensaje": "Facturación no configurada",
            }

    except Exception as e:
        logger.error(f"Error facturación: {e}", exc_info=True)
        comprobante_info = {
            "comprobante_emitido": False,
            "comprobante_mensaje": f"Error: {str(e)[:100]}",
        }

    return CobroResponse(
        success=True,
        mensaje=f"Cobro registrado: S/ {cobro.total:.2f} - {cobro.metodo_pago}",
        payment_id=payment.id,
        total=cobro.total,
        **comprobante_info,
    )


# ════════════════════════════════════════════════════════════
# VISTA PREVIA — genera PDF sin tocar BD ni SUNAT
# ════════════════════════════════════════════════════════════

@router.post("/cobrar/preview")
async def preview_cobro(
    cobro: RegistrarCobroRequest,
    db: Session = Depends(get_db),
):
    """
    Genera un PDF de vista previa del comprobante que se emitiría,
    sin reservar correlativo, sin persistir Payment/Comprobante
    y sin enviar a SUNAT. Devuelve el PDF en base64.

    Recibe exactamente los mismos parámetros que POST /cobrar.
    """
    import base64
    from app.services.pdf_preview_boleta import generar_pdf_preview

    org = db.query(Organization).first()
    if not org:
        return {"ok": False, "error": "Sin organización configurada"}

    # ── Validar items y construir listas en memoria (sin db.add) ──
    total_calculado = 0.0
    deudas_mock: list = []
    items_mock: list = []

    for item in cobro.items:
        if item.tipo == "deuda" and item.deuda_id:
            deuda = db.query(Debt).filter(
                Debt.id == item.deuda_id,
                Debt.status.in_(["pending", "partial"]),
            ).first()
            if not deuda:
                return {"ok": False, "error": f"Deuda {item.deuda_id} no encontrada o ya pagada"}
            saldo = float(deuda.balance or 0)
            deudas_mock.append(deuda)
            items_mock.append({
                "tipo": "deuda",
                "deuda_id": deuda.id,
                "descripcion": f"{deuda.concept or 'Cuota'} {deuda.periodo or ''}".strip(),
                "monto": saldo,
            })
            total_calculado += saldo

        elif item.tipo == "concepto" and item.concepto_id:
            concepto = db.query(ConceptoCobro).filter(
                ConceptoCobro.id == item.concepto_id,
                ConceptoCobro.activo == True,
            ).first()
            if not concepto:
                return {"ok": False, "error": f"Concepto {item.concepto_id} no encontrado"}
            if concepto.permite_monto_libre:
                monto = item.monto_unitario if item.monto_unitario > 0 else concepto.monto_base
            else:
                monto = concepto.monto_base
            if monto <= 0:
                return {"ok": False, "error": f"Monto inválido para {concepto.nombre}"}
            monto_total = monto * item.cantidad
            items_mock.append({
                "tipo": "concepto",
                "concepto_id": concepto.id,
                "codigo": concepto.codigo,
                "descripcion": concepto.nombre,
                "cantidad": item.cantidad,
                "monto_unitario": monto,
                "monto_total": monto_total,
                "afecto_igv": concepto.afecto_igv,
            })
            total_calculado += monto_total

        else:
            if item.monto_total <= 0:
                return {"ok": False, "error": "Item sin monto"}
            items_mock.append({
                "tipo": "libre",
                "descripcion": item.descripcion or "Otros",
                "cantidad": item.cantidad,
                "monto_total": item.monto_total,
            })
            total_calculado += item.monto_total

    if not items_mock:
        return {"ok": False, "error": "No hay items para cobrar"}

    if abs(total_calculado - cobro.total) > 0.02:
        return {"ok": False,
                "error": f"Total no coincide: calculado={total_calculado:.2f}, enviado={cobro.total:.2f}"}

    # ── Payment simulado (NO se agrega a la sesión) ──
    ahora = datetime.now(PERU_TZ)
    descripciones = [i["descripcion"] for i in items_mock]
    descripcion_pago = "; ".join(descripciones[:5])
    if len(descripciones) > 5:
        descripcion_pago += f" (+{len(descripciones) - 5} más)"
    ids_deudas = [str(d.id) for d in deudas_mock]
    ids_str = ",".join(ids_deudas)

    payment_mock = Payment(
        organization_id=org.id,
        colegiado_id=cobro.colegiado_id,
        amount=float(cobro.total),
        payment_method=cobro.metodo_pago,
        operation_code=cobro.referencia_pago,
        notes=f"[CAJA] {descripcion_pago} [DEBT_IDS:{ids_str}]",
        status="approved",
        reviewed_at=ahora,
    )
    payment_mock.created_at = ahora
    if cobro.tipo_comprobante == "01" and cobro.cliente_ruc:
        payment_mock.pagador_tipo = "empresa"
        payment_mock.pagador_documento = cobro.cliente_ruc
        payment_mock.pagador_nombre = cobro.cliente_razon_social

    # ── Reutilizar pipeline: cliente + items exactamente como en /cobrar real ──
    try:
        service = FacturacionService(db, org.id)
        tipo = cobro.tipo_comprobante or "03"

        forzar_cliente = None
        if tipo == "01" and cobro.cliente_ruc:
            forzar_cliente = {
                "tipo_doc": "6",
                "num_doc": cobro.cliente_ruc,
                "nombre": cobro.cliente_razon_social or "",
                "direccion": cobro.cliente_direccion or "",
                "email": "",
            }

        cliente_data = service._obtener_datos_cliente(payment_mock, forzar_cliente)
        items_doc = service._construir_items(payment_mock, tipo)

        # Serie resuelta igual que en emisión real (sin reservar correlativo)
        from app.services.facturacion import obtener_serie
        serie = obtener_serie(tipo, sede_id="1", config=service.config) if service.config else (
            "F001" if tipo == "01" else "B001"
        )

        # Totales
        subtotal = float(cobro.total)
        cfg = service.config
        if cfg and cfg.tipo_afectacion_igv == "10" and (cfg.porcentaje_igv or 0) > 0:
            # Precios incluyen IGV — descomponer
            pct = float(cfg.porcentaje_igv) / 100
            subtotal = round(float(cobro.total) / (1 + pct), 2)
            igv = round(float(cobro.total) - subtotal, 2)
        else:
            igv = 0.0
        total = float(cobro.total)

        matricula = None
        if cobro.colegiado_id:
            colegiado = db.query(Colegiado).filter(
                Colegiado.id == cobro.colegiado_id
            ).first()
            if colegiado:
                matricula = colegiado.codigo_matricula

        org_nombre_txt = (cfg.razon_social if cfg and cfg.razon_social else getattr(org, 'name', '')) or ""
        org_ruc_txt = (cfg.ruc if cfg and cfg.ruc else "") or ""
        org_direccion_txt = (cfg.direccion if cfg and cfg.direccion else "") or ""

        pdf_bytes = generar_pdf_preview(
            tipo_comprobante=tipo,
            serie=serie,
            cliente=cliente_data,
            items=items_doc,
            subtotal=subtotal,
            igv=igv,
            total=total,
            forma_pago=cobro.forma_pago,
            metodo_pago=cobro.metodo_pago,
            org_nombre=org_nombre_txt,
            org_ruc=org_ruc_txt,
            org_direccion=org_direccion_txt,
            matricula=matricula,
        )

        return {
            "ok": True,
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "nombre": f"preview_{serie}.pdf",
            "total": total,
            "items_count": len(items_doc),
        }
    except Exception as e:
        logger.error(f"Error generando preview: {e}", exc_info=True)
        return {"ok": False, "error": f"Error generando preview: {str(e)[:200]}"}


# ════════════════════════════════════════════════════════════
# AGREGAR ESTE ENDPOINT EN caja.py (después del endpoint /cobrar)
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
# AGREGAR ESTOS 2 ENDPOINTS EN caja.py
# (después del endpoint /cobrar)
#
# Imports necesarios al inicio de caja.py:
#   from starlette.responses import StreamingResponse
#   import httpx
# ════════════════════════════════════════════════════════════


@router.get("/comprobante/{payment_id}")
async def obtener_comprobante_pago(
    payment_id: int,
    db: Session = Depends(get_db),
):
    """
    Consulta el comprobante de un pago.
    Si pdf_url no existe localmente, consulta facturalo.pro y actualiza.
    Retorna proxy URL (no la URL directa de facturalo.pro).
    """
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(404, detail="Pago no encontrado")

    comp = db.query(Comprobante).filter(
        Comprobante.payment_id == payment_id,
        Comprobante.tipo.in_(["01", "03"]),
    ).order_by(Comprobante.created_at.desc()).first()

    if not comp:
        raise HTTPException(404, detail="Comprobante no encontrado")

    # Si no tenemos datos de SUNAT, consultar facturalo.pro
    if (not comp.pdf_url or comp.status == "pending") and comp.facturalo_id:
        try:
            config = db.query(ConfiguracionFacturacion).filter(
                ConfiguracionFacturacion.organization_id == payment.organization_id,
                ConfiguracionFacturacion.activo == True,
            ).first()

            if config and config.facturalo_token:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(
                        f"{config.facturalo_url}/comprobantes/{comp.facturalo_id}",
                        headers={
                            "X-API-Key": config.facturalo_token,
                            "X-API-Secret": config.facturalo_secret,
                        },
                    )
                    if r.status_code == 200:
                        data = r.json()
                        comp_data = data.get("comprobante", data)
                        archivos = data.get("archivos", {})

                        if archivos.get("pdf_url") or comp_data.get("pdf_url"):
                            comp.pdf_url = archivos.get("pdf_url") or comp_data.get("pdf_url")
                        if archivos.get("xml_url"):
                            comp.xml_url = archivos["xml_url"]
                        if comp_data.get("hash_cpe") and not comp.sunat_hash:
                            comp.sunat_hash = comp_data["hash_cpe"]
                        if comp_data.get("estado") == "aceptado" and comp.status == "pending":
                            comp.status = "accepted"
                            comp.sunat_response_description = comp_data.get("mensaje_sunat")
                            comp.sunat_response_code = str(comp_data.get("codigo_sunat", "0"))

                        db.commit()
        except Exception as e:
            logger.warning(f"Error consultando facturalo.pro: {e}")

    # Retornar proxy URL en lugar de la URL directa de facturalo.pro
    proxy_pdf = f"/api/caja/comprobante/{payment_id}/pdf" if (comp.pdf_url or comp.facturalo_id) else None

    return {
        "payment_id": payment_id,
        "comprobante_id": comp.id,
        "tipo": comp.tipo,
        "numero_formato": f"{comp.serie}-{str(comp.numero).zfill(8)}",
        "status": comp.status,
        "pdf_url": proxy_pdf,
        "sunat_hash": comp.sunat_hash,
        "sunat_response": comp.sunat_response_description,
    }


@router.get("/comprobante/{payment_id}/pdf")
async def descargar_pdf_comprobante(
    payment_id: int,
    db: Session = Depends(get_db),
):
    """
    Proxy: descarga el PDF desde facturalo.pro con autenticación
    y lo retransmite al navegador del usuario.
    """
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(404, detail="Pago no encontrado")

    comp = db.query(Comprobante).filter(
        Comprobante.payment_id == payment_id,
        Comprobante.tipo.in_(["01", "03"]),
    ).order_by(Comprobante.created_at.desc()).first()

    if not comp:
        raise HTTPException(404, detail="Comprobante no encontrado")

    if not comp.facturalo_id:
        raise HTTPException(404, detail="Comprobante sin ID en facturalo.pro")

    config = db.query(ConfiguracionFacturacion).filter(
        ConfiguracionFacturacion.organization_id == payment.organization_id,
        ConfiguracionFacturacion.activo == True,
    ).first()

    if not config or not config.facturalo_token:
        raise HTTPException(500, detail="Facturación no configurada")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{config.facturalo_url}/comprobantes/{comp.facturalo_id}/pdf",
                headers={
                    "X-API-Key": config.facturalo_token,
                    "X-API-Secret": config.facturalo_secret,
                },
            )

            if r.status_code != 200:
                raise HTTPException(502, detail=f"Error obteniendo PDF: {r.status_code}")

            content_type = r.headers.get("content-type", "application/pdf")
            numero_fmt = f"{comp.serie}-{str(comp.numero).zfill(8)}"

            return StreamingResponse(
                iter([r.content]),
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{numero_fmt}.pdf"',
                },
            )

    except httpx.TimeoutException:
        raise HTTPException(504, detail="Timeout obteniendo PDF")
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"Error de conexión: {str(e)}")


@router.get("/comprobantes/{comprobante_id}/pdf")
async def descargar_pdf_por_comprobante(
    comprobante_id: int,
    db: Session = Depends(get_db),
):
    """Proxy PDF por id de comprobante (soporta boleta, factura y notas de crédito/débito)."""
    comp = db.query(Comprobante).filter(Comprobante.id == comprobante_id).first()
    if not comp:
        raise HTTPException(404, detail="Comprobante no encontrado")
    if not comp.facturalo_id:
        raise HTTPException(404, detail="Comprobante sin ID en facturalo.pro")

    config = db.query(ConfiguracionFacturacion).filter(
        ConfiguracionFacturacion.organization_id == comp.organization_id,
        ConfiguracionFacturacion.activo == True,
    ).first()
    if not config or not config.facturalo_token:
        raise HTTPException(500, detail="Facturación no configurada")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{config.facturalo_url}/comprobantes/{comp.facturalo_id}/pdf",
                headers={
                    "X-API-Key": config.facturalo_token,
                    "X-API-Secret": config.facturalo_secret,
                },
            )
            if r.status_code != 200:
                raise HTTPException(502, detail=f"Error obteniendo PDF: {r.status_code}")

            content_type = r.headers.get("content-type", "application/pdf")
            numero_fmt = f"{comp.serie}-{str(comp.numero).zfill(8)}"
            return StreamingResponse(
                iter([r.content]),
                media_type=content_type,
                headers={
                    "Content-Disposition": f'inline; filename="{numero_fmt}.pdf"',
                },
            )
    except httpx.TimeoutException:
        raise HTTPException(504, detail="Timeout obteniendo PDF")
    except httpx.RequestError as e:
        raise HTTPException(502, detail=f"Error de conexión: {str(e)}")


# ============================================================
# RESUMEN Y ÚLTIMOS COBROS
# ============================================================

@router.get("/resumen-dia")
async def resumen_del_dia(db: Session = Depends(get_db)):
    """Resumen de cobros del día para la pantalla de caja."""
    ahora = datetime.now(PERU_TZ)
    inicio_dia = _inicio_dia_peru_utc()

    pagos_dia = db.query(Payment).filter(
        Payment.status.in_(["approved", "anulado"]),
        Payment.created_at >= inicio_dia,
        Payment.notes.like("[CAJA]%"),
    ).all()

    total = sum(float(p.amount or 0) for p in pagos_dia)
    cantidad = len(pagos_dia)

    por_metodo = {}
    for p in pagos_dia:
        metodo = p.payment_method or "efectivo"
        if metodo not in por_metodo:
            por_metodo[metodo] = {"cantidad": 0, "total": 0}
        por_metodo[metodo]["cantidad"] += 1
        por_metodo[metodo]["total"] += float(p.amount or 0)

    return {
        "fecha": ahora.strftime("%d/%m/%Y"),
        "total_cobrado": total,
        "cantidad_operaciones": cantidad,
        "por_metodo": por_metodo,
        "hora_actual": ahora.strftime("%H:%M"),
    }


@router.get("/ultimos-cobros")
async def ultimos_cobros(
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Últimos cobros realizados en caja (para el historial)"""
    ahora = datetime.now(PERU_TZ)
    inicio_dia = _inicio_dia_peru_utc()

    pagos = db.query(Payment).filter(
        Payment.notes.like("[CAJA]%"),
        Payment.created_at >= inicio_dia,
    ).order_by(Payment.created_at.desc()).limit(limit).all()

    resultado = []
    for p in pagos:
        col = None
        if p.colegiado_id:
            col = db.query(Colegiado).filter(Colegiado.id == p.colegiado_id).first()

        resultado.append({
            "id": p.id,
            "hora": p.created_at.strftime("%H:%M") if p.created_at else "",
            "colegiado": col.apellidos_nombres if col else "Público general",
            "matricula": col.codigo_matricula if col else None,
            "concepto": p.notes or "",
            "monto": float(p.amount or 0),
            "metodo": p.payment_method or "efectivo",
            "referencia": p.operation_code,
            "status": p.status,
        })

    return resultado


# ============================================================
# SESIÓN DE CAJA
# ============================================================

class AbrirCajaRequest(BaseModel):
    monto_apertura: float = 0
    centro_costo_id: int = 1

class CerrarCajaRequest(BaseModel):
    monto_cierre: float
    observaciones: Optional[str] = None


@router.post("/abrir-caja")
async def abrir_caja(
    datos: AbrirCajaRequest,
    db: Session = Depends(get_db),
):
    """Abre una sesión de caja. Solo 1 por centro de costo."""
    from app.models import SesionCaja

    ahora = datetime.now(PERU_TZ)

    caja_abierta = db.query(SesionCaja).filter(
        SesionCaja.centro_costo_id == datos.centro_costo_id,
        SesionCaja.estado == "abierta",
    ).first()

    if caja_abierta:
        cajero = db.query(UsuarioAdmin).filter(
            UsuarioAdmin.id == caja_abierta.usuario_admin_id
        ).first()
        raise HTTPException(400, detail={
            "error": f"Ya hay una caja abierta por {cajero.nombre_completo if cajero else 'otro usuario'}",
            "sesion_id": caja_abierta.id,
        })

    centro = db.query(CentroCosto).filter(CentroCosto.id == datos.centro_costo_id).first()
    if not centro:
        raise HTTPException(404, detail="Centro de costo no encontrado")

    org = db.query(Organization).first()

    usuario_admin = db.query(UsuarioAdmin).filter(
        UsuarioAdmin.organization_id == org.id,
        UsuarioAdmin.activo == True,
    ).first()

    sesion = SesionCaja(
        organization_id=org.id,
        centro_costo_id=datos.centro_costo_id,
        usuario_admin_id=usuario_admin.id if usuario_admin else 1,
        fecha=ahora,
        estado="abierta",
        monto_apertura=Decimal(str(datos.monto_apertura)),
        hora_apertura=ahora,
    )

    db.add(sesion)
    db.commit()
    db.refresh(sesion)

    return {
        "success": True,
        "mensaje": f"Caja abierta en {centro.nombre} con S/ {datos.monto_apertura:.2f}",
        "sesion_id": sesion.id,
    }


@router.get("/sesion-actual")
async def sesion_actual(
    centro_costo_id: int = Query(1),
    db: Session = Depends(get_db),
):
    """Retorna la sesión de caja abierta del centro de costo."""
    from app.models import SesionCaja, EgresoCaja

    sesion = db.query(SesionCaja).filter(
        SesionCaja.centro_costo_id == centro_costo_id,
        SesionCaja.estado == "abierta",
    ).first()

    if not sesion:
        return {"sesion": None, "caja_abierta": False}

    pagos = db.query(Payment).filter(
        Payment.status == "approved",
        Payment.notes.like("[CAJA]%"),
        Payment.created_at >= sesion.hora_apertura,
    ).all()

    total_efectivo = sum(float(p.amount or 0) for p in pagos if p.payment_method in ("efectivo",))
    total_digital = sum(float(p.amount or 0) for p in pagos if p.payment_method not in ("efectivo",))
    cantidad = len(pagos)

    total_egresos = float(
        db.query(func.coalesce(func.sum(EgresoCaja.monto), 0)).filter(
            EgresoCaja.sesion_caja_id == sesion.id
        ).scalar() or 0
    )

    monto_apertura = float(sesion.monto_apertura or 0)
    total_esperado = monto_apertura + total_efectivo - total_egresos

    cajero = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == sesion.usuario_admin_id).first()
    centro = db.query(CentroCosto).filter(CentroCosto.id == sesion.centro_costo_id).first()

    return {
        "caja_abierta": True,
        "sesion": {
            "id": sesion.id,
            "estado": sesion.estado,
            "cajero": cajero.nombre_completo if cajero else "?",
            "centro_costo": centro.nombre if centro else "?",
            "fecha": sesion.fecha.strftime("%d/%m/%Y") if sesion.fecha else "",
            "hora_apertura": sesion.hora_apertura.astimezone(PERU_TZ).strftime("%H:%M") if sesion.hora_apertura else "",
            "monto_apertura": monto_apertura,
            "total_cobros_efectivo": total_efectivo,
            "total_cobros_digital": total_digital,
            "total_egresos": total_egresos,
            "cantidad_operaciones": cantidad,
            "total_esperado": total_esperado,
            "total_general": total_efectivo + total_digital,
        }
    }


@router.post("/cerrar-caja/{sesion_id}")
async def cerrar_caja(
    sesion_id: int,
    datos: CerrarCajaRequest,
    db: Session = Depends(get_db),
):
    """Cierra una sesión de caja. El cajero declara cuánto tiene."""
    from app.models import SesionCaja, EgresoCaja

    ahora = datetime.now(PERU_TZ)

    sesion = db.query(SesionCaja).filter(
        SesionCaja.id == sesion_id,
        SesionCaja.estado == "abierta",
    ).first()

    if not sesion:
        raise HTTPException(404, detail="Sesión no encontrada o ya cerrada")

    pagos = db.query(Payment).filter(
        Payment.status == "approved",
        Payment.notes.like("[CAJA]%"),
        Payment.created_at >= sesion.hora_apertura,
    ).all()

    total_efectivo = sum(float(p.amount or 0) for p in pagos if p.payment_method in ("efectivo",))
    total_digital = sum(float(p.amount or 0) for p in pagos if p.payment_method not in ("efectivo",))
    cantidad = len(pagos)

    total_egresos = float(
        db.query(func.coalesce(func.sum(EgresoCaja.monto), 0)).filter(
            EgresoCaja.sesion_caja_id == sesion.id
        ).scalar() or 0
    )

    monto_apertura = float(sesion.monto_apertura or 0)
    total_esperado = monto_apertura + total_efectivo - total_egresos
    diferencia = datos.monto_cierre - total_esperado

    sesion.estado = "cerrada"
    sesion.total_cobros_efectivo = Decimal(str(total_efectivo))
    sesion.total_cobros_digital = Decimal(str(total_digital))
    sesion.total_egresos = Decimal(str(total_egresos))
    sesion.cantidad_operaciones = cantidad
    sesion.total_esperado = Decimal(str(total_esperado))
    sesion.monto_cierre = Decimal(str(datos.monto_cierre))
    sesion.diferencia = Decimal(str(diferencia))
    sesion.hora_cierre = ahora
    sesion.observaciones_cierre = datos.observaciones

    alerta = ""
    if abs(diferencia) > 50:
        if not datos.observaciones:
            raise HTTPException(400,
                detail="Diferencia mayor a S/ 50.00 — se requiere observación obligatoria")
        alerta = f" ⚠ Diferencia: S/ {diferencia:+.2f}"

    db.commit()

    return {
        "success": True,
        "mensaje": f"Caja cerrada.{alerta}",
        "resumen": {
            "monto_apertura": monto_apertura,
            "total_cobros_efectivo": total_efectivo,
            "total_cobros_digital": total_digital,
            "total_egresos": total_egresos,
            "cantidad_operaciones": cantidad,
            "total_esperado": total_esperado,
            "monto_cierre": datos.monto_cierre,
            "diferencia": diferencia,
        }
    }


@router.get("/cierre-caja/{sesion_id}/pdf")
async def pdf_cierre_caja(sesion_id: int, db: Session = Depends(get_db)):
    """Genera y descarga el PDF de cierre de caja."""
    from app.models import SesionCaja, EgresoCaja, Organization, CentroCosto, UsuarioAdmin
    from app.services.pdf_cierre_caja import generar_pdf_cierre

    sesion = db.query(SesionCaja).filter(SesionCaja.id == sesion_id).first()
    if not sesion:
        raise HTTPException(404, detail="Sesión no encontrada")

    if sesion.estado == "abierta":
        raise HTTPException(400, detail="La sesión aún está abierta. Cierre la caja primero.")

    # Datos de contexto
    org = db.query(Organization).filter(Organization.id == sesion.organization_id).first()
    org_nombre = org.name if org else "Organización"

    centro = db.query(CentroCosto).filter(CentroCosto.id == sesion.centro_costo_id).first()
    sede_nombre = centro.nombre if centro else "Sede Principal"

    cajero = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == sesion.usuario_admin_id).first()
    cajero_nombre = cajero.nombre_completo if cajero else "Cajero"

    # Pagos de la sesión (fix timezone: usar hora_apertura y hora_cierre)
    pagos = db.query(Payment).filter(
        Payment.status.in_(["approved", "anulado"]),
        Payment.notes.like("[CAJA]%"),
        Payment.created_at >= sesion.hora_apertura,
    ).order_by(Payment.created_at.asc()).all()

    # Si la sesión está cerrada, filtrar hasta hora de cierre
    if sesion.hora_cierre:
        pagos = [p for p in pagos if p.created_at <= sesion.hora_cierre]

    # Egresos
    egresos = db.query(EgresoCaja).filter(
        EgresoCaja.sesion_caja_id == sesion.id
    ).order_by(EgresoCaja.created_at.asc()).all()

    # Comprobantes emitidos para estos pagos
    payment_ids = [p.id for p in pagos]
    comprobantes = []
    if payment_ids:
        comprobantes = db.query(Comprobante).filter(
            Comprobante.payment_id.in_(payment_ids),
        ).order_by(Comprobante.created_at.asc()).all()

    # Generar PDF
    pdf_bytes = generar_pdf_cierre(
        sesion=sesion,
        org_nombre=org_nombre,
        sede_nombre=sede_nombre,
        cajero_nombre=cajero_nombre,
        pagos=pagos,
        egresos=egresos,
        comprobantes=comprobantes,
    )

    # Nombre del archivo
    fecha_str = "sin-fecha"
    if sesion.hora_apertura:
        from datetime import timezone as tz, timedelta as td
        TZ_PERU = tz(td(hours=-5))
        h = sesion.hora_apertura
        if h.tzinfo is None:
            h = h.replace(tzinfo=tz.utc)
        fecha_str = h.astimezone(TZ_PERU).strftime("%Y%m%d")

    filename = f"cierre_caja_{sesion.id}_{fecha_str}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


# ══════════════════════════════════════════════════════════
# ENDPOINT PARA LISTAR SESIONES CERRADAS (para historial)
# ══════════════════════════════════════════════════════════

@router.get("/sesiones-caja")
async def listar_sesiones(
    estado: Optional[str] = "cerrada",
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Lista sesiones de caja para acceder a reportes de cierre."""
    from app.models import SesionCaja, UsuarioAdmin, CentroCosto

    query = db.query(SesionCaja).filter(SesionCaja.organization_id == 1)

    if estado:
        query = query.filter(SesionCaja.estado == estado)

    sesiones = query.order_by(SesionCaja.fecha.desc()).limit(limit).all()

    resultado = []
    for s in sesiones:
        cajero = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == s.usuario_admin_id).first()
        centro = db.query(CentroCosto).filter(CentroCosto.id == s.centro_costo_id).first()

        from datetime import timezone as tz, timedelta as td
        TZ_PERU = tz(td(hours=-5))

        def _fmt(dt):
            if not dt:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz.utc)
            return dt.astimezone(TZ_PERU).strftime("%d/%m/%Y %H:%M")

        resultado.append({
            "id": s.id,
            "fecha": _fmt(s.fecha),
            "estado": s.estado,
            "cajero": cajero.nombre_completo if cajero else "-",
            "sede": centro.nombre if centro else "-",
            "hora_apertura": _fmt(s.hora_apertura),
            "hora_cierre": _fmt(s.hora_cierre),
            "total_cobros": float((s.total_cobros_efectivo or 0) + (s.total_cobros_digital or 0)),
            "total_egresos": float(s.total_egresos or 0),
            "diferencia": float(s.diferencia or 0),
            "cantidad_operaciones": s.cantidad_operaciones or 0,
        })

    return {"sesiones": resultado}

# ============================================================
# EGRESOS
# ============================================================

@router.post("/egreso")
async def registrar_egreso(
    datos: EgresoRequest,
    centro_costo_id: int = Query(1),
    db: Session = Depends(get_db),
    member = Depends(get_current_member),
):
    """Registra un egreso de caja. Estado inicial: pendiente."""
    from app.models import SesionCaja, EgresoCaja

    sesion = db.query(SesionCaja).filter(
        SesionCaja.centro_costo_id == centro_costo_id,
        SesionCaja.estado == "abierta",
    ).first()

    if not sesion:
        raise HTTPException(400, detail="No hay caja abierta")
    if datos.monto <= 0:
        raise HTTPException(400, detail="Monto debe ser mayor a 0")
    if not datos.responsable or not datos.responsable.strip():
        raise HTTPException(400, detail="Debe indicar el responsable")
    if not datos.concepto or not datos.concepto.strip():
        raise HTTPException(400, detail="Debe indicar el concepto/motivo")
    
    # ── Control de límites ──
    from app.services.limites_operacion import verificar_limite
    limite = verificar_limite(
        db=db,
        org_id=sesion.organization_id,
        operacion='registrar_egreso',
        monto=datos.monto,
        rol=member.role,
    )
    if not limite.permitido:
        raise HTTPException(status_code=403, detail={
            "requiere_aprobacion": True,
            "mensaje": limite.mensaje,
            "aprobador": limite.aprobador,
        })

    org = db.query(Organization).first()

    egreso = EgresoCaja(
        sesion_caja_id=sesion.id,
        organization_id=org.id,
        monto=Decimal(str(datos.monto)),
        concepto=datos.concepto.strip(),
        detalle=datos.detalle,
        tipo=datos.tipo,
        responsable=datos.responsable.strip(),
        estado="pendiente",
    )

    db.add(egreso)
    db.commit()
    db.refresh(egreso)

    return {
        "success": True,
        "mensaje": f"Egreso registrado: S/ {datos.monto:.2f} — {datos.concepto} → {datos.responsable}",
        "egreso_id": egreso.id,
    }


@router.get("/egresos/{sesion_id}")
async def listar_egresos(sesion_id: int, db: Session = Depends(get_db)):
    """Lista egresos de una sesión"""
    from app.models import EgresoCaja

    egresos = db.query(EgresoCaja).filter(
        EgresoCaja.sesion_caja_id == sesion_id
    ).order_by(EgresoCaja.created_at.desc()).all()

    return [{
        "id": e.id,
        "monto": float(e.monto),
        "monto_factura": float(e.monto_factura) if e.monto_factura is not None else None,
        "monto_devuelto": float(e.monto_devuelto or 0),
        "concepto": e.concepto,
        "detalle": e.detalle,
        "responsable": e.responsable or "",
        "tipo": e.tipo,
        "estado": e.estado or "pendiente",
        "numero_documento": e.numero_documento,
        "hora": e.created_at.astimezone(PERU_TZ).strftime("%H:%M") if e.created_at else "",
    } for e in egresos]


@router.get("/historial-sesiones")
async def historial_sesiones(
    centro_costo_id: Optional[int] = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Historial de sesiones de caja."""
    from app.models import SesionCaja

    query = db.query(SesionCaja)
    if centro_costo_id:
        query = query.filter(SesionCaja.centro_costo_id == centro_costo_id)

    sesiones = query.order_by(SesionCaja.fecha.desc()).limit(limit).all()

    resultado = []
    for s in sesiones:
        cajero = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == s.usuario_admin_id).first()
        centro = db.query(CentroCosto).filter(CentroCosto.id == s.centro_costo_id).first()

        resultado.append({
            "id": s.id,
            "fecha": s.fecha.strftime("%d/%m/%Y") if s.fecha else "",
            "centro_costo": centro.nombre if centro else "?",
            "cajero": cajero.nombre_completo if cajero else "?",
            "estado": s.estado,
            "monto_apertura": float(s.monto_apertura or 0),
            "total_cobros": float(s.total_cobros_efectivo or 0) + float(s.total_cobros_digital or 0),
            "total_egresos": float(s.total_egresos or 0),
            "total_esperado": float(s.total_esperado or 0),
            "monto_cierre": float(s.monto_cierre) if s.monto_cierre is not None else None,
            "diferencia": float(s.diferencia) if s.diferencia is not None else None,
            "cantidad_operaciones": s.cantidad_operaciones or 0,
            "hora_apertura": s.hora_apertura.strftime("%H:%M") if s.hora_apertura else "",
            "hora_cierre": s.hora_cierre.strftime("%H:%M") if s.hora_cierre else "",
        })

    return resultado


@router.post("/egreso/{egreso_id}/liquidar")
async def liquidar_egreso(
    egreso_id: int,
    datos: LiquidarEgresoRequest,
    db: Session = Depends(get_db),
):
    """Liquida un egreso: factura recibida + vuelto."""
    from app.models import EgresoCaja

    ahora = datetime.now(PERU_TZ)

    egreso = db.query(EgresoCaja).filter(
        EgresoCaja.id == egreso_id,
        EgresoCaja.estado == "pendiente",
    ).first()

    if not egreso:
        raise HTTPException(404, detail="Egreso no encontrado o ya liquidado")

    monto_entregado = float(egreso.monto)

    if datos.monto_factura < 0:
        raise HTTPException(400, detail="Monto de factura inválido")
    if datos.monto_factura > monto_entregado:
        raise HTTPException(400,
            detail=f"Factura (S/ {datos.monto_factura:.2f}) mayor al monto entregado (S/ {monto_entregado:.2f})")

    monto_devuelto = monto_entregado - datos.monto_factura

    egreso.monto_factura = Decimal(str(datos.monto_factura))
    egreso.monto_devuelto = Decimal(str(monto_devuelto))
    egreso.estado = "liquidado"
    egreso.liquidado_at = ahora
    egreso.numero_documento = datos.numero_documento
    if datos.observaciones:
        egreso.detalle = (egreso.detalle or "") + f"\n[Liquidación] {datos.observaciones}"

    db.commit()

    msg = f"Egreso liquidado. Factura: S/ {datos.monto_factura:.2f}"
    if monto_devuelto > 0:
        msg += f" — Vuelto: S/ {monto_devuelto:.2f} regresa a caja"

    return {
        "success": True,
        "mensaje": msg,
        "monto_factura": datos.monto_factura,
        "monto_devuelto": monto_devuelto,
    }


@router.get("/egresos-actual")
async def egresos_sesion_actual(
    centro_costo_id: int = Query(1),
    db: Session = Depends(get_db),
):
    """Egresos de la sesión de caja actual (abierta)"""
    from app.models import SesionCaja, EgresoCaja

    sesion = db.query(SesionCaja).filter(
        SesionCaja.centro_costo_id == centro_costo_id,
        SesionCaja.estado == "abierta",
    ).first()

    if not sesion:
        return {"egresos": [], "totales": {"entregado": 0, "facturado": 0, "devuelto": 0, "pendientes": 0}}

    egresos = db.query(EgresoCaja).filter(
        EgresoCaja.sesion_caja_id == sesion.id
    ).order_by(EgresoCaja.created_at.desc()).all()

    total_entregado = sum(float(e.monto or 0) for e in egresos)
    total_facturado = sum(float(e.monto_factura or 0) for e in egresos if e.estado == "liquidado")
    total_devuelto = sum(float(e.monto_devuelto or 0) for e in egresos if e.estado == "liquidado")
    pendientes = sum(1 for e in egresos if e.estado == "pendiente")

    return {
        "sesion_id": sesion.id,
        "egresos": [{
            "id": e.id,
            "monto": float(e.monto),
            "monto_factura": float(e.monto_factura) if e.monto_factura is not None else None,
            "monto_devuelto": float(e.monto_devuelto or 0),
            "concepto": e.concepto,
            "responsable": e.responsable or "",
            "tipo": e.tipo,
            "estado": e.estado or "pendiente",
            "numero_documento": e.numero_documento,
            "hora": e.created_at.astimezone(PERU_TZ).strftime("%H:%M") if e.created_at else "",
        } for e in egresos],
        "totales": {
            "entregado": total_entregado,
            "facturado": total_facturado,
            "devuelto": total_devuelto,
            "neto": total_entregado - total_devuelto,
            "pendientes": pendientes,
        }
    }


# ============================================================
# COMPROBANTES Y ANULACIÓN
# ============================================================

@router.get("/historial-cobros")
async def historial_cobros(
    fecha: str,
    metodo_pago: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Historial de cobros de caja por fecha."""
    try:
        dia = datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(400, detail="Formato de fecha inválido. Use YYYY-MM-DD")

    # Medianoche Perú (00:00 UTC-5) = 05:00 UTC
    # Railway guarda created_at/reviewed_at en UTC
    dia_inicio = dia.replace(hour=5, minute=0, second=0, microsecond=0)
    dia_fin = dia_inicio + timedelta(days=1)

    query = db.query(Payment).filter(
        Payment.status.in_(["approved", "anulado"]),
        Payment.reviewed_at >= dia_inicio,
        Payment.reviewed_at < dia_fin,
        Payment.notes.ilike("%[CAJA]%"),
    )

    if metodo_pago:
        query = query.filter(Payment.payment_method == metodo_pago)

    cobros = query.order_by(Payment.reviewed_at.desc()).limit(200).all()

    operaciones = []
    for p in cobros:
        numero_comprobante = None
        try:
            comp = db.query(Comprobante).filter(
                Comprobante.payment_id == p.id,
                Comprobante.tipo.in_(["01", "03"]),
            ).order_by(Comprobante.created_at.desc()).first()
            if comp:
                numero_comprobante = f"{comp.serie}-{str(comp.numero).zfill(8)}"
        except Exception:
            pass

        # Convertir hora a Perú para mostrar
        hora_peru = p.reviewed_at.replace(tzinfo=timezone.utc).astimezone(PERU_TZ) if p.reviewed_at else None

        operaciones.append({
            "id": p.id,
            "amount": float(p.amount or 0),
            "metodo_pago": p.payment_method,
            "notes": p.notes,
            "reviewed_at": hora_peru.isoformat() if hora_peru else None,
            "numero_comprobante": numero_comprobante,
            "status": p.status,
        })

    return {"operaciones": operaciones}


# ══════════════════════════════════════════════════════════
# REEMPLAZAR en caja.py — el endpoint anular_cobro completo
# ══════════════════════════════════════════════════════════

@router.post("/anular-cobro")
async def anular_cobro(
    request: Request,
    db: Session = Depends(get_db),
    member = Depends(get_current_member),
):
    """
    Anula un cobro:
    1. Emite Nota de Crédito ante SUNAT (si hay comprobante)
    2. Marca payment como anulado
    3. Revierte deudas a pendiente
    4. Revierte stock si aplica
    """
    data = await request.json()
    payment_id = data.get("payment_id")
    motivo_codigo = data.get("motivo_codigo", "01")
    motivo_texto = data.get("motivo_texto", "Anulación de la operación")
    monto = data.get("monto")  # None = total, float = parcial
    observaciones = data.get("observaciones", "")

    # ── Validaciones ──
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        raise HTTPException(404, detail="Pago no encontrado")
    if payment.status == "anulado":
        raise HTTPException(400, detail="Este cobro ya fue anulado")

    monto_anular = float(monto) if monto is not None else float(payment.amount)
    es_parcial = abs(monto_anular - float(payment.amount)) > 0.01

    # ── Control de límites ──
    from app.services.limites_operacion import verificar_limite
    limite = verificar_limite(
        db=db,
        org_id=payment.organization_id,
        operacion='anular_cobro',
        monto=monto_anular,
        rol=member.role,
    )
    if not limite.permitido:
        # Guardar solicitud pendiente para aprobación
        db.execute(text("""
            INSERT INTO operaciones_pendientes
                (organization_id, operacion, monto, solicitado_por,
                 aprobador_requerido, datos_json, estado, created_at)
            VALUES
                (:org, 'anular_cobro', :monto, :solicitado_por,
                 :aprobador, :datos, 'pendiente', NOW())
        """), {
            'org': payment.organization_id,
            'monto': monto_anular,
            'solicitado_por': member.id,
            'aprobador': limite.aprobador,
            'datos': json.dumps({
                'payment_id': payment_id,
                'motivo_codigo': motivo_codigo,
                'motivo_texto': motivo_texto,
                'observaciones': observaciones,
            }),
        })
        db.commit()
        raise HTTPException(status_code=403, detail={
            "requiere_aprobacion": True,
            "mensaje": limite.mensaje,
            "aprobador": limite.aprobador,
        })

    # ── Emitir Nota de Crédito si hay comprobante ──
    nota_credito_info = None
    nc_pdf_url = None
    comp = db.query(Comprobante).filter(
        Comprobante.payment_id == payment_id,
        Comprobante.status == "accepted",
        Comprobante.tipo.in_(["01", "03"]),  # Solo boletas/facturas, no NC sobre NC
    ).first()

    if comp:
        try:
            facturacion = FacturacionService(db, payment.organization_id)

            if facturacion.esta_configurado():
                resultado_nc = await facturacion.emitir_nota_credito(
                    comprobante_original_id=comp.id,
                    motivo_codigo=motivo_codigo,
                    motivo_texto=motivo_texto,
                    monto=monto_anular,
                )

                if resultado_nc["success"]:
                    nota_credito_info = resultado_nc["numero_formato"]
                    nc_pdf_url = resultado_nc.get("pdf_url")
                    logger.info(f"NC emitida: {nota_credito_info} para pago #{payment_id}")
                else:
                    # NC falló pero continuamos con la anulación local
                    logger.error(f"NC falló para pago #{payment_id}: {resultado_nc.get('error')}")
                    nota_credito_info = f"Error NC: {resultado_nc.get('error', 'desconocido')}"
            else:
                nota_credito_info = "Facturación no configurada, comprobante anulado solo localmente"

            # Marcar comprobante original como anulado
            comp.status = "anulado"
            comp.observaciones = (comp.observaciones or "") + f"\n[ANULADO] {motivo_texto}"

        except Exception as e:
            logger.error(f"Error emitiendo NC para pago #{payment_id}: {e}", exc_info=True)
            nota_credito_info = f"Error NC: {str(e)}"

    # ── Revertir deudas ──
    deudas_revertidas = 0
    if payment.colegiado_id:
        if es_parcial:
            # Parcial: revertir solo las deudas que correspondan al monto
            notas = payment.notes or ""
            deudas = db.query(Debt).filter(
                Debt.colegiado_id == payment.colegiado_id,
                Debt.status == "paid",
            ).order_by(Debt.periodo.desc()).all()

            monto_pendiente = monto_anular
            for deuda in deudas:
                if monto_pendiente <= 0:
                    break
                if deuda.concept and deuda.concept in notas:
                    deuda.status = "pending"
                    deuda.balance = deuda.amount
                    monto_pendiente -= float(deuda.amount)
                    deudas_revertidas += 1
        else:
            # Total: revertir todas las deudas del pago
            notas = payment.notes or ""
            deudas = db.query(Debt).filter(
                Debt.colegiado_id == payment.colegiado_id,
                Debt.status == "paid",
            ).all()

            for deuda in deudas:
                if deuda.concept and deuda.concept in notas:
                    deuda.status = "pending"
                    deuda.balance = deuda.amount
                    deudas_revertidas += 1

    # ── Revertir stock ──
    if not es_parcial:
        try:
            for item_note in (payment.notes or "").split(";"):
                item_note = item_note.strip()
                if not item_note:
                    continue
                concepto = db.query(ConceptoCobro).filter(
                    ConceptoCobro.nombre.ilike(f"%{item_note[:30]}%"),
                    ConceptoCobro.maneja_stock == True,
                ).first()
                if concepto:
                    concepto.stock_actual += 1
        except Exception:
            pass

    # ── Marcar anulado (solo si NC fue exitosa o no había comprobante) ──
    nc_exitosa = nota_credito_info and "Error" not in str(nota_credito_info)
    sin_comprobante = comp is None

    if nc_exitosa or sin_comprobante:
        motivo_full = motivo_texto
        if observaciones:
            motivo_full += f". {observaciones}"
        if nc_exitosa:
            motivo_full += f" [NC: {nota_credito_info}]"

        if es_parcial:
            payment.notes = (payment.notes or "") + f"\n[NC PARCIAL S/{monto_anular:.2f}] {motivo_full}"
        else:
            payment.status = "anulado"
            payment.notes = (payment.notes or "") + f"\n[ANULADO] {motivo_full}"

        db.commit()

        return {
            "success": True,
            "mensaje": f"{'Anulación parcial' if es_parcial else 'Cobro anulado'}. {deudas_revertidas} deuda(s) revertida(s).",
            "nota_credito": nota_credito_info,
            "nc_pdf_url": nc_pdf_url,
            "monto_anulado": monto_anular,
            "es_parcial": es_parcial,
        }
    else:
        # NC falló — NO tocar el pago, devolver error
        db.rollback()
        return {
            "success": False,
            "detail": f"No se pudo emitir la Nota de Crédito: {nota_credito_info}",
        }


@router.get("/comprobante/{payment_id}")
async def ver_comprobante(payment_id: int, db: Session = Depends(get_db)):
    """Detalle de comprobante(s) asociados a un pago."""
    comps = db.query(Comprobante).filter(
        Comprobante.payment_id == payment_id,
    ).order_by(Comprobante.created_at.asc()).all()

    if not comps:
        raise HTTPException(404, detail="No hay comprobantes para este pago")

    return {
        "comprobantes": [
            {
                "id": c.id,
                "tipo": c.tipo,
                "tipo_nombre": {"01": "Factura", "03": "Boleta", "07": "Nota de Crédito", "08": "Nota de Débito"}.get(c.tipo, c.tipo),
                "serie": c.serie,
                "numero": c.numero,
                "numero_formato": f"{c.serie}-{str(c.numero).zfill(8)}",
                "fecha": c.created_at.replace(tzinfo=timezone.utc).astimezone(PERU_TZ).strftime("%d/%m/%Y %H:%M") if c.created_at else "",
                "cliente_nombre": c.cliente_nombre,
                "cliente_doc": c.cliente_num_doc,
                "total": float(c.total or 0),
                "status": c.status,
                "pdf_url": c.pdf_url,
                "sunat_response": c.sunat_response_description,
                "comprobante_ref_id": c.comprobante_ref_id,
                "observaciones": c.observaciones,
            }
            for c in comps
        ]
    }


@router.get("/comprobantes")
async def listar_comps(
    buscar: Optional[str] = None,
    tipo: Optional[str] = None,
    estado: Optional[str] = None,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Lista todos los comprobantes con filtros.
    Busca por: número comprobante, DNI/RUC, nombre cliente.
    """
    query = db.query(Comprobante).filter(
        Comprobante.organization_id == 1,
    )

    # Filtro por tipo
    if tipo:
        query = query.filter(Comprobante.tipo == tipo)

    # Filtro por estado
    if estado:
        query = query.filter(Comprobante.status == estado)

    # Filtro por fechas (convertir a UTC: medianoche Perú = 05:00 UTC)
    if fecha_desde:
        try:
            fd = datetime.strptime(fecha_desde, "%Y-%m-%d")
            query = query.filter(Comprobante.created_at >= fd.replace(hour=5))
        except ValueError:
            pass

    if fecha_hasta:
        try:
            fh = datetime.strptime(fecha_hasta, "%Y-%m-%d")
            query = query.filter(Comprobante.created_at < (fh + timedelta(days=1)).replace(hour=5))
        except ValueError:
            pass

    # Búsqueda por texto
    if buscar:
        buscar = buscar.strip()
        query = query.filter(
            (Comprobante.cliente_num_doc.ilike(f"%{buscar}%")) |
            (Comprobante.cliente_nombre.ilike(f"%{buscar}%")) |
            (Comprobante.serie.ilike(f"%{buscar}%")) |
            (Comprobante.observaciones.ilike(f"%{buscar}%"))
        )

    total = query.count()
    comprobantes = query.order_by(
        Comprobante.created_at.desc()
    ).offset((page - 1) * limit).limit(limit).all()

    return {
        "comprobantes": [
            {
                "id": c.id,
                "tipo": c.tipo,
                "serie": c.serie,
                "numero": c.numero,
                "numero_formato": f"{c.serie}-{str(c.numero).zfill(8)}",
                "fecha": c.created_at.replace(tzinfo=timezone.utc).astimezone(PERU_TZ).strftime("%d/%m/%Y %H:%M") if c.created_at else "",
                "cliente_nombre": c.cliente_nombre,
                "cliente_doc": c.cliente_num_doc,
                "total": float(c.total or 0),
                "status": c.status,
                "payment_id": c.payment_id,
                "pdf_url": c.pdf_url,
                "comprobante_ref_id": c.comprobante_ref_id,
                "observaciones": c.observaciones,
                "sunat_response_description": c.sunat_response_description or "",
            }
            for c in comprobantes
        ],
        "total": total,
        "page": page,
        "pages": (total + limit - 1) // limit,
    }


@router.get("/comprobantes/{comprobante_id}/estado")
async def estado_comprobante(
    comprobante_id: int,
    db: Session = Depends(get_db),
):
    """Devuelve estado actualizado de un comprobante."""
    comp = db.query(Comprobante).filter(
        Comprobante.id == comprobante_id,
        Comprobante.organization_id == 1,
    ).first()
    if not comp:
        raise HTTPException(404, detail="Comprobante no encontrado")
    return {
        "id": comp.id,
        "status": comp.status,
        "sunat_response_description": comp.sunat_response_description or "",
        "cdr_url": comp.cdr_url or "",
    }


@router.post("/comprobantes/{comprobante_id}/reenviar")
async def reenviar_comprobante(
    comprobante_id: int,
    db: Session = Depends(get_db),
):
    """Reenvía a SUNAT (vía facturalo.pro) un comprobante en estado pending o rejected."""
    comp = db.query(Comprobante).filter(
        Comprobante.id == comprobante_id,
        Comprobante.organization_id == 1,
    ).first()
    if not comp:
        raise HTTPException(404, detail="Comprobante no encontrado")
    if comp.status not in ("pending", "rejected"):
        raise HTTPException(
            400,
            detail=f"No se puede reenviar: estado actual '{comp.status}'",
        )

    service = FacturacionService(db, comp.organization_id)
    if not service.esta_configurado():
        raise HTTPException(400, detail="Facturación no configurada")

    # Datos contextuales para el PDF
    colegiado = None
    payment = db.query(Payment).filter(Payment.id == comp.payment_id).first()
    if payment and payment.colegiado_id:
        colegiado = db.query(Colegiado).filter(
            Colegiado.id == payment.colegiado_id
        ).first()

    matricula = colegiado.codigo_matricula if colegiado else None
    estado_colegiado = None
    habil_hasta = None
    if colegiado:
        estado_colegiado = "HÁBIL" if getattr(colegiado, 'habilitado', False) else "INHÁBIL"
        habil_hasta = service._calcular_vigencia(colegiado.id)

    org = db.query(Organization).filter(Organization.id == comp.organization_id).first()
    url_consulta = None
    if org:
        slug = getattr(org, 'slug', None) or getattr(org, 'domain', None)
        if slug:
            url_consulta = f"{slug}/consulta/habilidad"

    # Marcar como pending antes de reintentar
    comp.status = "pending"
    comp.sunat_response_description = "Reenviado manualmente — en cola"
    db.commit()

    try:
        resultado = await service._enviar_a_facturalo(
            comp,
            codigo_matricula=matricula,
            estado_colegiado=estado_colegiado,
            habil_hasta=habil_hasta,
            url_consulta=url_consulta,
            forma_pago="contado",
        )
    except Exception as e:
        comp.status = "rejected"
        comp.sunat_response_description = f"Error reenviando: {str(e)[:200]}"
        db.commit()
        raise HTTPException(500, detail=f"Error reenviando: {str(e)}")

    if resultado.get("success"):
        comp.status = "accepted"
        comp.facturalo_id = resultado.get("facturalo_id") or comp.facturalo_id
        comp.facturalo_response = resultado.get("response")
        comp.sunat_response_code = resultado.get("sunat_code", "0")
        comp.sunat_response_description = resultado.get("sunat_description")
        comp.sunat_hash = resultado.get("hash") or comp.sunat_hash
        comp.pdf_url = resultado.get("pdf_url") or comp.pdf_url
        comp.xml_url = resultado.get("xml_url") or comp.xml_url
        comp.cdr_url = resultado.get("cdr_url") or comp.cdr_url
    else:
        comp.status = "rejected"
        comp.facturalo_response = resultado.get("response")
        comp.sunat_response_description = resultado.get("error") or "Rechazado"
        comp.observaciones = resultado.get("error")

    db.commit()

    return {
        "ok": resultado.get("success", False),
        "status": comp.status,
        "sunat_response_description": comp.sunat_response_description or "",
        "mensaje": "Comprobante reenviado" if resultado.get("success") else (resultado.get("error") or "Rechazado"),
    }


@router.get("/consulta-ruc/{ruc}")
async def consulta_ruc(ruc: str):
    """Proxy a facturalo.pro o API SUNAT para consultar RUC"""
    # Puedes usar la API de facturalo o apis.net.pe
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.apis.net.pe/v2/sunat/ruc?numero={ruc}")
        if r.status_code == 200:
            d = r.json()
            return {"razon_social": d.get("nombre"), "direccion": d.get("direccion")}
    return {"razon_social": None}



# ══════════════════════════════════════════════════════════════════════════════
# AGREGAR AL FINAL DE app/routers/caja.py
# Panel de corrección de datos — solo para registros antes del 01/04/2026
# ══════════════════════════════════════════════════════════════════════════════

from datetime import datetime as _dt

FECHA_CORTE_CORRECCION = _dt(2026, 4, 1)  # Solo datos anteriores a esta fecha


@router.get("/correccion/casos")
async def correccion_listar_casos(
    tipo: str = "todos",   # inhabil_sin_deuda | habil_con_deuda_alta | mixto | todos
    page: int = 1,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """
    Lista casos sospechosos para revisión de Caja.
    Solo muestra registros creados antes del 01/04/2026.
    """
    from sqlalchemy import text as _text
    org_id = member.organization_id
    limit  = 30
    offset = (page - 1) * limit

    casos = []

    if tipo in ("inhabil_sin_deuda", "todos"):
        rows = db.execute(_text("""
            SELECT
                c.id, c.codigo_matricula, c.apellidos_nombres,
                c.condicion, c.motivo_inhabilidad,
                0::numeric as deuda_total,
                0 as num_deudas,
                'inhabil_sin_deuda' as tipo_caso,
                'Inhábil sin deudas registradas' as descripcion_caso
            FROM colegiados c
            WHERE c.organization_id = :org
              AND c.condicion = 'inhabil'
              AND NOT EXISTS (
                  SELECT 1 FROM debts d
                  WHERE d.colegiado_id = c.id
                    AND d.status IN ('pending','partial')
                    AND d.estado_gestion IN ('vigente','en_cobranza')
              )
            ORDER BY c.codigo_matricula
            LIMIT :lim OFFSET :off
        """), {"org": org_id, "lim": limit, "off": offset}).fetchall()
        casos += [dict(r._mapping) for r in rows]

    if tipo in ("habil_con_deuda_alta", "todos"):
        rows = db.execute(_text("""
            SELECT
                c.id, c.codigo_matricula, c.apellidos_nombres,
                c.condicion, c.motivo_inhabilidad,
                COALESCE(SUM(d.balance),0) as deuda_total,
                COUNT(d.id) as num_deudas,
                'habil_con_deuda_alta' as tipo_caso,
                'Hábil con deuda > S/250 sin fraccionamiento activo' as descripcion_caso
            FROM colegiados c
            JOIN debts d ON d.colegiado_id = c.id
                AND d.status IN ('pending','partial')
                AND d.estado_gestion IN ('vigente','en_cobranza')
            WHERE c.organization_id = :org
              AND c.condicion = 'habil'
              AND NOT EXISTS (
                  SELECT 1 FROM fraccionamientos f
                  WHERE f.colegiado_id = c.id AND f.estado = 'activo'
              )
            GROUP BY c.id, c.codigo_matricula, c.apellidos_nombres,
                     c.condicion, c.motivo_inhabilidad
            HAVING SUM(d.balance) > 250
            ORDER BY deuda_total DESC
            LIMIT :lim OFFSET :off
        """), {"org": org_id, "lim": limit, "off": offset}).fetchall()
        casos += [dict(r._mapping) for r in rows]

    if tipo in ("mixto", "todos"):
        rows = db.execute(_text("""
            SELECT
                c.id, c.codigo_matricula, c.apellidos_nombres,
                c.condicion, c.motivo_inhabilidad,
                0::numeric as deuda_total, 0 as num_deudas,
                'mixto' as tipo_caso,
                'Tiene deudas condonadas Y vigentes' as descripcion_caso
            FROM colegiados c
            WHERE c.organization_id = :org
              AND EXISTS (
                  SELECT 1 FROM debts d WHERE d.colegiado_id = c.id
                  AND d.estado_gestion = 'condonada'
                  AND d.created_at < :corte
              )
              AND EXISTS (
                  SELECT 1 FROM debts d WHERE d.colegiado_id = c.id
                  AND d.estado_gestion = 'vigente'
                  AND d.status IN ('pending','partial')
                  AND d.created_at < :corte
              )
            ORDER BY c.codigo_matricula
            LIMIT :lim OFFSET :off
        """), {"org": org_id, "lim": limit, "off": offset,
               "corte": FECHA_CORTE_CORRECCION}).fetchall()
        casos += [dict(r._mapping) for r in rows]

    # Totales para paginación
    total = db.execute(_text("""
        SELECT
          (SELECT COUNT(*) FROM colegiados c
           WHERE c.organization_id = :org AND c.condicion='inhabil'
           AND NOT EXISTS (SELECT 1 FROM debts d WHERE d.colegiado_id=c.id
               AND d.status IN ('pending','partial')
               AND d.estado_gestion IN ('vigente','en_cobranza'))) as inhabil_sin_deuda,
          (SELECT COUNT(*) FROM (
              SELECT c.id FROM colegiados c
              JOIN debts d ON d.colegiado_id=c.id
                  AND d.status IN ('pending','partial')
                  AND d.estado_gestion IN ('vigente','en_cobranza')
              WHERE c.organization_id=:org AND c.condicion='habil'
              AND NOT EXISTS (SELECT 1 FROM fraccionamientos f
                  WHERE f.colegiado_id=c.id AND f.estado='activo')
              GROUP BY c.id HAVING SUM(d.balance)>500) x) as habil_con_deuda_alta,
          (SELECT COUNT(*) FROM colegiados c
           WHERE c.organization_id=:org
           AND EXISTS (SELECT 1 FROM debts d WHERE d.colegiado_id=c.id
               AND d.estado_gestion='condonada' AND d.created_at < :corte)
           AND EXISTS (SELECT 1 FROM debts d WHERE d.colegiado_id=c.id
               AND d.estado_gestion='vigente' AND d.status IN ('pending','partial')
               AND d.created_at < :corte)) as mixto
    """), {"org": org_id, "corte": FECHA_CORTE_CORRECCION}).fetchone()

    import decimal as _decimal, json as _json
    from fastapi.responses import Response as _Response

    def _serial(obj):
        if isinstance(obj, _decimal.Decimal): return float(obj)
        if hasattr(obj, 'isoformat'): return obj.isoformat()
        raise TypeError(f"No serializable: {type(obj)}")

    return _Response(
        content=_json.dumps({
            "casos":       casos,
            "totales":     dict(total._mapping) if total else {},
            "page":        page,
            "limit":       limit,
            "fecha_corte": "2026-03-31",
        }, default=_serial),
        media_type="application/json"
    )


@router.get("/correccion/deudas/{colegiado_id}")
async def correccion_deudas_colegiado(
    colegiado_id: int,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Deudas corregibles de un colegiado (solo antes del 01/04/2026)."""
    from app.models_debt_management import Debt as _Debt
    deudas = db.query(_Debt).filter(
        _Debt.colegiado_id    == colegiado_id,
        _Debt.organization_id == member.organization_id,
        _Debt.created_at      < FECHA_CORTE_CORRECCION,
        _Debt.status.in_(["pending", "partial"]),
    ).order_by(_Debt.periodo).all()

    return JSONResponse([{
        "id":              d.id,
        "concept":         d.concept,
        "periodo":         d.periodo or "",
        "period_label":    d.period_label or d.concept,
        "debt_type":       d.debt_type,
        "amount":          float(d.amount),
        "balance":         float(d.balance),
        "status":          d.status,
        "estado_gestion":  d.estado_gestion,
        "lote_migracion":  d.lote_migracion or "",
        "created_at":      d.created_at.strftime("%Y-%m-%d") if d.created_at else "",
    } for d in deudas])


@router.post("/correccion/aplicar")
async def correccion_aplicar(
    request: Request,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """
    Aplica correcciones a un colegiado.
    Registra en log de auditoría.
    Body JSON:
    {
      "colegiado_id": 123,
      "condicion": "habil",          // opcional
      "motivo_nota": "Revisión caja — estaba al día según Excel",
      "deudas": [
        {"id": 456, "estado_gestion": "condonada"},
        {"id": 789, "estado_gestion": "vigente"}
      ]
    }
    """
    from sqlalchemy import text as _text
    from app.models_debt_management import Debt as _Debt
    from app.models import Colegiado as _Col

    data = await request.json()
    col_id   = data.get("colegiado_id")
    condicion = data.get("condicion")
    motivo   = (data.get("motivo_nota") or "").strip()
    deudas   = data.get("deudas", [])

    if not col_id:
        return JSONResponse({"error": "colegiado_id requerido"}, status_code=400)
    if not motivo:
        return JSONResponse({"error": "motivo_nota obligatorio"}, status_code=400)

    col = db.query(_Col).filter(
        _Col.id == col_id,
        _Col.organization_id == member.organization_id
    ).first()
    if not col:
        return JSONResponse({"error": "Colegiado no encontrado"}, status_code=404)

    cambios = []

    # Cambio de condición
    if condicion and condicion != col.condicion:
        cambios.append(f"condicion: {col.condicion} → {condicion}")
        col.condicion = condicion
        if condicion == "habil":
            col.motivo_inhabilidad = None
            from datetime import date as _date
            col.habilidad_vence = _date(2026, 12, 31)

    # Cambios en deudas
    for dc in deudas:
        debt_id = dc.get("id")
        nuevo_eg = dc.get("estado_gestion")
        if not debt_id or not nuevo_eg:
            continue
        d = db.query(_Debt).filter(
            _Debt.id == debt_id,
            _Debt.colegiado_id == col_id,
            _Debt.created_at < FECHA_CORTE_CORRECCION,
        ).first()
        if not d:
            continue
        if d.estado_gestion != nuevo_eg:
            cambios.append(f"deuda#{debt_id} ({d.period_label or d.concept}): "
                           f"{d.estado_gestion} → {nuevo_eg}")
            d.estado_gestion = nuevo_eg

    if not cambios:
        return JSONResponse({"ok": True, "mensaje": "Sin cambios que aplicar"})

    # Log de auditoría
    db.execute(_text("""
        INSERT INTO caja_correccion_log
            (organization_id, colegiado_id, member_id, motivo, cambios, created_at)
        VALUES
            (:org, :col, :mem, :motivo, :cambios, NOW())
    """), {
        "org":    member.organization_id,
        "col":    col_id,
        "mem":    member.id,
        "motivo": motivo,
        "cambios": "\n".join(cambios),
    })

    db.commit()

    return JSONResponse({
        "ok":      True,
        "mensaje": f"{len(cambios)} cambio(s) aplicado(s)",
        "cambios": cambios,
    })


@router.get("/correccion/log")
async def correccion_log(
    page: int = 1,
    member: Member = Depends(get_current_member),
    db: Session = Depends(get_db),
):
    """Historial de correcciones realizadas."""
    from sqlalchemy import text as _text
    limit  = 50
    offset = (page - 1) * limit
    rows = db.execute(_text("""
        SELECT
            l.id, l.created_at,
            c.codigo_matricula, c.apellidos_nombres,
            m.role as operador_rol,
            u.name as operador_nombre,
            l.motivo, l.cambios
        FROM caja_correccion_log l
        JOIN colegiados c  ON c.id = l.colegiado_id
        JOIN members m     ON m.id = l.member_id
        JOIN users u       ON u.id = m.user_id
        WHERE l.organization_id = :org
        ORDER BY l.created_at DESC
        LIMIT :lim OFFSET :off
    """), {"org": member.organization_id, "lim": limit, "off": offset}).fetchall()

    return JSONResponse([dict(r._mapping) for r in rows])
