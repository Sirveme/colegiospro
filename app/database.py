from sqlalchemy import create_engine, Column, Integer, String, DateTime
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


# Crear tablas
Base.metadata.create_all(engine)