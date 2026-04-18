# ══════════════════════════════════════════════════════════
# app/models_secretaria.py — SecretariaPro
# Modelos de base de datos para el módulo SecretariaPro
# Reutiliza Base / engine de app.database
# ══════════════════════════════════════════════════════════

from datetime import datetime, timezone, timedelta
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, Boolean, ForeignKey,
    UniqueConstraint, JSON, Float
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


# ─── perfiles_remitente ───
# Perfiles de firmante que la secretaria puede elegir al generar un documento.
# Permite tener varias firmas (Decano, Subdecano, Director Académico, etc.).
class PerfilRemitente(Base):
    __tablename__ = "perfiles_remitente"

    id = Column(Integer, primary_key=True, autoincrement=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"), index=True)
    colegio_id = Column(Integer, nullable=True)
    nombre = Column(String(150), nullable=False)
    cargo = Column(String(100))
    tratamiento = Column(String(30), default="")  # Dr. / Mg. / CPC / Abog.
    sexo = Column(String(1), default="M")  # M / F
    institucion = Column(String(200))
    ciudad = Column(String(100), default="Iquitos")
    es_default = Column(Boolean, default=False)
    creado_en = Column(DateTime, default=_utcnow)


# ─── preferencias_secretaria ───
# Preferencias de UI y defaults del Redactor por usuario.
class PreferenciasSecretaria(Base):
    __tablename__ = "preferencias_secretaria"

    secretaria_id = Column(
        Integer, ForeignKey("usuarios_secretaria.id"), primary_key=True
    )
    tema = Column(String(20), default="claro")  # claro/oscuro/pastel/elegante
    fuente_size = Column(String(10), default="normal")  # pequeno/normal/grande/xl
    tipo_doc_default = Column(String(20), default="carta")
    tono_default = Column(String(20), default="formal")
    actualizado_en = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ─── directorio_contacto_extendido ───
# Datos privados de cada colegio sobre sus contactos en el directorio.
# NO se comparten entre colegios. PK compuesta (colegio_id, institucion_id).
class DirectorioContactoExtendido(Base):
    __tablename__ = "directorio_contacto_extendido"

    colegio_id = Column(Integer, primary_key=True)
    institucion_id = Column(
        Integer, ForeignKey("directorio_institucional.id"), primary_key=True
    )
    foto_url = Column(String(500))
    whatsapp = Column(String(30))
    red_social = Column(String(200))
    nombre_secretaria = Column(String(150))
    fecha_inicio_cargo = Column(String(20))  # ISO date string para flexibilidad
    notas_relacionamiento = Column(Text)
    actualizado_en = Column(DateTime, default=_utcnow, onupdate=_utcnow)


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


# ─── config_organizacion ───
# Configuración de la organización — se llena en onboarding
class ConfigOrganizacion(Base):
    __tablename__ = "config_organizacion"

    id = Column(Integer, primary_key=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    colegio_id = Column(Integer, nullable=True)
    nombre_organizacion = Column(String(200))
    siglas = Column(String(20))           # CCPL, UGEL-LOR, MPM
    ciudad = Column(String(100), default="Iquitos")
    sector = Column(String(20), default="privado")  # profesional / publico / privado
    anno_oficial = Column(String(300))    # "Año del Bicentenario..."
    anno_numero = Column(Integer, default=2026)
    tipos_doc_habilitados = Column(JSON)  # ["carta","oficio","circular"]
    preferencias_redaccion = Column(JSON)  # {"sin_relleno": true, ...}
    # Marca de agua en PDF
    marca_agua_activa = Column(Boolean, default=True)
    marca_agua_texto = Column(String(80), default="")   # "" => usa nombre_organizacion
    marca_agua_tamano = Column(Integer, default=48)     # puntos
    marca_agua_opacidad = Column(Float, default=0.08)   # 0.0 - 1.0
    marca_agua_angulo = Column(Integer, default=45)     # grados
    marca_agua_color = Column(String(10), default="gris")  # gris/azul/rojo/verde/negro
    onboarding_completo = Column(Boolean, default=False)
    actualizado_en = Column(DateTime, default=_utcnow)


# ─── correlatividad_documento ───
# Numeración correlativa por tipo de documento y año
class CorrelatividadDocumento(Base):
    __tablename__ = "correlatividad_documento"

    id = Column(Integer, primary_key=True)
    colegio_id = Column(Integer, nullable=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    tipo_documento = Column(String(30))
    anno = Column(Integer)
    ultimo_numero = Column(Integer, default=0)
    __table_args__ = (
        UniqueConstraint("secretaria_id", "tipo_documento", "anno"),
    )


# ─── agenda_eventos ───
# Eventos de la agenda inteligente del titular/decano
class AgendaEvento(Base):
    __tablename__ = "agenda_eventos"

    id = Column(Integer, primary_key=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    colegio_id = Column(Integer, nullable=True)
    titulo = Column(String(200), nullable=False)
    descripcion = Column(Text, default="")
    fecha_inicio = Column(DateTime, nullable=False)
    fecha_fin = Column(DateTime, nullable=False)
    tipo = Column(String(20), default="reunion")
    # reunion / bloque_enfoque / tarea / recordatorio / almuerzo
    lugar = Column(String(200), default="")
    modalidad = Column(String(15), default="presencial")
    # presencial / virtual / telefono
    participantes = Column(JSON, default=list)
    # [{"nombre": "CPC Santana", "cargo": "Decano", "confirmado": True}]
    documento_id = Column(
        Integer, ForeignKey("documentos_secretaria.id"), nullable=True
    )
    archivo_adjunto_url = Column(String(500), default="")
    archivo_adjunto_nombre = Column(String(200), default="")
    google_event_id = Column(String(200), default="")
    color = Column(String(10), default="#0D7A60")
    estado = Column(String(15), default="confirmado")
    # confirmado / tentativo / cancelado
    buffer_antes = Column(Integer, default=15)  # minutos
    alerta_minutos = Column(Integer, default=30)
    notif_enviada = Column(Boolean, default=False)
    sugerencia_ia = Column(Text, default="")
    creado_en = Column(DateTime, default=_utcnow)
    actualizado_en = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ─── agenda_accesos ───
# Control de quién ve la agenda de quién
class AgendaAcceso(Base):
    __tablename__ = "agenda_accesos"

    id = Column(Integer, primary_key=True)
    propietario_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    autorizado_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    nivel = Column(String(10), default="lectura")  # lectura / edicion
    activo = Column(Boolean, default=True)
    creado_en = Column(DateTime, default=_utcnow)


# ─── agenda_config ───
class AgendaConfig(Base):
    __tablename__ = "agenda_config"

    secretaria_id = Column(
        Integer, ForeignKey("usuarios_secretaria.id"), primary_key=True
    )
    google_calendar_id = Column(String(200), default="")
    google_refresh_token_enc = Column(String(500), default="")
    hora_inicio = Column(String(5), default="08:00")
    hora_fin = Column(String(5), default="17:00")
    duracion_bloque_enfoque = Column(Integer, default=90)
    dias_laborales = Column(JSON, default=lambda: [1, 2, 3, 4, 5])
    buffer_default = Column(Integer, default=15)
    notif_jefe_activa = Column(Boolean, default=True)
    notif_minutos_antes = Column(Integer, default=30)
    actualizado_en = Column(DateTime, default=_utcnow, onupdate=_utcnow)


# ─── transcripciones_reunion ───
class TranscripcionReunion(Base):
    __tablename__ = "transcripciones_reunion"

    id = Column(Integer, primary_key=True)
    secretaria_id = Column(Integer, ForeignKey("usuarios_secretaria.id"))
    colegio_id = Column(Integer, nullable=True)
    agenda_evento_id = Column(
        Integer, ForeignKey("agenda_eventos.id"), nullable=True
    )
    titulo = Column(String(200), nullable=False)
    audio_nombre = Column(String(300), default="")
    audio_duracion_seg = Column(Integer, default=0)
    tramos_excluidos = Column(JSON, default=list)
    texto_transcripcion = Column(Text, default="")
    texto_editado = Column(Text, default="")
    documento_generado_id = Column(
        Integer, ForeignKey("documentos_secretaria.id"), nullable=True
    )
    tipo_documento_generado = Column(String(20), default="")
    # acta / resumen / acuerdos / informe
    estado = Column(String(20), default="pendiente")
    # pendiente / transcribiendo / listo / error
    creado_en = Column(DateTime, default=_utcnow)


# ─── documento_revisiones ───
# Solicitudes de revisión con link público por token.
class DocumentoRevision(Base):
    __tablename__ = "documento_revisiones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    documento_id = Column(
        Integer, ForeignKey("documentos_secretaria.id"), index=True
    )
    token = Column(String(64), unique=True, index=True)
    correo_revisor = Column(String(200))
    mensaje_envio = Column(Text, default="")
    estado = Column(String(20), default="pendiente")
    # pendiente / aprobado / con_correcciones
    feedback = Column(Text, default="")
    creado_en = Column(DateTime, default=_utcnow)
    respondido_en = Column(DateTime, nullable=True)


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
