from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Control Plane DB ──────────────────────────────────────────────────────
    # Single Postgres DB holding: tenants, tenant_db_connections, skill_registry,
    # agent_registry, agent_supported_skills, tenant_policies.
    # Tenant-specific data (articles, sources, logs) lives in separate per-tenant DBs
    # whose URLs are stored in tenant_db_connections.db_url.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/aiworks"

    # ── Tenant DB defaults ────────────────────────────────────────────────────
    # Base URL pattern used by the seed script to construct per-tenant DB URLs.
    # Tenant DBs are named:  aiworks_t<short_id>  (e.g. aiworks_t001)
    tenant_db_base_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432"

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ── Inline (online) LLM-judge eval — runs after each successful agent invoke ─
    # Requires OPENAI_API_KEY or openai_api_key. Stores rows in tenant DB
    # `inline_eval_runs` (migration 008). Retries the agent at most `inline_eval_max_retries`
    # times when the judge fails (2 retries => up to 3 invocations total).
    inline_eval_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("INLINE_EVAL_ENABLED", "inline_eval_enabled"),
    )
    inline_eval_max_retries: int = Field(
        default=2,
        validation_alias=AliasChoices(
            "INLINE_EVAL_MAX_RETRIES", "inline_eval_max_retries"
        ),
    )
    inline_eval_threshold: float = Field(
        default=0.5,
        validation_alias=AliasChoices(
            "INLINE_EVAL_THRESHOLD", "inline_eval_threshold"
        ),
    )
    inline_eval_metric_name: str = Field(
        default="GoalAdequacy",
        validation_alias=AliasChoices(
            "INLINE_EVAL_METRIC_NAME", "inline_eval_metric_name"
        ),
    )

    # ── Service URLs — used by the Execution Engine to reach agent services ───
    content_ingestion_agent_url: str = "http://localhost:8001"
    scraper_mcp_url: str = "http://localhost:8002"
    content_curator_agent_url: str = "http://localhost:8003"
    generic_scraper_agent_url: str = "http://localhost:8004"
    generic_matcher_agent_url: str = "http://localhost:8005"
    scraper_http_timeout_seconds: float = 600.0

    # ── Ports (used when launching each service via uvicorn) ──────────────────
    control_plane_port: int = 8000
    content_ingestion_agent_port: int = 8001
    scraper_mcp_port: int = 8002
    content_curator_agent_port: int = 8003
    generic_scraper_agent_port: int = 8004
    generic_matcher_agent_port: int = 8005

    # ── Ingestion safety limits ────────────────────────────────────────────────
    # Hard runtime guardrails to prevent runaway BFS scraping.
    ingestion_max_depth_cap: int = 2
    # Upper bound only; per-source `max_links_to_scrape` in tenant DB is authoritative.
    ingestion_max_links_per_source: int = 100

    log_level: str = "INFO"

    # Comma-separated origins for the Next.js admin UI (dev defaults).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Langfuse (LLM observability) ─────────────────────────────────────────
    # Set both keys to enable tracing. See https://langfuse.com/docs
    langfuse_public_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGFUSE_PUBLIC_KEY", "langfuse_public_key"),
    )
    langfuse_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("LANGFUSE_SECRET_KEY", "langfuse_secret_key"),
    )
    langfuse_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST", "langfuse_base_url"),
    )
    langfuse_environment: str = Field(
        default="",
        validation_alias=AliasChoices(
            "LANGFUSE_TRACING_ENVIRONMENT", "langfuse_environment"
        ),
    )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)


settings = Settings()
