import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

type Product = {
  id: string;
  name: string;
  description: string;
  url: string | null;
  category: string;
  tags: string[];
  features: string[];
  active: boolean;
  created_at: string;
};

export default async function ProductsPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  let products: Product[] = [];
  let err: string | null = null;
  try {
    products = await apiGet<Product[]>(
      `/admin/tenants/${tenantId}/products`
    );
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name="Tenant" />
      <h1 className="font-display mb-4 text-2xl font-light text-ink">
        Products / Services
      </h1>

      {err && <p className="text-sm text-red-800">{err}</p>}

      {products.length === 0 && !err && (
        <p className="text-sm text-muted">
          No products configured. Run the seed script with product data.
        </p>
      )}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {products.map((p) => (
          <div
            key={p.id}
            className="panel-surface rounded-2xl p-4"
          >
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium text-ink">{p.name}</h3>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                  p.active
                    ? "bg-emerald-50 text-emerald-900"
                    : "bg-red-50 text-red-800"
                }`}
              >
                {p.active ? "active" : "inactive"}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">{p.category}</p>
            <p className="mt-2 text-sm leading-relaxed tracking-body text-muted line-clamp-3">
              {p.description}
            </p>
            {p.url && (
              <a
                href={p.url}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 block text-xs font-medium text-accent-link hover:text-ink hover:underline"
              >
                {p.url}
              </a>
            )}
            {p.tags.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-1">
                {p.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded-md border border-border bg-surface-muted px-1.5 py-0.5 text-xs text-ink-secondary"
                  >
                    {t}
                  </span>
                ))}
              </div>
            )}
            {p.features.length > 0 && (
              <ul className="mt-2 space-y-0.5 text-xs text-muted">
                {p.features.map((f) => (
                  <li key={f}>• {f}</li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
