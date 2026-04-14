"""Plantilla Secretaria — versión con botones grandes y CTA destacado."""
from pathlib import Path

MUNI_SEC_A2_BOTONES = (
    Path(__file__).parent / "muni" / "muni_sec_A2_botones.html"
).read_text(encoding="utf-8")
