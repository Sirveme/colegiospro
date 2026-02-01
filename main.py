import os
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="ColegiosPro",
    description="Plataforma Digital para Colegios Profesionales del Perú",
    version="1.0.0",
)

# ── Static files ──
app.mount("/static", StaticFiles(directory="static"), name="static")

# ── Templates ──
templates = Jinja2Templates(directory="app/templates")


# ── Routes ──

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("home.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok", "app": "colegiospro"}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)