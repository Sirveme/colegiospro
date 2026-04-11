# ══════════════════════════════════════════════════════════
# app/routers/secretaria.py — SecretariaPro
# Rutas bajo el prefijo /secretaria
# ══════════════════════════════════════════════════════════

from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Request, Form, Depends, HTTPException, UploadFile, File
from fastapi.responses import (
    HTMLResponse,
    RedirectResponse,
    Response,
    JSONResponse,
)
from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from app.models_secretaria import (
    UsuarioSecretaria,
    DirectorioInstitucional,
    DirectorioContactoExtendido,
    DocumentoSecretaria,
    ConfigSecretariaColegio,
)
from app.services.auth_service import (
    hash_password,
    verify_password,
    set_session_cookie,
    clear_session_cookie,
    get_current_user_id,
    generar_token_verificacion,
    COOKIE_NAME,
)
from app.services.redactor_service import generar_documento, TONOS
from app.services.pdf_service import texto_a_pdf_bytes, pdf_disponible
from app.services.extract_service import extraer_texto, soportado as extract_soportado
from app.services.corrector_service import (
    corregir_texto,
    listar_acciones as corrector_acciones,
    ACCIONES as CORRECTOR_ACCIONES,
)


router = APIRouter(prefix="/secretaria", tags=["SecretariaPro"])
templates = Jinja2Templates(directory="app/templates")


# ─── Helpers ───
def _db():
    return SessionLocal()


def _require_user(request: Request) -> UsuarioSecretaria:
    uid = get_current_user_id(request)
    if not uid:
        raise HTTPException(status_code=401, detail="No autenticado")
    db = _db()
    try:
        u = db.query(UsuarioSecretaria).filter(UsuarioSecretaria.id == uid).first()
        if not u:
            raise HTTPException(status_code=401, detail="Sesión inválida")
        return u
    finally:
        db.close()


def _user_or_redirect(request: Request):
    """Devuelve el usuario o un RedirectResponse al login (para vistas HTML)."""
    uid = get_current_user_id(request)
    if not uid:
        return None
    db = _db()
    try:
        return db.query(UsuarioSecretaria).filter(UsuarioSecretaria.id == uid).first()
    finally:
        db.close()


def _ctx(usuario: Optional[UsuarioSecretaria] = None, **extra):
    base = {"usuario": usuario, "modo_actual": None}
    base.update(extra)
    return base


def _config_remitente(colegio_id: Optional[int]) -> dict:
    if not colegio_id:
        return {}
    db = _db()
    try:
        cfg = db.query(ConfigSecretariaColegio).filter(
            ConfigSecretariaColegio.colegio_id == colegio_id
        ).first()
        if not cfg:
            return {}
        return {
            "nombre_colegio": cfg.nombre_colegio or "",
            "nombre_decano": cfg.nombre_decano or "",
            "ciudad": cfg.ciudad or "",
        }
    finally:
        db.close()


# ─── Dashboard ───
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "secretaria/dashboard.html",
        _ctx(usuario=usuario, modo_actual="dashboard"),
    )


# ─── Auth: login ───
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None, ok: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "secretaria/login.html",
        _ctx(error=error, ok=ok),
    )


@router.post("/login")
async def login_submit(
    request: Request,
    correo: str = Form(...),
    password: str = Form(...),
):
    db = _db()
    try:
        u = db.query(UsuarioSecretaria).filter(
            UsuarioSecretaria.correo == correo.strip().lower()
        ).first()
        if not u or not verify_password(password, u.password_hash):
            return RedirectResponse(
                "/secretaria/login?error=Credenciales+incorrectas", status_code=302
            )
        if not u.activo:
            return RedirectResponse(
                "/secretaria/login?error=Cuenta+desactivada", status_code=302
            )
        resp = RedirectResponse("/secretaria/", status_code=302)
        set_session_cookie(resp, u.id, u.nombre)
        return resp
    finally:
        db.close()


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/secretaria/login", status_code=302)
    clear_session_cookie(resp)
    return resp


# ─── Auth: registro ───
@router.get("/registro", response_class=HTMLResponse)
async def registro_form(request: Request, error: Optional[str] = None):
    return templates.TemplateResponse(
        request,
        "secretaria/registro.html",
        _ctx(error=error),
    )


@router.post("/registro")
async def registro_submit(
    request: Request,
    nombre: str = Form(...),
    correo: str = Form(...),
    password: str = Form(...),
):
    correo = correo.strip().lower()
    if len(password) < 6:
        return RedirectResponse(
            "/secretaria/registro?error=La+contrase%C3%B1a+debe+tener+al+menos+6+caracteres",
            status_code=302,
        )
    db = _db()
    try:
        existe = db.query(UsuarioSecretaria).filter(
            UsuarioSecretaria.correo == correo
        ).first()
        if existe:
            return RedirectResponse(
                "/secretaria/registro?error=Ese+correo+ya+est%C3%A1+registrado",
                status_code=302,
            )
        u = UsuarioSecretaria(
            nombre=nombre.strip(),
            correo=correo,
            password_hash=hash_password(password),
            token_verificacion=generar_token_verificacion(),
            correo_verificado=True,  # MVP: auto-verificado hasta que haya SMTP
            activo=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)

        # Auto-login + ir directo al dashboard.
        resp = RedirectResponse("/secretaria/", status_code=302)
        set_session_cookie(resp, u.id, u.nombre)
        return resp
    finally:
        db.close()


@router.get("/verificar/{token}")
async def verificar_correo(token: str):
    db = _db()
    try:
        u = db.query(UsuarioSecretaria).filter(
            UsuarioSecretaria.token_verificacion == token
        ).first()
        if not u:
            return RedirectResponse(
                "/secretaria/login?error=Token+inv%C3%A1lido", status_code=302
            )
        u.correo_verificado = True
        u.token_verificacion = None
        db.commit()
        return RedirectResponse(
            "/secretaria/login?ok=Correo+verificado", status_code=302
        )
    finally:
        db.close()


# ─── Modo 1: Redactor ───
@router.get("/redactor", response_class=HTMLResponse)
async def redactor_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        instituciones = (
            db.query(DirectorioInstitucional)
            .order_by(DirectorioInstitucional.nombre_institucion.asc())
            .limit(200)
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/redactor.html",
        _ctx(
            usuario=usuario,
            modo_actual="redactor",
            instituciones=instituciones,
        ),
    )


@router.post("/redactor/generar", response_class=HTMLResponse)
async def redactor_generar(
    request: Request,
    texto_entrada: str = Form(...),
    tono: str = Form("formal"),
    institucion_id: Optional[int] = Form(None),
    documento_referencia: Optional[UploadFile] = File(None),
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='error'>Sesión expirada</p>", status_code=401)

    # Normalizar tono
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in TONOS:
        tono_norm = "formal"

    # Destinatario desde el directorio
    destinatario = None
    db = _db()
    try:
        if institucion_id:
            inst = db.query(DirectorioInstitucional).filter(
                DirectorioInstitucional.id == institucion_id
            ).first()
            if inst:
                destinatario = {
                    "nombre_institucion": inst.nombre_institucion,
                    "titular_nombre": inst.titular_nombre,
                    "titular_cargo": inst.titular_cargo,
                    "titular_tratamiento": inst.titular_tratamiento,
                }
    finally:
        db.close()

    remitente = _config_remitente(usuario.colegio_id)

    # Documento de referencia opcional
    ref_texto: Optional[str] = None
    ref_aviso: Optional[str] = None
    if documento_referencia is not None and documento_referencia.filename:
        if not extract_soportado(documento_referencia.filename):
            ref_aviso = (
                f"Tipo de archivo no soportado: {documento_referencia.filename}"
            )
        else:
            contenido = await documento_referencia.read()
            ref_texto, err = extraer_texto(
                documento_referencia.filename, contenido
            )
            if err:
                ref_aviso = f"No se pudo extraer texto de {documento_referencia.filename}: {err}"
                ref_texto = None

    texto_salida = generar_documento(
        texto_entrada=texto_entrada.strip(),
        tono=tono_norm,
        destinatario=destinatario,
        remitente=remitente,
        documento_referencia=ref_texto,
    )

    # Guardar borrador (no marcado como guardado=True hasta que el usuario lo guarde)
    db = _db()
    try:
        doc = DocumentoSecretaria(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            modo="redactor",
            texto_entrada=texto_entrada.strip(),
            texto_salida=texto_salida,
            tono=tono_norm,
            institucion_destino_id=institucion_id,
            formato_salida="txt",
            guardado=False,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/_redactor_resultado.html",
        {
            "texto_salida": texto_salida,
            "documento_id": doc_id,
            "tono": tono_norm,
            "ref_aviso": ref_aviso,
            "ref_usado": bool(ref_texto),
        },
    )


# ─── Documento: guardar / PDF ───
@router.post("/documento/{doc_id}/guardar")
async def documento_guardar(doc_id: int, request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == doc_id,
            DocumentoSecretaria.secretaria_id == usuario.id,
        ).first()
        if not doc:
            raise HTTPException(404, "Documento no encontrado")
        doc.guardado = True
        db.commit()
        return JSONResponse({"status": "ok"})
    finally:
        db.close()


@router.get("/documento/{doc_id}/pdf")
async def documento_pdf(doc_id: int, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == doc_id,
            DocumentoSecretaria.secretaria_id == usuario.id,
        ).first()
        if not doc:
            raise HTTPException(404, "Documento no encontrado")
        texto = doc.texto_salida or ""
    finally:
        db.close()

    contenido = texto_a_pdf_bytes(texto, titulo=f"Documento_{doc_id}")
    if pdf_disponible():
        return Response(
            content=contenido,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="documento_{doc_id}.pdf"'
            },
        )
    # Fallback: HTML imprimible
    return Response(
        content=contenido,
        media_type="text/html; charset=utf-8",
    )


# ─── Historial ───
@router.get("/historial", response_class=HTMLResponse)
async def historial(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        docs = (
            db.query(DocumentoSecretaria)
            .filter(
                DocumentoSecretaria.secretaria_id == usuario.id,
                DocumentoSecretaria.guardado == True,  # noqa: E712
            )
            .order_by(DocumentoSecretaria.creado_en.desc())
            .limit(100)
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/historial.html",
        _ctx(usuario=usuario, modo_actual="historial", documentos=docs),
    )


# ─── Directorio ───
@router.get("/directorio", response_class=HTMLResponse)
async def directorio_lista(request: Request, q: Optional[str] = None):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        query = db.query(DirectorioInstitucional)
        if q:
            like = f"%{q.strip()}%"
            query = query.filter(DirectorioInstitucional.nombre_institucion.ilike(like))
        instituciones = query.order_by(
            DirectorioInstitucional.nombre_institucion.asc()
        ).limit(200).all()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/directorio.html",
        _ctx(
            usuario=usuario,
            modo_actual="directorio",
            instituciones=instituciones,
            q=q or "",
        ),
    )


@router.post("/directorio/nuevo")
async def directorio_nuevo(
    request: Request,
    nombre_institucion: str = Form(...),
    ruc: Optional[str] = Form(None),
    tipo: Optional[str] = Form(None),
    region: Optional[str] = Form(None),
    ciudad: Optional[str] = Form(None),
    titular_nombre: Optional[str] = Form(None),
    titular_cargo: Optional[str] = Form(None),
    titular_tratamiento: Optional[str] = Form(None),
    correo: Optional[str] = Form(None),
    telefono: Optional[str] = Form(None),
    direccion: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    db = _db()
    try:
        inst = DirectorioInstitucional(
            nombre_institucion=nombre_institucion.strip(),
            ruc=(ruc or "").strip() or None,
            tipo=tipo,
            region=region,
            ciudad=ciudad,
            titular_nombre=titular_nombre,
            titular_cargo=titular_cargo,
            titular_tratamiento=titular_tratamiento,
            correo=correo,
            telefono=telefono,
            direccion=direccion,
            registrado_por_colegio_id=usuario.colegio_id,
            pendiente_revision=True,
            validado=False,
        )
        db.add(inst)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/directorio", status_code=302)


# ─── Proxy SUNAT (api.apis.net.pe — pública, sin key) ───
@router.get("/api/sunat-ruc")
async def sunat_ruc(request: Request, ruc: str):
    """
    Consulta el RUC en api.apis.net.pe y devuelve datos normalizados
    listos para pre-llenar el formulario del directorio.
    Requiere sesión iniciada.
    """
    _ = _require_user(request)
    ruc = (ruc or "").strip()
    if not ruc.isdigit() or len(ruc) != 11:
        return JSONResponse(
            {"ok": False, "error": "El RUC debe tener exactamente 11 dígitos"},
            status_code=400,
        )

    # api.apis.net.pe v1 sigue siendo pública (sin token).
    # v2 ya exige Bearer token, así que usamos v1.
    url = f"https://api.apis.net.pe/v1/ruc?numero={ruc}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url, headers={"Accept": "application/json"})
        if r.status_code == 404:
            return JSONResponse(
                {"ok": False, "error": "RUC no encontrado en SUNAT"},
                status_code=404,
            )
        if r.status_code >= 400:
            return JSONResponse(
                {"ok": False, "error": f"SUNAT respondió {r.status_code}"},
                status_code=502,
            )
        data = r.json() or {}
    except httpx.TimeoutException:
        return JSONResponse(
            {"ok": False, "error": "Timeout consultando SUNAT"}, status_code=504
        )
    except Exception as e:
        return JSONResponse(
            {"ok": False, "error": f"Error: {e}"}, status_code=502
        )

    # v1 devuelve: nombre, numeroDocumento, estado, condicion, direccion,
    # ubigeo, departamento, provincia, distrito, viaNombre, etc.
    return JSONResponse({
        "ok": True,
        "ruc": data.get("numeroDocumento") or ruc,
        "nombre_institucion": data.get("nombre") or data.get("razonSocial") or "",
        "direccion": data.get("direccion") or "",
        "departamento": data.get("departamento") or "",
        "provincia": data.get("provincia") or "",
        "distrito": data.get("distrito") or "",
        "estado": data.get("estado") or "",
        "condicion": data.get("condicion") or "",
    })


@router.get("/directorio/{inst_id}", response_class=HTMLResponse)
async def directorio_detalle(inst_id: int, request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        inst = db.query(DirectorioInstitucional).filter(
            DirectorioInstitucional.id == inst_id
        ).first()
        if not inst:
            raise HTTPException(404, "Institución no encontrada")
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/directorio_detalle.html",
        _ctx(usuario=usuario, modo_actual="directorio", inst=inst),
    )


# ─── Ficha del destinatario (datos extendidos privados del colegio) ───
def _colegio_id_de(usuario: UsuarioSecretaria) -> int:
    """colegio_id usable para PK compuesta. 0 cuando el usuario aún no
    tiene colegio asociado (MVP)."""
    return int(usuario.colegio_id or 0)


def _ficha_to_dict(ficha: Optional[DirectorioContactoExtendido]) -> dict:
    if not ficha:
        return {
            "foto_url": "",
            "whatsapp": "",
            "red_social": "",
            "nombre_secretaria": "",
            "fecha_inicio_cargo": "",
            "notas_relacionamiento": "",
        }
    return {
        "foto_url": ficha.foto_url or "",
        "whatsapp": ficha.whatsapp or "",
        "red_social": ficha.red_social or "",
        "nombre_secretaria": ficha.nombre_secretaria or "",
        "fecha_inicio_cargo": ficha.fecha_inicio_cargo or "",
        "notas_relacionamiento": ficha.notas_relacionamiento or "",
    }


@router.get("/destinatario/{inst_id}/ficha", response_class=HTMLResponse)
async def destinatario_ficha(inst_id: int, request: Request):
    """Devuelve el panel HTML de la ficha del destinatario (institución +
    datos extendidos privados del colegio). Pensado para HTMX."""
    usuario = _require_user(request)
    db = _db()
    try:
        inst = db.query(DirectorioInstitucional).filter(
            DirectorioInstitucional.id == inst_id
        ).first()
        if not inst:
            raise HTTPException(404, "Institución no encontrada")
        ficha = db.query(DirectorioContactoExtendido).filter(
            DirectorioContactoExtendido.colegio_id == _colegio_id_de(usuario),
            DirectorioContactoExtendido.institucion_id == inst_id,
        ).first()
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/_ficha_destinatario.html",
        {
            "inst": inst,
            "ficha": _ficha_to_dict(ficha),
        },
    )


@router.post("/destinatario/{inst_id}/ficha")
async def destinatario_ficha_guardar(
    inst_id: int,
    request: Request,
    foto_url: Optional[str] = Form(None),
    whatsapp: Optional[str] = Form(None),
    red_social: Optional[str] = Form(None),
    nombre_secretaria: Optional[str] = Form(None),
    fecha_inicio_cargo: Optional[str] = Form(None),
    notas_relacionamiento: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    cid = _colegio_id_de(usuario)
    db = _db()
    try:
        inst = db.query(DirectorioInstitucional).filter(
            DirectorioInstitucional.id == inst_id
        ).first()
        if not inst:
            raise HTTPException(404, "Institución no encontrada")

        ficha = db.query(DirectorioContactoExtendido).filter(
            DirectorioContactoExtendido.colegio_id == cid,
            DirectorioContactoExtendido.institucion_id == inst_id,
        ).first()
        if not ficha:
            ficha = DirectorioContactoExtendido(
                colegio_id=cid,
                institucion_id=inst_id,
            )
            db.add(ficha)

        ficha.foto_url = (foto_url or "").strip() or None
        ficha.whatsapp = (whatsapp or "").strip() or None
        ficha.red_social = (red_social or "").strip() or None
        ficha.nombre_secretaria = (nombre_secretaria or "").strip() or None
        ficha.fecha_inicio_cargo = (fecha_inicio_cargo or "").strip() or None
        ficha.notas_relacionamiento = (notas_relacionamiento or "").strip() or None

        db.commit()
        db.refresh(ficha)
        return JSONResponse({"ok": True})
    finally:
        db.close()


# ─── Modo 2: Corrector ─────────────────────────────────────────────
@router.get("/corrector", response_class=HTMLResponse)
async def corrector_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "secretaria/corrector.html",
        _ctx(
            usuario=usuario,
            modo_actual="corrector",
            acciones=corrector_acciones(),
        ),
    )


@router.post("/corrector/procesar", response_class=HTMLResponse)
async def corrector_procesar(
    request: Request,
    texto: Optional[str] = Form(""),
    accion: str = Form("ortografia"),
    documento_referencia: Optional[UploadFile] = File(None),
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='error'>Sesión expirada</p>", status_code=401)

    accion_norm = (accion or "ortografia").strip().lower()
    if accion_norm not in CORRECTOR_ACCIONES:
        accion_norm = "ortografia"

    # Si se subió un archivo y el textarea está vacío, extraemos su texto
    texto_final = (texto or "").strip()
    ref_aviso: Optional[str] = None
    if (not texto_final) and documento_referencia and documento_referencia.filename:
        if not extract_soportado(documento_referencia.filename):
            ref_aviso = f"Tipo no soportado: {documento_referencia.filename}"
        else:
            contenido = await documento_referencia.read()
            extraido, err = extraer_texto(documento_referencia.filename, contenido)
            if err:
                ref_aviso = f"No se pudo extraer texto: {err}"
            else:
                texto_final = extraido

    if not texto_final:
        return HTMLResponse(
            "<p class='sp-alert sp-alert-error'>"
            "Pega un texto o sube un archivo con texto para procesar.</p>",
            status_code=400,
        )

    texto_salida = corregir_texto(texto_final, accion_norm)

    db = _db()
    try:
        doc = DocumentoSecretaria(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            modo="corrector",
            texto_entrada=texto_final[:5000],
            texto_salida=texto_salida,
            tono=accion_norm,
            formato_salida="txt",
            guardado=False,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        doc_id = doc.id
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/_corrector_resultado.html",
        {
            "texto_salida": texto_salida,
            "documento_id": doc_id,
            "accion": accion_norm,
            "accion_etiqueta": CORRECTOR_ACCIONES[accion_norm]["etiqueta"],
            "ref_aviso": ref_aviso,
        },
    )


# ─── Modo 3: Comunicado (placeholder navegable) ────────────────────
@router.get("/comunicado", response_class=HTMLResponse)
async def comunicado_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "secretaria/comunicado.html",
        _ctx(usuario=usuario, modo_actual="comunicado"),
    )


# ─── Modo 4: Post Redes (placeholder navegable) ────────────────────
@router.get("/post-redes", response_class=HTMLResponse)
async def post_redes_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "secretaria/post_redes.html",
        _ctx(usuario=usuario, modo_actual="post-redes"),
    )
