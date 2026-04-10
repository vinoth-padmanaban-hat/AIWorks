"use client";

import { TenantNav } from "@/components/TenantNav";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

type ProductRef = {
  product_id: string;
  product_name: string;
  relevance_score: number;
  match_reason: string;
};

type MediaItem = { type: string; src: string; alt?: string };

type MediaRefs = {
  images?: MediaItem[];
  videos?: MediaItem[];
  audio?: MediaItem[];
};

type Newsletter = {
  id: string;
  execution_id: string;
  article_id: string | null;
  title: string;
  summary: string;
  body_preview: string;
  body?: string;
  product_refs: ProductRef[];
  tags: string[];
  source_url: string;
  img_url: string | null;
  media_refs?: MediaRefs | null;
  status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  published_at: string | null;
  publish_channel: string | null;
  created_at: string;
  source_published_at?: string | null;
  source_author?: string | null;
  article_summary?: string | null;
  article_created_at?: string | null;
  source_feed_url?: string | null;
  source_type?: string | null;
};

type PagedResult = {
  total: number;
  limit: number;
  offset: number;
  items: Newsletter[];
};

const API =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"
    : "http://127.0.0.1:8000";

const statusStyles: Record<string, string> = {
  draft: "bg-amber-50 text-amber-900",
  approved: "bg-emerald-50 text-emerald-900",
  published: "bg-surface-muted text-ink border border-border",
  rejected: "bg-red-50 text-red-900",
};

function hostLabel(url: string | null | undefined): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 48);
  }
}

function formatWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return iso;
  }
}

function youtubeEmbedUrl(src: string): string | null {
  const m =
    /youtube\.com\/watch\?v=([^&]+)/.exec(src) ||
    /youtu\.be\/([^?]+)/.exec(src) ||
    /youtube\.com\/embed\/([^?]+)/.exec(src);
  if (!m) return null;
  return `https://www.youtube.com/embed/${m[1]}`;
}

function VideoBlock({ src, title }: { src: string; title: string }) {
  const embed = youtubeEmbedUrl(src);
  if (embed) {
    return (
      <div className="overflow-hidden rounded-xl border border-border bg-black/5 shadow-card">
        <iframe
          title={title}
          src={embed}
          className="aspect-video w-full"
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
        />
      </div>
    );
  }
  return (
    <video
      src={src}
      controls
      className="w-full rounded-xl border border-border bg-black/5 shadow-card"
      preload="metadata"
    />
  );
}

export default function NewslettersPage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const [items, setItems] = useState<Newsletter[]>([]);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detailBody, setDetailBody] = useState<Record<string, string>>({});
  const [detailLoading, setDetailLoading] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [productUrlById, setProductUrlById] = useState<Record<string, string>>(
    {}
  );
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(
    async (showSpinner = true) => {
      if (showSpinner) {
        setLoading(true);
        setError(null);
      }
      try {
        const qs = filter ? `?status=${encodeURIComponent(filter)}` : "";
        const res = await fetch(
          `${API}/admin/tenants/${tenantId}/newsletters${qs}`,
          { cache: "no-store" }
        );
        if (!res.ok) throw new Error(await res.text());
        const data: PagedResult = await res.json();
        setItems(data.items);
        setTotal(data.total);
      } catch (e) {
        if (showSpinner) {
          setError(e instanceof Error ? e.message : String(e));
        }
      } finally {
        if (showSpinner) setLoading(false);
      }
    },
    [tenantId, filter]
  );

  useEffect(() => {
    load(true);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `${API}/admin/tenants/${tenantId}/products`,
          { cache: "no-store" }
        );
        if (!res.ok) return;
        const products: { id: string; url: string | null }[] = await res.json();
        if (cancelled) return;
        const map: Record<string, string> = {};
        for (const p of products) {
          if (p.url) map[p.id] = p.url;
        }
        setProductUrlById(map);
      } catch {
        /* optional enrichment */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tenantId]);

  useEffect(() => {
    pollRef.current = setInterval(() => load(false), 50_000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  const fetchDetailBody = async (id: string) => {
    if (detailBody[id]) return;
    setDetailLoading(id);
    try {
      const res = await fetch(
        `${API}/admin/tenants/${tenantId}/newsletters/${id}`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(await res.text());
      const row: Newsletter = await res.json();
      setDetailBody((prev) => ({ ...prev, [id]: row.body ?? "" }));
    } catch {
      setDetailBody((prev) => ({
        ...prev,
        [id]: prev[id] ?? "",
      }));
    } finally {
      setDetailLoading(null);
    }
  };

  const toggleExpand = async (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);
    await fetchDetailBody(id);
  };

  const review = async (id: string, status: "approved" | "rejected") => {
    try {
      await fetch(
        `${API}/admin/tenants/${tenantId}/newsletters/${id}/review`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, reviewed_by: "admin" }),
        }
      );
      load(true);
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="pb-16">
      <TenantNav tenantId={tenantId} name="Tenant" />

      <div className="mx-auto max-w-[960px] px-4 sm:px-0">
        <header className="mb-10">
          <h1 className="font-display text-[2rem] font-light leading-[1.1] tracking-tight text-ink">
            Newsletter articles
          </h1>
          <p className="mt-2 text-[0.88rem] leading-[1.6] tracking-body-lg text-ink-secondary">
            Curated drafts with source media, product matches, and review
            workflow. The list refreshes periodically while this page is open.
          </p>

          <div className="mt-6 flex flex-wrap items-center gap-3">
            <div
              className="flex flex-wrap gap-2"
              role="tablist"
              aria-label="Filter by status"
            >
              {["", "draft", "approved", "rejected", "published"].map((s) => (
                <button
                  key={s || "all"}
                  type="button"
                  role="tab"
                  aria-selected={filter === s}
                  onClick={() => setFilter(s)}
                  className={`rounded-full border px-3.5 py-2 text-[0.81rem] font-medium tracking-body transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                    filter === s
                      ? "border-ink bg-ink text-white shadow-card"
                      : "border-border bg-surface text-ink shadow-card hover:bg-surface-muted"
                  }`}
                >
                  {s || "All"}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => load(true)}
              className="rounded-full border border-border bg-surface px-4 py-2 text-[0.81rem] font-medium tracking-body text-ink shadow-card hover:bg-surface-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            >
              Refresh now
            </button>
            <span className="text-[0.75rem] tracking-body text-muted">
              {total} total
            </span>
          </div>
        </header>

        {error && (
          <p className="mb-6 text-sm text-red-800" role="alert">
            {error}
          </p>
        )}
        {loading && (
          <p className="text-[0.88rem] tracking-body text-muted">Loading…</p>
        )}

        {!loading && items.length === 0 && (
          <p className="rounded-2xl border border-border bg-surface p-8 text-center text-[0.94rem] leading-[1.6] text-muted shadow-elev-outline">
            No newsletter articles yet. Run a{" "}
            <span className="font-medium text-ink">content curation</span> job
            for this tenant (ingestion alone does not create newsletter rows).
          </p>
        )}

        <div className="flex flex-col gap-10">
          {items.map((nl) => {
            const extraVideos = nl.media_refs?.videos?.filter((v) => v.src) ?? [];
            const extraImages =
              nl.media_refs?.images?.filter(
                (im) => im.src && im.src !== nl.img_url
              ) ?? [];
            const audioItems = nl.media_refs?.audio?.filter((a) => a.src) ?? [];
            const publishedLine =
              nl.published_at && formatWhen(nl.published_at);
            const sourcePub =
              nl.source_published_at && formatWhen(nl.source_published_at);
            const ingested =
              nl.article_created_at && formatWhen(nl.article_created_at);
            const curated = formatWhen(nl.created_at);
            const displayBody =
              expanded === nl.id
                ? detailBody[nl.id] ?? nl.body_preview
                : null;

            return (
              <article
                key={nl.id}
                className="overflow-hidden rounded-2xl border border-border bg-surface shadow-elev-outline"
              >
                <div className="grid gap-0 md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
                  <div className="relative aspect-[16/11] min-h-[200px] bg-surface-muted md:aspect-auto md:min-h-[280px]">
                    {nl.img_url ? (
                      /* eslint-disable-next-line @next/next/no-img-element */
                      <img
                        src={nl.img_url}
                        alt=""
                        className="h-full w-full object-cover"
                        loading="lazy"
                        referrerPolicy="no-referrer"
                      />
                    ) : (
                      <div className="flex h-full min-h-[200px] items-center justify-center text-[0.75rem] tracking-body text-muted md:min-h-[280px]">
                        No hero image
                      </div>
                    )}
                  </div>

                  <div className="flex flex-col p-6 md:p-7">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <h2 className="font-display text-[1.35rem] font-light leading-[1.2] text-ink">
                        {nl.title}
                      </h2>
                      <span
                        className={`shrink-0 rounded-md border border-transparent px-2.5 py-1 text-[0.7rem] font-medium uppercase tracking-wide ${
                          statusStyles[nl.status] ??
                          "bg-surface-muted text-muted"
                        }`}
                      >
                        {nl.status}
                      </span>
                    </div>

                    <dl className="mt-4 grid gap-2 text-[0.78rem] leading-snug tracking-body text-ink-secondary sm:grid-cols-2">
                      {nl.source_url && (
                        <div className="sm:col-span-2">
                          <dt className="font-medium text-muted">Source page</dt>
                          <dd className="mt-0.5">
                            <a
                              href={nl.source_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="break-all text-accent-link underline-offset-2 hover:text-ink hover:underline"
                            >
                              {hostLabel(nl.source_url)}
                            </a>
                          </dd>
                        </div>
                      )}
                      {nl.source_feed_url && (
                        <div>
                          <dt className="font-medium text-muted">
                            Configured source
                          </dt>
                          <dd className="mt-0.5">
                            <a
                              href={nl.source_feed_url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="break-all text-accent-link underline-offset-2 hover:text-ink hover:underline"
                            >
                              {hostLabel(nl.source_feed_url)}
                              {nl.source_type ? ` · ${nl.source_type}` : ""}
                            </a>
                          </dd>
                        </div>
                      )}
                      {nl.source_author && (
                        <div>
                          <dt className="font-medium text-muted">Author</dt>
                          <dd className="mt-0.5 text-ink">{nl.source_author}</dd>
                        </div>
                      )}
                      {sourcePub && (
                        <div>
                          <dt className="font-medium text-muted">
                            Original published
                          </dt>
                          <dd className="mt-0.5">{sourcePub}</dd>
                        </div>
                      )}
                      {ingested && (
                        <div>
                          <dt className="font-medium text-muted">
                            Ingested into catalog
                          </dt>
                          <dd className="mt-0.5">{ingested}</dd>
                        </div>
                      )}
                      <div>
                        <dt className="font-medium text-muted">Curated draft</dt>
                        <dd className="mt-0.5">{curated}</dd>
                      </div>
                      {publishedLine && (
                        <div>
                          <dt className="font-medium text-muted">
                            Newsletter published
                          </dt>
                          <dd className="mt-0.5">{publishedLine}</dd>
                        </div>
                      )}
                      {nl.publish_channel && (
                        <div>
                          <dt className="font-medium text-muted">Channel</dt>
                          <dd className="mt-0.5">{nl.publish_channel}</dd>
                        </div>
                      )}
                      <div className="sm:col-span-2">
                        <dt className="font-medium text-muted">Execution</dt>
                        <dd className="mt-0.5 font-mono text-[0.72rem] text-ink-secondary">
                          {nl.execution_id}
                        </dd>
                      </div>
                      {nl.article_id && (
                        <div className="sm:col-span-2">
                          <dt className="font-medium text-muted">Article id</dt>
                          <dd className="mt-0.5 font-mono text-[0.72rem] text-ink-secondary">
                            {nl.article_id}
                          </dd>
                        </div>
                      )}
                    </dl>

                    {nl.reviewed_at && (
                      <p className="mt-3 text-[0.75rem] tracking-body text-muted">
                        Reviewed {formatWhen(nl.reviewed_at)}
                        {nl.reviewed_by ? ` · ${nl.reviewed_by}` : ""}
                      </p>
                    )}

                    <p className="mt-4 text-[0.94rem] leading-[1.62] tracking-body text-ink-secondary">
                      {nl.summary}
                    </p>

                    {nl.article_summary && nl.article_summary !== nl.summary && (
                      <p className="mt-3 border-l-2 border-border pl-3 text-[0.85rem] leading-[1.55] tracking-body text-muted">
                        <span className="font-medium text-ink-secondary">
                          Source summary:{" "}
                        </span>
                        {nl.article_summary}
                      </p>
                    )}

                    {nl.tags.length > 0 && (
                      <div className="mt-4 flex flex-wrap gap-1.5">
                        {nl.tags.map((t: string) => (
                          <span
                            key={t}
                            className="rounded-md border border-border bg-surface-muted px-2 py-1 text-[0.72rem] tracking-body text-ink-secondary"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}

                    {(extraImages.length > 0 ||
                      extraVideos.length > 0 ||
                      audioItems.length > 0) && (
                      <div className="mt-6 border-t border-border pt-6">
                        <p className="mb-3 text-[0.72rem] font-semibold uppercase tracking-wide text-muted">
                          Multimedia from source
                        </p>
                        {extraImages.length > 0 && (
                          <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3">
                            {extraImages.map((im, i) => (
                              <a
                                key={`img-${i}`}
                                href={im.src}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="group relative aspect-[4/3] overflow-hidden rounded-lg border border-border bg-surface-muted"
                              >
                                {/* eslint-disable-next-line @next/next/no-img-element */}
                                <img
                                  src={im.src}
                                  alt={im.alt || `Image ${i + 1}`}
                                  className="h-full w-full object-cover transition group-hover:opacity-95"
                                  loading="lazy"
                                  referrerPolicy="no-referrer"
                                />
                              </a>
                            ))}
                          </div>
                        )}
                        {extraVideos.map((v, i) => (
                          <div key={`vid-${i}`} className="mb-4 last:mb-0">
                            <VideoBlock
                              src={v.src}
                              title={v.alt || `Video ${i + 1}`}
                            />
                            <a
                              href={v.src}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="mt-1 inline-block text-[0.72rem] text-accent-link hover:underline"
                            >
                              Open video URL
                            </a>
                          </div>
                        ))}
                        {audioItems.map((a, i) => (
                          <div key={`aud-${i}`} className="mb-2">
                            <audio
                              controls
                              src={a.src}
                              className="w-full max-w-md"
                              preload="metadata"
                            />
                          </div>
                        ))}
                      </div>
                    )}

                    {nl.product_refs && nl.product_refs.length > 0 && (
                      <div className="mt-6 rounded-xl border border-border bg-surface-muted/80 p-4">
                        <p className="mb-3 text-[0.72rem] font-semibold uppercase tracking-wide text-muted">
                          Product references
                        </p>
                        <ul className="space-y-3">
                          {nl.product_refs.map((pr: ProductRef, i: number) => {
                            const purl = productUrlById[pr.product_id];
                            return (
                              <li
                                key={i}
                                className="border-b border-border/50 pb-3 text-[0.84rem] last:border-0 last:pb-0"
                              >
                                <div className="flex flex-wrap items-baseline gap-2">
                                  {purl ? (
                                    <a
                                      href={purl}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className="font-medium text-accent-link hover:underline"
                                    >
                                      {pr.product_name}
                                    </a>
                                  ) : (
                                    <span className="font-medium text-ink">
                                      {pr.product_name}
                                    </span>
                                  )}
                                  <span className="text-[0.72rem] text-muted">
                                    {(Number(pr.relevance_score) * 100).toFixed(0)}%
                                    match
                                  </span>
                                </div>
                                <p className="mt-1 text-[0.8rem] leading-snug text-ink-secondary">
                                  {pr.match_reason}
                                </p>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}

                    <div className="mt-6 flex flex-wrap items-center gap-2 border-t border-border pt-6">
                      <button
                        type="button"
                        onClick={() => toggleExpand(nl.id)}
                        className="rounded-full px-3 py-2 text-[0.88rem] font-medium tracking-body text-accent-link underline-offset-4 hover:text-ink hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                      >
                        {expanded === nl.id
                          ? "Collapse full body"
                          : "Read full newsletter body"}
                      </button>
                      {detailLoading === nl.id && (
                        <span className="text-[0.78rem] text-muted">
                          Loading full text…
                        </span>
                      )}

                      {nl.status === "draft" && (
                        <>
                          <button
                            type="button"
                            onClick={() => review(nl.id, "approved")}
                            className="ml-auto inline-flex items-center justify-center rounded-full bg-accent px-4 py-2 text-[0.88rem] font-medium text-accent-on-accent shadow-card transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2 focus-visible:ring-offset-surface-secondary"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            onClick={() => review(nl.id, "rejected")}
                            className="rounded-[30px] bg-surface-warm px-4 py-2 text-[0.88rem] font-medium text-ink shadow-warm transition hover:opacity-90 focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                          >
                            Reject
                          </button>
                        </>
                      )}
                    </div>

                    {expanded === nl.id && (
                      <div className="mt-4 rounded-xl border border-border bg-surface-muted p-5">
                        <p className="whitespace-pre-wrap text-[0.94rem] leading-[1.65] tracking-body text-ink-secondary">
                          {displayBody || "No body content."}
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
