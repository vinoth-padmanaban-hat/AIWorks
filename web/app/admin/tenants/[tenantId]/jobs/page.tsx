import Link from "next/link";
import { JsonBlock } from "@/components/JsonBlock";
import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

type Overview = { display_name: string };

type Execution = {
  execution_id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  summary_json: unknown;
};

export default async function TenantJobsPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  let meta: Overview = { display_name: tenantId };
  let executions: Execution[] = [];
  let err: string | null = null;
  try {
    meta = await apiGet<Overview>(`/admin/tenants/${tenantId}/overview`);
    executions = await apiGet<Execution[]>(
      `/admin/tenants/${tenantId}/executions?limit=50`,
    );
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name={meta.display_name} />
      <h2 className="font-display mb-4 text-2xl font-light text-ink">Ingestion jobs</h2>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && (
        <div className="overflow-x-auto rounded-2xl border border-border bg-surface shadow-elev-outline">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="border-b border-border bg-surface-muted text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">Execution</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Started</th>
                <th className="px-4 py-3">Finished</th>
                <th className="px-4 py-3" />
              </tr>
            </thead>
            <tbody>
              {executions.map((ex) => (
                <tr key={ex.execution_id} className="border-b border-border/60 last:border-0">
                  <td className="px-4 py-3 font-mono text-xs text-ink">
                    {ex.execution_id}
                  </td>
                  <td className="px-4 py-3 text-ink">{ex.status}</td>
                  <td className="px-4 py-3 text-muted">{ex.started_at ?? "—"}</td>
                  <td className="px-4 py-3 text-muted">{ex.finished_at ?? "—"}</td>
                  <td className="px-4 py-3 text-right">
                    <Link
                      href={`/admin/tenants/${tenantId}/jobs/${ex.execution_id}`}
                      className="font-medium text-ink underline-offset-4 hover:underline"
                    >
                      Logs →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {executions.length === 0 && (
            <p className="p-6 text-sm text-muted">No ingestion executions yet.</p>
          )}
        </div>
      )}
      {!err && executions.length > 0 && (
        <p className="mt-4 text-xs text-muted">
          Raw JSON (last page):{" "}
        </p>
      )}
      {!err && executions.length > 0 && <JsonBlock data={executions} />}
    </div>
  );
}
