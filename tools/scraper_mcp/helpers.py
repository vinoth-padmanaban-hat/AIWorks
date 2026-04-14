"""
Scraper helpers — crawl4ai (Playwright-based) backend.

Handles:
  - Static HTML (SSR, plain HTML)
  - Client-Side Rendered apps (React, Vue, Angular, Next.js CSR, etc.)
  - JS-heavy pages with deferred content, infinite scroll, overlays
  - Deep crawls (BFS / DFS / BestFirst / Adaptive)
  - Media extraction (images, videos, audio)
  - Structured data extraction (LLM-based and CSS/XPath)
  - Screenshots
  - Batch parallel crawling

Falls back to requests+trafilatura for text-only when crawl4ai is unavailable.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from collections.abc import AsyncIterable
from typing import Any
from urllib.parse import urljoin, urlparse

from app.core.config import settings

logger = logging.getLogger(__name__)


def pick_primary_image_url(images: list[Any] | None) -> str | None:
    """
    Choose a hero image from scraper media lists (dicts or objects with src/score).
    Higher score wins; falls back to first usable src.
    """
    if not images:
        return None
    scored: list[tuple[float, str]] = []
    for img in images:
        src = ""
        score = 0.0
        if isinstance(img, dict):
            src = str(img.get("src") or "").strip()
            score = float(img.get("score") or 0.0)
        else:
            src = str(getattr(img, "src", "") or "").strip()
            score = float(getattr(img, "score", 0.0) or 0.0)
        if src:
            scored.append((score, src))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def compact_media_payload(
    images: list[Any] | None,
    videos: list[Any] | None,
    audio: list[Any] | None,
    *,
    max_each: int = 12,
) -> dict[str, Any]:
    """Compact images/videos/audio for JSONB storage (UI / newsletters)."""

    def row(m: Any, kind: str) -> dict[str, Any]:
        if isinstance(m, dict):
            src = str(m.get("src") or "").strip()
            alt = str(m.get("alt") or "")
        else:
            src = str(getattr(m, "src", "") or "").strip()
            alt = str(getattr(m, "alt", "") or "")
        return {"type": kind, "src": src, "alt": alt}

    out: dict[str, Any] = {"images": [], "videos": [], "audio": []}
    for m in (images or [])[:max_each]:
        r = row(m, "image")
        if r["src"]:
            out["images"].append(r)
    for m in (videos or [])[:max_each]:
        r = row(m, "video")
        if r["src"]:
            out["videos"].append(r)
    for m in (audio or [])[:max_each]:
        r = row(m, "audio")
        if r["src"]:
            out["audio"].append(r)
    return out


# One browser process for the whole service — avoids Playwright launch overhead.
_shared_crawler: Any = None
_init_lock = asyncio.Lock()
_arun_lock = asyncio.Lock()


async def _collect_arun_results(arun_result: Any) -> list[Any]:
    """
    Normalize crawl4ai arun() output across versions.
    Some versions return an async iterable, others return a list/single result.
    """
    if isinstance(arun_result, AsyncIterable):
        out: list[Any] = []
        async for item in arun_result:
            out.append(item)
        return out
    if isinstance(arun_result, list):
        return arun_result
    if arun_result is None:
        return []
    return [arun_result]


# ── Browser / crawler lifecycle ───────────────────────────────────────────────

def _default_browser_config() -> Any:
    from crawl4ai import BrowserConfig

    return BrowserConfig(
        headless=True,
        verbose=False,
        memory_saving_mode=True,
    )


async def init_shared_crawler() -> None:
    global _shared_crawler
    async with _init_lock:
        if _shared_crawler is not None:
            return
        try:
            from crawl4ai import AsyncWebCrawler
        except ImportError:
            logger.warning("crawl4ai not installed — shared crawler not started")
            return
        _shared_crawler = AsyncWebCrawler(config=_default_browser_config())
        await _shared_crawler.start()
        logger.info("[Scraper] shared AsyncWebCrawler started")


async def shutdown_shared_crawler() -> None:
    global _shared_crawler
    async with _init_lock:
        if _shared_crawler is None:
            return
        await _shared_crawler.close()
        _shared_crawler = None
        logger.info("[Scraper] shared AsyncWebCrawler closed")


# ── Shared helpers ─────────────────────────────────────────────────────────────

def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()


def _normalise_url(href: str, base_url: str) -> str | None:
    try:
        absolute = urljoin(base_url, href.strip())
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            return None
        return absolute.split("#")[0].rstrip("/")
    except Exception:
        return None


def _same_domain(url_a: str, url_b: str) -> bool:
    def _host(u: str) -> str:
        return urlparse(u).netloc.lstrip("www.")
    return _host(url_a) == _host(url_b)


def _base_run_config(
    wait_for: str | None = None,
    js_code: str | None = None,
    scroll_to_bottom: bool = False,
    remove_overlays: bool = True,
    word_count_threshold: int = 10,
) -> Any:
    from crawl4ai import CacheMode, CrawlerRunConfig

    effective_js = js_code or ""
    if scroll_to_bottom:
        scroll_js = (
            "window.scrollTo(0, document.body.scrollHeight); "
            "await new Promise(r => setTimeout(r, 1500));"
        )
        effective_js = f"{effective_js}\n{scroll_js}".strip()

    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        wait_for=wait_for,
        js_code=effective_js or None,
        word_count_threshold=word_count_threshold,
        excluded_tags=["script", "style", "nav", "footer", "header"],
        remove_overlay_elements=remove_overlays,
    )


def _extract_media(result: Any) -> dict[str, list[dict[str, Any]]]:
    """Pull images/videos/audio from a crawl4ai CrawlResult."""
    media: dict[str, list[dict[str, Any]]] = {"images": [], "videos": [], "audio": []}
    if not hasattr(result, "media") or not result.media:
        return media
    for key in ("images", "videos", "audio"):
        items = result.media.get(key, [])
        media[key] = [
            {
                "src": item.get("src") or "",
                "alt": item.get("alt") or "",
                "score": float(item.get("score") or 0.0),
                "type": item.get("type") or key.rstrip("s"),
            }
            for item in items
            if item.get("src")
        ]
    return media


def _extract_links(result: Any, same_domain_only: bool, base_url: str) -> dict[str, list[dict]]:
    """Pull internal/external links from a crawl4ai CrawlResult."""
    links: dict[str, list[dict]] = {"internal": [], "external": []}
    if not hasattr(result, "links") or not result.links:
        return links
    for category in ("internal", "external"):
        if same_domain_only and category == "external":
            continue
        for item in result.links.get(category, []):
            href = item.get("href", "") if isinstance(item, dict) else str(item)
            norm = _normalise_url(href, base_url)
            if norm:
                links[category].append({"href": norm, "text": item.get("text", "")})
    return links


def _extract_metadata(result: Any) -> dict[str, str]:
    meta = result.metadata or {}
    return {
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "published_at": meta.get("published_date", ""),
        "canonical_url": meta.get("canonical", ""),
        "description": meta.get("description", ""),
    }


# ── 1. fetch_page — single page, text only (existing, extended) ───────────────

async def fetch_page_async(
    url: str,
    last_content_hash: str | None = None,
    wait_for: str | None = None,
    js_code: str | None = None,
    session_id: str | None = None,
    scroll_to_bottom: bool = False,
    stealth_mode: bool = False,
    proxy: str | None = None,
    scraping_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig
    except ImportError:
        return fetch_page_sync(url=url, last_content_hash=last_content_hash)

    t0 = time.monotonic()
    try:
        await init_shared_crawler()
        crawler = _shared_crawler
        if crawler is None:
            return fetch_page_sync(url=url, last_content_hash=last_content_hash)

        # Stealth / proxy require a fresh crawler instance
        if stealth_mode or proxy:
            browser_cfg = BrowserConfig(
                headless=True,
                verbose=False,
                memory_saving_mode=True,
                **({"proxy": proxy} if proxy else {}),
            )
            crawler = AsyncWebCrawler(config=browser_cfg)
            await crawler.start()

        run_cfg = _base_run_config(
            wait_for=wait_for,
            js_code=js_code,
            scroll_to_bottom=scroll_to_bottom,
        )

        async with _arun_lock:
            result = await crawler.arun(
                url=url,
                config=run_cfg,
                **({"session_id": session_id} if session_id else {}),
            )

        if stealth_mode or proxy:
            await crawler.close()

        duration_ms = int((time.monotonic() - t0) * 1000)

        if not result.success:
            return _error_page(url, result.status_code or 0, result.error_message, duration_ms)

        raw_html = result.html or ""
        clean_text = result.markdown or result.cleaned_html or ""
        title = (result.metadata or {}).get("title", "")
        new_hash = content_hash(clean_text or raw_html)
        changed = new_hash != (last_content_hash or "")

        return {
            "url": url,
            "status_code": result.status_code or 200,
            "changed": changed,
            "raw_html": raw_html if changed else "",
            "clean_text": clean_text if changed else "",
            "title": title,
            "etag": None,
            "last_modified_header": None,
            "content_hash": new_hash,
            "duration_ms": duration_ms,
            "error": None,
        }

    except Exception as exc:
        logger.exception("fetch_page_async error for %s: %s", url, exc)
        return _error_page(url, 0, str(exc), int((time.monotonic() - t0) * 1000))


def _error_page(url: str, status: int, error: str | None, duration_ms: int) -> dict[str, Any]:
    return {
        "url": url,
        "status_code": status,
        "changed": False,
        "raw_html": "",
        "clean_text": "",
        "title": "",
        "etag": None,
        "last_modified_header": None,
        "content_hash": "",
        "duration_ms": duration_ms,
        "error": error or "unknown error",
    }


# ── 2. fetch_page_full — text + media + links + optional screenshot ───────────

async def fetch_page_full_async(
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
) -> dict[str, Any]:
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    except ImportError:
        base = fetch_page_sync(url=url, last_content_hash=last_content_hash)
        base.update({"metadata": {}, "images": [], "videos": [], "audio": [],
                     "links": {"internal": [], "external": []}, "screenshot_base64": None})
        return base

    t0 = time.monotonic()
    try:
        await init_shared_crawler()
        crawler = _shared_crawler
        if crawler is None:
            base = fetch_page_sync(url=url, last_content_hash=last_content_hash)
            base.update({"metadata": {}, "images": [], "videos": [], "audio": [],
                         "links": {"internal": [], "external": []}, "screenshot_base64": None})
            return base

        if stealth_mode or proxy:
            browser_cfg = BrowserConfig(
                headless=True, verbose=False, memory_saving_mode=True,
                **({"proxy": proxy} if proxy else {}),
            )
            crawler = AsyncWebCrawler(config=browser_cfg)
            await crawler.start()

        effective_js = js_code or ""
        if scroll_to_bottom:
            effective_js += "\nwindow.scrollTo(0, document.body.scrollHeight); await new Promise(r => setTimeout(r, 1500));"

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            js_code=effective_js.strip() or None,
            word_count_threshold=5,
            excluded_tags=["script", "style"],
            remove_overlay_elements=True,
            screenshot=screenshot,
        )

        async with _arun_lock:
            result = await crawler.arun(
                url=url, config=run_cfg,
                **({"session_id": session_id} if session_id else {}),
            )

        if stealth_mode or proxy:
            await crawler.close()

        duration_ms = int((time.monotonic() - t0) * 1000)

        if not result.success:
            return {
                **_error_page(url, result.status_code or 0, result.error_message, duration_ms),
                "metadata": {}, "images": [], "videos": [], "audio": [],
                "links": {"internal": [], "external": []}, "screenshot_base64": None,
            }

        raw_html = result.html or ""
        clean_text = result.markdown or result.cleaned_html or ""
        new_hash = content_hash(clean_text or raw_html)
        changed = new_hash != (last_content_hash or "")

        media = _extract_media(result) if include_media else {"images": [], "videos": [], "audio": []}
        links = _extract_links(result, same_domain_only=False, base_url=url) if include_links else {"internal": [], "external": []}
        metadata = _extract_metadata(result)

        screenshot_b64: str | None = None
        if screenshot and hasattr(result, "screenshot") and result.screenshot:
            screenshot_b64 = (
                base64.b64encode(result.screenshot).decode()
                if isinstance(result.screenshot, bytes)
                else result.screenshot
            )

        return {
            "url": url,
            "status_code": result.status_code or 200,
            "changed": changed,
            "clean_text": clean_text if changed else "",
            "raw_html": raw_html if (changed and include_raw_html) else "",
            "title": metadata["title"],
            "content_hash": new_hash,
            "duration_ms": duration_ms,
            "metadata": metadata,
            "images": media["images"],
            "videos": media["videos"],
            "audio": media["audio"],
            "links": links,
            "screenshot_base64": screenshot_b64,
            "error": None,
        }

    except Exception as exc:
        logger.exception("fetch_page_full_async error for %s: %s", url, exc)
        return {
            **_error_page(url, 0, str(exc), int((time.monotonic() - t0) * 1000)),
            "metadata": {}, "images": [], "videos": [], "audio": [],
            "links": {"internal": [], "external": []}, "screenshot_base64": None,
        }


# ── 3. fetch_pages_batch — parallel multi-URL crawl ──────────────────────────

async def fetch_pages_batch_async(
    urls: list[str],
    include_media: bool = False,
    include_links: bool = False,
    max_concurrent: int = 3,
    wait_for: str | None = None,
    js_code: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
    except ImportError:
        return [fetch_page_sync(url=u) for u in urls]

    t0 = time.monotonic()
    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return [fetch_page_sync(url=u) for u in urls]

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            js_code=js_code,
            word_count_threshold=5,
            excluded_tags=["script", "style"],
            remove_overlay_elements=True,
        )

        # arun_many handles concurrency internally
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _crawl_one(url: str) -> dict[str, Any]:
            async with semaphore:
                async with _arun_lock:
                    result = await _shared_crawler.arun(url=url, config=run_cfg)
                if not result.success:
                    return {
                        **_error_page(url, result.status_code or 0, result.error_message, 0),
                        "metadata": {}, "images": [], "videos": [], "audio": [],
                        "links": {"internal": [], "external": []},
                    }
                clean_text = result.markdown or result.cleaned_html or ""
                media = _extract_media(result) if include_media else {"images": [], "videos": [], "audio": []}
                links = _extract_links(result, same_domain_only=False, base_url=url) if include_links else {"internal": [], "external": []}
                return {
                    "url": url,
                    "status_code": result.status_code or 200,
                    "changed": True,
                    "clean_text": clean_text,
                    "raw_html": "",
                    "title": (result.metadata or {}).get("title", ""),
                    "content_hash": content_hash(clean_text),
                    "duration_ms": 0,
                    "metadata": _extract_metadata(result),
                    "images": media["images"],
                    "videos": media["videos"],
                    "audio": media["audio"],
                    "links": links,
                    "error": None,
                }

        results = await asyncio.gather(*[_crawl_one(u) for u in urls], return_exceptions=False)
        logger.info(
            "[Scraper] fetch_pages_batch  total=%d  elapsed=%dms",
            len(urls), int((time.monotonic() - t0) * 1000),
        )
        return list(results)

    except Exception as exc:
        logger.exception("fetch_pages_batch_async error: %s", exc)
        return [_error_page(u, 0, str(exc), 0) for u in urls]


# ── 4. fetch_links — single-page link extraction (existing, extended) ─────────

async def fetch_links_async(
    url: str,
    same_domain_only: bool = True,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    wait_for: str | None = None,
    session_id: str | None = None,
    max_links: int = 200,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
    except ImportError:
        return {"url": url, "links": [], "status_code": 0,
                "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"url": url, "links": [], "status_code": 0,
                    "duration_ms": 0, "error": "crawl4ai not available"}

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            word_count_threshold=5,
            excluded_tags=["script", "style"],
            remove_overlay_elements=True,
        )
        async with _arun_lock:
            result = await _shared_crawler.arun(
                url=url, config=run_cfg,
                **({"session_id": session_id} if session_id else {}),
            )

        duration_ms = int((time.monotonic() - t0) * 1000)
        if not result.success:
            return {"url": url, "links": [], "status_code": result.status_code or 0,
                    "duration_ms": duration_ms, "error": result.error_message}

        raw_links: list[dict] = []
        if hasattr(result, "links") and result.links:
            raw_links.extend(result.links.get("internal", []))
            if not same_domain_only:
                raw_links.extend(result.links.get("external", []))

        seen: set[str] = set()
        filtered: list[dict] = []
        for item in raw_links:
            href = item.get("href", "") if isinstance(item, dict) else str(item)
            norm = _normalise_url(href, url)
            if not norm or norm in seen:
                continue
            if same_domain_only and not _same_domain(norm, url):
                continue
            if include_patterns and not any(p in norm for p in include_patterns):
                continue
            if exclude_patterns and any(p in norm for p in exclude_patterns):
                continue
            seen.add(norm)
            filtered.append({"href": norm, "text": item.get("text", "") if isinstance(item, dict) else ""})
            if len(filtered) >= max_links:
                break

        logger.info(
            "[Scraper] fetch_links  url=%s  found=%d  filtered=%d",
            url[:80], len(raw_links), len(filtered),
        )
        return {
            "url": url,
            "links": filtered,
            "status_code": result.status_code or 200,
            "duration_ms": duration_ms,
            "error": None,
        }

    except Exception as exc:
        logger.exception("fetch_links_async error for %s: %s", url, exc)
        return {"url": url, "links": [], "status_code": 0,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── 5. discover_urls — multi-depth URL discovery (prefetch, no content) ───────

async def discover_urls_async(
    seed_url: str,
    max_depth: int = 2,
    max_total_urls: int = 100,
    same_domain_only: bool = True,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
        from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
    except ImportError:
        return {"seed_url": seed_url, "urls": [], "total": 0,
                "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"seed_url": seed_url, "urls": [], "total": 0,
                    "duration_ms": 0, "error": "crawl4ai not available"}

        strategy = BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_total_urls,
            include_external=not same_domain_only,
        )

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            deep_crawl_strategy=strategy,
            word_count_threshold=0,
            # prefetch=True skips markdown/media — fast URL discovery only
        )

        discovered: list[dict[str, Any]] = []
        async with _arun_lock:
            arun_result = await _shared_crawler.arun(url=seed_url, config=run_cfg)
            results = await _collect_arun_results(arun_result)

        for result in results:
            if not result.success:
                continue
            url = str(result.url or "")
            depth = (result.metadata or {}).get("depth", 0)
            parent = str((result.metadata or {}).get("parent_url") or "")
            if include_patterns and not any(p in url for p in include_patterns):
                continue
            if exclude_patterns and any(p in url for p in exclude_patterns):
                continue
            discovered.append({"url": url, "depth": depth, "parent_url": parent})
            if len(discovered) >= max_total_urls:
                break

        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "[Scraper] discover_urls  seed=%s  found=%d  elapsed=%dms",
            seed_url[:80], len(discovered), duration_ms,
        )
        return {
            "seed_url": seed_url,
            "urls": discovered,
            "total": len(discovered),
            "duration_ms": duration_ms,
            "error": None,
        }

    except Exception as exc:
        logger.exception("discover_urls_async error for %s: %s", seed_url, exc)
        return {"seed_url": seed_url, "urls": [], "total": 0,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── 6. deep_crawl — full content deep crawl with strategy ────────────────────

async def deep_crawl_async(
    seed_url: str,
    strategy: str = "bfs",
    max_depth: int = 2,
    max_pages: int = 50,
    include_external: bool = False,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    include_media: bool = False,
    query: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
        from crawl4ai.deep_crawling import (
            BestFirstCrawlingStrategy,
            BFSDeepCrawlStrategy,
            DFSDeepCrawlStrategy,
        )
    except ImportError:
        return {"seed_url": seed_url, "pages": [], "total": 0,
                "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"seed_url": seed_url, "pages": [], "total": 0,
                    "duration_ms": 0, "error": "crawl4ai not available"}

        strategy_map = {
            "bfs": BFSDeepCrawlStrategy,
            "dfs": DFSDeepCrawlStrategy,
            "best_first": BestFirstCrawlingStrategy,
        }
        StrategyClass = strategy_map.get(strategy, BFSDeepCrawlStrategy)
        crawl_strategy = StrategyClass(
            max_depth=max_depth,
            max_pages=max_pages,
            include_external=include_external,
        )

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            deep_crawl_strategy=crawl_strategy,
            word_count_threshold=10,
            excluded_tags=["script", "style"],
            remove_overlay_elements=True,
        )

        pages: list[dict[str, Any]] = []
        async with _arun_lock:
            arun_result = await _shared_crawler.arun(url=seed_url, config=run_cfg)
            results = await _collect_arun_results(arun_result)

        for result in results:
            if not result.success:
                continue
            url = str(result.url or "")
            depth = (result.metadata or {}).get("depth", 0)
            parent = str((result.metadata or {}).get("parent_url") or "")

            if include_patterns and not any(p in url for p in include_patterns):
                continue
            if exclude_patterns and any(p in url for p in exclude_patterns):
                continue

            clean_text = result.markdown or result.cleaned_html or ""
            media = _extract_media(result) if include_media else {"images": [], "videos": [], "audio": []}

            pages.append({
                "url": url,
                "depth": depth,
                "parent_url": parent,
                "clean_text": clean_text,
                "title": str((result.metadata or {}).get("title") or ""),
                "metadata": _extract_metadata(result),
                "images": media["images"],
                "videos": media["videos"],
                "audio": media["audio"],
                "status_code": result.status_code or 200,
                "error": None,
            })

            if len(pages) >= max_pages:
                break

        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.info(
            "[Scraper] deep_crawl  seed=%s  strategy=%s  pages=%d  elapsed=%dms",
            seed_url[:80], strategy, len(pages), duration_ms,
        )
        return {
            "seed_url": seed_url,
            "strategy": strategy,
            "pages": pages,
            "total": len(pages),
            "duration_ms": duration_ms,
            "error": None,
        }

    except Exception as exc:
        logger.exception("deep_crawl_async error for %s: %s", seed_url, exc)
        return {"seed_url": seed_url, "pages": [], "total": 0,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── 7. screenshot_page ────────────────────────────────────────────────────────

async def screenshot_page_async(
    url: str,
    wait_for: str | None = None,
    full_page: bool = False,
    js_code: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
    except ImportError:
        return {"url": url, "screenshot_base64": None, "width": 0, "height": 0,
                "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"url": url, "screenshot_base64": None, "width": 0, "height": 0,
                    "duration_ms": 0, "error": "crawl4ai not available"}

        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            js_code=js_code,
            screenshot=True,
            word_count_threshold=0,
        )

        async with _arun_lock:
            result = await _shared_crawler.arun(url=url, config=run_cfg)

        duration_ms = int((time.monotonic() - t0) * 1000)

        if not result.success or not getattr(result, "screenshot", None):
            return {"url": url, "screenshot_base64": None, "width": 0, "height": 0,
                    "duration_ms": duration_ms, "error": result.error_message or "no screenshot"}

        screenshot_b64 = (
            base64.b64encode(result.screenshot).decode()
            if isinstance(result.screenshot, bytes)
            else result.screenshot
        )

        return {
            "url": url,
            "screenshot_base64": screenshot_b64,
            "width": 1280,
            "height": 900,
            "duration_ms": duration_ms,
            "error": None,
        }

    except Exception as exc:
        logger.exception("screenshot_page_async error for %s: %s", url, exc)
        return {"url": url, "screenshot_base64": None, "width": 0, "height": 0,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── 8. extract_structured — LLM-based schema extraction ──────────────────────

async def extract_structured_async(
    url: str,
    schema_json: dict[str, Any],
    wait_for: str | None = None,
    js_code: str | None = None,
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        from crawl4ai import CacheMode, CrawlerRunConfig, LLMConfig
        from crawl4ai.extraction_strategy import LLMExtractionStrategy
    except ImportError:
        return {"url": url, "data": None, "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"url": url, "data": None, "duration_ms": 0, "error": "crawl4ai not available"}

        extraction = LLMExtractionStrategy(
            schema=schema_json,
            verbose=False,
            llm_config=LLMConfig(
                provider=f"openai/{settings.openai_model}",
                api_token=settings.openai_api_key or None,
            ),
        )
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            js_code=js_code,
            extraction_strategy=extraction,
            word_count_threshold=5,
        )

        async with _arun_lock:
            result = await _shared_crawler.arun(url=url, config=run_cfg)

        duration_ms = int((time.monotonic() - t0) * 1000)

        if not result.success:
            return {"url": url, "data": None, "duration_ms": duration_ms,
                    "error": result.error_message}

        import json as _json
        data = None
        if result.extracted_content:
            try:
                data = _json.loads(result.extracted_content)
            except Exception:
                data = result.extracted_content

        return {"url": url, "data": data, "duration_ms": duration_ms, "error": None}

    except Exception as exc:
        logger.exception("extract_structured_async error for %s: %s", url, exc)
        return {"url": url, "data": None,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── 9. extract_structured_no_llm — JsonCssExtractionStrategy (CSS selectors) ───

async def extract_structured_no_llm_async(
    url: str,
    extraction_schema: dict[str, Any],
    wait_for: str | None = None,
    js_code: str | None = None,
) -> dict[str, Any]:
    """
    CSS-based structured extraction via crawl4ai ``JsonCssExtractionStrategy``.

    **Reference (non-LLM strategies overview, manual CSS/XPath/regex, nesting, ``generate_schema``):**
    https://docs.crawl4ai.com/assets/llm.txt/txt/extraction-no-llm.txt

    **This function implements only** ``JsonCssExtractionStrategy`` (CSS selectors). Pass the
    schema dict described in that doc under “Manual CSS/XPath Strategies” — including
    ``baseSelector``, optional ``baseFields``, ``fields``, and nested field types such as
    ``nested_list``, ``nested``, and ``list`` with recursive ``fields``.

    **Not implemented here** (use crawl4ai in-process or extend the MCP): ``JsonXPathExtractionStrategy``,
    ``RegexExtractionStrategy``, and ``JsonCssExtractionStrategy.generate_schema`` (one-shot LLM schema generation).

    **Shorthand** (field name → selector string) is expanded by ``_normalize_extraction_schema``;
    full crawl4ai objects are passed through unchanged when they already include ``baseSelector``
    and a ``fields`` list.
    """
    t0 = time.monotonic()

    def _normalize_extraction_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """
        Accept either crawl4ai-native schema or shorthand key->selector format.
        Shorthand examples supported:
        - {"title": "h1", "links": "a[href]"}
        - {"baseSelector": "article", "title": "h2", "url": "a[href]"}
        """
        if not isinstance(schema, dict):
            return {
                "name": "ExtractedItems",
                "baseSelector": "body",
                "fields": [],
            }

        # Already in expected crawl4ai format.
        if "baseSelector" in schema and isinstance(schema.get("fields"), list):
            return schema

        base_selector = str(schema.get("baseSelector") or "body")
        name = str(schema.get("name") or "ExtractedItems")

        skip_keys = {
            "name",
            "baseSelector",
            "fields",
            "base_selector",
            "selector",
            "selectors",
        }
        fields: list[dict[str, Any]] = []

        for field_name, field_selector in schema.items():
            if field_name in skip_keys:
                continue
            if not isinstance(field_selector, str) or not field_selector.strip():
                continue
            selector = field_selector.strip()
            field_type = "attribute" if "[href]" in selector else "text"
            field: dict[str, Any] = {
                "name": str(field_name),
                "selector": selector,
                "type": field_type,
            }
            if field_type == "attribute":
                field["attribute"] = "href"
            fields.append(field)

        return {
            "name": name,
            "baseSelector": base_selector,
            "fields": fields,
        }

    try:
        from crawl4ai import CacheMode, CrawlerRunConfig
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    except ImportError:
        return {"url": url, "data": None, "duration_ms": 0, "error": "crawl4ai not installed"}

    try:
        await init_shared_crawler()
        if _shared_crawler is None:
            return {"url": url, "data": None, "duration_ms": 0, "error": "crawl4ai not available"}

        normalized_schema = _normalize_extraction_schema(extraction_schema)
        extraction = JsonCssExtractionStrategy(schema=normalized_schema, verbose=False)
        run_cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            wait_for=wait_for,
            js_code=js_code,
            extraction_strategy=extraction,
            word_count_threshold=0,
        )

        async with _arun_lock:
            result = await _shared_crawler.arun(url=url, config=run_cfg)

        duration_ms = int((time.monotonic() - t0) * 1000)

        if not result.success:
            return {"url": url, "data": None, "duration_ms": duration_ms,
                    "error": result.error_message}

        import json as _json
        data = None
        if result.extracted_content:
            try:
                data = _json.loads(result.extracted_content)
            except Exception:
                data = result.extracted_content

        return {"url": url, "data": data, "duration_ms": duration_ms, "error": None}

    except Exception as exc:
        logger.exception("extract_structured_no_llm_async error for %s: %s", url, exc)
        return {"url": url, "data": None,
                "duration_ms": int((time.monotonic() - t0) * 1000), "error": str(exc)}


# ── requests + trafilatura sync fallback ──────────────────────────────────────

def fetch_page_sync(
    url: str,
    last_content_hash: str | None = None,
    last_etag: str | None = None,
    last_modified: str | None = None,
) -> dict[str, Any]:
    import requests

    headers: dict[str, str] = {"User-Agent": "AIWorksBot/1.0 (research)"}
    if last_etag:
        headers["If-None-Match"] = last_etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    t0 = time.monotonic()
    try:
        resp = requests.get(url, headers=headers, timeout=20, allow_redirects=True)
        duration_ms = int((time.monotonic() - t0) * 1000)
    except requests.RequestException as exc:
        return _error_page(url, 0, str(exc), int((time.monotonic() - t0) * 1000))

    if resp.status_code == 304:
        return {
            "url": url, "status_code": 304, "changed": False,
            "raw_html": "", "clean_text": "", "title": "",
            "etag": last_etag, "last_modified_header": last_modified,
            "content_hash": last_content_hash or "", "duration_ms": duration_ms, "error": None,
        }

    raw_html = resp.text
    try:
        import trafilatura
        clean_text = trafilatura.extract(raw_html, url=url) or ""
        meta = trafilatura.extract_metadata(raw_html, default_url=url)
        title = (meta.title or "") if meta else ""
    except Exception:
        clean_text = re.sub(r"<[^>]+>", " ", raw_html)
        clean_text = " ".join(clean_text.split())[:8000]
        title = ""

    new_hash = content_hash(clean_text or raw_html)
    changed = new_hash != (last_content_hash or "")

    return {
        "url": url,
        "status_code": resp.status_code,
        "changed": changed,
        "raw_html": raw_html if changed else "",
        "clean_text": clean_text if changed else "",
        "title": title,
        "etag": resp.headers.get("ETag"),
        "last_modified_header": resp.headers.get("Last-Modified"),
        "content_hash": new_hash,
        "duration_ms": duration_ms,
        "error": None,
    }


def html_to_text(raw_html: str, url: str = "") -> dict[str, str]:
    try:
        import trafilatura
        from trafilatura.settings import use_config
        cfg = use_config()
        cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
        body = trafilatura.extract(raw_html, url=url, config=cfg) or ""
        meta = trafilatura.extract_metadata(raw_html, default_url=url)
        title = (meta.title or "") if meta else ""
        return {"title": title, "text": body}
    except Exception:
        body = re.sub(r"<[^>]+>", " ", raw_html)
        return {"title": "", "text": " ".join(body.split())[:8000]}
