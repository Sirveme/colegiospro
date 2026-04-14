"""Plantilla base para Secretaria de Municipalidad — versión narrativa.

El HTML vive en muni/muni_sec_A1_base.html (editable como archivo HTML
estándar) y se carga aquí como string en MUNI_SEC_A1_BASE.
"""
from pathlib import Path

MUNI_SEC_A1_BASE = (
    Path(__file__).parent / "muni" / "muni_sec_A1_base.html"
).read_text(encoding="utf-8")
