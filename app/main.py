import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel
from typing import Optional

from app.database import SessionLocal, Lead
from app.routers import verificacion

app = FastAPI(
    title="ColegiosPro",
    description="Plataforma Digital para Colegios Profesionales del Peru",
    version="1.0.0",
)


# -- HTTPS Redirect Middleware --
@app.middleware("http")
async def redirect_to_https(request: Request, call_next):
    # Railway pasa el header x-forwarded-proto
    if request.headers.get("x-forwarded-proto") == "http":
        url = request.url.replace(scheme="https")
        return RedirectResponse(url, status_code=301)
    return await call_next(request)


# -- Static files --
app.mount("/static", StaticFiles(directory="static"), name="static")

# -- Templates --
templates = Jinja2Templates(directory="app/templates")


app.include_router(verificacion.router)

# -- Schemas --

class ContactForm(BaseModel):
    colegio: str
    region: Optional[str] = None
    cantidad: Optional[str] = None
    decano: Optional[str] = None
    admin: Optional[str] = None
    tesoreria: Optional[str] = None
    secretaria: Optional[str] = None


# -- Routes --

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.post("/api/contacto")
async def recibir_contacto(form: ContactForm, request: Request):
    db = SessionLocal()
    try:
        lead = Lead(
            colegio=form.colegio,
            region=form.region,
            cantidad=form.cantidad,
            decano_wsp=form.decano,
            admin_wsp=form.admin,
            tesoreria_wsp=form.tesoreria,
            secretaria_wsp=form.secretaria,
            ip=request.client.host,
        )
        db.add(lead)
        db.commit()
        return {"status": "ok", "message": "Solicitud recibida"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        db.close()


@app.get("/api/leads")
async def ver_leads():
    db = SessionLocal()
    try:
        leads = db.query(Lead).order_by(Lead.created_at.desc()).all()
        return [
            {
                "id": l.id,
                "colegio": l.colegio,
                "region": l.region,
                "cantidad": l.cantidad,
                "decano": l.decano_wsp,
                "admin": l.admin_wsp,
                "tesoreria": l.tesoreria_wsp,
                "secretaria": l.secretaria_wsp,
                "fecha": l.created_at.isoformat() if l.created_at else None,
            }
            for l in leads
        ]
    finally:
        db.close()


@app.get("/health")
async def health():
    return {"status": "ok", "app": "colegiospro"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)