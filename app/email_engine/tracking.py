# ══════════════════════════════════════════════════════════
# app/email_engine/tracking.py
# Rutas públicas de tracking: pixel, clics, PDF, baja, objeción
# ══════════════════════════════════════════════════════════

import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import Response, RedirectResponse, HTMLResponse

from app.database import SessionLocal
from .models import (
    EmailEnvio, EmailEvento, EmailContacto, EmailCampana, EmailObjecion,
)


router = APIRouter(prefix="/track", tags=["tracking"])


# GIF 1x1 transparente en base64
GIF_1X1 = base64.b64decode(
    "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
)


def _db():
    return SessionLocal()


def _registrar_evento(db, envio, tipo, url="", request=None, meta=None):
    evento = EmailEvento(
        envio_id=envio.id,
        tipo=tipo,
        url_destino=url[:500] if url else "",
        ip=(request.client.host if request and request.client else "")[:45],
        user_agent=(request.headers.get("user-agent", "")[:300] if request else ""),
        meta=meta or {},
        creado_en=datetime.utcnow(),
    )
    db.add(evento)


@router.get("/open/{token}.gif")
async def track_open(token: str, request: Request):
    db = _db()
    try:
        envio = db.query(EmailEnvio).filter_by(token=token).first()
        if envio:
            if envio.estado == "enviado":
                envio.estado = "abierto"
                envio.abierto_en = datetime.utcnow()
            _registrar_evento(db, envio, "open", request=request)
            campana = db.get(EmailCampana, envio.campana_id)
            if campana:
                campana.total_abiertos = (campana.total_abiertos or 0) + 1
            db.commit()
    finally:
        db.close()

    return Response(
        content=GIF_1X1,
        media_type="image/gif",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@router.get("/click/{token}")
async def track_click(
    token: str,
    request: Request,
    url: str = "/",
    tipo: str = "click",
):
    db = _db()
    try:
        envio = db.query(EmailEnvio).filter_by(token=token).first()
        if envio:
            if not envio.primer_clic_en:
                envio.primer_clic_en = datetime.utcnow()
            _registrar_evento(db, envio, tipo, url=url, request=request)
            campana = db.get(EmailCampana, envio.campana_id)
            if campana:
                if tipo == "pdf_download":
                    campana.total_descargas = (campana.total_descargas or 0) + 1
                else:
                    campana.total_clics = (campana.total_clics or 0) + 1
            db.commit()
    finally:
        db.close()

    destino = url if url.startswith("http") else f"https://colegiospro.org.pe{url}"
    return RedirectResponse(url=destino, status_code=302)


@router.get("/pdf/{token}")
async def track_pdf(token: str, request: Request):
    """Tracking de descarga de PDF — redirige al PDF real."""
    db = _db()
    try:
        envio = db.query(EmailEnvio).filter_by(token=token).first()
        if envio:
            _registrar_evento(db, envio, "pdf_download", request=request)
            campana = db.get(EmailCampana, envio.campana_id)
            if campana:
                campana.total_descargas = (campana.total_descargas or 0) + 1
            db.commit()
    finally:
        db.close()
    return RedirectResponse(
        url="https://colegiospro.org.pe/static/docs/guia_secretaria_pro_2026.pdf",
        status_code=302,
    )


@router.get("/baja/{token}")
async def track_baja(token: str, request: Request):
    """Procesa baja de lista (unsubscribe)."""
    db = _db()
    try:
        envio = db.query(EmailEnvio).filter_by(token=token).first()
        if envio:
            contacto = db.get(EmailContacto, envio.contacto_id)
            if contacto:
                contacto.baja = True
                contacto.baja_en = datetime.utcnow()
            _registrar_evento(db, envio, "unsubscribe", request=request)
            campana = db.get(EmailCampana, envio.campana_id)
            if campana:
                campana.total_bajas = (campana.total_bajas or 0) + 1
            db.commit()
    finally:
        db.close()

    return HTMLResponse("""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Baja confirmada</title>
<style>
  body{font-family:-apple-system,sans-serif;background:#F8F5EF;
       display:flex;align-items:center;justify-content:center;
       min-height:100vh;margin:0;text-align:center}
  .card{background:#fff;padding:3rem 2rem;border-radius:16px;
        box-shadow:0 4px 24px rgba(0,0,0,.08);max-width:420px;width:90%}
  h2{color:#0D7A60;margin-top:0}
  p{color:#5A6275;line-height:1.6}
</style></head>
<body>
<div class="card">
  <div style="font-size:48px">✓</div>
  <h2>Fuiste removido de nuestra lista</h2>
  <p>No recibirás más correos de esta campaña.</p>
  <p style="font-size:13px;color:#888">
    Si fue un error, escríbenos a
    <a href="mailto:hola@colegiospro.org.pe">hola@colegiospro.org.pe</a>.
  </p>
</div>
</body></html>""")


@router.get("/objecion/{token}")
async def form_objecion(token: str):
    """Formulario de objeciones — por qué no usas la herramienta."""
    html = """<!DOCTYPE html><html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tu opinión importa</title>
<style>
  body{font-family:sans-serif;background:#F8F5EF;display:flex;align-items:center;
        justify-content:center;min-height:100vh;margin:0}
  .card{background:white;border-radius:16px;padding:40px;max-width:480px;width:90%;
          box-shadow:0 4px 24px rgba(0,0,0,0.08)}
  h2{color:#0F3460;font-size:22px;margin-bottom:8px}
  p{color:#5A6275;font-size:14px;margin-bottom:24px}
  label{display:block;font-size:13px;font-weight:600;color:#0F3460;margin-bottom:8px;margin-top:16px}
  .opciones{display:flex;flex-direction:column;gap:8px}
  .op{display:flex;align-items:center;gap:10px;background:#F8F5EF;border:1.5px solid #E0DDD8;
        border-radius:10px;padding:12px 16px;cursor:pointer;font-size:14px;transition:all 0.2s}
  .op:hover{border-color:#0D7A60;background:#E8F5F0}
  input[type=radio]{accent-color:#0D7A60}
  textarea{width:100%;border:1.5px solid #E0DDD8;border-radius:10px;padding:12px;
             font-size:14px;font-family:sans-serif;resize:vertical;margin-top:6px;box-sizing:border-box}
  .btn{width:100%;background:#0D7A60;color:white;border:none;border-radius:12px;
         padding:16px;font-size:15px;font-weight:700;cursor:pointer;margin-top:20px}
  .btn:hover{background:#0F3460}
  .exito{display:none;text-align:center;padding:40px 0}
  .exito h3{color:#0D7A60;font-size:20px}
</style></head>
<body>
<div class="card">
  <div id="formulario">
    <h2>Tu opinión nos importa</h2>
    <p>¿Qué te faltó para probar SecretariaPro? Tus respuestas nos ayudan a mejorar.</p>
    <form id="form-obj" onsubmit="enviar(event)">
      <input type="hidden" name="token" value="__TOKEN__">
      <label>¿Por qué no lo probaste aún?</label>
      <div class="opciones">
        <label class="op"><input type="radio" name="objecion" value="no_autorizacion" required>
          No tengo autorización de mi jefe</label>
        <label class="op"><input type="radio" name="objecion" value="privacidad">
          Me preocupa la privacidad de mis documentos</label>
        <label class="op"><input type="radio" name="objecion" value="no_convence">
          No estoy segura de que funcione para mi institución</label>
        <label class="op"><input type="radio" name="objecion" value="sin_tiempo">
          No tuve tiempo de revisarlo</label>
        <label class="op"><input type="radio" name="objecion" value="otro">
          Otro motivo</label>
      </div>
      <label style="margin-top:20px">¿Qué haría que lo uses hoy?</label>
      <div class="opciones">
        <label class="op"><input type="radio" name="que_necesita" value="demo" required>
          Ver una demo de 5 minutos</label>
        <label class="op"><input type="radio" name="que_necesita" value="whatsapp">
          Hablar por WhatsApp con alguien</label>
        <label class="op"><input type="radio" name="que_necesita" value="caso_similar">
          Ver que otra municipalidad similar ya la usa</label>
        <label class="op"><input type="radio" name="que_necesita" value="prueba_mes">
          Que mi institución pruebe gratis un mes completo</label>
      </div>
      <label style="margin-top:20px">Comentario adicional (opcional)</label>
      <textarea name="comentario" rows="3" placeholder="Puedes escribir lo que quieras..."></textarea>
      <button type="submit" class="btn">Enviar mi opinión</button>
    </form>
  </div>
  <div class="exito" id="exito">
    <div style="font-size:48px">🙏</div>
    <h3>¡Gracias por tu respuesta!</h3>
    <p style="color:#5A6275">Te escribiremos para resolver exactamente lo que necesitas.</p>
    <a href="https://colegiospro.org.pe/secretaria/registro"
       style="display:inline-block;margin-top:20px;background:#0D7A60;color:white;
              padding:14px 28px;border-radius:100px;text-decoration:none;font-weight:700">
      Probar gratis ahora →
    </a>
  </div>
</div>
<script>
async function enviar(e) {
  e.preventDefault();
  const data = new FormData(e.target);
  await fetch('/track/objecion/guardar', {
    method:'POST',
    body: new URLSearchParams(data)
  });
  document.getElementById('formulario').style.display='none';
  document.getElementById('exito').style.display='block';
}
</script>
</body></html>"""
    return HTMLResponse(html.replace("__TOKEN__", token))


@router.post("/objecion/guardar")
async def guardar_objecion(request: Request):
    form = await request.form()
    token = form.get("token", "")
    db = _db()
    try:
        envio = db.query(EmailEnvio).filter_by(token=token).first() if token else None
        correo = ""
        muni = ""
        if envio:
            contacto = db.get(EmailContacto, envio.contacto_id)
            if contacto:
                correo = contacto.correo or ""
                muni = contacto.municipalidad or ""
        obj = EmailObjecion(
            envio_id=envio.id if envio else None,
            correo=correo,
            municipalidad=muni,
            objecion=form.get("objecion", "")[:100],
            que_necesita=form.get("que_necesita", "")[:100],
            comentario=form.get("comentario", "")[:2000],
        )
        db.add(obj)
        if envio:
            _registrar_evento(db, envio, "objecion", request=request, meta={
                "objecion": obj.objecion,
                "que_necesita": obj.que_necesita,
            })
        db.commit()
    finally:
        db.close()
    return {"ok": True}
