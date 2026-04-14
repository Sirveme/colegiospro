"""Plantilla Alcalde / Directivo de Municipalidad — versión base."""
from pathlib import Path

MUNI_ALC_B1_BASE = (
    Path(__file__).parent / "muni" / "muni_alc_B1_base.html"
).read_text(encoding="utf-8")
