-- Newsletter hero image + structured media refs (images/videos/audio URLs from scraper).
-- Apply to EACH tenant database:
--   psql $TENANT_DB_URL -f db/migrations/tenant/007_newsletter_media.sql

ALTER TABLE newsletter_articles
    ADD COLUMN IF NOT EXISTS img_url TEXT,
    ADD COLUMN IF NOT EXISTS media_refs JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN newsletter_articles.img_url IS
    'Primary/hero image URL extracted from the source page when available.';
COMMENT ON COLUMN newsletter_articles.media_refs IS
    'JSON object with images, videos, audio arrays (src, alt, type) from the scraper.';
