"""
TenantDBResolver — resolves tenant_id → per-tenant Postgres DB session.

Architecture:
  - The CONTROL PLANE DB (settings.database_url) stores tenant_db_connections.
  - Each tenant has its own Postgres database (completely separate).
  - Tenant DB tables have NO tenant_id columns; the DB boundary is the tenant boundary.

Workflow per request:
  1. Look up tenant_db_connections.db_url from the control plane DB.
  2. Create (and process-level cache) an AsyncEngine for that tenant's DB.
  3. Yield an AsyncSession via get_tenant_db_session(tenant_id).

Cache:
  Engines are cached by str(tenant_id) for the lifetime of the process.
  Call evict_tenant_engine(tenant_id) if connection info changes.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.db import AsyncSessionLocal  # control plane sessions

logger = logging.getLogger(__name__)

# Process-level engine cache: str(tenant_id) → AsyncEngine
_tenant_engines: dict[str, AsyncEngine] = {}


async def _resolve_tenant_db_url(tenant_id: uuid.UUID) -> str:
    """
    Fetch the tenant's DB URL from the control plane tenant_db_connections table.
    Raises RuntimeError if no record exists — run seed_content_tenants.py first.
    """
    logger.debug(
        "  [TenantDB] Resolving DB connection for tenant=%s ...", tenant_id
    )
    try:
        async with AsyncSessionLocal() as db:
            row = await db.execute(
                text(
                    "SELECT db_url, region FROM tenant_db_connections WHERE tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
            conn = row.fetchone()
    except ProgrammingError as exc:
        # Common setup issue: migrations applied to a different DB than settings.database_url.
        raise RuntimeError(
            "Control plane schema is missing table 'tenant_db_connections'. "
            "Apply control-plane migration 003 to the SAME database used by "
            "settings.database_url, then restart services.\n"
            "Expected command:\n"
            "  psql $DATABASE_URL -f db/migrations/003_tenant_db_connections.sql"
        ) from exc

    if not conn:
        raise RuntimeError(
            f"No tenant_db_connections record found for tenant_id={tenant_id}. "
            "Run: uv run python scripts/seed_content_tenants.py"
        )

    db_name = conn.db_url.split("/")[-1]
    logger.info(
        "  [TenantDB] Resolved → db=%s  region=%s  tenant=%s",
        db_name,
        conn.region,
        tenant_id,
    )
    return conn.db_url


async def get_tenant_engine(tenant_id: uuid.UUID) -> AsyncEngine:
    """
    Return a cached AsyncEngine for the tenant's DB.
    Creates a new pool on first call; reuses it on subsequent calls.
    """
    key = str(tenant_id)
    if key not in _tenant_engines:
        url = await _resolve_tenant_db_url(tenant_id)
        engine = create_async_engine(
            url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _tenant_engines[key] = engine
        logger.info(
            "  [TenantDB] Created connection pool for tenant=%s", tenant_id
        )
    return _tenant_engines[key]


def evict_tenant_engine(tenant_id: uuid.UUID) -> None:
    """
    Dispose and remove the cached engine for a tenant.
    Use after rotating DB credentials or changing connection info.
    """
    key = str(tenant_id)
    engine = _tenant_engines.pop(key, None)
    if engine:
        logger.info(
            "  [TenantDB] Evicted + disposed engine pool for tenant=%s", tenant_id
        )


@asynccontextmanager
async def get_tenant_db_session(
    tenant_id: uuid.UUID,
) -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager: yield a SQLAlchemy session bound to the tenant's own DB.

    Usage:
        async with get_tenant_db_session(tenant_id) as db:
            rows = await db.execute(text("SELECT * FROM tenant_sources"))
            await db.commit()
    """
    engine = await get_tenant_engine(tenant_id)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )
    async with session_factory() as session:
        yield session
