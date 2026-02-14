# ══════════════════════════════════════════════════════════
# app/database.py — ColegiosPro
# PostgreSQL (Railway) con fallback a SQLite local
# ══════════════════════════════════════════════════════════

import os
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

# ─── DATABASE URL ───
# Railway inyecta DATABASE_URL automáticamente al agregar PostgreSQL plugin
# Formato Railway: postgres://user:pass@host:port/db
# SQLAlchemy necesita: postgresql://user:pass@host:port/db

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///leads.db")

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# ─── ENGINE ───
# PostgreSQL: pool_pre_ping evita conexiones muertas (mismo fix que facturalo.pro)
if DATABASE_URL.startswith("postgresql://"):
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
else:
    # SQLite para desarrollo local
    engine = create_engine(DATABASE_URL, echo=False)

SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# ─── MODELS ───

class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    colegio = Column(String(200), nullable=False)
    region = Column(String(100))
    cantidad = Column(String(50))
    decano_wsp = Column(String(50))
    admin_wsp = Column(String(50))
    tesoreria_wsp = Column(String(50))
    secretaria_wsp = Column(String(50))
    ip = Column(String(50))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Visit(Base):
    """Tracks visitor actions on the landing page"""
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ref = Column(String(100), index=True)
    nombre = Column(String(200))
    cargo = Column(String(100))
    action = Column(String(50), nullable=False, index=True)
    ip = Column(String(50))
    user_agent = Column(String(500))
    referrer = Column(String(500))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


class ChatMessage(Base):
    """Chat messages between visitors and admin"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(100), index=True, nullable=False)
    ref = Column(String(100), index=True)
    nombre = Column(String(200))
    cargo = Column(String(100))
    sender = Column(String(20), nullable=False)  # "visitor" or "admin"
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)


# ─── CREATE TABLES ───
Base.metadata.create_all(engine)