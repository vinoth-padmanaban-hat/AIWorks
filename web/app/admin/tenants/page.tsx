import { apiGet } from "@/lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

type TenantRow = {
  id: string;
  display_name: string;
  domain: string;
  created_at: string | null;
  db_url: string | null;
  region: string | null;
  persona_count?: number;
};

export default async function TenantsPage() {
  let rows: TenantRow[] = [];
  let err: string | null = null;
  try {
    rows = await apiGet<TenantRow[]>("/admin/platform/tenants");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <h2 className="font-display text-2xl font-light tracking-tight text-ink">Tenants</h2>
      <p className="mt-2 text-sm leading-relaxed tracking-body text-ink-secondary">
        Each tenant has its own Postgres database (see{" "}
        <code className="code-inline">db_url</code>).
      </p>
      {err && (
        <p className="mt-4 text-sm text-red-800">{err}</p>
      )}
      <div className="mt-6 overflow-x-auto rounded-2xl border border-border bg-surface shadow-elev-outline">
        <table className="w-full min-w-[640px] text-left text-sm">
          <thead className="border-b border-border bg-surface-muted text-xs uppercase tracking-wide text-muted">
            <tr>
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Domain</th>
              <th className="px-4 py-3">Region</th>
              <th className="px-4 py-3 text-center">Personas</th>
              <th className="px-4 py-3">Database</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className="border-b border-border/60 last:border-0">
                <td className="px-4 py-3 font-medium text-ink">{t.display_name}</td>
                <td className="px-4 py-3 text-muted">{t.domain || "—"}</td>
                <td className="px-4 py-3 text-muted">{t.region || "—"}</td>
                <td className="px-4 py-3 text-center tabular-nums text-muted">
                  {t.persona_count ?? "—"}
                </td>
                <td className="max-w-xs truncate px-4 py-3 font-mono text-xs text-muted">
                  {t.db_url
                    ? t.db_url.split("/").pop()
                    : "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  <Link
                    href={`/admin/tenants/${t.id}`}
                    className="font-medium text-ink underline-offset-4 hover:underline"
                  >
                    Open →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && !err && (
          <p className="p-6 text-sm text-muted">No tenants in control plane.</p>
        )}
      </div>
    </div>
  );
}
