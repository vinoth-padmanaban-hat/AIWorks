import { JsonBlock } from "@/components/JsonBlock";
import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

type Overview = {
  id: string;
  display_name: string;
  domain: string;
  created_at: string | null;
  db_url: string | null;
  region: string | null;
  policy: Record<string, unknown> | null;
  counts?: Record<string, number>;
  tenant_db_error?: string;
};

export default async function TenantOverviewPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  let data: Overview | null = null;
  let err: string | null = null;
  try {
    data = await apiGet<Overview>(`/admin/tenants/${tenantId}/overview`);
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  if (err) {
    return (
      <div>
        <p className="text-red-800">{err}</p>
      </div>
    );
  }

  if (!data) {
    return null;
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name={data.display_name} />
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="panel-surface rounded-2xl p-5">
          <h2 className="font-display text-xl font-light text-ink">Connection</h2>
          <dl className="mt-3 space-y-2 text-sm tracking-body">
            <div>
              <dt className="text-muted">Tenant ID</dt>
              <dd className="font-mono text-xs text-ink">{data.id}</dd>
            </div>
            <div>
              <dt className="text-muted">Domain</dt>
              <dd className="text-ink">{data.domain || "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">Region</dt>
              <dd className="text-ink">{data.region || "—"}</dd>
            </div>
            <div>
              <dt className="text-muted">DB URL</dt>
              <dd className="break-all font-mono text-xs text-muted">{data.db_url || "—"}</dd>
            </div>
          </dl>
        </section>
        <section className="panel-surface rounded-2xl p-5">
          <h2 className="font-display text-xl font-light text-ink">Row counts (tenant DB)</h2>
          {data.tenant_db_error && (
            <p className="mt-2 text-sm text-amber-900">{data.tenant_db_error}</p>
          )}
          {data.counts && (
            <ul className="mt-3 space-y-1 text-sm">
              {Object.entries(data.counts).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-4 border-b border-border/40 py-1 last:border-0">
                  <span className="font-mono text-muted">{k}</span>
                  <span className="tabular-nums text-ink">{v}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <section className="mt-6">
        <h2 className="font-display mb-2 text-xl font-light text-ink">Policy (control plane)</h2>
        {data.policy ? (
          <JsonBlock data={data.policy} />
        ) : (
          <p className="text-sm text-muted">No policy row for this tenant.</p>
        )}
      </section>
    </div>
  );
}
