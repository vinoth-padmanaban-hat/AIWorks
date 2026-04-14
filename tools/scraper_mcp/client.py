"""Async FastMCP client for the scraper MCP server."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp import Client
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# ── Response models ────────────────────────────────────────────────────────────

class FetchPageResult(BaseModel):
    url: str
    status_code: int
    changed: bool
    raw_html: str = ""
    clean_text: str = ""
    title: str = ""
    etag: str | None = None
    last_modified_header: str | None = None
    content_hash: str = ""
    duration_ms: int = 0
    error: str | None = None


class MediaItem(BaseModel):
    src: str = ""
    alt: str = ""
    score: float = 0.0
    type: str = ""

    @field_validator("src", "alt", "type", mode="before")
    @classmethod
    def _coerce_str_fields(cls, v: object) -> str:
        return "" if v is None else str(v)

    @field_validator("score", mode="before")
    @classmethod
    def _coerce_score(cls, v: object) -> float:
        if v is None:
            return 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0


class LinkItem(BaseModel):
    href: str = ""
    text: str = ""

    @field_validator("href", "text", mode="before")
    @classmethod
    def _coerce_link_str(cls, v: object) -> str:
        return "" if v is None else str(v)


class FetchPageFullResult(BaseModel):
    url: str
    status_code: int
    changed: bool
    clean_text: str = ""
    raw_html: str = ""
    title: str = ""
    content_hash: str = ""
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    images: list[MediaItem] = Field(default_factory=list)
    videos: list[MediaItem] = Field(default_factory=list)
    audio: list[MediaItem] = Field(default_factory=list)
    links: dict[str, list[LinkItem]] = Field(default_factory=dict)
    screenshot_base64: str | None = None
    error: str | None = None


class FetchLinksResult(BaseModel):
    url: str
    links: list[LinkItem] = Field(default_factory=list)
    status_code: int = 0
    duration_ms: int = 0
    error: str | None = None


class DiscoveredUrl(BaseModel):
    url: str
    depth: int = 0
    parent_url: str = ""


class DiscoverUrlsResult(BaseModel):
    seed_url: str
    urls: list[DiscoveredUrl] = Field(default_factory=list)
    total: int = 0
    duration_ms: int = 0
    error: str | None = None


class DeepCrawlPage(BaseModel):
    url: str
    depth: int = 0
    parent_url: str = ""
    clean_text: str = ""
    title: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    images: list[MediaItem] = Field(default_factory=list)
    videos: list[MediaItem] = Field(default_factory=list)
    audio: list[MediaItem] = Field(default_factory=list)
    status_code: int = 200
    error: str | None = None


class DeepCrawlResult(BaseModel):
    seed_url: str
    strategy: str = "bfs"
    pages: list[DeepCrawlPage] = Field(default_factory=list)
    total: int = 0
    duration_ms: int = 0
    error: str | None = None


class ScreenshotResult(BaseModel):
    url: str
    screenshot_base64: str | None = None
    width: int = 0
    height: int = 0
    duration_ms: int = 0
    error: str | None = None


class ExtractStructuredResult(BaseModel):
    url: str
    data: Any = None
    duration_ms: int = 0
    error: str | None = None


# ── Client ─────────────────────────────────────────────────────────────────────

class ScraperMCPClient:
    def __init__(self, base_url: str, timeout_seconds: float = 600.0) -> None:
        clean = base_url.rstrip("/")
        self._endpoint = clean if clean.endswith("/mcp") else f"{clean}/mcp"
        self._timeout = timeout_seconds

    async def _call(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        t0 = time.monotonic()
        try:
            client = Client(self._endpoint)
            async with client:
                result = await client.call_tool(tool_name, payload, timeout=self._timeout)
                data: Any = result.data

                # FastMCP commonly returns Root(...) wrappers (including nested fields).
                # Normalize to plain dict/list for Pydantic validation.
                def _plain(v: Any) -> Any:
                    if hasattr(v, "model_dump"):
                        return _plain(v.model_dump())
                    if hasattr(v, "__dict__"):
                        return {k: _plain(val) for k, val in dict(v.__dict__).items()}
                    if isinstance(v, dict):
                        return {k: _plain(val) for k, val in v.items()}
                    if isinstance(v, list):
                        return [_plain(val) for val in v]
                    if isinstance(v, tuple):
                        return [_plain(val) for val in v]
                    return v

                data = _plain(data)
                if isinstance(data, dict) and "result" in data and len(data) == 1:
                    inner = data["result"]
                    if isinstance(inner, dict):
                        return inner
                    return {"result": inner}
                if isinstance(data, dict):
                    return data
                return {"result": data}
        except Exception as exc:
            logger.warning("[ScraperClient] %s failed: %s", tool_name, exc)
            return {"error": str(exc), "duration_ms": int((time.monotonic() - t0) * 1000)}

    # ── 1. fetch_page ──────────────────────────────────────────────────────────

    async def fetch_page(
        self,
        url: str,
        last_content_hash: str | None = None,
        wait_for: str | None = None,
        js_code: str | None = None,
        session_id: str | None = None,
        scroll_to_bottom: bool = False,
        stealth_mode: bool = False,
        proxy: str | None = None,
        scraping_config: dict[str, Any] | None = None,
        # backward-compat aliases (ignored)
        last_etag: str | None = None,
        last_modified: str | None = None,
    ) -> FetchPageResult:
        data = await self._call("fetch_page", {"req": {
            "url": url,
            "last_content_hash": last_content_hash,
            "wait_for": wait_for,
            "js_code": js_code,
            "session_id": session_id,
            "scroll_to_bottom": scroll_to_bottom,
            "stealth_mode": stealth_mode,
            "proxy": proxy,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return FetchPageResult(url=url, status_code=0, changed=False,
                               content_hash="", duration_ms=0, **data) \
            if "error" in data and "status_code" not in data \
            else FetchPageResult.model_validate(data)

    # ── 2. fetch_page_full ─────────────────────────────────────────────────────

    async def fetch_page_full(
        self,
        url: str,
        include_media: bool = True,
        include_links: bool = True,
        include_raw_html: bool = False,
        screenshot: bool = False,
        wait_for: str | None = None,
        js_code: str | None = None,
        session_id: str | None = None,
        scroll_to_bottom: bool = False,
        stealth_mode: bool = False,
        proxy: str | None = None,
        last_content_hash: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> FetchPageFullResult:
        data = await self._call("fetch_page_full", {"req": {
            "url": url,
            "include_media": include_media,
            "include_links": include_links,
            "include_raw_html": include_raw_html,
            "screenshot": screenshot,
            "wait_for": wait_for,
            "js_code": js_code,
            "session_id": session_id,
            "scroll_to_bottom": scroll_to_bottom,
            "stealth_mode": stealth_mode,
            "proxy": proxy,
            "last_content_hash": last_content_hash,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        # _post returns only {error, duration_ms} on HTTP/network failure — same as fetch_page.
        if "error" in data and "status_code" not in data:
            return FetchPageFullResult(
                url=url,
                status_code=0,
                changed=False,
                clean_text="",
                raw_html="",
                title="",
                content_hash="",
                duration_ms=int(data.get("duration_ms") or 0),
                error=str(data.get("error") or "unknown error"),
            )
        return FetchPageFullResult.model_validate(data)

    # ── 3. fetch_pages_batch ───────────────────────────────────────────────────

    async def fetch_pages_batch(
        self,
        urls: list[str],
        include_media: bool = False,
        include_links: bool = False,
        wait_for: str | None = None,
        js_code: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> list[FetchPageFullResult]:
        data = await self._call("fetch_pages_batch", {"req": {
            "urls": urls,
            "include_media": include_media,
            "include_links": include_links,
            "wait_for": wait_for,
            "js_code": js_code,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        out: list[FetchPageFullResult] = []
        for p in data.get("pages", []):
            if isinstance(p, dict) and "error" in p and "status_code" not in p:
                out.append(
                    FetchPageFullResult(
                        url=str(p.get("url") or ""),
                        status_code=0,
                        changed=False,
                        duration_ms=int(p.get("duration_ms") or 0),
                        error=str(p.get("error") or "unknown error"),
                    )
                )
            else:
                out.append(FetchPageFullResult.model_validate(p))
        return out

    # ── 4. fetch_links ─────────────────────────────────────────────────────────

    async def fetch_links(
        self,
        url: str,
        same_domain_only: bool = True,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        wait_for: str | None = None,
        session_id: str | None = None,
        max_links: int = 200,
        scraping_config: dict[str, Any] | None = None,
    ) -> FetchLinksResult:
        data = await self._call("fetch_links", {"req": {
            "url": url,
            "same_domain_only": same_domain_only,
            "include_patterns": include_patterns or [],
            "exclude_patterns": exclude_patterns or [],
            "wait_for": wait_for,
            "session_id": session_id,
            "max_links": max_links,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return FetchLinksResult.model_validate(data)

    # ── 5. discover_urls ───────────────────────────────────────────────────────

    async def discover_urls(
        self,
        seed_url: str,
        max_depth: int = 2,
        max_total_urls: int = 100,
        same_domain_only: bool = True,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> DiscoverUrlsResult:
        data = await self._call("discover_urls", {"req": {
            "seed_url": seed_url,
            "max_depth": max_depth,
            "max_total_urls": max_total_urls,
            "same_domain_only": same_domain_only,
            "include_patterns": include_patterns or [],
            "exclude_patterns": exclude_patterns or [],
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return DiscoverUrlsResult.model_validate(data)

    # ── 6. deep_crawl ──────────────────────────────────────────────────────────

    async def deep_crawl(
        self,
        seed_url: str,
        strategy: str = "bfs",
        max_depth: int = 2,
        max_pages: int = 50,
        include_external: bool = False,
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
        include_media: bool = False,
        query: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> DeepCrawlResult:
        data = await self._call("deep_crawl", {"req": {
            "seed_url": seed_url,
            "strategy": strategy,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "include_external": include_external,
            "include_patterns": include_patterns or [],
            "exclude_patterns": exclude_patterns or [],
            "include_media": include_media,
            "query": query,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        if "error" in data and "seed_url" not in data:
            return DeepCrawlResult(
                seed_url=seed_url,
                strategy=strategy,
                pages=[],
                total=0,
                duration_ms=int(data.get("duration_ms") or 0),
                error=str(data.get("error") or "unknown error"),
            )
        return DeepCrawlResult.model_validate(data)

    # ── 7. screenshot_page ─────────────────────────────────────────────────────

    async def screenshot_page(
        self,
        url: str,
        wait_for: str | None = None,
        full_page: bool = False,
        js_code: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> ScreenshotResult:
        data = await self._call("screenshot_page", {"req": {
            "url": url,
            "wait_for": wait_for,
            "full_page": full_page,
            "js_code": js_code,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return ScreenshotResult.model_validate(data)

    # ── 8. extract_structured ──────────────────────────────────────────────────

    async def extract_structured(
        self,
        url: str,
        schema_json: dict[str, Any],
        wait_for: str | None = None,
        js_code: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> ExtractStructuredResult:
        data = await self._call("extract_structured", {"req": {
            "url": url,
            "schema_json": schema_json,
            "wait_for": wait_for,
            "js_code": js_code,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return ExtractStructuredResult.model_validate(data)

    # ── 9. extract_structured_no_llm ───────────────────────────────────────────

    async def extract_structured_no_llm(
        self,
        url: str,
        extraction_schema: dict[str, Any],
        wait_for: str | None = None,
        js_code: str | None = None,
        scraping_config: dict[str, Any] | None = None,
    ) -> ExtractStructuredResult:
        data = await self._call("extract_structured_no_llm", {"req": {
            "url": url,
            "extraction_schema": extraction_schema,
            "wait_for": wait_for,
            "js_code": js_code,
            **({"scraping_config": scraping_config} if scraping_config else {}),
        }})
        return ExtractStructuredResult.model_validate(data)
