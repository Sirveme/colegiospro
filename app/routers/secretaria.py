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
    PerfilRemitente,
    PreferenciasSecretaria,
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
from app.services.redactor_service import (
    generar_documento,
    ajustar_documento,
    TONOS,
    TIPOS,
    AJUSTES,
    listar_tipos,
    listar_ajustes,
)
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


def _config_remitente(
    colegio_id: Optional[int],
    perfil_id: Optional[int] = None,
    secretaria_id: Optional[int] = None,
) -> dict:
    """Devuelve un dict con los datos del remitente para el prompt.
    Combina la config del colegio con un perfil de remitente opcional."""
    base = {
        "nombre_colegio": "",
        "nombre_decano": "",
        "nombre_firmante": "",
        "cargo_firmante": "Decano",
        "tratamiento_firmante": "",
        "ciudad": "",
    }
    db = _db()
    try:
        if colegio_id:
            cfg = db.query(ConfigSecretariaColegio).filter(
                ConfigSecretariaColegio.colegio_id == colegio_id
            ).first()
            if cfg:
                base["nombre_colegio"] = cfg.nombre_colegio or ""
                base["nombre_decano"] = cfg.nombre_decano or ""
                base["nombre_firmante"] = cfg.nombre_decano or ""
                base["ciudad"] = cfg.ciudad or ""

        if perfil_id and secretaria_id:
            perf = db.query(PerfilRemitente).filter(
                PerfilRemitente.id == perfil_id,
                PerfilRemitente.secretaria_id == secretaria_id,
            ).first()
            if perf:
                base["nombre_firmante"] = perf.nombre or base["nombre_firmante"]
                base["cargo_firmante"] = perf.cargo or "Decano"
                base["tratamiento_firmante"] = perf.tratamiento or ""
                if perf.institucion:
                    base["nombre_colegio"] = perf.institucion
                if perf.ciudad:
                    base["ciudad"] = perf.ciudad
        elif secretaria_id:
            # Si no se pasó perfil explícito, intentar el default del usuario
            perf = db.query(PerfilRemitente).filter(
                PerfilRemitente.secretaria_id == secretaria_id,
                PerfilRemitente.es_default == True,  # noqa: E712
            ).first()
            if perf:
                base["nombre_firmante"] = perf.nombre or base["nombre_firmante"]
                base["cargo_firmante"] = perf.cargo or "Decano"
                base["tratamiento_firmante"] = perf.tratamiento or ""
                if perf.institucion:
                    base["nombre_colegio"] = perf.institucion
                if perf.ciudad:
                    base["ciudad"] = perf.ciudad

        return base
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
        perfiles = (
            db.query(PerfilRemitente)
            .filter(PerfilRemitente.secretaria_id == usuario.id)
            .order_by(PerfilRemitente.es_default.desc(), PerfilRemitente.nombre.asc())
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
            perfiles=perfiles,
            tipos=listar_tipos(),
            ajustes=listar_ajustes(),
        ),
    )


@router.post("/redactor/generar", response_class=HTMLResponse)
async def redactor_generar(
    request: Request,
    texto_entrada: str = Form(...),
    tono: str = Form("formal"),
    tipo_documento: str = Form("carta"),
    institucion_id: Optional[int] = Form(None),
    perfil_remitente_id: Optional[int] = Form(None),
    documento_referencia: Optional[UploadFile] = File(None),
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='error'>Sesión expirada</p>", status_code=401)

    # Normalizar tono y tipo
    tono_norm = (tono or "formal").strip().lower()
    if tono_norm not in TONOS:
        tono_norm = "formal"
    tipo_norm = (tipo_documento or "carta").strip().lower()
    if tipo_norm not in TIPOS:
        tipo_norm = "carta"

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

    remitente = _config_remitente(
        usuario.colegio_id,
        perfil_id=perfil_remitente_id,
        secretaria_id=usuario.id,
    )

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
        tipo_documento=tipo_norm,
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
            formato_salida=tipo_norm,
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
            "tipo_documento": tipo_norm,
            "ref_aviso": ref_aviso,
            "ref_usado": bool(ref_texto),
            "ajustes": listar_ajustes(),
        },
    )


# ─── Modo 1: Ajustes post-generación ───
@router.post("/redactor/ajustar", response_class=HTMLResponse)
async def redactor_ajustar(
    request: Request,
    ajuste: str = Form(...),
    doc_id: int = Form(...),
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='error'>Sesión expirada</p>", status_code=401)

    if ajuste not in AJUSTES:
        return HTMLResponse(
            f"<p class='sp-alert sp-alert-error'>Ajuste no válido: {ajuste}</p>",
            status_code=400,
        )

    db = _db()
    try:
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == doc_id,
            DocumentoSecretaria.secretaria_id == usuario.id,
        ).first()
        if not doc:
            return HTMLResponse(
                "<p class='sp-alert sp-alert-error'>Documento no encontrado.</p>",
                status_code=404,
            )

        texto_actual = doc.texto_salida or ""
        nuevo_texto = ajustar_documento(texto_actual, ajuste)

        # "sugerir_asunto" devuelve solo 3 sugerencias — no machacar el documento.
        # Para los demás ajustes sí actualizamos el texto guardado.
        if ajuste == "sugerir_asunto":
            return HTMLResponse(
                "<div class='sp-sugerencias'>"
                "<h4 style='margin:0 0 .5rem;'>💡 Sugerencias de asunto</h4>"
                f"<pre style='white-space:pre-wrap; margin:0;'>{nuevo_texto}</pre>"
                "</div>"
            )

        doc.texto_salida = nuevo_texto
        db.commit()
        db.refresh(doc)
        tono_norm = doc.tono or "formal"
        tipo_norm = doc.formato_salida or "carta"
        doc_id_final = doc.id
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/_redactor_resultado.html",
        {
            "texto_salida": nuevo_texto,
            "documento_id": doc_id_final,
            "tono": tono_norm,
            "tipo_documento": tipo_norm,
            "ref_aviso": None,
            "ref_usado": False,
            "ajustes": listar_ajustes(),
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
        tono_doc = doc.tono or "formal"
        cfg_col = None
        if usuario.colegio_id:
            cfg_col = db.query(ConfigSecretariaColegio).filter(
                ConfigSecretariaColegio.colegio_id == usuario.colegio_id
            ).first()
    finally:
        db.close()

    contenido = texto_a_pdf_bytes(
        texto,
        titulo=f"Documento_{doc_id}",
        tono=tono_doc,
        config_colegio=cfg_col,
    )
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
async def historial(
    request: Request,
    q: Optional[str] = None,
    modo: Optional[str] = None,
    tono: Optional[str] = None,
    desde: Optional[str] = None,
    hasta: Optional[str] = None,
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)

    db = _db()
    try:
        query = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.secretaria_id == usuario.id,
            DocumentoSecretaria.guardado == True,  # noqa: E712
        )
        if q:
            like = f"%{q.strip()}%"
            query = query.filter(
                (DocumentoSecretaria.texto_salida.ilike(like))
                | (DocumentoSecretaria.texto_entrada.ilike(like))
            )
        if modo:
            query = query.filter(DocumentoSecretaria.modo == modo)
        if tono:
            query = query.filter(DocumentoSecretaria.tono == tono)
        if desde:
            try:
                from datetime import datetime as _dt
                d_desde = _dt.fromisoformat(desde)
                query = query.filter(DocumentoSecretaria.creado_en >= d_desde)
            except Exception:
                pass
        if hasta:
            try:
                from datetime import datetime as _dt, timedelta as _td
                d_hasta = _dt.fromisoformat(hasta) + _td(days=1)
                query = query.filter(DocumentoSecretaria.creado_en < d_hasta)
            except Exception:
                pass

        docs = query.order_by(DocumentoSecretaria.creado_en.desc()).limit(200).all()
    finally:
        db.close()

    return templates.TemplateResponse(
        request,
        "secretaria/historial.html",
        _ctx(
            usuario=usuario,
            modo_actual="historial",
            documentos=docs,
            filtro_q=q or "",
            filtro_modo=modo or "",
            filtro_tono=tono or "",
            filtro_desde=desde or "",
            filtro_hasta=hasta or "",
        ),
    )


@router.get("/historial/{doc_id}/reabrir")
async def historial_reabrir(doc_id: int, request: Request):
    """Carga un documento del historial en el Redactor para reusarlo."""
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
        # Marcar como NO guardado para que el flujo del Redactor lo trate como borrador
        # (la fila guardada original sigue intacta — ésta es una copia conceptual.)
    finally:
        db.close()
    # En la práctica, devolvemos al Redactor con los datos en query params
    from urllib.parse import quote
    texto = (doc.texto_entrada or "")[:500]
    return RedirectResponse(
        f"/secretaria/redactor?reabrir={doc_id}&texto={quote(texto)}&tono={doc.tono or 'formal'}",
        status_code=302,
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


# ═══════════════════════════════════════════════════════════════════
# Perfiles de remitente
# ═══════════════════════════════════════════════════════════════════
@router.get("/remitentes", response_class=HTMLResponse)
async def remitentes_lista(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        perfiles = (
            db.query(PerfilRemitente)
            .filter(PerfilRemitente.secretaria_id == usuario.id)
            .order_by(PerfilRemitente.es_default.desc(), PerfilRemitente.nombre.asc())
            .all()
        )
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/remitentes.html",
        _ctx(usuario=usuario, modo_actual="remitentes", perfiles=perfiles),
    )


@router.get("/remitentes/nuevo", response_class=HTMLResponse)
async def remitentes_nuevo_form(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    return templates.TemplateResponse(
        request,
        "secretaria/remitente_form.html",
        _ctx(usuario=usuario, modo_actual="remitentes"),
    )


@router.post("/remitentes/nuevo")
async def remitentes_nuevo_submit(
    request: Request,
    nombre: str = Form(...),
    cargo: Optional[str] = Form(None),
    tratamiento: Optional[str] = Form(None),
    institucion: Optional[str] = Form(None),
    ciudad: Optional[str] = Form(None),
    es_default: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    db = _db()
    try:
        marcar_default = bool(es_default)
        if marcar_default:
            # Limpiar default anterior
            db.query(PerfilRemitente).filter(
                PerfilRemitente.secretaria_id == usuario.id,
                PerfilRemitente.es_default == True,  # noqa: E712
            ).update({"es_default": False})

        p = PerfilRemitente(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            nombre=nombre.strip(),
            cargo=(cargo or "").strip() or None,
            tratamiento=(tratamiento or "").strip() or "",
            institucion=(institucion or "").strip() or None,
            ciudad=(ciudad or "Iquitos").strip(),
            es_default=marcar_default,
        )
        db.add(p)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/remitentes", status_code=302)


@router.post("/remitentes/{perfil_id}/default")
async def remitentes_marcar_default(perfil_id: int, request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        p = db.query(PerfilRemitente).filter(
            PerfilRemitente.id == perfil_id,
            PerfilRemitente.secretaria_id == usuario.id,
        ).first()
        if not p:
            raise HTTPException(404, "Perfil no encontrado")
        db.query(PerfilRemitente).filter(
            PerfilRemitente.secretaria_id == usuario.id,
            PerfilRemitente.es_default == True,  # noqa: E712
        ).update({"es_default": False})
        p.es_default = True
        db.commit()
        return JSONResponse({"ok": True})
    finally:
        db.close()


@router.post("/remitentes/{perfil_id}/eliminar")
async def remitentes_eliminar(perfil_id: int, request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        p = db.query(PerfilRemitente).filter(
            PerfilRemitente.id == perfil_id,
            PerfilRemitente.secretaria_id == usuario.id,
        ).first()
        if not p:
            raise HTTPException(404, "Perfil no encontrado")
        db.delete(p)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/remitentes", status_code=302)


# ═══════════════════════════════════════════════════════════════════
# Configuración + preferencias
# ═══════════════════════════════════════════════════════════════════
def _get_o_crear_prefs(db, secretaria_id: int) -> PreferenciasSecretaria:
    p = db.query(PreferenciasSecretaria).filter(
        PreferenciasSecretaria.secretaria_id == secretaria_id
    ).first()
    if not p:
        p = PreferenciasSecretaria(secretaria_id=secretaria_id)
        db.add(p)
        db.commit()
        db.refresh(p)
    return p


@router.get("/configuracion", response_class=HTMLResponse)
async def configuracion_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        prefs = _get_o_crear_prefs(db, usuario.id)
        cfg = None
        if usuario.colegio_id:
            cfg = db.query(ConfigSecretariaColegio).filter(
                ConfigSecretariaColegio.colegio_id == usuario.colegio_id
            ).first()
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/configuracion.html",
        _ctx(
            usuario=usuario,
            modo_actual="configuracion",
            prefs=prefs,
            colegio_cfg=cfg,
        ),
    )


@router.post("/configuracion/apariencia")
async def configuracion_apariencia(
    request: Request,
    tema: str = Form("claro"),
    fuente_size: str = Form("normal"),
    tipo_doc_default: str = Form("carta"),
    tono_default: str = Form("formal"),
):
    usuario = _require_user(request)
    if tema not in ("claro", "oscuro", "pastel", "elegante"):
        tema = "claro"
    if fuente_size not in ("pequeno", "normal", "grande", "xl"):
        fuente_size = "normal"
    if tipo_doc_default not in TIPOS:
        tipo_doc_default = "carta"
    if tono_default not in TONOS:
        tono_default = "formal"
    db = _db()
    try:
        p = _get_o_crear_prefs(db, usuario.id)
        p.tema = tema
        p.fuente_size = fuente_size
        p.tipo_doc_default = tipo_doc_default
        p.tono_default = tono_default
        db.commit()
        return JSONResponse({"ok": True, "prefs": {
            "tema": p.tema,
            "fuente_size": p.fuente_size,
            "tipo_doc_default": p.tipo_doc_default,
            "tono_default": p.tono_default,
        }})
    finally:
        db.close()


@router.post("/configuracion/colegio")
async def configuracion_colegio(
    request: Request,
    nombre_colegio: Optional[str] = Form(None),
    nombre_decano: Optional[str] = Form(None),
    ciudad: Optional[str] = Form("Iquitos"),
    membrete_url: Optional[str] = Form(None),
    api_key_openai: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    cid = int(usuario.colegio_id or 0)
    db = _db()
    try:
        cfg = db.query(ConfigSecretariaColegio).filter(
            ConfigSecretariaColegio.colegio_id == cid
        ).first()
        if not cfg:
            cfg = ConfigSecretariaColegio(colegio_id=cid)
            db.add(cfg)

        if nombre_colegio is not None:
            cfg.nombre_colegio = nombre_colegio.strip() or None
        if nombre_decano is not None:
            cfg.nombre_decano = nombre_decano.strip() or None
        if ciudad is not None:
            cfg.ciudad = ciudad.strip() or "Iquitos"
        if membrete_url is not None:
            cfg.membrete_url = membrete_url.strip() or None

        # API key del colegio (encriptación opcional con Fernet si está disponible)
        if api_key_openai:
            try:
                from cryptography.fernet import Fernet
                fernet_key = os.environ.get("FERNET_KEY")
                if fernet_key:
                    f = Fernet(fernet_key.encode() if isinstance(fernet_key, str) else fernet_key)
                    cfg.api_key_openai_enc = f.encrypt(api_key_openai.encode()).decode()
                else:
                    cfg.api_key_openai_enc = api_key_openai  # sin encriptar (dev)
            except Exception:
                cfg.api_key_openai_enc = api_key_openai

        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/configuracion", status_code=302)
