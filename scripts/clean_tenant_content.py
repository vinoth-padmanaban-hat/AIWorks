#!/usr/bin/env python3
"""
Delete ingested content and execution history for a tenant DB while keeping
sources, tags, products, and format templates (so you can re-run ingestion).

Usage:
  uv run python scripts/clean_tenant_content.py --tenant-index 1
  uv run python scripts/clean_tenant_content.py --tenant-id 00000000-0000-0000-0000-000000000001

Optional:
  --reset-source-cursors   Clear last_scraped_at / etag / hash so the next crawl
                           revisits pages (full re-fetch).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import AsyncSessionLocal

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

TENANT_INDEX_TO_UUID = {
    1: uuid.UUID("00000000-0000-0000-0000-000000000001"),
    2: uuid.UUID("00000000-0000-0000-0000-000000000002"),
    3: uuid.UUID("00000000-0000-0000-0000-000000000003"),
    4: uuid.UUID("00000000-0000-0000-0000-000000000004"),
}


async def _resolve_db_url(tenant_id: uuid.UUID) -> str:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT db_url FROM tenant_db_connections WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )
        r = row.fetchone()
    if not r:
        raise SystemExit(
            f"No tenant_db_connections row for tenant_id={tenant_id}. "
            "Run scripts/seed_content_tenants.py first."
        )
    return str(r[0])


async def _clean(db_url: str, *, reset_cursors: bool) -> None:
    engine = create_async_engine(db_url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        has_inline = await session.execute(
            text(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'inline_eval_runs'
                )
                """
            )
        )
        inline = bool(has_inline.scalar())

        tables = [
            "newsletter_articles",
            "execution_steps",
            "executions",
            "article_tags",
            "formatted_articles",
            "articles",
            "ingestion_log_entries",
            "ingestion_executions",
        ]
        if inline:
            tables.append("inline_eval_runs")

        stmt = "TRUNCATE TABLE " + ", ".join(tables) + " RESTART IDENTITY CASCADE"
        await session.execute(text(stmt))
        await session.commit()
        logger.info("Truncated: %s", ", ".join(tables))

        if reset_cursors:
            await session.execute(
                text(
                    """
                    UPDATE tenant_sources SET
                        last_scraped_at = NULL,
                        last_etag = NULL,
                        last_content_hash = NULL
                    """
                )
            )
            await session.commit()
            logger.info(
                "Reset crawl cursors on tenant_sources (full re-fetch next run)."
            )

    await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description="Clean tenant content DB for re-run.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--tenant-index",
        type=int,
        choices=(1, 2, 3, 4),
        help="Seed tenant index (1 = NeuralEdge / aiworks_t001)",
    )
    g.add_argument(
        "--tenant-id",
        type=uuid.UUID,
        help="Tenant UUID (control plane tenants.id)",
    )
    p.add_argument(
        "--reset-source-cursors",
        action="store_true",
        help="Clear last_scraped_at / etag / content hash on all sources",
    )
    args = p.parse_args()

    tid = args.tenant_id if args.tenant_id else TENANT_INDEX_TO_UUID[args.tenant_index]
    logger.info("Control plane DB: %s", settings.database_url.split("@")[-1])
    asyncio.run(_run(tid, reset_cursors=args.reset_source_cursors))


async def _run(tenant_id: uuid.UUID, *, reset_cursors: bool) -> None:
    db_url = await _resolve_db_url(tenant_id)
    db_name = db_url.rsplit("/", 1)[-1]
    logger.info("Tenant %s → database %s", tenant_id, db_name)
    await _clean(db_url, reset_cursors=reset_cursors)
    logger.info("Done.")


if __name__ == "__main__":
    main()
