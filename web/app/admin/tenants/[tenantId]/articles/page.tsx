import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

type ArticleRow = {
  id: string;
  source_id: string;
  url: string;
  canonical_url: string | null;
  title: string;
  author: string | null;
  published_at: string | null;
  img_url: string | null;
  summary: string | null;
  text_preview?: string;
  created_at: string;
};

type ArticlesRes = {
  total: number;
  limit: number;
  offset: number;
  items: ArticleRow[];
};

export default async function TenantArticlesPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  let meta = { display_name: tenantId };
  let rows: ArticlesRes | null = null;
  let err: string | null = null;
  try {
    const o = await apiGet<{ display_name: string }>(
      `/admin/tenants/${tenantId}/overview`,
    );
    meta = o;
    rows = await apiGet<ArticlesRes>(
      `/admin/tenants/${tenantId}/articles?limit=50&offset=0`,
    );
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="pb-12">
      <TenantNav tenantId={tenantId} name={meta.display_name} />

      <header className="mb-8">
        <h2 className="font-display text-[1.75rem] font-light leading-tight tracking-tight text-ink">
          Articles
        </h2>
        {rows && (
          <p className="mt-2 text-[0.94rem] leading-[1.6] tracking-body-lg text-ink-secondary">
            Showing {rows.items.length} of {rows.total} ingested articles
          </p>
        )}
      </header>

      {err && (
        <p className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-[0.88rem] text-red-900">
          {err}
        </p>
      )}

      {rows && rows.items.length === 0 && (
        <p className="rounded-2xl border border-border bg-surface p-8 text-center text-[0.94rem] text-muted shadow-elev-outline">
          No articles yet. Run content ingestion or curation for this tenant.
        </p>
      )}

      {rows && rows.items.length > 0 && (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
          {rows.items.map((a) => (
            <article
              key={a.id}
              className="flex flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-elev-outline"
            >
              {a.img_url ? (
                <div className="relative aspect-[16/10] w-full bg-surface-muted">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={a.img_url}
                    alt=""
                    className="h-full w-full object-cover"
                    loading="lazy"
                    referrerPolicy="no-referrer"
                  />
                </div>
              ) : (
                <div className="flex aspect-[16/10] w-full items-center justify-center bg-surface-muted text-[0.75rem] text-muted">
                  No image
                </div>
              )}
              <div className="flex flex-1 flex-col p-5">
                <h3 className="font-display text-[1.15rem] font-light leading-snug text-ink">
                  {a.title || "Untitled"}
                </h3>
                {a.summary && (
                  <p className="mt-2 line-clamp-3 text-[0.88rem] leading-[1.6] tracking-body text-muted">
                    {a.summary}
                  </p>
                )}
                {!a.summary && a.text_preview && (
                  <p className="mt-2 line-clamp-3 text-[0.88rem] leading-[1.6] tracking-body text-muted">
                    {a.text_preview}
                  </p>
                )}
                <div className="mt-4 flex flex-wrap items-center gap-3 text-[0.78rem] tracking-body text-muted">
                  <time dateTime={a.created_at}>
                    {new Date(a.created_at).toLocaleString()}
                  </time>
                </div>
                <a
                  href={a.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-4 inline-flex text-[0.88rem] font-medium text-accent-link underline-offset-2 hover:text-ink hover:underline"
                >
                  Open source URL
                </a>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
