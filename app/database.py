# ══════════════════════════════════════════════════════════
# app/database.py — ColegiosPro
# Modelos: Lead, Visit, ChatMessage
# ══════════════════════════════════════════════════════════

from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone

DATABASE_URL = "sqlite:///leads.db"

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, autoincrement=True)
    colegio = Column(String, nullable=False)
    region = Column(String)
    cantidad = Column(String)
    decano_wsp = Column(String)
    admin_wsp = Column(String)
    tesoreria_wsp = Column(String)
    secretaria_wsp = Column(String)
    ip = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Visit(Base):
    """Tracks every visitor action on the landing page"""
    __tablename__ = "visits"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Tracking params from URL
    ref = Column(String, index=True)          # e.g. CIP-LORETO
    nombre = Column(String)                    # Visitor name
    cargo = Column(String)                     # Visitor role
    # Action tracking
    action = Column(String, nullable=False)    # page_view, chat_opened, pwa_installed, message_sent
    # Technical info
    ip = Column(String)
    user_agent = Column(String)
    referrer = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    """Chat messages between visitors and admin (Duil)"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Session identification
    session_id = Column(String, index=True, nullable=False)  # Unique per visitor session
    ref = Column(String, index=True)
    nombre = Column(String)
    cargo = Column(String)
    # Message
    sender = Column(String, nullable=False)    # "visitor" or "admin"
    content = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# Create tables
Base.metadata.create_all(engine)