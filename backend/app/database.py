"""
Capa de base de datos asíncrona con SQLAlchemy 2.x + asyncpg.
Provee:
  - async engine configurado con pool
  - AsyncSessionLocal como factory de sesiones
  - Base declarativa compartida con los modelos
  - get_db() — dependency de FastAPI (yields AsyncSession)
  - init_db() — crea todas las tablas definidas en los modelos al arrancar
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# ---------------------------------------------------------------------------
# Engine asíncrono
# Pool dimensionado para carga de un sistema de monitoreo LoRaWAN típico.
# ---------------------------------------------------------------------------
engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # Detecta conexiones muertas antes de usarlas
    echo=False,           # Cambia a True temporalmente para depurar SQL
)

# ---------------------------------------------------------------------------
# Factory de sesiones asíncronas
# expire_on_commit=False: evita cargas adicionales tras el commit en async
# ---------------------------------------------------------------------------
AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Base declarativa — todos los modelos ORM la heredan
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Dependency de FastAPI: inyecta una sesión por request
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Genera una sesión AsyncSession por request y garantiza su cierre.
    Uso en endpoint:  db: AsyncSession = Depends(get_db)
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ---------------------------------------------------------------------------
# Creación de tablas al inicio de la aplicación (dev / primer arranque)
# En producción se prefieren migraciones Alembic.
# ---------------------------------------------------------------------------
async def init_db() -> None:
    """
    Crea todas las tablas declaradas en Base.metadata si no existen.
    Se invoca desde el lifespan de FastAPI (main.py).
    No elimina ni modifica tablas existentes (checkfirst=True implícito).
    """
    async with engine.begin() as conn:
        # Importa los modelos para que Base.metadata los registre
        from app import models  # noqa: F401  — importación necesaria para el registro
        await conn.run_sync(Base.metadata.create_all)
