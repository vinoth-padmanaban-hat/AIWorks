"""
Read-only Admin API for the control plane + per-tenant databases.

Used by the Next.js admin UI (platform view + tenant-scoped view).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.tenant_db import get_tenant_db_session

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Shared helpers ────────────────────────────────────────────────────────────


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    m = row._mapping
    return {k: _json_safe(v) for k, v in dict(m).items()}


def _json_safe(v: Any) -> Any:
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


# ── Platform (control plane) ──────────────────────────────────────────────────


class PlatformSummary(BaseModel):
    tenant_count: int
    skill_count: int
    agent_count: int
    policy_count: int
    persona_count: int


@router.get("/platform/summary", response_model=PlatformSummary)
async def platform_summary(db: AsyncSession = Depends(get_db)) -> PlatformSummary:
    async def _count(table: str) -> int:
        r = await db.execute(text(f"SELECT COUNT(*) AS c FROM {table}"))
        row = r.fetchone()
        return int(row[0]) if row else 0

    try:
        tenants = await _count("tenants")
    except Exception:
        tenants = 0
    skills = await _count("skill_registry")
    agents = await _count("agent_registry")
    try:
        policies = await _count("tenant_policies")
    except Exception:
        policies = 0
    try:
        personas = await _count("personas")
    except Exception:
        personas = 0

    return PlatformSummary(
        tenant_count=tenants,
        skill_count=skills,
        agent_count=agents,
        policy_count=policies,
        persona_count=personas,
    )


@router.get("/platform/tenants")
async def list_tenants(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT t.id, t.display_name, t.domain, t.created_at,
                   c.db_url, c.region, c.updated_at AS connection_updated_at,
                   (SELECT COUNT(*)::int FROM personas p WHERE p.tenant_id = t.id)
                       AS persona_count
            FROM tenants t
            LEFT JOIN tenant_db_connections c ON c.tenant_id = t.id
            ORDER BY t.display_name
            """
        )
    )
    return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/platform/skills")
async def list_skills(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT skill_id, name, description, domain, tags,
                   input_schema, output_schema, active, created_at
            FROM skill_registry
            ORDER BY domain, name
            """
        )
    )
    return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/platform/agents")
async def list_agents(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT agent_id, display_name, description, version, endpoint,
                   protocol, health_status, active, created_at
            FROM agent_registry
            ORDER BY display_name
            """
        )
    )
    agents = [_row_to_dict(row) for row in r.fetchall()]
    r2 = await db.execute(
        text(
            """
            SELECT agent_id, skill_id, quality_score, cost_profile
            FROM agent_supported_skills
            """
        )
    )
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in r2.fetchall():
        d = _row_to_dict(row)
        aid = str(d["agent_id"])
        by_agent.setdefault(aid, []).append(
            {
                "skill_id": d["skill_id"],
                "quality_score": float(d["quality_score"])
                if d.get("quality_score") is not None
                else None,
                "cost_profile": d.get("cost_profile"),
            }
        )
    for a in agents:
        aid = str(a["agent_id"])
        a["supported_skills"] = by_agent.get(aid, [])
    return agents


@router.get("/platform/policies")
async def list_policies(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT p.id, p.tenant_id, t.display_name AS tenant_name,
                   p.name, p.policy_json, p.created_at, p.updated_at
            FROM tenant_policies p
            JOIN tenants t ON t.id = p.tenant_id
            ORDER BY t.display_name
            """
        )
    )
    return [_row_to_dict(row) for row in r.fetchall()]


# ── Tenant overview + data plane ────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/overview")
async def tenant_overview(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    r = await db.execute(
        text(
            """
            SELECT t.id, t.display_name, t.domain, t.created_at,
                   c.db_url, c.region
            FROM tenants t
            LEFT JOIN tenant_db_connections c ON c.tenant_id = t.id
            WHERE t.id = :tid
            """
        ),
        {"tid": tenant_id},
    )
    row = r.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    base = _row_to_dict(row)
    pol = await db.execute(
        text(
            """
            SELECT id, name, policy_json, created_at, updated_at
            FROM tenant_policies WHERE tenant_id = :tid
            """
        ),
        {"tid": tenant_id},
    )
    prow = pol.fetchone()
    base["policy"] = _row_to_dict(prow) if prow else None

    counts: dict[str, int] = {}
    try:
        async with get_tenant_db_session(tenant_id) as tdb:
            for table in (
                "tenant_sources",
                "articles",
                "ingestion_executions",
                "tenant_tags",
                "tenant_article_format_templates",
            ):
                cr = await tdb.execute(text(f"SELECT COUNT(*) AS c FROM {table}"))
                crow = cr.fetchone()
                counts[table] = int(crow[0]) if crow else 0
            # New tables (may not exist on older tenant DBs)
            for table in ("tenant_products", "executions", "newsletter_articles"):
                try:
                    cr = await tdb.execute(text(f"SELECT COUNT(*) AS c FROM {table}"))
                    crow = cr.fetchone()
                    counts[table] = int(crow[0]) if crow else 0
                except Exception:
                    counts[table] = 0
    except Exception as exc:
        base["tenant_db_error"] = str(exc)

    base["counts"] = counts
    return base


@router.get("/tenants/{tenant_id}/sources")
async def tenant_sources(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT id, url, type, active, last_scraped_at, last_etag,
                       last_content_hash, max_depth, same_domain_only, include_patterns,
                       max_child_links_per_page, max_links_to_scrape, exclude_patterns,
                       min_text_chars, require_title, created_at
                FROM tenant_sources
                ORDER BY url
                """
            )
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/articles")
async def tenant_articles(
    tenant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    async with get_tenant_db_session(tenant_id) as tdb:
        cr = await tdb.execute(text("SELECT COUNT(*) AS c FROM articles"))
        total = int(cr.fetchone()[0])
        r = await tdb.execute(
            text(
                """
                SELECT id, source_id, url, canonical_url, title, author,
                       published_at, img_url, summary,
                       LEFT(text, 4000) AS text_preview,
                       created_at
                FROM articles
                ORDER BY created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            {"lim": limit, "off": offset},
        )
        rows = [_row_to_dict(row) for row in r.fetchall()]
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


@router.get("/tenants/{tenant_id}/executions")
async def tenant_executions(
    tenant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT execution_id, started_at, finished_at, status, summary_json,
                       persona_id
                FROM ingestion_executions
                ORDER BY started_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/executions/{execution_id}/logs")
async def tenant_execution_logs(
    tenant_id: uuid.UUID,
    execution_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT id, execution_id, source_id, article_id, step_name, status,
                       details_json, tokens_in, tokens_out, cost_usd, duration_ms, created_at
                FROM ingestion_log_entries
                WHERE execution_id = :eid
                ORDER BY created_at
                LIMIT :lim
                """
            ),
            {"eid": execution_id, "lim": limit},
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/tags")
async def tenant_tags(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT id, name, description, created_at
                FROM tenant_tags
                ORDER BY name
                """
            )
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/templates")
async def tenant_templates(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT id, name, template_json, is_default, created_at
                FROM tenant_article_format_templates
                ORDER BY name
                """
            )
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/formatted-articles")
async def tenant_formatted_articles(
    tenant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT article_id, format_template_id, formatted_json, created_at
                FROM formatted_articles
                ORDER BY created_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
        return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/platform/personas")
async def list_all_personas(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT p.persona_id, p.tenant_id, t.display_name AS tenant_name,
                   p.display_name, p.slug, p.role_description, p.tone_style,
                   p.goals, p.constraints, p.default_skills, p.guardrail_profile,
                   p.active, p.is_default, p.created_at, p.updated_at
            FROM personas p
            JOIN tenants t ON t.id = p.tenant_id
            ORDER BY t.display_name, p.display_name
            """
        )
    )
    return [_row_to_dict(row) for row in r.fetchall()]


@router.get("/tenants/{tenant_id}/personas")
async def list_tenant_personas(
    tenant_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    r = await db.execute(
        text(
            """
            SELECT persona_id, tenant_id, display_name, slug, role_description,
                   tone_style, goals, constraints, default_skills, guardrail_profile,
                   active, is_default, created_at, updated_at
            FROM personas
            WHERE tenant_id = :tid
            ORDER BY is_default DESC, display_name
            """
        ),
        {"tid": tenant_id},
    )
    return [_row_to_dict(row) for row in r.fetchall()]


# ── Products ────────────────────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/products")
async def tenant_products(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT id, name, description, url, category, tags, features,
                       active, created_at
                FROM tenant_products
                ORDER BY name
                """
            )
        )
        return [_row_to_dict(row) for row in r.fetchall()]


# ── Generic Executions ─────────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/generic-executions")
async def tenant_generic_executions(
    tenant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT execution_id, skill_id, persona_id, goal,
                       started_at, finished_at, status, result_json, cost_json
                FROM executions
                ORDER BY started_at DESC
                LIMIT :lim
                """
            ),
            {"lim": limit},
        )
        return [_row_to_dict(row) for row in r.fetchall()]


# ── Newsletter Articles ────────────────────────────────────────────────────


@router.get("/tenants/{tenant_id}/newsletters")
async def tenant_newsletters(
    tenant_id: uuid.UUID,
    status: str | None = Query(default=None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    async with get_tenant_db_session(tenant_id) as tdb:
        where = "WHERE 1=1"
        params: dict[str, Any] = {"lim": limit, "off": offset}
        if status:
            where += " AND n.status = :status"
            params["status"] = status

        cr = await tdb.execute(
            text(f"SELECT COUNT(*) AS c FROM newsletter_articles n {where}"),
            params,
        )
        total = int(cr.fetchone()[0])

        r = await tdb.execute(
            text(
                f"""
                SELECT n.id, n.execution_id, n.article_id, n.title, n.summary,
                       LEFT(n.body, 12000) AS body_preview,
                       n.product_refs, n.tags, n.source_url, n.img_url, n.media_refs,
                       n.status,
                       n.reviewed_by, n.reviewed_at, n.published_at, n.publish_channel,
                       n.created_at,
                       a.published_at AS source_published_at,
                       a.author AS source_author,
                       a.summary AS article_summary,
                       a.created_at AS article_created_at,
                       ts.url AS source_feed_url,
                       ts.type AS source_type
                FROM newsletter_articles n
                LEFT JOIN articles a ON a.id = n.article_id
                LEFT JOIN tenant_sources ts ON ts.id = a.source_id
                {where}
                ORDER BY n.created_at DESC
                LIMIT :lim OFFSET :off
                """
            ),
            params,
        )
        rows = [_row_to_dict(row) for row in r.fetchall()]
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


@router.get("/tenants/{tenant_id}/newsletters/{newsletter_id}")
async def tenant_newsletter_detail(
    tenant_id: uuid.UUID,
    newsletter_id: uuid.UUID,
) -> dict[str, Any]:
    async with get_tenant_db_session(tenant_id) as tdb:
        r = await tdb.execute(
            text(
                """
                SELECT n.id, n.execution_id, n.article_id, n.title, n.summary, n.body,
                       n.product_refs, n.tags, n.source_url, n.img_url, n.media_refs,
                       n.status,
                       n.reviewed_by, n.reviewed_at, n.published_at, n.publish_channel,
                       n.created_at,
                       a.published_at AS source_published_at,
                       a.author AS source_author,
                       a.summary AS article_summary,
                       a.created_at AS article_created_at,
                       ts.url AS source_feed_url,
                       ts.type AS source_type
                FROM newsletter_articles n
                LEFT JOIN articles a ON a.id = n.article_id
                LEFT JOIN tenant_sources ts ON ts.id = a.source_id
                WHERE n.id = :nid
                """
            ),
            {"nid": newsletter_id},
        )
        row = r.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Newsletter article not found")
        return _row_to_dict(row)


class ReviewAction(BaseModel):
    status: str  # "approved" | "rejected"
    reviewed_by: str = ""


@router.patch("/tenants/{tenant_id}/newsletters/{newsletter_id}/review")
async def review_newsletter(
    tenant_id: uuid.UUID,
    newsletter_id: uuid.UUID,
    action: ReviewAction,
) -> dict[str, Any]:
    """Human review: approve or reject a newsletter article."""
    if action.status not in ("approved", "rejected"):
        raise HTTPException(
            status_code=400,
            detail="status must be 'approved' or 'rejected'",
        )

    async with get_tenant_db_session(tenant_id) as tdb:
        await tdb.execute(
            text(
                """
                UPDATE newsletter_articles
                SET status = :status,
                    reviewed_by = :reviewer,
                    reviewed_at = now()
                WHERE id = :nid
                """
            ),
            {
                "nid": newsletter_id,
                "status": action.status,
                "reviewer": action.reviewed_by,
            },
        )
        await tdb.commit()

        r = await tdb.execute(
            text(
                "SELECT id, title, status, reviewed_by, reviewed_at "
                "FROM newsletter_articles WHERE id = :nid"
            ),
            {"nid": newsletter_id},
        )
        row = r.fetchone()
        return _row_to_dict(row) if row else {}
