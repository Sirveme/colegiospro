"""
Servicio: Facturación Electrónica
app/services/facturacion.py

Integración con facturalo.pro para emisión de comprobantes

v6 - Cambios:
- FIX: Agregar fecha_emision y hora_emision al payload de facturalo.pro
- Resolución de series por sede (FACTURALO_SERIES env var)
- Soporte para múltiples sedes/puntos de emisión
- Serie se resuelve por sede_id + tipo_comprobante
- Mantiene compatibilidad con ConfiguracionFacturacion (fallback)
- Descripción de items en 3 líneas:
  L1: CUOTA ORDINARIA ENERO, FEBRERO 2026 (2 MESES)
  L2: RESTUCCIA ESLAVA, DUILIO CESAR
  L3: DNI [05393776] Cód. Matr. [10-2244]
- Pasa estado_colegiado, habil_hasta, url_consulta a facturalo
"""

import httpx
import os
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models import (
    Comprobante,
    ConfiguracionFacturacion,
    Payment,
    Colegiado,
    Organization
)
from app.models_debt_management import Debt

logger = logging.getLogger(__name__)

# Timezone Perú (UTC-5)
TZ_PERU = timezone(timedelta(hours=-5))


# ═══════════════════════════════════════════════════════════════
# RESOLUCIÓN DE SERIES POR SEDE
# ═══════════════════════════════════════════════════════════════

_SERIES_CACHE = None

def _cargar_series():
    """
    Carga series desde variable de entorno FACTURALO_SERIES (JSON).
    Formato: {"1":{"nombre":"Of. Principal","boleta":"B001","factura":"F001",
              "nc_boleta":"BC01","nc_factura":"FC01"}, "2":{...}}
    """
    global _SERIES_CACHE
    if _SERIES_CACHE is None:
        raw = os.getenv("FACTURALO_SERIES", "")
        if raw:
            try:
                _SERIES_CACHE = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("FACTURALO_SERIES tiene JSON inválido, usando defaults")
                _SERIES_CACHE = {}
        else:
            _SERIES_CACHE = {}
    return _SERIES_CACHE


def obtener_serie(tipo_comprobante: str, sede_id: str = "1",
                  tipo_original: str = None,
                  config: ConfiguracionFacturacion = None) -> str:
    """
    Resuelve la serie correcta según tipo de comprobante y sede.

    Args:
        tipo_comprobante: '01'=Factura, '03'=Boleta, '07'=NC, '08'=ND
        sede_id: ID de la sede/centro de costo ('1', '2', etc.)
        tipo_original: Para NC/ND — tipo del comprobante original ('01' o '03')
        config: ConfiguracionFacturacion como fallback si no hay env var

    Returns:
        Serie como string, ej: 'B001', 'F002', 'BC01'
    """
    series = _cargar_series()
    sede = series.get(str(sede_id), series.get("1", {}))

    # Si hay series configuradas en env var, usarlas
    if sede:
        if tipo_comprobante == "03":
            return sede.get("boleta", "B001")
        elif tipo_comprobante == "01":
            return sede.get("factura", "F001")
        elif tipo_comprobante == "07":
            if tipo_original == "01":
                return sede.get("nc_factura", "FC01")
            else:
                return sede.get("nc_boleta", "BC01")
        elif tipo_comprobante == "08":
            if tipo_original == "01":
                return sede.get("nc_factura", "FC01")
            else:
                return sede.get("nc_boleta", "BC01")

    # Fallback: usar ConfiguracionFacturacion (tabla BD)
    if config:
        if tipo_comprobante == "01":
            return config.serie_factura or "F001"
        elif tipo_comprobante == "03":
            return config.serie_boleta or "B001"
        elif tipo_comprobante == "07":
            return "BC01" if (tipo_original != "01") else "FC01"

    return "B001"


def listar_sedes() -> dict:
    """Retorna las sedes configuradas con sus series."""
    return _cargar_series()


# ═══════════════════════════════════════════════════════════════
# SERVICIO PRINCIPAL
# ═══════════════════════════════════════════════════════════════

class FacturacionService:
    """Servicio para emitir comprobantes electrónicos vía facturalo.pro"""

    def __init__(self, db: Session, org_id: int):
        self.db = db
        self.org_id = org_id
        self.config = self._get_config()

    def _get_config(self) -> Optional[ConfiguracionFacturacion]:
        return self.db.query(ConfiguracionFacturacion).filter(
            ConfiguracionFacturacion.organization_id == self.org_id,
            ConfiguracionFacturacion.activo == True
        ).first()

    def esta_configurado(self) -> bool:
        return self.config is not None and self.config.facturalo_token is not None

    async def emitir_comprobante_por_pago(
        self,
        payment_id: int,
        tipo: str = "03",
        forzar_datos_cliente: Dict = None,
        sede_id: str = "1",
        forma_pago: str = "contado",
    ) -> Dict[str, Any]:
        """
        Emite un comprobante electrónico a partir de un pago aprobado.

        Args:
            payment_id: ID del pago
            tipo: '01' = Factura, '03' = Boleta
            forzar_datos_cliente: {tipo_doc, num_doc, nombre, direccion, email}
            sede_id: ID de la sede para resolver series ('1', '2', etc.)
        """
        if not self.esta_configurado():
            return {"success": False, "error": "Facturación no configurada"}

        existe = self.db.query(Comprobante).filter(
            Comprobante.payment_id == payment_id
        ).first()
        if existe:
            return {"success": False, "error": "Ya existe comprobante para este pago",
                    "comprobante_id": existe.id}

        payment = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if not payment:
            return {"success": False, "error": "Pago no encontrado"}
        if payment.status != "approved":
            return {"success": False, "error": "El pago no está aprobado"}

        # Datos del cliente según tipo de comprobante
        cliente = self._obtener_datos_cliente(payment, forzar_datos_cliente)

        # Serie — usa resolver de series por sede
        serie = obtener_serie(tipo, sede_id=sede_id, config=self.config)

        # Número correlativo
        if tipo == "01":
            numero = self.config.ultimo_numero_factura + 1
        else:
            numero = self.config.ultimo_numero_boleta + 1

        # Items con descripción en 3 líneas
        items = self._construir_items(payment, tipo)

        # Totales
        subtotal = payment.amount
        igv = subtotal * (self.config.porcentaje_igv / 100) if self.config.porcentaje_igv > 0 else 0
        total = subtotal + igv

        # Obtener datos del colegiado para campos extra
        colegiado = self.db.query(Colegiado).filter(
            Colegiado.id == payment.colegiado_id
        ).first()

        estado_colegiado = None
        habil_hasta = None
        matricula = None
        if colegiado:
            matricula = colegiado.codigo_matricula
            estado_colegiado = "HÁBIL" if getattr(colegiado, 'habilitado', False) else "INHÁBIL"
            habil_hasta = self._calcular_vigencia(colegiado.id)

        # URL de consulta del emisor
        org = self.db.query(Organization).filter(
            Organization.id == self.org_id
        ).first()
        url_consulta = None
        if org:
            slug = getattr(org, 'slug', None) or getattr(org, 'domain', None)
            if slug:
                url_consulta = f"{slug}/consulta/habilidad"

        # Crear comprobante en BD
        comprobante = Comprobante(
            organization_id=self.org_id,
            payment_id=payment_id,
            tipo=tipo,
            serie=serie,
            numero=numero,
            subtotal=subtotal,
            igv=igv,
            total=total,
            cliente_tipo_doc=cliente["tipo_doc"],
            cliente_num_doc=cliente["num_doc"],
            cliente_nombre=cliente["nombre"],
            cliente_direccion=cliente.get("direccion"),
            cliente_email=cliente.get("email"),
            items=items,
            status="pending"
        )
        self.db.add(comprobante)
        self.db.flush()

        # Enviar a facturalo.pro
        resultado = await self._enviar_a_facturalo(
            comprobante,
            codigo_matricula=matricula,
            estado_colegiado=estado_colegiado,
            habil_hasta=habil_hasta,
            url_consulta=url_consulta,
            forma_pago=forma_pago,
        )

        if resultado["success"]:
            comprobante.status = "accepted"
            comprobante.facturalo_id = resultado.get("facturalo_id")
            comprobante.facturalo_response = resultado.get("response")
            comprobante.sunat_response_code = resultado.get("sunat_code", "0")
            comprobante.sunat_response_description = resultado.get("sunat_description")
            comprobante.sunat_hash = resultado.get("hash")
            comprobante.pdf_url = resultado.get("pdf_url")
            comprobante.xml_url = resultado.get("xml_url")
            comprobante.cdr_url = resultado.get("cdr_url")

            if tipo == "01":
                self.config.ultimo_numero_factura = numero
            else:
                self.config.ultimo_numero_boleta = numero
        else:
            comprobante.status = "rejected"
            comprobante.facturalo_response = resultado.get("response")
            comprobante.observaciones = resultado.get("error")

        self.db.commit()

        return {
            "success": resultado["success"],
            "comprobante_id": comprobante.id,
            "serie": serie,
            "numero": numero,
            "numero_formato": f"{serie}-{str(numero).zfill(8)}",
            "pdf_url": comprobante.pdf_url,
            "error": resultado.get("error")
        }

    
    
    async def emitir_nota_credito(
        self,
        comprobante_original_id: int,
        motivo_codigo: str = "01",
        motivo_texto: str = "Anulación de la operación",
        monto: float = None,
        sede_id: str = "1",
    ) -> Dict[str, Any]:
        """
        Emite una Nota de Crédito (tipo 07) referenciando un comprobante existente.

        Args:
            comprobante_original_id: ID del comprobante a anular/corregir
            motivo_codigo: '01'-'07' según catálogo SUNAT 09
            motivo_texto: Descripción del motivo
            monto: Monto de la NC. None = total del original. Parcial si < total.
            sede_id: Sede para resolver serie

        Catálogo SUNAT 09 - Códigos de NC:
            01 = Anulación de la operación
            02 = Anulación por error en RUC
            03 = Corrección por error en descripción
            04 = Descuento global
            05 = Descuento por ítem
            06 = Devolución total
            07 = Devolución parcial
        """
        if not self.esta_configurado():
            return {"success": False, "error": "Facturación no configurada"}

        # ── Obtener comprobante original ──
        original = self.db.query(Comprobante).filter(
            Comprobante.id == comprobante_original_id,
            Comprobante.organization_id == self.org_id,
        ).first()

        if not original:
            return {"success": False, "error": "Comprobante original no encontrado"}

        if original.status not in ("accepted", "anulado"):
            return {"success": False, "error": f"Comprobante en estado '{original.status}', no se puede emitir NC"}

        # Verificar que no exista NC previa aceptada para este comprobante
        nc_existente = self.db.query(Comprobante).filter(
            Comprobante.comprobante_ref_id == original.id,
            Comprobante.tipo == "07",
            Comprobante.status == "accepted",
        ).first()
        if nc_existente:
            return {
                "success": False,
                "error": f"Ya existe NC {nc_existente.serie}-{str(nc_existente.numero).zfill(8)} para este comprobante",
            }

        # ── Monto de la NC ──
        monto_nc = float(monto) if monto is not None else float(original.total)
        if monto_nc > float(original.total):
            return {"success": False, "error": f"Monto NC (S/ {monto_nc}) supera el total original (S/ {original.total})"}
        if monto_nc <= 0:
            return {"success": False, "error": "El monto debe ser mayor a 0"}

        es_parcial = abs(monto_nc - float(original.total)) > 0.01

        # ── Serie NC ──
        serie_nc = obtener_serie("07", sede_id=sede_id, tipo_original=original.tipo, config=self.config)

        # ── Número correlativo ──
        from sqlalchemy import func as sa_func
        ultimo = self.db.query(sa_func.max(Comprobante.numero)).filter(
            Comprobante.organization_id == self.org_id,
            Comprobante.tipo == "07",
            Comprobante.serie == serie_nc,
        ).scalar()
        numero_nc = (ultimo or 0) + 1

        # ── Items de la NC ──
        items_nc = self._construir_items_nc(original, monto_nc, es_parcial)

        # ── Calcular IGV ──
        tipo_afectacion = self.config.tipo_afectacion_igv or "20"
        if tipo_afectacion == "10":
            subtotal_nc = round(monto_nc / 1.18, 2)
            igv_nc = round(monto_nc - subtotal_nc, 2)
        else:
            subtotal_nc = monto_nc
            igv_nc = 0

        # ── Fecha/hora Perú ──
        ahora_peru = datetime.now(TZ_PERU)

        # ── Crear comprobante NC en BD local (ccploreto) ──
        nc = Comprobante(
            organization_id=self.org_id,
            payment_id=original.payment_id,
            comprobante_ref_id=original.id,
            tipo="07",
            serie=serie_nc,
            numero=numero_nc,
            subtotal=subtotal_nc,
            igv=igv_nc,
            total=monto_nc,
            cliente_tipo_doc=original.cliente_tipo_doc,
            cliente_num_doc=original.cliente_num_doc,
            cliente_nombre=original.cliente_nombre,
            cliente_direccion=original.cliente_direccion,
            cliente_email=original.cliente_email,
            items=items_nc,
            status="pending",
            observaciones=f"NC por: {motivo_texto}",
        )
        self.db.add(nc)
        self.db.flush()

        # ══════════════════════════════════════════════════════
        # PAYLOAD — Campos planos, como espera facturalo.pro
        # ══════════════════════════════════════════════════════
        payload = {
            "tipo_comprobante": "07",
            "serie": serie_nc,
            "fecha_emision": ahora_peru.strftime("%Y-%m-%d"),
            "hora_emision": ahora_peru.strftime("%H:%M:%S"),
            "moneda": "PEN",
            "forma_pago": "Contado",
            # ── Referencia al documento original ──
            "documento_ref_tipo": original.tipo,         # "03" o "01"
            "documento_ref_serie": original.serie,        # "B001" o "F001"
            "documento_ref_numero": original.numero,      # entero: 6
            # ── Motivo ──
            "motivo_nota": motivo_codigo,
            # ── Cliente ──
            "cliente": {
                "tipo_documento": original.cliente_tipo_doc,
                "numero_documento": original.cliente_num_doc,
                "razon_social": original.cliente_nombre,
                "direccion": original.cliente_direccion,
                "email": original.cliente_email,
            },
            # ── Items ──
            "items": [{
                "descripcion": item.get("descripcion", "Nota de crédito"),
                "cantidad": item.get("cantidad", 1),
                "unidad_medida": "ZZ",
                "precio_unitario": item.get("precio_unitario"),
                "tipo_afectacion_igv": tipo_afectacion,
            } for item in items_nc],
            "enviar_email": bool(original.cliente_email),
            "referencia_externa": f"NC-PAGO-{original.payment_id}",
        }

        print(f"🚀 NC PAYLOAD ref: tipo={payload.get('documento_ref_tipo')}, serie={payload.get('documento_ref_serie')}, num={payload.get('documento_ref_numero')}, motivo={payload.get('motivo_nota')}")

        # ── Enviar a facturalo.pro ──
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.config.facturalo_url}/comprobantes",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": self.config.facturalo_token,
                        "X-API-Secret": self.config.facturalo_secret,
                    },
                )

                data = response.json()

                if response.status_code in [200, 201] and data.get("exito"):
                    comp_data = data.get("comprobante", {})
                    archivos = data.get("archivos", {})

                    nc.status = "accepted"
                    nc.facturalo_id = comp_data.get("id")
                    nc.facturalo_response = data
                    nc.sunat_response_code = comp_data.get("codigo_sunat", "0")
                    nc.sunat_response_description = comp_data.get("mensaje_sunat")
                    nc.sunat_hash = comp_data.get("hash_cpe")
                    nc.pdf_url = archivos.get("pdf_url")
                    nc.xml_url = archivos.get("xml_url")
                    nc.cdr_url = archivos.get("cdr_url")

                    self.db.commit()

                    numero_fmt = f"{serie_nc}-{str(numero_nc).zfill(8)}"
                    logger.info(f"NC emitida: {numero_fmt} por S/ {monto_nc} | Ref: {original.serie}-{str(original.numero).zfill(8)}")

                    return {
                        "success": True,
                        "comprobante_id": nc.id,
                        "serie": serie_nc,
                        "numero": numero_nc,
                        "numero_formato": numero_fmt,
                        "monto": monto_nc,
                        "pdf_url": nc.pdf_url,
                    }
                else:
                    error_msg = data.get("mensaje", data.get("error", "Error desconocido"))
                    nc.status = "rejected"
                    nc.facturalo_response = data
                    nc.observaciones = f"RECHAZADA: {error_msg}"
                    self.db.commit()

                    logger.error(f"NC rechazada por facturalo.pro: {error_msg}")
                    return {"success": False, "error": error_msg, "response": data}

        except httpx.TimeoutException:
            nc.status = "error"
            nc.observaciones = "Timeout conectando a facturalo.pro"
            self.db.commit()
            return {"success": False, "error": "Timeout conectando a facturalo.pro"}
        except httpx.RequestError as e:
            nc.status = "error"
            nc.observaciones = f"Error de conexión: {str(e)}"
            self.db.commit()
            return {"success": False, "error": f"Error de conexión: {str(e)}"}
        except Exception as e:
            nc.status = "error"
            nc.observaciones = f"Error inesperado: {str(e)}"
            self.db.commit()
            logger.error(f"Error inesperado emitiendo NC: {e}", exc_info=True)
            return {"success": False, "error": f"Error inesperado: {str(e)}"}
    
    

    def _construir_items_nc(self, original: "Comprobante", monto_nc: float, es_parcial: bool) -> list:
        """
        Items para la Nota de Crédito.
        Total: mismos items del original (preserva las 4 líneas con L4 de pago).
        Parcial: un solo item con el monto parcial, preservando L2-L4 del original.
        """
        if not es_parcial and original.items:
            return original.items

        # ── Parcial: construir descripción con contexto del original ──
        descripcion = "NOTA DE CRÉDITO"
        if original.items and len(original.items) > 0:
            desc_original = original.items[0].get("descripcion", "")
            if desc_original:
                lineas = desc_original.split("\n")
                # L1 del original → prefijada con "NC:"
                primera_linea = lineas[0]
                descripcion = f"NC: {primera_linea}"
                # Preservar L2, L3, L4 si existen
                if len(lineas) > 1:
                    lineas_extra = "\n".join(lineas[1:])
                    descripcion += f"\n{lineas_extra}"

        return [{
            "codigo": "SRV001",
            "descripcion": descripcion,
            "unidad": "ZZ",
            "cantidad": 1,
            "precio_unitario": monto_nc,
            "valor_venta": monto_nc,
            "tipo_afectacion_igv": self.config.tipo_afectacion_igv or "20",
            "igv": 0 if (self.config.tipo_afectacion_igv or "20") != "10" else round(monto_nc - (monto_nc / 1.18), 2),
        }]


    
    # ───────────────────────────────────────────────────────
    # DATOS DEL CLIENTE
    # ───────────────────────────────────────────────────────

    def _obtener_datos_cliente(self, payment: Payment, forzar: Dict = None) -> Dict:
        """Datos del cliente para el comprobante."""
        if forzar:
            return {
                "tipo_doc": forzar.get("tipo_doc", "1"),
                "num_doc": forzar.get("num_doc"),
                "nombre": forzar.get("nombre"),
                "direccion": forzar.get("direccion"),
                "email": forzar.get("email")
            }

        if payment.pagador_tipo == "empresa" and payment.pagador_documento:
            return {
                "tipo_doc": "6",
                "num_doc": payment.pagador_documento,
                "nombre": payment.pagador_nombre,
                "direccion": getattr(payment, 'pagador_direccion', None),
                "email": None
            }

        if payment.pagador_tipo == "tercero" and payment.pagador_documento:
            return {
                "tipo_doc": "1",
                "num_doc": payment.pagador_documento,
                "nombre": payment.pagador_nombre,
                "direccion": None,
                "email": None
            }

        colegiado = self.db.query(Colegiado).filter(
            Colegiado.id == payment.colegiado_id
        ).first()

        if colegiado:
            return {
                "tipo_doc": "1",
                "num_doc": colegiado.dni,
                "nombre": colegiado.apellidos_nombres,
                "direccion": colegiado.direccion,
                "email": colegiado.email,
                "matricula": colegiado.codigo_matricula
            }

        return {
            "tipo_doc": "0",
            "num_doc": "00000000",
            "nombre": "CLIENTE VARIOS",
            "direccion": None,
            "email": None
        }

    # ───────────────────────────────────────────────────────
    # ITEMS DEL COMPROBANTE
    # ───────────────────────────────────────────────────────

    def _construir_items(self, payment: "Payment", tipo_comprobante: str = "03") -> list:
        """
        Items con descripción en 4 líneas separadas por \\n:
          L1: CUOTA ORDINARIA ENERO, FEBRERO 2026 (2 MESES)
          L2: RESTUCCIA ESLAVA, DUILIO CESAR
          L3: DNI [05393776] Cód. Matr. [10-2244]
          L4: Yape: Fecha [16-02-2026] Hora [09:25] N° Operación [464564]
        """
        items = []

        colegiado = self.db.query(Colegiado).filter(
            Colegiado.id == payment.colegiado_id
        ).first()

        # ── L2: Nombre del colegiado/pagador ──
        linea_nombre = ""
        # ── L3: Documentos ──
        linea_docs = ""

        if colegiado:
            linea_nombre = colegiado.apellidos_nombres or ""
            dni = colegiado.dni or ""
            matr = colegiado.codigo_matricula or ""
            linea_docs = f"DNI [{dni}] Cód. Matr. [{matr}]"
        elif payment.pagador_nombre:
            # Público general o tercero
            linea_nombre = payment.pagador_nombre or ""
            doc = payment.pagador_documento or ""
            if doc:
                linea_docs = f"DNI [{doc}]"

        # ── L4: Forma de pago ──
        linea_pago = self._construir_linea_pago(payment)

        # ── Buscar deudas pagadas para armar L1 ──
        notas = payment.notes or ""
        deudas_pagadas = []

        # Intentar extraer DEBT_IDS del notes
        match = re.search(r'\[DEBT_IDS:([\d,]+)\]', notas)
        if match:
            # Método preciso: usar IDs exactos
            ids = [int(x) for x in match.group(1).split(',') if x.strip()]
            if ids:
                deudas_pagadas = self.db.query(Debt).filter(
                    Debt.id.in_(ids)
                ).order_by(Debt.periodo.asc()).all()
        elif payment.colegiado_id and payment.related_debt_id:
            # Fallback: una sola deuda
            deuda = self.db.query(Debt).filter(
                Debt.id == payment.related_debt_id
            ).first()
            if deuda:
                deudas_pagadas = [deuda]
        # Si no hay nada, items vacío → boleta sin detalle de deudas

        if deudas_pagadas:
            # Normalizar clave de agrupación: quitar mes/año del concepto
            # (las deudas se crean con concepto "Cuota Ordinaria <Mes> <Año>" — sin
            # normalizar, cada mes formaría su propio item y la boleta se multiplicaría).
            _meses_re = r'enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre'
            _patron_periodo = re.compile(rf'\b({_meses_re})\b|\b\d{{4}}\b|\b\d{{4}}-\d{{2}}\b', re.IGNORECASE)

            def _clave_agrupacion(txt: str) -> str:
                limpio = _patron_periodo.sub('', txt or '')
                return re.sub(r'\s+', ' ', limpio).strip().lower() or (txt or 'cuota').lower()

            grupos = {}
            orden = []
            for deuda in deudas_pagadas:
                concepto_orig = deuda.concept or "Cuota ordinaria"
                clave = _clave_agrupacion(concepto_orig)
                if clave not in grupos:
                    nombre_grupo = _patron_periodo.sub('', concepto_orig)
                    nombre_grupo = re.sub(r'\s+', ' ', nombre_grupo).strip() or concepto_orig
                    grupos[clave] = {"nombre": nombre_grupo, "periodos": [], "monto_total": 0.0}
                    orden.append(clave)
                if deuda.periodo:
                    grupos[clave]["periodos"].append(deuda.periodo)
                grupos[clave]["monto_total"] += float(deuda.amount or deuda.balance or 0)

            # Distribuir payment.amount proporcionalmente entre grupos
            # (si hay un solo grupo, recibe todo el monto).
            total_base = sum(g["monto_total"] for g in grupos.values()) or float(payment.amount)
            asignado = 0.0
            claves = orden
            tipo_afect = self.config.tipo_afectacion_igv
            for idx, clave in enumerate(claves):
                datos = grupos[clave]
                periodos = datos["periodos"]
                cantidad = len(periodos) if periodos else 1

                if idx == len(claves) - 1:
                    # Último grupo: absorbe cualquier resto por redondeo
                    valor_venta = round(float(payment.amount) - asignado, 2)
                else:
                    share = datos["monto_total"] / total_base if total_base > 0 else 1 / len(claves)
                    valor_venta = round(float(payment.amount) * share, 2)
                    asignado += valor_venta

                precio_unitario = round(valor_venta / cantidad, 2) if cantidad else valor_venta

                if periodos:
                    periodos_fmt = self._formatear_periodos(periodos)
                    nombre_upper = datos["nombre"].upper()
                    periodos_upper = periodos_fmt.upper()
                    if periodos_upper in nombre_upper:
                        linea_1 = datos["nombre"]
                    else:
                        linea_1 = f"{datos['nombre']} {periodos_fmt}"
                    if cantidad > 1:
                        linea_1 += f" ({cantidad} MESES)"
                else:
                    linea_1 = datos["nombre"]

                descripcion = linea_1.upper()
                if linea_nombre:
                    descripcion += f"\n{linea_nombre}"
                if linea_docs:
                    descripcion += f"\n{linea_docs}"
                descripcion += f"\n{linea_pago}"

                items.append({
                    "codigo": "SRV001",
                    "descripcion": descripcion,
                    "unidad": "ZZ",
                    "cantidad": cantidad,
                    "precio_unitario": precio_unitario,
                    "valor_venta": valor_venta,
                    "tipo_afectacion_igv": tipo_afect,
                    "igv": 0 if tipo_afect == "20" else round(valor_venta * 0.18, 2),
                })

        # ── Fallback: sin deudas asociadas ──
        if not items:
            descripcion_raw = notas.replace("[CAJA] ", "").split("\n")[0]
            linea_1 = descripcion_raw if descripcion_raw else "Pago de cuotas de colegiatura"

            descripcion = linea_1.upper()
            if linea_nombre:
                descripcion += f"\n{linea_nombre}"
            if linea_docs:
                descripcion += f"\n{linea_docs}"
            descripcion += f"\n{linea_pago}"

            items.append({
                "codigo": "SRV001",
                "descripcion": descripcion,
                "unidad": "ZZ",
                "cantidad": 1,
                "precio_unitario": payment.amount,
                "valor_venta": payment.amount,
                "tipo_afectacion_igv": self.config.tipo_afectacion_igv,
                "igv": 0 if self.config.tipo_afectacion_igv == "20" else payment.amount * 0.18
            })

        return items

    # ───────────────────────────────────────────────────────
    # UTILIDADES
    # ───────────────────────────────────────────────────────

    def _calcular_vigencia(self, colegiado_id: int) -> Optional[str]:
        """Calcula fecha de vigencia. Retorna "DD/MM/YYYY" o None."""
        import calendar

        ultima_deuda = self.db.query(Debt).filter(
            Debt.colegiado_id == colegiado_id,
            Debt.status == "paid"
        ).order_by(Debt.periodo.desc()).first()

        if not ultima_deuda or not ultima_deuda.periodo:
            return None

        periodo = str(ultima_deuda.periodo).strip()

        try:
            if "-" in periodo and len(periodo.split("-")) == 2:
                year, mes = periodo.split("-")
                year = int(year)
                mes = int(mes)
                ultimo_dia = calendar.monthrange(year, mes)[1]
                return f"{ultimo_dia:02d}/{mes:02d}/{year}"

            meses_map = {
                "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
                "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
                "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
            }
            partes = periodo.lower().split()
            if len(partes) == 2 and partes[0] in meses_map:
                mes = meses_map[partes[0]]
                year = int(partes[1])
                ultimo_dia = calendar.monthrange(year, mes)[1]
                return f"{ultimo_dia:02d}/{mes:02d}/{year}"
        except (ValueError, KeyError):
            pass

        return None

    def _formatear_periodos(self, periodos: list) -> str:
        """["2026-01","2026-02"] -> "Enero, Febrero 2026" """
        if not periodos:
            return ""

        meses_es = {
            "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
            "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
            "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre",
        }

        parsed = []
        years = set()

        for p in periodos:
            p_str = str(p).strip()
            if "-" in p_str and len(p_str.split("-")) == 2:
                year, mes = p_str.split("-")
                mes_nombre = meses_es.get(mes.zfill(2), mes)
                parsed.append(mes_nombre)
                years.add(year)
            elif " " in p_str:
                partes = p_str.split(" ")
                parsed.append(partes[0])
                if len(partes) > 1:
                    years.add(partes[1])
            else:
                parsed.append(p_str)

        if parsed:
            meses_str = ", ".join(parsed)
            if years:
                return f"{meses_str} {sorted(years)[-1]}"
            return meses_str

        return ", ".join(periodos)
    
    def _construir_linea_pago(self, payment: "Payment") -> str:
        """
        Construye la línea de forma de pago para la descripción del comprobante.

        Formato:
            Yape: Fecha [16-02-2026] Hora [09:25] N° Operación [456841]
            EFECTIVO: Fecha [16-02-2026] Hora [09:25]
            Transferencia BBVA: Fecha [...] Hora [...] N° Operación [...]

        Usa payment.created_at (convertido a hora Perú) como fecha/hora.
        Si en el futuro se agrega campo transaction_at, se priorizaría ese.
        """
        # ── Mapeo de payment_method → etiqueta visible ──
        ETIQUETAS = {
            "Efectivo":       "EFECTIVO",
            "Yape":           "Yape",
            "Plin":           "Plin",
            "Transferencia":  "Transferencia BBVA",
            "Izipay":         "IZIPAY",
            "Izipay-Yape":    "Izipay - Yape",
            "Izipay-Plin":    "Izipay - Plin",
            "Izipay-Banco":   "Izipay - Banco",
            "Izipay-Tarjeta": "Izipay - Tarjeta",
        }

        metodo = payment.payment_method or "Efectivo"
        # Buscar case-insensitive
        etiqueta = metodo  # fallback
        for key, val in ETIQUETAS.items():
            if key.lower() == metodo.lower():
                etiqueta = val
                break

        # ── Fecha y hora (priorizar transaction_at si existe en el futuro) ──
        ts = getattr(payment, 'transaction_at', None) or payment.created_at
        if ts:
            # Convertir a hora Perú si tiene timezone
            if ts.tzinfo is not None:
                ts_peru = ts.astimezone(TZ_PERU)
            else:
                # Asumir UTC si no tiene timezone
                from datetime import timezone as tz
                ts_peru = ts.replace(tzinfo=tz.utc).astimezone(TZ_PERU)
            fecha_str = ts_peru.strftime("%d-%m-%Y")
            hora_str = ts_peru.strftime("%H:%M")
        else:
            fecha_str = ""
            hora_str = ""

        # ── Construir línea ──
        op_code = payment.operation_code or ""

        # Efectivo no tiene N° Operación
        if metodo == "Efectivo":
            return f"{etiqueta}: Fecha [{fecha_str}] Hora [{hora_str}]"

        # Todos los demás incluyen N° Operación
        return f"{etiqueta}: Fecha [{fecha_str}] Hora [{hora_str}] N° Operación [{op_code}]"

    # ───────────────────────────────────────────────────────
    # ENVÍO A FACTURALO.PRO
    # ───────────────────────────────────────────────────────

    async def _enviar_a_facturalo(self, comprobante: Comprobante,
                                   codigo_matricula=None, estado_colegiado=None,
                                   habil_hasta=None, url_consulta=None,
                                   forma_pago="contado") -> Dict:
        """Envía el comprobante a facturalo.pro con campos extra para el PDF"""

        # Fecha y hora de emisión en timezone Perú (UTC-5)
        ahora_peru = datetime.now(TZ_PERU)

        payload = {
            "tipo_comprobante": comprobante.tipo,
            "serie": comprobante.serie,
            "fecha_emision": ahora_peru.strftime("%Y-%m-%d"),
            "hora_emision": ahora_peru.strftime("%H:%M:%S"),
            "moneda": "PEN",
            "forma_pago": forma_pago.capitalize(),
            "codigo_matricula": codigo_matricula,
            "estado_colegiado": estado_colegiado,
            "habil_hasta": habil_hasta,
            "url_consulta": url_consulta,
            "cliente": {
                "tipo_documento": comprobante.cliente_tipo_doc,
                "numero_documento": comprobante.cliente_num_doc,
                "razon_social": comprobante.cliente_nombre,
                "direccion": comprobante.cliente_direccion,
                "email": comprobante.cliente_email
            },
            "items": [{
                "descripcion": item.get("descripcion", "Cuotas de colegiatura"),
                "cantidad": item.get("cantidad", 1),
                "unidad_medida": "ZZ",
                "precio_unitario": item.get("precio_unitario", comprobante.total),
                "tipo_afectacion_igv": self.config.tipo_afectacion_igv
            } for item in (comprobante.items or [{
                "descripcion": "Cuotas de colegiatura",
                "precio_unitario": comprobante.total
            }])],
            "enviar_email": bool(comprobante.cliente_email),
            "referencia_externa": f"PAGO-{comprobante.payment_id}"
        }

        logger.error(f"NC PAYLOAD KEYS: {list(payload.keys())}")
        import json as _json
        logger.error(f"NC PAYLOAD JSON: {_json.dumps(payload, default=str)}")

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.config.facturalo_url}/comprobantes",
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-API-Key": self.config.facturalo_token,
                        "X-API-Secret": self.config.facturalo_secret
                    }
                )

                # ── DEBUG temporal ──
                print(f"🔴 FACTURALO status={response.status_code}")
                print(f"🔴 FACTURALO body={response.text[:500]}")

                data = response.json()

                if response.status_code in [200, 201] and data.get("exito"):
                    comp_data = data.get("comprobante", {})
                    archivos = data.get("archivos", {})
                    return {
                        "success": True,
                        "facturalo_id": comp_data.get("id"),
                        "response": data,
                        "sunat_code": comp_data.get("codigo_sunat", "0"),
                        "sunat_description": comp_data.get("mensaje_sunat"),
                        "hash": comp_data.get("hash_cpe"),
                        "pdf_url": archivos.get("pdf_url"),
                        "xml_url": archivos.get("xml_url"),
                        "cdr_url": archivos.get("cdr_url"),
                        "numero_formato": comp_data.get("numero_formato")
                    }
                else:
                    error_msg = data.get("mensaje", data.get("error", "Error desconocido"))
                    logger.error(f"facturalo.pro rechazó comprobante: {response.status_code} - {error_msg}")
                    logger.error(f"Payload enviado: fecha={ahora_peru.strftime('%Y-%m-%d')}, hora={ahora_peru.strftime('%H:%M:%S')}, tipo={comprobante.tipo}, serie={comprobante.serie}")
                    return {
                        "success": False,
                        "error": error_msg,
                        "response": data
                    }

        except httpx.TimeoutException:
            return {"success": False, "error": "Timeout conectando a facturalo.pro"}
        except httpx.RequestError as e:
            return {"success": False, "error": f"Error de conexión: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"Error inesperado: {str(e)}"}

    # ───────────────────────────────────────────────────────
    # CONSULTAS
    # ───────────────────────────────────────────────────────

    def _obtener_matricula(self, payment_id: int) -> str:
        payment = self.db.query(Payment).filter(Payment.id == payment_id).first()
        if payment and payment.colegiado_id:
            colegiado = self.db.query(Colegiado).filter(
                Colegiado.id == payment.colegiado_id).first()
            if colegiado:
                return colegiado.codigo_matricula
        return None

    def obtener_comprobante(self, comprobante_id: int) -> Optional[Comprobante]:
        return self.db.query(Comprobante).filter(
            Comprobante.id == comprobante_id,
            Comprobante.organization_id == self.org_id
        ).first()

    def obtener_comprobante_por_pago(self, payment_id: int) -> Optional[Comprobante]:
        return self.db.query(Comprobante).filter(
            Comprobante.payment_id == payment_id
        ).first()

    def listar_comprobantes(self, limit=50, offset=0, tipo=None, status=None) -> list:
        query = self.db.query(Comprobante).filter(
            Comprobante.organization_id == self.org_id)
        if tipo:
            query = query.filter(Comprobante.tipo == tipo)
        if status:
            query = query.filter(Comprobante.status == status)
        return query.order_by(Comprobante.created_at.desc()).offset(offset).limit(limit).all()


# ═══════════════════════════════════════════════════════════════
# HELPER: Emisión automática (standalone)
# ═══════════════════════════════════════════════════════════════

async def emitir_comprobante_automatico(db: Session, payment_id: int) -> Dict:
    """Emite comprobante al aprobar un pago (desde endpoint de validación)"""
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if not payment:
        return {"success": False, "error": "Pago no encontrado"}

    config = db.query(ConfiguracionFacturacion).filter(
        ConfiguracionFacturacion.organization_id == payment.organization_id,
        ConfiguracionFacturacion.activo == True,
        ConfiguracionFacturacion.emitir_automatico == True
    ).first()
    if not config:
        return {"success": False, "error": "Emisión automática no configurada"}

    tipo = "01" if payment.pagador_tipo == "empresa" else "03"
    service = FacturacionService(db, payment.organization_id)
    return await service.emitir_comprobante_por_pago(payment_id, tipo, forma_pago="contado")