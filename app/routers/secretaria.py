# ══════════════════════════════════════════════════════════
# app/routers/secretaria.py — SecretariaPro
# Rutas bajo el prefijo /secretaria
# ══════════════════════════════════════════════════════════

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Form, Depends, HTTPException
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
from app.services.redactor_service import generar_documento
from app.services.pdf_service import texto_a_pdf_bytes, pdf_disponible


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


def _ctx(request: Request, usuario: Optional[UsuarioSecretaria] = None, **extra):
    base = {"request": request, "usuario": usuario, "modo_actual": None}
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
        "secretaria/dashboard.html",
        _ctx(request, usuario=usuario, modo_actual="dashboard"),
    )


# ─── Auth: login ───
@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, error: Optional[str] = None, ok: Optional[str] = None):
    return templates.TemplateResponse(
        "secretaria/login.html",
        _ctx(request, error=error, ok=ok),
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
        "secretaria/registro.html",
        _ctx(request, error=error),
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
        token = generar_token_verificacion()
        u = UsuarioSecretaria(
            nombre=nombre.strip(),
            correo=correo,
            password_hash=hash_password(password),
            token_verificacion=token,
            correo_verificado=False,
            activo=True,
        )
        db.add(u)
        db.commit()
        db.refresh(u)

        # TODO: enviar correo real con SMTP del colegio.
        # Por ahora se deja la URL de verificación en el redirect para que la
        # secretaria la pueda usar manualmente en MVP.
        verif_url = f"/secretaria/verificar/{token}"
        resp = RedirectResponse(
            f"/secretaria/login?ok=Cuenta+creada.+Verifica+tu+correo:+{verif_url}",
            status_code=302,
        )
        # Auto-login para el MVP (la verificación queda como paso opcional).
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
        "secretaria/redactor.html",
        _ctx(
            request,
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
):
    usuario = _user_or_redirect(request)
    if not usuario:
        return HTMLResponse("<p class='error'>Sesión expirada</p>", status_code=401)

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

    texto_salida = generar_documento(
        texto_entrada=texto_entrada.strip(),
        tono=tono,
        destinatario=destinatario,
        remitente=remitente,
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
            tono=tono,
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
        "secretaria/_redactor_resultado.html",
        {
            "request": request,
            "texto_salida": texto_salida,
            "documento_id": doc_id,
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
        "secretaria/historial.html",
        _ctx(request, usuario=usuario, modo_actual="historial", documentos=docs),
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
        "secretaria/directorio.html",
        _ctx(
            request,
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
        "secretaria/directorio_detalle.html",
        _ctx(request, usuario=usuario, modo_actual="directorio", inst=inst),
    )
