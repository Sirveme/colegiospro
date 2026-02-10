"""
Router: Verificación de Certificados
colegiospro.org.pe
"""

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["Verificación"])
templates = Jinja2Templates(directory="app/templates")

# APIs de colegios registrados
APIS_COLEGIOS = {
    "ccploreto": "https://ccploreto.metraes.com/api/publico"
}


@router.get("/verificar/{codigo}", response_class=HTMLResponse)
async def verificar_certificado(request: Request, codigo: str):
    """Página pública de verificación de certificados"""
    
    certificado = None
    error = None
    
    # Consultar API de ccploreto
    api_url = f"{APIS_COLEGIOS['ccploreto']}/certificado/{codigo}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(api_url)
            data = response.json()
            
            if data.get("encontrado"):
                certificado = data.get("certificado")
                certificado["vigente"] = data.get("vigente", False)
                certificado["mensaje"] = data.get("mensaje", "")
            else:
                error = data.get("mensaje", "Certificado no encontrado")
                
    except httpx.TimeoutException:
        error = "No se pudo conectar con el servidor del colegio"
    except Exception as e:
        error = f"Error al verificar: {str(e)}"
    
    return templates.TemplateResponse(
        "colegiospro/verificacion.html",
        {
            "request": request,
            "codigo": codigo,
            "certificado": certificado,
            "error": error
        }
    )


@router.get("/verificar", response_class=HTMLResponse)
async def pagina_verificar(request: Request):
    """Página para ingresar código manualmente"""
    return templates.TemplateResponse(
        "colegiospro/verificar_form.html",
        {"request": request}
    )