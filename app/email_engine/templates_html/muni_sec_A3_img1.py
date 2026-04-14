"""Plantilla Secretaria — versión con imagen vertical (mockup)."""
from pathlib import Path

MUNI_SEC_A3_IMG1 = (
    Path(__file__).parent / "muni" / "muni_sec_A3_img1.html"
).read_text(encoding="utf-8")
