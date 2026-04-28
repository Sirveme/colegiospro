"""Plantilla Secretaria de Municipalidad — variante MiSecretaria.pro."""
from pathlib import Path

MUNI_SEC_A4_MISECRETARIA = (
    Path(__file__).parent / "muni" / "muni_sec_A4_misecretaria.html"
).read_text(encoding="utf-8")
