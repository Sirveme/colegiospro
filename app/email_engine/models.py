# ══════════════════════════════════════════════════════════
# app/email_engine/models.py
# Modelos del motor de envío masivo de email con tracking
# ══════════════════════════════════════════════════════════

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, JSON, ForeignKey, UniqueConstraint
)

from app.database import Base, engine


class EmailConfig(Base):
    """Configuración SMTP — múltiples cuentas."""
    __tablename__ = "email_configs"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(50), unique=True)  # "colegiospro" / "perusistemas"
    smtp_host = Column(String(150), default="smtp.gmail.com")
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String(150))
    smtp_pass_enc = Column(String(500))  # Fernet
    from_name = Column(String(100))
    from_email = Column(String(150))
    activo = Column(Boolean, default=True)
    limite_dia = Column(Integer, default=50)
    limite_hora = Column(Integer, default=10)
    enviados_hoy = Column(Integer, default=0)
    enviados_hora_actual = Column(Integer, default=0)
    hora_reset = Column(DateTime)
    dia_reset = Column(DateTime)
    creado_en = Column(DateTime, default=datetime.utcnow)


class EmailCampana(Base):
    """Campaña de email — plantilla + configuración."""
    __tablename__ = "email_campanas"

    id = Column(Integer, primary_key=True)
    nombre = Column(String(100))
    asunto = Column(String(200))
    asunto_template = Column(String(200))
    html_template = Column(Text)
    config_id = Column(Integer, ForeignKey("email_configs.id"))
    segmento = Column(String(30))
    # "A_secretaria" / "B_alcalde" / "seguimiento"
    estado = Column(String(15), default="borrador")
    # borrador / activa / pausada / completada
    total_contactos = Column(Integer, default=0)
    total_enviados = Column(Integer, default=0)
    total_abiertos = Column(Integer, default=0)
    total_clics = Column(Integer, default=0)
    total_descargas = Column(Integer, default=0)
    total_rebotes = Column(Integer, default=0)
    total_bajas = Column(Integer, default=0)
    creado_en = Column(DateTime, default=datetime.utcnow)
    iniciado_en = Column(DateTime)
    completado_en = Column(DateTime)


class EmailContacto(Base):
    """Contacto individual del directorio."""
    __tablename__ = "email_contactos"

    id = Column(Integer, primary_key=True)
    correo = Column(String(200), unique=True)
    nombre = Column(String(150), default="")
    municipalidad = Column(String(200), default="")
    provincia = Column(String(100), default="")
    departamento = Column(String(100), default="")
    alcalde = Column(String(150), default="")
    telefono = Column(String(30), default="")
    whatsapp = Column(String(30), default="")
    tipo_correo = Column(String(50), default="")
    segmento = Column(String(30), default="")
    # "A_secretaria" / "B_alcalde"
    activo = Column(Boolean, default=True)
    baja = Column(Boolean, default=False)
    baja_en = Column(DateTime)
    creado_en = Column(DateTime, default=datetime.utcnow)


class EmailEnvio(Base):
    """Un envío = un correo enviado a un contacto en una campaña."""
    __tablename__ = "email_envios"

    id = Column(Integer, primary_key=True)
    campana_id = Column(Integer, ForeignKey("email_campanas.id"))
    contacto_id = Column(Integer, ForeignKey("email_contactos.id"))
    token = Column(String(64), unique=True)  # uuid4 hex — para tracking
    estado = Column(String(15), default="pendiente")
    # pendiente / enviado / abierto / rebotado / baja
    asunto_final = Column(String(300))
    enviado_en = Column(DateTime)
    abierto_en = Column(DateTime)
    primer_clic_en = Column(DateTime)
    rebote_mensaje = Column(Text, default="")
    intentos = Column(Integer, default=0)
    __table_args__ = (
        UniqueConstraint("campana_id", "contacto_id"),
    )


class EmailEvento(Base):
    """Log de cada interacción — apertura, clic, descarga."""
    __tablename__ = "email_eventos"

    id = Column(Integer, primary_key=True)
    envio_id = Column(Integer, ForeignKey("email_envios.id"))
    tipo = Column(String(20))
    # open / click / pdf_download / bounce / unsubscribe
    url_destino = Column(String(500), default="")
    ip = Column(String(45), default="")
    user_agent = Column(String(300), default="")
    # NOTE: "metadata" es un atributo reservado en SQLAlchemy declarative_base.
    # Usamos "meta" como nombre Python y "metadata" como nombre de columna SQL.
    meta = Column("metadata", JSON, default=dict)
    creado_en = Column(DateTime, default=datetime.utcnow)


class EmailObjecion(Base):
    """Respuestas al formulario de objeciones."""
    __tablename__ = "email_objeciones"

    id = Column(Integer, primary_key=True)
    envio_id = Column(Integer, ForeignKey("email_envios.id"), nullable=True)
    correo = Column(String(200), default="")
    municipalidad = Column(String(200), default="")
    objecion = Column(String(100))
    # "no_autorizacion" / "privacidad" / "no_convence" / "otro"
    que_necesita = Column(String(100))
    # "demo" / "whatsapp" / "prueba_gratis" / "caso_similar"
    comentario = Column(Text, default="")
    creado_en = Column(DateTime, default=datetime.utcnow)


# create_all es idempotente — solo crea lo que no existe
Base.metadata.create_all(engine)
