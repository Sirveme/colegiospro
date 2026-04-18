# ══════════════════════════════════════════════════════════
# app/routers/secretaria.py — SecretariaPro
# Rutas bajo el prefijo /secretaria
# ══════════════════════════════════════════════════════════

import os
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
    ConfigOrganizacion,
    CorrelatividadDocumento,
    DocumentoRevision,
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
    clasificar_instruccion,
    obtener_siguiente_correlativo,
    TONOS,
    TIPOS,
    AJUSTES,
    NEGATIVOS_OPCIONALES,
    listar_tipos,
    listar_ajustes,
)
from app.services.pdf_service import (
    texto_a_pdf_bytes,
    pdf_disponible,
    construir_nombre_archivo,
)
from app.services.docx_service import generar_docx_bytes, docx_disponible
from app.services.extract_service import extraer_texto, soportado as extract_soportado
from app.services.corrector_service import (
    corregir_texto,
    listar_acciones as corrector_acciones,
    ACCIONES as CORRECTOR_ACCIONES,
)


router = APIRouter(prefix="/secretaria", tags=["SecretariaPro"])
templates = Jinja2Templates(directory="app/templates")


# ─── Cache busting de assets estáticos ────────────────────────────
# Inyecta `static_version` global en TODAS las plantillas como sufijo
# `?v=N` en los <link>/<script> para invalidar la caché del navegador
# automáticamente en cada deploy (mtime del archivo cambia → N cambia).
def _static_version() -> str:
    import os, time
    candidatos = [
        "static/secretaria/secretaria.js",
        "static/secretaria/secretaria.css",
    ]
    mt = 0
    for p in candidatos:
        try:
            m = int(os.path.getmtime(p))
            if m > mt:
                mt = m
        except OSError:
            pass
    return str(mt or int(time.time()))


templates.env.globals["static_version"] = _static_version()


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
        "nombre": "",
        "cargo_firmante": "Decano",
        "cargo": "Decano",
        "tratamiento_firmante": "",
        "tratamiento": "",
        "sexo": "M",
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

        def _apply_perfil(perf):
            base["nombre_firmante"] = perf.nombre or base["nombre_firmante"]
            base["nombre"] = perf.nombre or base["nombre"]
            base["cargo_firmante"] = perf.cargo or "Decano"
            base["cargo"] = perf.cargo or "Decano"
            base["tratamiento_firmante"] = perf.tratamiento or ""
            base["tratamiento"] = perf.tratamiento or ""
            base["sexo"] = getattr(perf, "sexo", "M") or "M"
            if perf.institucion:
                base["nombre_colegio"] = perf.institucion
            if perf.ciudad:
                base["ciudad"] = perf.ciudad

        if perfil_id and secretaria_id:
            perf = db.query(PerfilRemitente).filter(
                PerfilRemitente.id == perfil_id,
                PerfilRemitente.secretaria_id == secretaria_id,
            ).first()
            if perf:
                _apply_perfil(perf)
        elif secretaria_id:
            perf = db.query(PerfilRemitente).filter(
                PerfilRemitente.secretaria_id == secretaria_id,
                PerfilRemitente.es_default == True,  # noqa: E712
            ).first()
            if perf:
                _apply_perfil(perf)

        return base
    finally:
        db.close()


ANNO_OFICIAL_DEFAULT = "Año del Bicentenario de la Integración Latinoamericana y Caribeña"


def _get_config_org(db, secretaria_id: int) -> Optional[ConfigOrganizacion]:
    return db.query(ConfigOrganizacion).filter(
        ConfigOrganizacion.secretaria_id == secretaria_id
    ).first()


def _get_o_crear_config_org(db, secretaria_id: int, colegio_id=None) -> ConfigOrganizacion:
    cfg = _get_config_org(db, secretaria_id)
    if not cfg:
        cfg = ConfigOrganizacion(
            secretaria_id=secretaria_id,
            colegio_id=colegio_id,
            anno_oficial=ANNO_OFICIAL_DEFAULT,
            anno_numero=datetime.now(timezone.utc).year,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def _check_onboarding(request: Request, usuario):
    """Devuelve RedirectResponse si el onboarding no está completo, o None."""
    if not usuario:
        return None
    db = _db()
    try:
        cfg = _get_config_org(db, usuario.id)
        if not cfg or not cfg.onboarding_completo:
            return RedirectResponse("/secretaria/onboarding", status_code=302)
    finally:
        db.close()
    return None


def _context_flags(usuario) -> dict:
    """Flags para ocultar/mostrar secciones vacías en templates."""
    db = _db()
    try:
        tiene_remitentes = db.query(PerfilRemitente).filter(
            PerfilRemitente.secretaria_id == usuario.id
        ).count() > 0
        tiene_historial = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.secretaria_id == usuario.id,
            DocumentoSecretaria.guardado == True,  # noqa: E712
        ).count() > 0
        tiene_directorio = db.query(DirectorioInstitucional).count() > 0
        cfg = _get_config_org(db, usuario.id)
        onboarding_completo = cfg.onboarding_completo if cfg else False
        return {
            "tiene_remitentes": tiene_remitentes,
            "tiene_historial": tiene_historial,
            "tiene_directorio": tiene_directorio,
            "onboarding_completo": onboarding_completo,
        }
    finally:
        db.close()


def _banner_anno(usuario) -> tuple:
    """Devuelve (mostrar_banner: bool, anno_actual: int)."""
    anno_actual = datetime.now(timezone.utc).year
    db = _db()
    try:
        cfg = _get_config_org(db, usuario.id)
        if cfg and cfg.anno_numero and cfg.anno_numero < anno_actual:
            return True, anno_actual
        return False, anno_actual
    finally:
        db.close()


# ─── Onboarding ───
@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_view(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        tipos_sel = cfg.tipos_doc_habilitados or list(TIPOS.keys())
    finally:
        db.close()
    return templates.TemplateResponse(
        request,
        "secretaria/onboarding.html",
        _ctx(
            usuario=usuario,
            modo_actual="onboarding",
            config=cfg,
            tipos_sel=tipos_sel,
            anno_oficial_default=ANNO_OFICIAL_DEFAULT,
        ),
    )


@router.post("/onboarding/paso/{n}")
async def onboarding_paso(n: int, request: Request):
    usuario = _require_user(request)
    data = await request.json()
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        if n == 1:
            cfg.nombre_organizacion = (data.get("nombre_organizacion") or "").strip()
            cfg.siglas = (data.get("siglas") or "").strip()
            cfg.ciudad = (data.get("ciudad") or "Iquitos").strip()
            cfg.sector = (data.get("sector") or "profesional").strip()
        elif n == 2:
            cfg.tipos_doc_habilitados = data.get("tipos_doc") or list(TIPOS.keys())
        elif n == 3:
            remitentes = data.get("remitentes") or []
            for r in remitentes:
                nombre = (r.get("nombre") or "").strip()
                if not nombre:
                    continue
                p = PerfilRemitente(
                    secretaria_id=usuario.id,
                    colegio_id=usuario.colegio_id,
                    nombre=nombre,
                    cargo=(r.get("cargo") or "").strip() or None,
                    tratamiento=(r.get("tratamiento") or "").strip(),
                    institucion=(r.get("area") or "").strip() or None,
                    ciudad=cfg.ciudad or "Iquitos",
                    es_default=(remitentes.index(r) == 0),
                )
                db.add(p)
        elif n == 4:
            cfg.anno_oficial = (data.get("anno_oficial") or ANNO_OFICIAL_DEFAULT).strip()
            cfg.anno_numero = datetime.now(timezone.utc).year
        cfg.actualizado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True, "paso": n})


@router.post("/onboarding/completar")
async def onboarding_completar(request: Request):
    usuario = _require_user(request)
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        cfg.onboarding_completo = True
        cfg.actualizado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


# ─── Dashboard ───
@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    redir = _check_onboarding(request, usuario)
    if redir:
        return redir
    flags = _context_flags(usuario)
    return templates.TemplateResponse(
        request,
        "secretaria/dashboard.html",
        _ctx(usuario=usuario, modo_actual="dashboard", **flags),
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
    redir = _check_onboarding(request, usuario)
    if redir:
        return redir
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
    banner, anno_actual = _banner_anno(usuario)
    flags = _context_flags(usuario)

    # Serializar para data-* del wrap (consumido por el panel JS)
    import json as _json
    remitentes_data = [
        {
            "id": p.id,
            "nombre": p.nombre or "",
            "cargo": p.cargo or "",
            "tratamiento": p.tratamiento or "",
            "es_default": bool(p.es_default),
            "foto_url": "",
        }
        for p in perfiles
    ]
    destinatarios_data = [
        {
            "id": i.id,
            "nombre": i.nombre_institucion or "",
            "cargo": i.titular_nombre or "",
            "titular_cargo": i.titular_cargo or "",
            "institucion": i.nombre_institucion or "",
        }
        for i in instituciones
    ]
    remitentes_json = _json.dumps(remitentes_data, ensure_ascii=True)
    destinatarios_json = _json.dumps(destinatarios_data, ensure_ascii=True)

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
            banner_anno=banner,
            anno_actual=anno_actual,
            remitentes_json=remitentes_json,
            destinatarios_json=destinatarios_json,
            **flags,
        ),
    )


@router.post("/redactor/analizar", response_class=HTMLResponse)
async def redactor_analizar(
    request: Request,
    texto_entrada: str = Form(""),
    tipo_documento: str = Form("carta"),
    documento_referencia: Optional[UploadFile] = File(None),
):
    """Agente clasificador: analiza la instrucción y propone parámetros."""
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='sp-alert sp-alert-error'>Sesión expirada</p>", 401)

    texto = (texto_entrada or "").strip()
    if not texto:
        return HTMLResponse(
            "<p class='sp-alert sp-alert-error'>Escribe o dicta una instrucción primero.</p>", 400
        )

    # Extraer texto de referencia si se subió
    ref_texto = ""
    if documento_referencia and documento_referencia.filename:
        if extract_soportado(documento_referencia.filename):
            contenido = await documento_referencia.read()
            ref_texto, _ = extraer_texto(documento_referencia.filename, contenido)

    db = _db()
    try:
        cfg = _get_config_org(db, usuario.id)
        tipos_habilitados = (cfg.tipos_doc_habilitados if cfg else None) or list(TIPOS.keys())
        perfiles = (
            db.query(PerfilRemitente)
            .filter(PerfilRemitente.secretaria_id == usuario.id)
            .order_by(PerfilRemitente.es_default.desc(), PerfilRemitente.nombre.asc())
            .all()
        )
        remitentes_list = [
            {"nombre": p.nombre, "cargo": p.cargo or "", "id": p.id,
             "tratamiento": p.tratamiento or "", "es_default": p.es_default}
            for p in perfiles
        ]
    finally:
        db.close()

    resultado = clasificar_instruccion(
        texto=texto,
        tipos_habilitados=tipos_habilitados,
        remitentes=remitentes_list,
        texto_referencia=ref_texto or "",
    )

    tipo_sug = resultado.get("tipo_sugerido", tipo_documento)
    tipo_cfg = TIPOS.get(tipo_sug, TIPOS.get("carta", {}))
    tono_sug = resultado.get("tono_sugerido", "formal")
    tono_cfg = TONOS.get(tono_sug, TONOS.get("formal", {}))

    return templates.TemplateResponse(
        request,
        "secretaria/_redactor_propuesta.html",
        {
            "tipo_sugerido": tipo_sug,
            "tipo_label": tipo_cfg.get("label", tipo_sug),
            "asunto_sugerido": resultado.get("asunto_sugerido", ""),
            "tono_sugerido": tono_sug,
            "tono_label": tono_cfg.get("etiqueta", tono_sug),
            "remitentes": perfiles,
            "preguntas": resultado.get("preguntas") or [],
            "razon": resultado.get("razon", ""),
            "tipo_no_habilitado": tipo_sug not in tipos_habilitados,
        },
    )


@router.post("/config/habilitar-tipo")
async def config_habilitar_tipo(request: Request):
    """Agrega un tipo de documento a los habilitados del usuario."""
    usuario = _require_user(request)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not data:
        form = await request.form()
        data = {"tipo": form.get("tipo", "")}
    tipo = (data.get("tipo") or "").strip()
    if tipo and tipo in TIPOS:
        db = _db()
        try:
            cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
            habilitados = cfg.tipos_doc_habilitados or list(TIPOS.keys())
            if tipo not in habilitados:
                habilitados.append(tipo)
                cfg.tipos_doc_habilitados = habilitados
                db.commit()
        finally:
            db.close()
    return JSONResponse({"ok": True})


@router.post("/redactor/generar", response_class=HTMLResponse)
async def redactor_generar(
    request: Request,
    texto_entrada: str = Form(...),
    tono: str = Form("formal"),
    tipo_documento: str = Form("carta"),
    institucion_id: Optional[int] = Form(None),
    perfil_remitente_id: Optional[int] = Form(None),
    documento_referencia: Optional[UploadFile] = File(None),
    asunto_confirmado: Optional[str] = Form(None),
    respuestas_agente: Optional[str] = Form(None),
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

    # Config de organización para año oficial, siglas, etc.
    db = _db()
    try:
        cfg_org = _get_config_org(db, usuario.id)
        config_org_dict = {}
        prefs_redaccion = {}
        if cfg_org:
            config_org_dict = {
                "nombre_organizacion": cfg_org.nombre_organizacion or "",
                "siglas": cfg_org.siglas or "",
                "ciudad": cfg_org.ciudad or remitente.get("ciudad", "Lima"),
                "anno_oficial": cfg_org.anno_oficial or ANNO_OFICIAL_DEFAULT,
                "secretaria_id": usuario.id,
            }
            prefs_redaccion = cfg_org.preferencias_redaccion or {}
            # Enriquecer remitente con datos de org si faltan
            if not remitente.get("nombre_colegio") and cfg_org.nombre_organizacion:
                remitente["nombre_colegio"] = cfg_org.nombre_organizacion
            if not remitente.get("ciudad") and cfg_org.ciudad:
                remitente["ciudad"] = cfg_org.ciudad

        # Numeración correlativa
        num_correlativo = obtener_siguiente_correlativo(
            tipo_norm, usuario.id, db
        )
    finally:
        db.close()

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

    # Parsear respuestas del agente
    resp_agente = {}
    if respuestas_agente:
        try:
            import json
            resp_agente = json.loads(respuestas_agente)
        except Exception:
            pass

    texto_salida, alertas = generar_documento(
        texto_entrada=texto_entrada.strip(),
        tono=tono_norm,
        destinatario=destinatario,
        remitente=remitente,
        documento_referencia=ref_texto,
        tipo_documento=tipo_norm,
        config_org=config_org_dict,
        asunto_confirmado=(asunto_confirmado or "").strip(),
        respuestas_agente=resp_agente,
        num_correlativo=num_correlativo,
        preferencias_prompt=prefs_redaccion,
    )

    # Guardar borrador
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
            "alertas": alertas or [],
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
    import re

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
        creado = doc.creado_en or datetime.utcnow()
        cfg_col = None
        if usuario.colegio_id:
            cfg_col = db.query(ConfigSecretariaColegio).filter(
                ConfigSecretariaColegio.colegio_id == usuario.colegio_id
            ).first()
        cfg_org = _get_config_org(db, usuario.id)
    finally:
        db.close()

    tipo_doc = doc.formato_salida or "carta"

    # Extraer número del documento desde el texto (ej: "OFICIO N° 045-2026-SIGLAS")
    numero_doc = ""
    numero_solo = ""
    match = re.search(
        r"N[°º]\s*([0-9]{1,4}[-\u2013\u2014]?[0-9]{4}(?:[-\u2013\u2014][A-Za-zÁÉÍÓÚÑ\.]+)?)",
        texto,
    )
    if match:
        numero_doc = match.group(1).strip()
        m_num = re.match(r"(\d+)", numero_doc)
        if m_num:
            numero_solo = m_num.group(1).zfill(3)

    contenido = texto_a_pdf_bytes(
        texto,
        titulo=f"Documento_{doc_id}",
        tono=tono_doc,
        config_colegio=cfg_col,
        tipo_documento=tipo_doc,
        config_organizacion=cfg_org,
        numero_documento=numero_doc,
    )

    # Nombre estándar: TIPO_SIGLAS_CORRELATIVO_FECHA.pdf
    siglas = (cfg_org.siglas if cfg_org else "") or "DOC"
    nombre_archivo = construir_nombre_archivo(
        tipo_doc=tipo_doc,
        siglas=siglas,
        numero_correlativo=numero_solo or numero_doc,
        ext="pdf",
        fecha=creado,
    )

    if pdf_disponible():
        return Response(
            content=contenido,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
                "Content-Type": "application/pdf",
                "X-Content-Type-Options": "nosniff",
            },
        )
    # Fallback: HTML imprimible
    return Response(
        content=contenido,
        media_type="text/html; charset=utf-8",
    )


@router.get("/documento/{doc_id}/docx")
async def documento_docx(doc_id: int, request: Request):
    """Descarga el documento como .docx (Word) con membrete institucional."""
    import re

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
        creado = doc.creado_en or datetime.utcnow()
        cfg_org = _get_config_org(db, usuario.id)
    finally:
        db.close()

    tipo_doc = doc.formato_salida or "carta"

    # Extraer número del documento desde el texto
    numero_doc = ""
    numero_solo = ""
    match = re.search(
        r"N[°º]\s*([0-9]{1,4}[-\u2013\u2014]?[0-9]{4}(?:[-\u2013\u2014][A-Za-zÁÉÍÓÚÑ\.]+)?)",
        texto,
    )
    if match:
        numero_doc = match.group(1).strip()
        m_num = re.match(r"(\d+)", numero_doc)
        if m_num:
            numero_solo = m_num.group(1).zfill(3)

    # Armar número completo estilo "OFICIO N° 045-2026-SIGLAS"
    tipo_label_upper = tipo_doc.replace("_", " ").upper()
    numero_completo = f"{tipo_label_upper} N° {numero_doc}" if numero_doc else ""

    org_dict = {}
    if cfg_org:
        org_dict = {
            "nombre_organizacion": cfg_org.nombre_organizacion or "",
            "siglas": cfg_org.siglas or "",
            "ciudad": cfg_org.ciudad or "",
            "anno_oficial": cfg_org.anno_oficial or "",
        }

    contenido = generar_docx_bytes(
        texto=texto,
        config_org=org_dict,
        tipo_doc=tipo_doc,
        numero_doc=numero_completo,
    )

    # Nombre estándar: TIPO_SIGLAS_CORRELATIVO_FECHA.docx
    siglas = (cfg_org.siglas if cfg_org else "") or "DOC"
    nombre_archivo = construir_nombre_archivo(
        tipo_doc=tipo_doc,
        siglas=siglas,
        numero_correlativo=numero_solo or numero_doc,
        ext="docx",
        fecha=creado,
    )

    mime_docx = (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    )
    if docx_disponible():
        return Response(
            content=contenido,
            media_type=mime_docx,
            headers={
                "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
                "Content-Type": mime_docx,
                "X-Content-Type-Options": "nosniff",
            },
        )
    # Fallback: texto plano si python-docx no está disponible
    return Response(
        content=contenido,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}.txt"',
            "Content-Type": "text/plain; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ─── Revisión por token público ───
@router.post("/documento/{doc_id}/compartir")
async def documento_compartir(doc_id: int, request: Request):
    """Genera un token único y devuelve el link público de revisión."""
    import secrets
    usuario = _require_user(request)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    correo_revisor = (data.get("correo_revisor") or "").strip().lower()
    mensaje_envio = (data.get("mensaje_envio") or "").strip()[:1000]

    if "@" not in correo_revisor or "." not in correo_revisor:
        return JSONResponse(
            {"ok": False, "error": "Correo inválido"}, status_code=400
        )

    db = _db()
    try:
        doc = db.query(DocumentoSecretaria).filter(
            DocumentoSecretaria.id == doc_id,
            DocumentoSecretaria.secretaria_id == usuario.id,
        ).first()
        if not doc:
            raise HTTPException(404, "Documento no encontrado")

        token = secrets.token_urlsafe(32)[:64]
        rev = DocumentoRevision(
            documento_id=doc.id,
            token=token,
            correo_revisor=correo_revisor,
            mensaje_envio=mensaje_envio,
            estado="pendiente",
        )
        db.add(rev)
        db.commit()
        db.refresh(rev)
    finally:
        db.close()

    base_url = os.environ.get("BASE_URL", "https://colegiospro.org.pe").rstrip("/")
    link = f"{base_url}/ver/{token}"

    # Envío por correo (best-effort usando email_engine si está configurado)
    try:
        _enviar_correo_revision(correo_revisor, link, mensaje_envio, usuario.nombre)
    except Exception:
        pass

    return JSONResponse({
        "ok": True,
        "token": token,
        "link": link,
        "revision_id": rev.id,
    })


def _enviar_correo_revision(correo: str, link: str, mensaje: str, remitente: str):
    """Envío best-effort de un correo de revisión.
    Si no hay SMTP configurado, lo omite silenciosamente."""
    import smtplib
    from email.mime.text import MIMEText

    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    if not (host and user and pwd):
        return

    cuerpo = f"""Hola,

{remitente or 'Una secretaría'} te solicita revisar un documento.

{mensaje}

Puedes verlo y responder aquí (no requiere cuenta):
{link}

— SecretariaPro · ColegiosPro
"""
    msg = MIMEText(cuerpo, "plain", "utf-8")
    msg["Subject"] = "Solicitud de revisión de documento"
    msg["From"] = user
    msg["To"] = correo

    puerto = int(os.environ.get("SMTP_PORT", 587))
    with smtplib.SMTP(host, puerto, timeout=15) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(user, [correo], msg.as_string())


@router.get("/revisiones", response_class=HTMLResponse)
async def revisiones_lista(request: Request):
    usuario = _user_or_redirect(request)
    if not usuario:
        return RedirectResponse("/secretaria/login", status_code=302)
    db = _db()
    try:
        revisiones = (
            db.query(DocumentoRevision, DocumentoSecretaria)
            .join(
                DocumentoSecretaria,
                DocumentoRevision.documento_id == DocumentoSecretaria.id,
            )
            .filter(DocumentoSecretaria.secretaria_id == usuario.id)
            .order_by(DocumentoRevision.creado_en.desc())
            .limit(200)
            .all()
        )
    finally:
        db.close()
    base_url = os.environ.get("BASE_URL", "https://colegiospro.org.pe").rstrip("/")
    return templates.TemplateResponse(
        request,
        "secretaria/revisiones.html",
        _ctx(
            usuario=usuario,
            modo_actual="revisiones",
            revisiones=revisiones,
            base_url=base_url,
        ),
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
    sexo: Optional[str] = Form("M"),
    es_default: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    db = _db()
    try:
        marcar_default = bool(es_default)
        if marcar_default:
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
            sexo=(sexo or "M").strip()[:1].upper(),
            institucion=(institucion or "").strip() or None,
            ciudad=(ciudad or "Iquitos").strip(),
            es_default=marcar_default,
        )
        db.add(p)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/remitentes", status_code=302)


@router.get("/remitentes/modal-nuevo", response_class=HTMLResponse)
async def remitentes_modal_nuevo(request: Request):
    """HTML del modal para agregar un remitente (cargado vía HTMX)."""
    _ = _require_user(request)
    return templates.TemplateResponse(
        request,
        "secretaria/_modal_nuevo_remitente.html",
        {},
    )


@router.get("/destinatarios/modal-nuevo", response_class=HTMLResponse)
async def destinatarios_modal_nuevo(request: Request):
    """HTML del modal para agregar un destinatario (cargado vía HTMX)."""
    _ = _require_user(request)
    return templates.TemplateResponse(
        request,
        "secretaria/_modal_nuevo_destinatario.html",
        {},
    )


@router.post("/api/remitentes/crear")
async def api_remitentes_crear(request: Request):
    """Crea un perfil de remitente desde un modal AJAX y devuelve JSON."""
    usuario = _require_user(request)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not data:
        form = await request.form()
        data = dict(form)

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return JSONResponse({"ok": False, "error": "El nombre es obligatorio"}, status_code=400)

    db = _db()
    try:
        p = PerfilRemitente(
            secretaria_id=usuario.id,
            colegio_id=usuario.colegio_id,
            nombre=nombre,
            cargo=(data.get("cargo") or "").strip() or None,
            tratamiento=(data.get("tratamiento") or "").strip() or "",
            sexo=((data.get("sexo") or "M").strip()[:1].upper() or "M"),
            institucion=(data.get("institucion") or "").strip() or None,
            ciudad=(data.get("ciudad") or "Iquitos").strip(),
            es_default=False,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        payload = {
            "ok": True,
            "perfil": {
                "id": p.id,
                "nombre": p.nombre,
                "cargo": p.cargo or "",
                "tratamiento": p.tratamiento or "",
            },
        }
    finally:
        db.close()
    return JSONResponse(payload)


@router.post("/api/destinatarios/crear")
async def api_destinatarios_crear(request: Request):
    """Crea una institución del directorio desde un modal AJAX y devuelve JSON."""
    usuario = _require_user(request)
    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    if not data:
        form = await request.form()
        data = dict(form)

    nombre_inst = (data.get("nombre_institucion") or "").strip()
    if not nombre_inst:
        return JSONResponse({"ok": False, "error": "El nombre de la institución es obligatorio"}, status_code=400)

    db = _db()
    try:
        inst = DirectorioInstitucional(
            nombre_institucion=nombre_inst,
            ruc=(data.get("ruc") or "").strip() or None,
            tipo=(data.get("tipo") or "").strip() or None,
            ciudad=(data.get("ciudad") or "").strip() or None,
            titular_nombre=(data.get("titular_nombre") or "").strip() or None,
            titular_cargo=(data.get("titular_cargo") or "").strip() or None,
            titular_tratamiento=(data.get("titular_tratamiento") or "").strip() or None,
            correo=(data.get("correo") or "").strip() or None,
            telefono=(data.get("telefono") or "").strip() or None,
            direccion=(data.get("direccion") or "").strip() or None,
            registrado_por_colegio_id=usuario.colegio_id,
            pendiente_revision=True,
            validado=False,
        )
        db.add(inst)
        db.commit()
        db.refresh(inst)
        payload = {
            "ok": True,
            "institucion": {
                "id": inst.id,
                "nombre_institucion": inst.nombre_institucion,
                "titular_nombre": inst.titular_nombre or "",
                "titular_cargo": inst.titular_cargo or "",
            },
        }
    finally:
        db.close()
    return JSONResponse(payload)


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
        config_org = _get_config_org(db, usuario.id)
    finally:
        db.close()
    anno_actual = datetime.now(timezone.utc).year
    flags = _context_flags(usuario)
    prefs_redaccion = (config_org.preferencias_redaccion or {}) if config_org else {}
    return templates.TemplateResponse(
        request,
        "secretaria/configuracion.html",
        _ctx(
            usuario=usuario,
            modo_actual="configuracion",
            prefs=prefs,
            colegio_cfg=cfg,
            config_org=config_org,
            anno_actual=anno_actual,
            negativos_opcionales=NEGATIVOS_OPCIONALES,
            prefs_redaccion=prefs_redaccion,
            **flags,
        ),
    )


@router.post("/configuracion/redaccion")
async def configuracion_redaccion(request: Request):
    """Guardar preferencias de redacción (prompts negativos opcionales)."""
    usuario = _require_user(request)
    form = await request.form()
    prefs = {}
    for key in NEGATIVOS_OPCIONALES:
        prefs[key] = bool(form.get(f"pref_{key}"))
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        cfg.preferencias_redaccion = prefs
        cfg.actualizado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


@router.post("/configuracion/organizacion")
async def configuracion_organizacion(
    request: Request,
    nombre_organizacion: Optional[str] = Form(None),
    siglas: Optional[str] = Form(None),
    anno_oficial: Optional[str] = Form(None),
    ciudad_org: Optional[str] = Form(None),
):
    usuario = _require_user(request)
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        if nombre_organizacion is not None:
            cfg.nombre_organizacion = nombre_organizacion.strip() or None
        if siglas is not None:
            cfg.siglas = siglas.strip() or None
        if anno_oficial is not None:
            cfg.anno_oficial = anno_oficial.strip() or None
            cfg.anno_numero = datetime.now(timezone.utc).year
        if ciudad_org is not None:
            cfg.ciudad = ciudad_org.strip() or "Iquitos"
        cfg.actualizado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return RedirectResponse("/secretaria/configuracion", status_code=302)


@router.post("/configuracion/marca-agua")
async def configuracion_marca_agua(
    request: Request,
    marca_agua_activa: Optional[str] = Form(None),
    marca_agua_texto: Optional[str] = Form(None),
    marca_agua_tamano: Optional[int] = Form(48),
    marca_agua_opacidad: Optional[float] = Form(0.08),
    marca_agua_angulo: Optional[int] = Form(45),
    marca_agua_color: Optional[str] = Form("gris"),
):
    usuario = _require_user(request)
    db = _db()
    try:
        cfg = _get_o_crear_config_org(db, usuario.id, usuario.colegio_id)
        cfg.marca_agua_activa = bool(marca_agua_activa)
        cfg.marca_agua_texto = (marca_agua_texto or "").strip()[:80]
        try:
            cfg.marca_agua_tamano = max(10, min(200, int(marca_agua_tamano or 48)))
        except (TypeError, ValueError):
            cfg.marca_agua_tamano = 48
        try:
            op = float(marca_agua_opacidad or 0.08)
        except (TypeError, ValueError):
            op = 0.08
        cfg.marca_agua_opacidad = max(0.0, min(1.0, op))
        try:
            cfg.marca_agua_angulo = int(marca_agua_angulo or 45) % 360
        except (TypeError, ValueError):
            cfg.marca_agua_angulo = 45
        color = (marca_agua_color or "gris").strip().lower()
        if color not in {"gris", "azul", "rojo", "verde", "negro"}:
            color = "gris"
        cfg.marca_agua_color = color
        cfg.actualizado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()
    return JSONResponse({"ok": True})


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
