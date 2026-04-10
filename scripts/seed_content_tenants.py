"""
Seed 4 test tenants for the AIWorks content-ingestion PoC.

Crawl defaults (merged into every source unless overridden):
  max_depth=2, max_child_links_per_page=4, max_links_to_scrape=25

Tenants
-------
T1  NeuralEdge AI        — AI / ML industry
T2  FreshPath Logistics  — fresh produce supply chain / transport
T3  PatentGuard Legal    — standards / SEP / patent policy portals
T4  GovSafe Analytics    — restricted policy demo

DB split
--------
  Control plane DB  : tenants, tenant_db_connections, tenant_policies
  Tenant DBs        : sources, tags, templates (no tenant_id columns)

Setup (run once before this script)
------------------------------------
  psql $DATABASE_URL -f db/migrations/001_schema.sql
  psql $DATABASE_URL -f db/migrations/002_tenant_policies.sql
  psql $DATABASE_URL -f db/migrations/003_tenant_db_connections.sql
  psql $DATABASE_URL -f db/migrations/004_personas.sql

  createdb aiworks_t001  # or CREATE DATABASE ...
  createdb aiworks_t002
  createdb aiworks_t003
  createdb aiworks_t004

  for DB in aiworks_t001 aiworks_t002 aiworks_t003 aiworks_t004; do
    psql postgresql://USER@localhost:5432/$DB -f db/migrations/tenant/001_tenant_schema.sql
    psql postgresql://USER@localhost:5432/$DB -f db/migrations/tenant/002_tenant_sources_article_rules.sql
    psql postgresql://USER@localhost:5432/$DB -f db/migrations/tenant/003_ingestion_persona.sql
    # … 004–006 as in repo order; admin newsletters API needs img_url + media_refs:
    psql postgresql://USER@localhost:5432/$DB -f db/migrations/tenant/007_newsletter_media.sql
  done

  uv run python scripts/seed_content_tenants.py
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import AsyncSessionLocal  # control plane sessions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)

_SEP = "=" * 72

# Merged into each source dict; per-source keys override these.
SOURCE_DEFAULTS: dict = {
    "max_depth": 2,
    "max_child_links_per_page": 4,
    "max_links_to_scrape": 25,
    "exclude_patterns": ["/login", "/signin", "/cookie", "/privacy"],
    "min_text_chars": 40,
    "require_title": True,
}


def _merge_source(src: dict) -> dict:
    return {**SOURCE_DEFAULTS, **src}


# ── Tenant definitions ─────────────────────────────────────────────────────────

TENANTS = [
    # Tenant 1 — NeuralEdge AI
    {
        "id":           "00000000-0000-0000-0000-000000000001",
        "display_name": "NeuralEdge AI",
        "domain":       "ai_company",
        "db_name":      "aiworks_t001",
        "sources": [
            {
                "url":              "https://simonwillison.net/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
            },
            {
                "url":              "https://www.deeplearning.ai/the-batch/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": ["the-batch", "blog"],
            },
            {
                "url":              "https://huggingface.co/blog",
                "type":             "html",
                "max_depth":        1,
                "same_domain_only": True,
                "include_patterns": [],
            },
            {
                "url":              "https://www.fruitnet.com/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
            },
            {
                "url":              "https://www.freshplaza.com/asia/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": ["freshplaza.com"],
            },
        ],
        "tags": [
            ("llm",             "Large Language Models and prompt engineering"),
            ("ai_safety",       "AI alignment and safety research"),
            ("ml_ops",          "MLOps tooling, infra, and deployment"),
            ("vector_db",       "Vector databases and semantic search"),
            ("agentic_ai",      "Autonomous agents and multi-agent systems"),
            ("llm_in_prod",     "Running LLMs reliably in production"),
            ("fine_tuning",     "Model fine-tuning and RLHF"),
            ("open_source_ai",  "Open source models and frameworks"),
        ],
        "products": [
            {
                "name":        "NeuralEdge AutoML Studio",
                "description": "End-to-end AutoML platform for building, training, and deploying ML models with minimal code. Supports tabular, vision, and NLP tasks.",
                "url":         "https://neuraledge.ai/automl-studio",
                "category":    "ml_platform",
                "tags":        ["automl", "ml_ops", "model_training"],
                "features":    ["Auto feature engineering", "Hyperparameter tuning", "One-click deployment"],
            },
            {
                "name":        "NeuralEdge VectorVault",
                "description": "Managed vector database service optimized for LLM applications, RAG pipelines, and semantic search at scale.",
                "url":         "https://neuraledge.ai/vectorvault",
                "category":    "vector_database",
                "tags":        ["vector_db", "rag", "semantic_search"],
                "features":    ["Sub-ms latency", "Auto-scaling", "Hybrid search", "Multi-tenant isolation"],
            },
            {
                "name":        "NeuralEdge AgentForge",
                "description": "Framework for building production-grade AI agents with built-in guardrails, tool orchestration, and multi-agent collaboration.",
                "url":         "https://neuraledge.ai/agentforge",
                "category":    "agent_framework",
                "tags":        ["agentic_ai", "llm_in_prod", "multi_agent"],
                "features":    ["ReAct patterns", "Tool registry", "Safety guardrails", "Cost tracking"],
            },
            {
                "name":        "NeuralEdge FineTune Pro",
                "description": "Managed fine-tuning service for open-source LLMs. Supports LoRA, QLoRA, and full fine-tuning with evaluation pipelines.",
                "url":         "https://neuraledge.ai/finetune-pro",
                "category":    "fine_tuning",
                "tags":        ["fine_tuning", "open_source_ai", "llm"],
                "features":    ["LoRA/QLoRA", "Dataset management", "Auto evaluation", "Model registry"],
            },
            {
                "name":        "NeuralEdge Sentinel",
                "description": "AI safety and guardrails platform. Real-time content moderation, prompt injection detection, and policy enforcement for LLM applications.",
                "url":         "https://neuraledge.ai/sentinel",
                "category":    "ai_safety",
                "tags":        ["ai_safety", "llm_in_prod", "guardrails"],
                "features":    ["Prompt injection detection", "PII redaction", "Toxicity filtering", "Policy engine"],
            },
        ],
        "format_template": {
            "headlineField":      "title",
            "summaryField":       "auto_generated_summary",
            "bodyField":          "text",
            "imageField":         "img_url",
            "primaryTagField":    "primary_tag",
            "secondaryTagsField": "secondary_tags",
            "includeScore":       True,
        },
        "policy": {
            "capabilities": {
                "allowed": [
                    "fetch_tenant_sources",
                    "scrape_source_urls_incremental",
                    "extract_and_normalize_articles",
                    "tag_content_item",
                    "apply_article_format_template",
                    "record_ingestion_log_entry",
                    "content_ingestion",
                    "content_curation",
                ],
                "blocked":         [],
                "requireApproval": [],
                "defaultAllow":    True,
            },
            "budget": {
                "perExecutionUsdLimit":   5.0,
                "maxTokensPerExecution":  200_000,
            },
            "security": {
                "allowWebScraping":      True,
                "allowExternalApiCalls": True,
                "allowedDataTags":       ["PUBLIC_WEB_CONTENT"],
            },
        },
        "persona": {
            "display_name":     "NeuralEdge Insights Analyst",
            "slug":             "default",
            "role_description": (
                "AI/ML industry analyst coworker — curates research and news for the team, "
                "creates newsletter articles that reference NeuralEdge products where relevant."
            ),
            "tone_style":       "analytical, concise, citation-aware",
            "goals": [
                "Surface actionable ML/AI intelligence from public sources",
                "Prefer primary sources and technical depth over hype",
                "Create newsletter articles that naturally reference NeuralEdge products",
            ],
            "is_default": True,
        },
        "extra_personas": [
            {
                "display_name":     "NeuralEdge Quick Curator",
                "slug":             "quick_scan",
                "role_description": "Lightweight scan persona for high-volume source checks.",
                "tone_style":       "brief, bullet-friendly",
                "goals":            ["Rapid triage of new articles"],
                "is_default":       False,
            },
        ],
    },

    # Tenant 2 — FreshPath Logistics (produce / transport persona)
    {
        "id":           "00000000-0000-0000-0000-000000000002",
        "display_name": "FreshPath Logistics",
        "domain":       "fresh_produce_transport",
        "db_name":      "aiworks_t002",
        "sources": [
            {
                "url":              "https://www.freshplaza.com/asia/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": ["asia", "news", "article", "fruit", "vegetable"],
            },
            {
                "url":              "https://www.fruitnet.com/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
            },
            {
                "url":              "https://www.freshfruitportal.com/news/category/more-news/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": ["news", "category"],
            },
            {
                "url":              "https://www.thepacker.com/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
            },
        ],
        "tags": [
            ("cold_chain",       "Cold-chain logistics and temperature control"),
            ("last_mile",        "Last-mile delivery for perishables"),
            ("supply_chain",     "Fresh produce supply chain management"),
            ("food_safety",      "Food safety regulations and traceability"),
            ("sustainability",   "Sustainable packaging and carbon footprint"),
            ("market_prices",    "Wholesale and retail produce pricing"),
            ("seasonal_trends",  "Seasonal availability and crop reports"),
        ],
        "format_template": {
            "headlineField":      "title",
            "summaryField":       "auto_generated_summary",
            "bodyField":          "text",
            "imageField":         "img_url",
            "primaryTagField":    "primary_tag",
            "secondaryTagsField": "secondary_tags",
            "includeScore":       True,
            "includePublishedAt": True,
        },
        "policy": {
            "capabilities": {
                "allowed": [
                    # Registry skill_ids (required when defaultAllow is false)
                    "content_ingestion",
                    "content_curation",
                    # Planner / legacy capability names (documentation; not used for skill filter)
                    "fetch_tenant_sources",
                    "scrape_source_urls_incremental",
                    "extract_and_normalize_articles",
                    "tag_content_item",
                    "apply_article_format_template",
                    "record_ingestion_log_entry",
                ],
                "blocked":         [],
                "requireApproval": [],
                "defaultAllow":    False,
            },
            "budget": {
                "perExecutionUsdLimit":   1.5,
                "maxTokensPerExecution":  75_000,
            },
            "security": {
                "allowWebScraping":      True,
                "allowExternalApiCalls": True,
                "allowedDataTags":       ["PUBLIC_WEB_CONTENT"],
            },
        },
        "persona": {
            "display_name":     "FreshPath Supply Analyst",
            "slug":             "default",
            "role_description": (
                "Perishable supply chain coworker — cold chain, sourcing, and market signals."
            ),
            "tone_style":       "practical, operations-focused",
            "goals": [
                "Track produce logistics and food-safety signals relevant to operations",
            ],
            "is_default": True,
        },
    },

    # Tenant 3 — PatentGuard Legal (standards / patent-policy persona)
    {
        "id":           "00000000-0000-0000-0000-000000000003",
        "display_name": "PatentGuard Legal",
        "domain":       "patent_litigation",
        "db_name":      "aiworks_t003",
        "sources": [
            {
                "url":              "https://ipr.etsi.org/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://standards.ieee.org/about/sasb/patcom/patents/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://mentor.ieee.org/bp/StartPage",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://mentor.ieee.org/802.11/documents",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://www.itu.int/net4/ipr/search.aspx",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              (
                    "https://www.itu.int/net/ITU-R/index.asp?redirect=true&category=study-groups"
                    "&rlink=patents&lang=en#lang=en"
                ),
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              (
                    "https://isotc.iso.org/livelink/livelink/13622347/"
                    "Patents_database.xls?func=doc.Fetch&nodeId=13622347"
                ),
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              (
                    "https://www.iso.org/resources/publicly-available-resources.html"
                    "?t=0anPz3TMFpHPMzf5b0mZkRYtHArk-eAG7zgOrPegH2z5D7LngLQt7ZulXvt7OSez"
                    "&view=documents#section-isodocuments-top"
                ),
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              (
                    "https://www.iso.org/resources/publicly-available-resources.html"
                    "?t=SdvUSXQKduohw657DTulr1XRfdBUGqzHhttbbgNgTT84LivA-3B8C0H2Jh6P2-E"
                    "&view=documents#section-isodocuments-top"
                ),
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://datatracker.ietf.org/ipr",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://patents.iec.ch/iec/pa.nsf/pa_h.xsp",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
            {
                "url":              "https://www.atis.org/policy/patent-assurances/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
                "require_title":    False,
            },
        ],
        "tags": [
            ("patent_filing",      "Patent application and prosecution"),
            ("ip_litigation",      "Intellectual property litigation and disputes"),
            ("prior_art",          "Prior art searches and invalidity"),
            ("claim_construction", "Patent claim construction and interpretation"),
            ("ipr_proceedings",    "Inter partes review and PTAB decisions"),
            ("licensing",          "Patent licensing, SEPs, and royalty disputes"),
            ("trade_secrets",      "Trade secret misappropriation cases"),
        ],
        "format_template": {
            "headlineField":       "title",
            "summaryField":        "text",
            "imageField":          "img_url",
            "primaryTagField":     "primary_tag",
            "secondaryTagsField":  "secondary_tags",
            "includeScore":        False,
            "requiresHumanReview": True,
        },
        "policy": {
            "capabilities": {
                "allowed": [
                    "content_ingestion",
                    "content_curation",
                    "fetch_tenant_sources",
                    "scrape_source_urls_incremental",
                    "extract_and_normalize_articles",
                    "tag_content_item",
                    "apply_article_format_template",
                    "record_ingestion_log_entry",
                ],
                "blocked":         [],
                "requireApproval": ["apply_article_format_template"],
                "defaultAllow":    False,
            },
            "budget": {
                "perExecutionUsdLimit":   1.0,
                "maxTokensPerExecution":  50_000,
            },
            "security": {
                "allowWebScraping":      True,
                "allowExternalApiCalls": True,
                "allowedDataTags":       ["PUBLIC_WEB_CONTENT"],
            },
        },
        "persona": {
            "display_name":     "PatentGuard Standards Counsel",
            "slug":             "default",
            "role_description": (
                "IP and standards policy coworker — SEPs, FRAND, and standards-body filings."
            ),
            "tone_style":       "formal, precise, risk-aware",
            "goals": [
                "Monitor standards-body patent declarations and policy updates",
            ],
            "is_default": True,
        },
    },

    # Tenant 4 — GovSafe Analytics (policy-governance demo)
    {
        "id":           "00000000-0000-0000-0000-000000000004",
        "display_name": "GovSafe Analytics",
        "domain":       "government_compliance",
        "db_name":      "aiworks_t004",
        "sources": [
            {
                "url":              "https://www.gao.gov/products/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": [],
            },
            {
                "url":              "https://www.federalregister.gov/",
                "type":             "html",
                "same_domain_only": True,
                "include_patterns": ["rule", "notice", "regulation"],
            },
        ],
        "tags": [
            ("federal_regulation", "Federal rules and regulatory guidance"),
            ("compliance_update",  "Compliance requirements and deadlines"),
            ("gao_report",         "GAO reports and recommendations"),
            ("audit_finding",      "Government audit findings"),
        ],
        "format_template": {
            "headlineField":    "title",
            "summaryField":     "text",
            "primaryTagField":  "primary_tag",
            "includeScore":     False,
        },
        "policy": {
            "capabilities": {
                "allowed": [
                    "content_ingestion",
                    "content_curation",
                    "fetch_tenant_sources",
                    "scrape_source_urls_incremental",
                    "extract_and_normalize_articles",
                    "record_ingestion_log_entry",
                ],
                "blocked": [
                    "tag_content_item",
                    "apply_article_format_template",
                ],
                "requireApproval": [],
                "defaultAllow":    False,
            },
            "budget": {
                "perExecutionUsdLimit":   0.05,
                "maxTokensPerExecution":  5_000,
            },
            "security": {
                "allowWebScraping":      True,
                "allowExternalApiCalls": False,
                "allowedDataTags":       ["PUBLIC_WEB_CONTENT", "GOVERNMENT_DATA"],
            },
        },
        "persona": {
            "display_name":     "GovSafe Compliance Reader",
            "slug":             "default",
            "role_description": (
                "Public-sector compliance coworker — summarizes rules and audit signals."
            ),
            "tone_style":       "neutral, factual, conservative",
            "goals": [
                "Extract compliance-relevant facts from public government sources",
            ],
            "is_default": True,
        },
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────────────


async def _get_tenant_session(db_name: str) -> async_sessionmaker:
    """Build a session factory for a tenant-specific database."""
    base = settings.tenant_db_base_url.rstrip("/")
    url  = f"{base}/{db_name}"
    engine = create_async_engine(url, echo=False, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _seed_control_plane(t: dict, now: datetime) -> None:
    """Register tenant, DB connection, and policy in the control plane DB."""
    tid = t["id"]
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO tenants (id, display_name, domain, created_at) "
                "VALUES (:id, :name, :domain, :now) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"id": tid, "name": t["display_name"], "domain": t["domain"], "now": now},
        )

        base   = settings.tenant_db_base_url.rstrip("/")
        db_url = f"{base}/{t['db_name']}"
        await db.execute(
            text(
                "INSERT INTO tenant_db_connections (tenant_id, db_url, created_at, updated_at) "
                "VALUES (:tid, :url, :now, :now) "
                "ON CONFLICT (tenant_id) DO UPDATE SET db_url=EXCLUDED.db_url, updated_at=now()"
            ),
            {"tid": tid, "url": db_url, "now": now},
        )

        await db.execute(
            text(
                "INSERT INTO tenant_policies (tenant_id, name, policy_json, created_at, updated_at) "
                "VALUES (:tid, :name, CAST(:policy AS jsonb), :now, :now) "
                "ON CONFLICT (tenant_id) DO UPDATE "
                "SET policy_json=EXCLUDED.policy_json, updated_at=now()"
            ),
            {
                "tid":    tid,
                "name":   t["display_name"],
                "policy": json.dumps(t["policy"]),
                "now":    now,
            },
        )

        personas_to_seed: list[dict] = []
        if t.get("persona"):
            personas_to_seed.append(t["persona"])
        personas_to_seed.extend(t.get("extra_personas", []))
        for persona in personas_to_seed:
            await db.execute(
                text(
                    """
                    INSERT INTO personas (
                        tenant_id, display_name, slug, role_description, tone_style,
                        goals, is_default, active, created_at, updated_at
                    )
                    VALUES (
                        :tid, :dn, :slug, :role, :tone,
                        CAST(:goals AS jsonb), :is_def, true, :now, :now
                    )
                    ON CONFLICT (tenant_id, slug) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        role_description = EXCLUDED.role_description,
                        tone_style = EXCLUDED.tone_style,
                        goals = EXCLUDED.goals,
                        is_default = EXCLUDED.is_default,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "tid":   tid,
                    "dn":    persona["display_name"],
                    "slug":  persona.get("slug", "default"),
                    "role":  persona["role_description"],
                    "tone":  persona["tone_style"],
                    "goals": json.dumps(persona.get("goals", [])),
                    "is_def": persona.get("is_default", True),
                    "now":   now,
                },
            )

        await db.commit()


async def _seed_tenant_db(t: dict, now: datetime) -> None:
    """Seed sources, tags, and format template into the tenant's own DB."""
    session_factory = await _get_tenant_session(t["db_name"])
    async with session_factory() as db:
        await db.execute(
            text(
                "ALTER TABLE ingestion_executions "
                "ADD COLUMN IF NOT EXISTS persona_id UUID"
            )
        )
        await db.execute(
            text(
                "ALTER TABLE newsletter_articles "
                "ADD COLUMN IF NOT EXISTS img_url TEXT"
            )
        )
        await db.execute(
            text(
                "ALTER TABLE newsletter_articles "
                "ADD COLUMN IF NOT EXISTS media_refs JSONB NOT NULL DEFAULT '{}'::jsonb"
            )
        )

        for raw in t["sources"]:
            src = _merge_source(raw)
            await db.execute(
                text(
                    "INSERT INTO tenant_sources "
                    "    (url, type, max_depth, same_domain_only, include_patterns, "
                    "     max_child_links_per_page, max_links_to_scrape, exclude_patterns, "
                    "     min_text_chars, require_title, created_at) "
                    "VALUES (:url, :type, :depth, :same_domain, :patterns, "
                    "        :child_cap, :total_cap, :exclude, :min_chars, :req_title, :now) "
                    "ON CONFLICT (url) DO UPDATE SET "
                    "    max_depth=EXCLUDED.max_depth, "
                    "    same_domain_only=EXCLUDED.same_domain_only, "
                    "    include_patterns=EXCLUDED.include_patterns, "
                    "    max_child_links_per_page=EXCLUDED.max_child_links_per_page, "
                    "    max_links_to_scrape=EXCLUDED.max_links_to_scrape, "
                    "    exclude_patterns=EXCLUDED.exclude_patterns, "
                    "    min_text_chars=EXCLUDED.min_text_chars, "
                    "    require_title=EXCLUDED.require_title"
                ),
                {
                    "url":         src["url"],
                    "type":        src["type"],
                    "depth":       src["max_depth"],
                    "same_domain": src["same_domain_only"],
                    "patterns":    src["include_patterns"],
                    "child_cap":   src["max_child_links_per_page"],
                    "total_cap":   src["max_links_to_scrape"],
                    "exclude":     src["exclude_patterns"],
                    "min_chars":   src["min_text_chars"],
                    "req_title":   src["require_title"],
                    "now":         now,
                },
            )

        for name, desc in t["tags"]:
            await db.execute(
                text(
                    "INSERT INTO tenant_tags (name, description, created_at) "
                    "VALUES (:name, :desc, :now) ON CONFLICT (name) DO NOTHING"
                ),
                {"name": name, "desc": desc, "now": now},
            )

        await db.execute(
            text(
                "INSERT INTO tenant_article_format_templates "
                "    (name, template_json, is_default, created_at) "
                "VALUES (:name, CAST(:tmpl AS jsonb), true, :now) "
                "ON CONFLICT DO NOTHING"
            ),
            {
                "name": f"default_{t['domain']}",
                "tmpl": json.dumps(t["format_template"]),
                "now":  now,
            },
        )

        # ── Products ─────────────────────────────────────────────────────────
        for prod in t.get("products", []):
            await db.execute(
                text(
                    "INSERT INTO tenant_products "
                    "    (name, description, url, category, tags, features, created_at) "
                    "VALUES (:name, :desc, :url, :cat, :tags, :features, :now) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "name":     prod["name"],
                    "desc":     prod.get("description", ""),
                    "url":      prod.get("url"),
                    "cat":      prod.get("category", ""),
                    "tags":     prod.get("tags", []),
                    "features": prod.get("features", []),
                    "now":      now,
                },
            )

        await db.commit()


# ── Main seed routine ──────────────────────────────────────────────────────────


async def seed() -> None:
    now = datetime.now(timezone.utc)

    logger.info(_SEP)
    logger.info("AIWorks Tenant Seed — starting (%d tenants)", len(TENANTS))
    logger.info(
        "Default crawl: depth=%s child_links/url=%s total_cap=%s",
        SOURCE_DEFAULTS["max_depth"],
        SOURCE_DEFAULTS["max_child_links_per_page"],
        SOURCE_DEFAULTS["max_links_to_scrape"],
    )
    logger.info(_SEP)

    for t in TENANTS:
        tid = t["id"]
        logger.info("")
        logger.info("Tenant: %-30s  id=%s", t["display_name"], tid)
        logger.info("  domain=%-30s  db=%s", t["domain"], t["db_name"])

        await _seed_control_plane(t, now)
        blocked      = t["policy"]["capabilities"]["blocked"]
        req_approval = t["policy"]["capabilities"]["requireApproval"]
        budget       = t["policy"]["budget"]["perExecutionUsdLimit"]
        logger.info("  [Control Plane] tenant + policy + persona(s) + db_connection registered")
        logger.info(
            "    blocked=%-35s  requireApproval=%-35s  budget=$%.2f/exec",
            str(blocked or "none"), str(req_approval or "none"), budget,
        )

        await _seed_tenant_db(t, now)
        products = t.get("products", [])
        logger.info(
            "  [Tenant DB %-15s]  sources=%d  tags=%d  products=%d  template=default_%s",
            t["db_name"],
            len(t["sources"]),
            len(t["tags"]),
            len(products),
            t["domain"],
        )
        for raw in t["sources"]:
            s = _merge_source(raw)
            logger.info(
                "    source  depth=%d  child/url=%d  total_cap=%d  url=%s",
                s["max_depth"],
                s["max_child_links_per_page"],
                s["max_links_to_scrape"],
                s["url"][:90] + ("…" if len(s["url"]) > 90 else ""),
            )
        for prod in products:
            logger.info(
                "    product  %-35s  category=%s",
                prod["name"], prod.get("category", ""),
            )

    logger.info("")
    logger.info(_SEP)
    logger.info("All %d tenants seeded successfully.", len(TENANTS))
    logger.info(_SEP)


if __name__ == "__main__":
    asyncio.run(seed())
