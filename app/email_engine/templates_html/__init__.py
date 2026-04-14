"""Catálogo de plantillas HTML del Email Engine.

Cada plantilla vive como archivo .html en la subcarpeta del sector
(muni/, educ/, salud/, seguimiento/) y se expone como string vía un
módulo .py homónimo en este paquete.

Para agregar una plantilla nueva:
  1. Crear el .html en la subcarpeta del sector.
  2. Crear el wrapper .py que la lea (ver muni_sec_A1_base.py).
  3. Agregar la entrada al diccionario PLANTILLAS de abajo.
"""
from .muni_sec_A1_base import MUNI_SEC_A1_BASE
from .muni_sec_A2_botones import MUNI_SEC_A2_BOTONES
from .muni_sec_A3_img1 import MUNI_SEC_A3_IMG1
from .muni_alc_B1_base import MUNI_ALC_B1_BASE


PLANTILLAS = {
    "muni_sec_A1_base": {
        "html": MUNI_SEC_A1_BASE,
        "label": "Municipalidades · Secretaria · Base",
        "asunto_default": "Para la secretaria de {{ municipalidad }}: redacta oficios en 15 seg",
    },
    "muni_sec_A2_botones": {
        "html": MUNI_SEC_A2_BOTONES,
        "label": "Municipalidades · Secretaria · Botones grandes",
        "asunto_default": "Redacta cartas, memos y oficios en 10 segundos — {{ municipalidad }}",
    },
    "muni_sec_A3_img1": {
        "html": MUNI_SEC_A3_IMG1,
        "label": "Municipalidades · Secretaria · Imagen vertical",
        "asunto_default": "Mira lo que SecretariaPro hace en su pantalla — {{ municipalidad }}",
    },
    "muni_alc_B1_base": {
        "html": MUNI_ALC_B1_BASE,
        "label": "Municipalidades · Alcalde · Base",
        "asunto_default": "Para la autoridad de {{ municipalidad }}: comunicación institucional a su altura",
    },
}


__all__ = [
    "PLANTILLAS",
    "MUNI_SEC_A1_BASE",
    "MUNI_SEC_A2_BOTONES",
    "MUNI_SEC_A3_IMG1",
    "MUNI_ALC_B1_BASE",
]
