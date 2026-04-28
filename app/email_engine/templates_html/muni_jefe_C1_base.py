"""Plantilla Jefe / Funcionario de Municipalidad — versión base."""
from pathlib import Path

MUNI_JEFE_C1_BASE = (
    Path(__file__).parent / "muni" / "muni_jefe_C1_base.html"
).read_text(encoding="utf-8")
