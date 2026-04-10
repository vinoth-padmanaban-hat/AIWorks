# Content Curation & Publishing – Tenant PoC Design

This document describes the PoC architecture for a **content management / news feed** service for a tenant:

- Scrape a list of websites.
- Normalize, tag (≤ 6 tags), and score content.
- Assemble a news feed.
- Let a human reviewer approve/edit.
- Publish approved items to LinkedIn, blog, and other social channels.[web:237][web:239][web:244]

It follows our persona / skill / agent / policy / registry model.

---

## 1. Personas (Content Tenant)

### 1.1 `content_curator`

- **Who**: Marketing/analyst persona configuring sources and curation rules.
- **Goals**:
  - Maintain list of content sources (websites, RSS).
  - Define topics/tags and scoring rules per tenant.
  - Get a ranked candidate feed daily.
- **Constraints**:
  - No direct posting to social; read/write only on configuration and feed metadata.

### 1.2 `content_reviewer_editor`

- **Who**: Human editor reviewing feed items.
- **Goals**:
  - Review auto‑generated items (title, summary, tags, score).
  - Edit and approve/reject items.
  - Choose which platforms to post to.
- **Constraints**:
  - Must always approve before any item is posted.

### 1.3 `social_publisher_manager`

- **Who**: Social / comms owner.
- **Goals**:
  - View queue of approved items by platform.
  - Adjust schedule and messaging if needed.
- **Constraints**:
  - Can pause/cancel scheduled posts.

---

## 2. Tenant Policy

**Tenant ID**: `content_curation_tenant`

### 2.1 Capabilities

Allowed capabilities (skills):

- `scrape_source_urls`
- `extract_and_normalize_articles`
- `tag_content_item`
- `score_content_item`
- `assemble_news_feed`
- `generate_platform_post_variants`
- `create_review_task_for_item`
- `approve_or_reject_item`
- `schedule_publication_to_platforms`

Blocked: none initially.  
`defaultAllow: false` (only explicit skills are allowed).

### 2.2 Tools

Allowed tools:

- `http_scraper_tool`
- `rss_fetch_tool`
- `html_to_text_tool`
- `tagging_model_tool`
- `scoring_model_tool`
- `linkedin_publisher_tool`
- `blog_publisher_tool`
- `generic_social_publisher_tool`

Blocked tools: anything not explicitly allowed (e.g., generic web search).

### 2.3 Security & Governance

Security:

- `allowExternalApiCalls: true` but only to whitelisted domains (configured sources + posting APIs).
- `allowWebScraping: true` but only for configured `sources.url`.
- `allowSensitiveDataAccess: false` (no PII).

Governance:

- `requireHumanApprovalForCapabilities`:
  - `schedule_publication_to_platforms`
- Only personas with role `social_publisher_manager` or `content_reviewer_editor` can approve publishing.

---

## 3. Skills / Capabilities

All skills live in the **Skill Registry** with JSON schemas. Below is the conceptual API for each.

### 3.1 `scrape_source_urls`

Fetch new raw content from a list of sources.

- **Input**:
  - `tenant_id: string`
  - `source_ids: string[]` (or raw URLs in PoC)
  - `since_timestamp?: ISO8601 string`
- **Output**:
  - `raw_items: RawItem[]`
    - `source_id: string`
    - `url: string`
    - `raw_html: string`
    - `fetched_at: ISO8601 string`

### 3.2 `extract_and_normalize_articles`

Turn raw HTML into normalized articles.

- **Input**:
  - `raw_items: RawItem[]`
- **Output**:
  - `articles: Article[]`
    - `id: string`
    - `source_id: string`
    - `url: string`
    - `canonical_url?: string`
    - `title: string`
    - `author?: string`
    - `published_at?: ISO8601 string`
    - `text: string`
    - `created_at: ISO8601 string`

### 3.3 `tag_content_item`

Assign up to 6 tags from tenant’s taxonomy, with confidences.

- **Input**:
  - `tenant_id: string`
  - `article_id: string`
  - `candidate_tags: string[]` (tenant tag set)
- **Output**:
  - `tags: string[]` (max length 6)
  - `tag_confidences: Record<string, number>`

### 3.4 `score_content_item`

Compute overall relevance/quality score.

- **Input**:
  - `tenant_id: string`
  - `article_id: string`
  - `features: object` (source authority, age, tags, length, etc.)
- **Output**:
  - `score: number` (0–100)
  - `relevance_breakdown: object` (e.g., `{ freshness: 0.9, topic_match: 0.8 }`)
  - `spam_or_low_quality: boolean`

### 3.5 `assemble_news_feed`

Rank/bundle items into a candidate feed.

- **Input**:
  - `tenant_id: string`
  - `time_window: { from: ISO8601, to: ISO8601 }`
  - `min_score: number`
  - `max_items?: number`
- **Output**:
  - `feed_items: FeedItem[]`
    - `article_id: string`
    - `title: string`
    - `score: number`
    - `primary_tags: string[]`
    - `snippet: string`

### 3.6 `generate_platform_post_variants`

Generate LinkedIn/blog/social drafts per article.

- **Input**:
  - `article_id: string`
  - `platforms: ("linkedin" | "blog" | "twitter" | string)[]`
  - `tone_preferences?: object`
- **Output**:
  - `post_variants: PostVariant[]`
    - `platform: string`
    - `title: string`
    - `body: string`
    - `cta?: string`
    - `suggested_hashtags: string[]`

### 3.7 `create_review_task_for_item`

Create a review task for a curated feed item.

- **Input**:
  - `tenant_id: string`
  - `article_id: string`
  - `feed_metadata: { score: number; primary_tags: string[] }`
- **Output**:
  - `review_task_id: string`
  - `initial_status: "PENDING_REVIEW"`

### 3.8 `approve_or_reject_item`

Persist reviewer decision and editor changes.

- **Input**:
  - `review_task_id: string`
  - `decision: "APPROVE" | "REJECT" | "NEEDS_EDIT"`
  - `editor_changes?: { title?: string; summary?: string; post_variants?: PostVariant[] }`
- **Output**:
  - `updated_status: string`
  - `final_snapshot_id: string` (pointer to stored result)

### 3.9 `schedule_publication_to_platforms`

Schedule posts to platforms (after approval).

- **Input**:
  - `article_id: string`
  - `platform_posts: { platform: string; body: string; title?: string; scheduled_at: ISO8601 }[]`
- **Output**:
  - `publication_jobs: { job_id: string; platform: string; scheduled_at: ISO8601; status: string }[]`

---

## 4. Agents (Domain Services)

Agents implement one or more of the skills above and call tools.

### 4.1 `content_scraper_agent`

- **Skills**:
  - `scrape_source_urls`
  - `extract_and_normalize_articles`
- **Tools**:
  - `http_scraper_tool`
  - `rss_fetch_tool`
  - `html_to_text_tool`
- **Notes**:
  - Could be a LangGraph graph:
    - Node 1: Fetch (HTTP/RSS).
    - Node 2: Normalize (HTML → text, metadata).

### 4.2 `content_enrichment_agent`

- **Skills**:
  - `tag_content_item`
  - `score_content_item`
- **Tools**:
  - `tagging_model_tool` (LLM/classifier).
  - `tenant_topics_vector_store` (vector DB).
  - `scoring_model_tool` (LLM or code‑based heuristic).
- **Notes**:
  - Enforces “max 6 tags” rule in the graph or code.
  - Write results to `article_tags` and `article_scores` tables.

### 4.3 `feed_curation_agent`

- **Skills**:
  - `assemble_news_feed`
  - `create_review_task_for_item`
- **Tools**:
  - Postgres read/write (articles + tags + scores).
- **Notes**:
  - Implements ranking strategy and diversity.
  - Creates `review_tasks` for each selected item.

### 4.4 `social_publisher_agent`

- **Skills**:
  - `generate_platform_post_variants`
  - `schedule_publication_to_platforms`
- **Tools**:
  - `linkedin_publisher_tool`
  - `blog_publisher_tool`
  - `generic_social_publisher_tool`
- **Notes**:
  - Scheduling always goes through policy & human approval.

---

## 5. Tools (Tool Registry)

Registered in the **Tool/MCP Registry**.

- `http_scraper_tool`
  - Input: URLs.
  - Output: HTML + status code.
- `rss_fetch_tool`
  - Input: feed URLs.
  - Output: entries (title, link, published_at).
- `html_to_text_tool`
  - Input: HTML.
  - Output: cleaned text + extracted title.
- `tagging_model_tool`
  - Input: text + candidate tags.
  - Output: top tags with scores.
- `scoring_model_tool`
  - Input: features (date, source, tags, etc.).
  - Output: numeric score + explanation.
- `linkedin_publisher_tool`
  - Input: text + auth.
  - Output: LinkedIn post ID / error.
- `blog_publisher_tool`
  - Input: title/body + slug/category.
  - Output: blog post URL / ID.
- `generic_social_publisher_tool`
  - Input: platform identifier + text.
  - Output: platform post ID.

---

## 6. Storage Model (Postgres + Vector DB)

### 6.1 Postgres tables

- `sources`
  - `id`, `tenant_id`, `url`, `type` (`rss`/`html`), `active`, `created_at`.
- `articles`
  - `id`, `tenant_id`, `source_id`, `url`, `canonical_url`, `title`,
    `author`, `published_at`, `text`, `created_at`.
- `tags`
  - `id`, `tenant_id`, `name`, `description`.
- `article_tags`
  - `article_id`, `tag_id`, `confidence`.
- `article_scores`
  - `article_id`, `score`, `relevance_breakdown_json`, `spam_flag`, `created_at`.
- `review_tasks`
  - `id`, `tenant_id`, `article_id`, `status`, `assigned_to`, `created_at`, `updated_at`,
    `post_variants_json` (drafts).
- `publication_jobs`
  - `id`, `tenant_id`, `article_id`, `platform`, `scheduled_at`,
    `status`, `external_post_id`, `created_at`, `updated_at`.

### 6.2 Vector DB

- `article_embeddings`
  - `article_id`, `tenant_id`, `embedding`, optional `tags_hint[]`.

Used by `tagging_model_tool` and enrichment for similarity and topic matching.

---

## 7. End‑to‑End Flows

### 7.1 Flow A – Nightly ingestion & enrichment

**Goal**: From configured sources → enriched candidate items with tags + scores + review tasks.

1. **Trigger**
   - Cron or API: `RunIngestion(tenant_id)`.

2. **Orchestrator**
   - Loads tenant policy & persona (`content_curator` or system persona).
   - Planner builds plan:
     1. `scrape_source_urls`
     2. `extract_and_normalize_articles`
     3. For each new article:
        - `tag_content_item`
        - `score_content_item`
     4. `assemble_news_feed`
     5. `create_review_task_for_item` for each feed item.

3. **Execution Engine**
   - Calls `content_scraper_agent` for steps 1–2.
   - Calls `content_enrichment_agent` for tagging & scoring.
   - Calls `feed_curation_agent` for ranking & review task creation.

4. **Result**
   - `review_tasks` table populated with `PENDING_REVIEW` items ready for editors.

### 7.2 Flow B – Reviewer approval & post generation

**Goal**: Editor reviews item, edits, and prepares platform drafts.

1. **UI** loads `review_tasks` + joined `articles`, `tags`, `scores`.
2. On “Generate drafts”:
   - Backend triggers `generate_platform_post_variants` with chosen platforms.
   - `social_publisher_agent`:
     - Reads article text.
     - Produces LinkedIn/blog drafts.
     - Writes `post_variants_json` on `review_tasks`.
3. Editor tweaks drafts, then calls `approve_or_reject_item`:
   - On APPROVE, backend:
     - Marks task `APPROVED`.
     - Stores final edited variants in snapshot.

### 7.3 Flow C – Scheduling & publishing

**Goal**: Approved items are scheduled for LinkedIn/blog/social.

1. Editor or `social_publisher_manager` chooses times and platforms.
2. Backend calls `schedule_publication_to_platforms`:
   - Policy confirms human approval satisfied.
   - `social_publisher_agent` creates `publication_jobs` (`SCHEDULED`).
3. A background worker picks due jobs:
   - Posts via `linkedin_publisher_tool` / `blog_publisher_tool` / `generic_social_publisher_tool`.
   - Updates `publication_jobs.status` accordingly.

---

## 8. Implementation Notes

- Implement each skill as:
  - Pydantic input/output models.
  - LangGraph nodes in the appropriate agent package.
- Keep:
  - **Domain logic** (scraping, tagging, scoring) inside agents.
  - **Registries & policy** in the control plane.
- Use OTel to trace:
  - `execution_id`, `step_id`, `article_id`, `tenant_id` across orchestrator, agents, tools.
