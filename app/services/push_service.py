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


def enviar_push_a_suscriptor(suscriptor, payload: dict) -> bool:
    """Envía un push a un único suscriptor. Devuelve True si el envío
    fue aceptado por el endpoint (no garantiza entrega al dispositivo)."""
    if not push_habilitado():
        logger.info("push deshabilitado (sin VAPID o pywebpush)")
        return False

    priv, _, sub = _vapid_keys()
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
        return True
    except WebPushException as e:
        # 404/410 = suscripción expirada; marcar inactivo en caller
        status = getattr(getattr(e, "response", None), "status_code", 0)
        logger.warning("WebPushException status=%s err=%s", status, e)
        if status in (404, 410):
            try:
                suscriptor.activo = False
            except Exception:
                pass
        return False
    except Exception as e:
        logger.warning("push falló: %s", e)
        return False


def enviar_push_multi(suscriptores, payload: dict) -> dict:
    """Envía a varios suscriptores. Retorna dict con contadores."""
    enviados = 0
    fallidos = 0
    for s in suscriptores:
        if not s.activo:
            continue
        ok = enviar_push_a_suscriptor(s, payload)
        if ok:
            enviados += 1
        else:
            fallidos += 1
    return {"enviados": enviados, "fallidos": fallidos}
