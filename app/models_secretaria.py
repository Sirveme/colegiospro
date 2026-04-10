# ══════════════════════════════════════════════════════════
# app/models_secretaria.py — SecretariaPro
# Modelos de base de datos para el módulo SecretariaPro
# Reutiliza Base / engine de app.database
# ══════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Boolean, ForeignKey
)

from app.database import Base, engine


def _utcnow():
    return datetime.now(timezone.utc)


def _expira_en_un_anio():
    return datetime.now(timezone.utc) + timedelta(days=365)


# ─── usuarios_secretaria ───
class UsuarioSecretaria(Base):
    __tablename__ = "usuarios_secretaria"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nombre = Column(String(100), nullable=False)
    correo = Column(String(150), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)  # Argon2
    colegio_id = Column(Integer, nullable=True)  # FK lógica a tabla colegios (futura)
    rol = Column(String(20), default="secretaria")  # secretaria / directivo / decano
    notif_push_decano = Column(Boolean, default=False)
    correo_verificado = Column(Boolean, default=False)
    token_verificacion = Column(String(100), nullable=True, index=True)
    activo = Column(Boolean, default=True)
    creado_en = Column(DateTime, default=_utcnow)


# ─── directorio_institucional ───
class DirectorioInstitucional(Base):
    __tablename__ = "directorio_institucional"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ruc = Column(String(20), index=True, nullable=True)
    nombre_institucion = Column(String(200), nullable=False, index=True)
    tipo = Column(String(50))  # municipalidad/gr/osce/contraloria/universidad/empresa/otro
    region = Column(String(100))
    ciudad = Column(String(100))
    titular_nombre = Column(String(150))
    titular_cargo = Column(String(100))
    titular_tratamiento = Column(String(30))  # Sr. / Dr. / Mg. / CPC / Abog.
    correo = Column(String(150))
    telefono = Column(String(20))
    direccion = Column(String(255))
    validado = Column(Boolean, default=False)
    pendiente_revision = Column(Boolean, default=False)
    registrado_por_colegio_id = Column(Integer, nullable=True)
    actualizado_en = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ─── documentos_secretaria ───
class DocumentoSecretaria(Base):
    __tablename__ = "documentos_secretaria"

    id = Column(Integer, primary_key=True, autoincrement=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"), index=True)
    colegio_id = Column(Integer, nullable=True)
    modo = Column(String(30))  # redactor / corrector / comunicado / post_red
    texto_entrada = Column(Text)
    texto_salida = Column(Text)
    tono = Column(String(20))  # formal / cordial / protocolar
    institucion_destino_id = Column(
        Integer, ForeignKey("directorio_institucional.id"), nullable=True
    )
    formato_salida = Column(String(10))  # pdf / docx / png / txt
    enviado_correo = Column(Boolean, default=False)
    notif_decano_enviada = Column(Boolean, default=False)
    guardado = Column(Boolean, default=False)
    expira_en = Column(DateTime, default=_expira_en_un_anio)
    creado_en = Column(DateTime, default=_utcnow, index=True)


# ─── imagenes_generadas ───
class ImagenGenerada(Base):
    __tablename__ = "imagenes_generadas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    colegio_id = Column(Integer, nullable=True)
    prompt_usado = Column(Text)
    url_resultado = Column(String(500))
    red_social_destino = Column(String(30))  # facebook / whatsapp / general
    api_propia = Column(Boolean, default=False)
    creado_en = Column(DateTime, default=_utcnow)


# ─── config_secretaria_colegio ───
class ConfigSecretariaColegio(Base):
    __tablename__ = "config_secretaria_colegio"

    colegio_id = Column(Integer, primary_key=True)
    api_key_openai_enc = Column(String(500))  # Fernet
    imagenes_gratis_usadas = Column(Integer, default=0)
    imagenes_gratis_limite = Column(Integer, default=10)
    notif_decano_automatica = Column(Boolean, default=False)
    smtp_host = Column(String(150))
    smtp_puerto = Column(Integer, default=587)
    smtp_usuario = Column(String(150))
    smtp_pass_enc = Column(String(500))  # Fernet
    membrete_url = Column(String(500))
    firma_decano_url = Column(String(500))
    nombre_colegio = Column(String(200))
    nombre_decano = Column(String(150))
    ciudad = Column(String(100))


# ─── CREATE TABLES ───
# create_all es idempotente: solo crea las tablas que faltan.
# Las tablas existentes (leads, visits, chat_messages) no se tocan.
Base.metadata.create_all(engine)


# ─── MIGRACIONES LIGERAS (sin Alembic) ───
# Para tablas que ya existen en Railway de despliegues previos, agregamos
# columnas nuevas con ALTER TABLE. Es idempotente: si la columna ya existe,
# se ignora el error silenciosamente.
def _migrar_columnas():
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if not insp.has_table("directorio_institucional"):
        return

    cols_existentes = {c["name"] for c in insp.get_columns("directorio_institucional")}
    if "ruc" not in cols_existentes:
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE directorio_institucional ADD COLUMN ruc VARCHAR(20)"
                ))
        except Exception:
            pass  # Otra instancia ya hizo la migración


_migrar_columnas()
