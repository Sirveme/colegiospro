# app/services/push_service.py
# Envío de Web Push con VAPID (pywebpush). Best-effort: si no hay
# claves VAPID o pywebpush no está instalado, no envía y devuelve False.

import json
import logging
import os
from typing import Optional

logger = logging.getLogger("secretaria.push")

try:
    from pywebpush import webpush, WebPushException  # type: ignore
    _HAS_PYWEBPUSH = True
except Exception:
    webpush = None  # type: ignore
    WebPushException = Exception  # type: ignore
    _HAS_PYWEBPUSH = False


def _vapid_keys():
    priv = os.environ.get("VAPID_PRIVATE_KEY", "").strip()
    pub = os.environ.get("VAPID_PUBLIC_KEY", "").strip()
    claims_sub = os.environ.get("VAPID_CLAIMS_SUB", "mailto:admin@colegiospro.org.pe")
    return priv, pub, claims_sub


def vapid_public_key() -> str:
    _, pub, _ = _vapid_keys()
    return pub


def push_habilitado() -> bool:
    priv, pub, _ = _vapid_keys()
    return _HAS_PYWEBPUSH and bool(priv) and bool(pub)


def _endpoint_corto(endpoint: str) -> str:
    """Versión abreviada del endpoint para logs (sin token completo)."""
    if not endpoint:
        return "(vacío)"
    try:
        from urllib.parse import urlparse
        p = urlparse(endpoint)
        host = p.netloc or "?"
        tail = (p.path or "")[-20:]
        return f"{host}…{tail}"
    except Exception:
        return endpoint[:40] + "…"


def enviar_push_a_suscriptor(suscriptor, payload: dict) -> bool:
    """Envía un push a un único suscriptor. Devuelve True si el envío
    fue aceptado por el endpoint (no garantiza entrega al dispositivo)."""
    priv, pub, sub = _vapid_keys()

    if not _HAS_PYWEBPUSH:
        logger.error("push deshabilitado: pywebpush NO instalado")
        return False
    if not priv or not pub:
        logger.error(
            "push deshabilitado: VAPID_PRIVATE_KEY=%s VAPID_PUBLIC_KEY=%s",
            "OK" if priv else "FALTA",
            "OK" if pub else "FALTA",
        )
        return False

    ep_corto = _endpoint_corto(getattr(suscriptor, "endpoint", "") or "")
    logger.info(
        "push → id=%s endpoint=%s categoria=%s",
        getattr(suscriptor, "id", "?"),
        ep_corto,
        payload.get("categoria", "general"),
    )

    subscription_info = {
        "endpoint": suscriptor.endpoint,
        "keys": {
            "p256dh": suscriptor.p256dh or "",
            "auth": suscriptor.auth or "",
        },
    }
    try:
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=priv,
            vapid_claims={"sub": sub},
            ttl=60 * 60 * 24,
        )
        logger.info("push OK id=%s", getattr(suscriptor, "id", "?"))
        return True
    except WebPushException as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", 0)
        body = ""
        try:
            body = resp.text if resp is not None else "sin respuesta"
        except Exception:
            body = "(no se pudo leer response.text)"
        logger.error(
            "WebPushException id=%s status=%s err=%s",
            getattr(suscriptor, "id", "?"), status, e,
        )
        logger.error("Response body: %s", body[:500] if body else "sin respuesta")
        if status in (404, 410):
            try:
                suscriptor.activo = False
                logger.info("suscriptor id=%s marcado inactivo (status %s)",
                            getattr(suscriptor, "id", "?"), status)
            except Exception:
                pass
        return False
    except Exception as e:
        logger.exception(
            "Error push inesperado id=%s: %s: %s",
            getattr(suscriptor, "id", "?"), type(e).__name__, e,
        )
        return False


def enviar_push_multi(suscriptores, payload: dict) -> dict:
    """Envía a varios suscriptores. Retorna dict con contadores."""
    logger.info(
        "enviar_push_multi: suscriptores=%d payload_keys=%s",
        len(list(suscriptores)) if not hasattr(suscriptores, '__len__') else len(suscriptores),
        list(payload.keys()),
    )
    enviados = 0
    fallidos = 0
    inactivos = 0
    for s in suscriptores:
        if not s.activo:
            inactivos += 1
            continue
        ok = enviar_push_a_suscriptor(s, payload)
        if ok:
            enviados += 1
        else:
            fallidos += 1
    logger.info(
        "enviar_push_multi resumen: enviados=%d fallidos=%d inactivos=%d",
        enviados, fallidos, inactivos,
    )
    return {"enviados": enviados, "fallidos": fallidos, "inactivos": inactivos}
