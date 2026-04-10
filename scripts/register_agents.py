"""
Register skills and agents into the control-plane registries.

Must be run once after the DB schema is applied.
Re-running is safe (ON CONFLICT DO NOTHING / DO UPDATE).

Usage:
    uv run python scripts/register_agents.py
"""

import asyncio
import uuid

from sqlalchemy import text

from app.core.config import settings
from app.core.db import AsyncSessionLocal

# Fixed UUIDs for reproducible dev/test setups
CONTENT_INGESTION_AGENT_ID = uuid.UUID("10000000-0000-0000-0000-000000000001")
CONTENT_CURATOR_AGENT_ID   = uuid.UUID("10000000-0000-0000-0000-000000000002")
GENERIC_SCRAPER_AGENT_ID   = uuid.UUID("10000000-0000-0000-0000-000000000003")
GENERIC_MATCHER_AGENT_ID   = uuid.UUID("10000000-0000-0000-0000-000000000004")

SKILLS = [
    {
        "skill_id": "content_ingestion",
        "name": "Content Ingestion",
        "description": (
            "Incrementally scrape, normalize, tag, and format articles "
            "from a tenant's configured web sources."
        ),
        "domain": "content",
        "tags": ["ingestion", "scraping", "tagging", "formatting"],
        "input_schema": '{"type":"object","properties":{"tenant_id":{"type":"string"}},"required":["tenant_id"]}',
        "output_schema": '{"type":"object","properties":{"sources_scraped":{"type":"integer"},"new_articles":{"type":"integer"},"estimated_cost_usd":{"type":"number"}}}',
    },
    {
        "skill_id": "content_curation",
        "name": "Content Curation",
        "description": (
            "Full content curation pipeline: scrape websites, extract articles, "
            "match with tenant products/services, and generate newsletter-ready "
            "articles with product references for human review."
        ),
        "domain": "content",
        "tags": ["curation", "newsletter", "product_matching", "scraping", "content_generation"],
        "input_schema": '{"type":"object","properties":{"tenant_id":{"type":"string"},"goal":{"type":"string"}},"required":["tenant_id"]}',
        "output_schema": '{"type":"object","properties":{"articles_extracted":{"type":"integer"},"newsletter_articles_created":{"type":"integer"},"products_matched":{"type":"integer"},"estimated_cost_usd":{"type":"number"}}}',
    },
    {
        "skill_id": "scrape_urls",
        "name": "Scrape URLs",
        "description": "Crawl one or more URLs and return clean text, media, and links.",
        "domain": "generic",
        "tags": ["scraping", "crawling", "generic"],
        "input_schema": '{"type":"object","properties":{"urls":{"type":"array","items":{"type":"string"}},"strategy":{"type":"string"}},"required":["urls"]}',
        "output_schema": '{"type":"object","properties":{"pages":{"type":"array"},"total_scraped":{"type":"integer"}}}',
    },
    {
        "skill_id": "search_and_scrape",
        "name": "Search and Scrape",
        "description": "Run web search queries and crawl the top results.",
        "domain": "generic",
        "tags": ["scraping", "search", "generic"],
        "input_schema": '{"type":"object","properties":{"search_queries":{"type":"array","items":{"type":"string"}}},"required":["search_queries"]}',
        "output_schema": '{"type":"object","properties":{"pages":{"type":"array"},"total_scraped":{"type":"integer"}}}',
    },
    {
        "skill_id": "extract_media_from_url",
        "name": "Extract Media from URL",
        "description": "Extract images, videos, and audio from a web page.",
        "domain": "generic",
        "tags": ["scraping", "media", "generic"],
        "input_schema": '{"type":"object","properties":{"urls":{"type":"array","items":{"type":"string"}}},"required":["urls"]}',
        "output_schema": '{"type":"object","properties":{"pages":{"type":"array"}}}',
    },
    {
        "skill_id": "match_content_to_entities",
        "name": "Match Content to Entities",
        "description": "Match text content to tenant entities (products, KB articles, cases) via vector search + LLM re-ranking.",
        "domain": "generic",
        "tags": ["matching", "vector_search", "generic"],
        "input_schema": '{"type":"object","properties":{"content":{"type":"string"},"entity_type":{"type":"string"},"entity_table":{"type":"string"}},"required":["content","entity_type","entity_table"]}',
        "output_schema": '{"type":"object","properties":{"matches":{"type":"array"},"total_matches":{"type":"integer"}}}',
    },
    {
        "skill_id": "vector_search_entities",
        "name": "Vector Search Entities",
        "description": "Semantic similarity search against a tenant entity table using embeddings.",
        "domain": "generic",
        "tags": ["vector_search", "semantic", "generic"],
        "input_schema": '{"type":"object","properties":{"content":{"type":"string"},"entity_type":{"type":"string"},"entity_table":{"type":"string"}},"required":["content","entity_type","entity_table"]}',
        "output_schema": '{"type":"object","properties":{"matches":{"type":"array"},"total_matches":{"type":"integer"}}}',
    },
]

AGENTS = [
    {
        "agent_id": CONTENT_INGESTION_AGENT_ID,
        "display_name": "Content Ingestion Agent",
        "description": (
            "LangGraph agent that scrapes, normalises, tags, and formats "
            "web articles for a tenant."
        ),
        "version": "1.0.0",
        "endpoint": settings.content_ingestion_agent_url,
        "skills": ["content_ingestion"],
    },
    {
        "agent_id": CONTENT_CURATOR_AGENT_ID,
        "display_name": "Content Curator Agent",
        "description": (
            "LangGraph agent that scrapes sources, extracts articles, matches "
            "them with tenant products, and generates newsletter-ready content "
            "for human review and social media publishing."
        ),
        "version": "1.0.0",
        "endpoint": settings.content_curator_agent_url,
        "skills": ["content_curation"],
    },
    {
        "agent_id": GENERIC_SCRAPER_AGENT_ID,
        "display_name": "Generic Scraper Agent",
        "description": (
            "Reusable web acquisition agent. Supports single-page, batch, and deep crawl "
            "(BFS/DFS/BestFirst/Adaptive). Enforces tenant scraping limits. "
            "Optionally normalises output to a caller-supplied JSON schema."
        ),
        "version": "1.0.0",
        "endpoint": settings.generic_scraper_agent_url,
        "skills": ["scrape_urls", "search_and_scrape", "extract_media_from_url"],
    },
    {
        "agent_id": GENERIC_MATCHER_AGENT_ID,
        "display_name": "Generic Content Matcher Agent",
        "description": (
            "Reusable content-to-entity matching agent. "
            "Combines vector search + DB lookup + LLM re-ranking. "
            "Works for products, KB articles, legal cases, HR policies."
        ),
        "version": "1.0.0",
        "endpoint": settings.generic_matcher_agent_url,
        "skills": ["match_content_to_entities", "vector_search_entities"],
    },
]


async def register() -> None:
    async with AsyncSessionLocal() as db:
        # ── Skill Registry ────────────────────────────────────────────────────
        for skill in SKILLS:
            await db.execute(
                text(
                    """
                    INSERT INTO skill_registry
                        (skill_id, name, description, domain, tags,
                         input_schema, output_schema)
                    VALUES (
                        :skill_id, :name, :description, :domain, :tags,
                        CAST(:input_schema AS jsonb),
                        CAST(:output_schema AS jsonb)
                    )
                    ON CONFLICT (skill_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        description = EXCLUDED.description,
                        domain = EXCLUDED.domain,
                        tags = EXCLUDED.tags,
                        input_schema = EXCLUDED.input_schema,
                        output_schema = EXCLUDED.output_schema
                    """
                ),
                {
                    "skill_id": skill["skill_id"],
                    "name": skill["name"],
                    "description": skill["description"],
                    "domain": skill["domain"],
                    "tags": skill["tags"],
                    "input_schema": skill["input_schema"],
                    "output_schema": skill["output_schema"],
                },
            )
            print(f"  skill: {skill['skill_id']}")

        # ── Agent Registry ────────────────────────────────────────────────────
        for agent in AGENTS:
            await db.execute(
                text(
                    """
                    INSERT INTO agent_registry
                        (agent_id, display_name, description, version,
                         endpoint, protocol, health_status)
                    VALUES (
                        :agent_id, :display_name, :description, :version,
                        :endpoint, 'http_json', 'OK'
                    )
                    ON CONFLICT (agent_id) DO UPDATE SET
                        display_name = EXCLUDED.display_name,
                        description = EXCLUDED.description,
                        version = EXCLUDED.version,
                        endpoint = EXCLUDED.endpoint
                    """
                ),
                {
                    "agent_id": agent["agent_id"],
                    "display_name": agent["display_name"],
                    "description": agent["description"],
                    "version": agent["version"],
                    "endpoint": agent["endpoint"],
                },
            )
            print(f"  agent: {agent['display_name']} -> {agent['endpoint']}")

            # ── Supported skills ──────────────────────────────────────────────
            for skill_id in agent["skills"]:
                await db.execute(
                    text(
                        """
                        INSERT INTO agent_supported_skills
                            (agent_id, skill_id, quality_score, cost_profile)
                        VALUES (:agent_id, :skill_id, 1.0, 'standard')
                        ON CONFLICT DO NOTHING
                        """
                    ),
                    {"agent_id": agent["agent_id"], "skill_id": skill_id},
                )
                print(f"    skill mapping: {agent['display_name']} -> {skill_id}")

        await db.commit()

    print("\nAgent registry ready.")
    print("Services:")
    print(f"  Control Plane:          http://localhost:{settings.control_plane_port}")
    print(f"  Content Ingestion:      {settings.content_ingestion_agent_url}")
    print(f"  Content Curator:        {settings.content_curator_agent_url}")
    print(f"  Scraper MCP:            {settings.scraper_mcp_url}")
    print(f"  Generic Scraper Agent:  {settings.generic_scraper_agent_url}")
    print(f"  Generic Matcher Agent:  {settings.generic_matcher_agent_url}")
    print()
    print("Execute content curation:")
    print('  curl -X POST http://localhost:8000/execute \\')
    print('    -H "Content-Type: application/json" \\')
    print('    -d \'{"tenant_id": "00000000-0000-0000-0000-000000000001",')
    print('          "goal": "Scrape configured sources, create newsletter articles with product references"}\'')


if __name__ == "__main__":
    asyncio.run(register())
