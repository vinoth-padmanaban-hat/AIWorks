import { JsonBlock } from "@/components/JsonBlock";
import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

export default async function ExecutionLogsPage({
  params,
}: {
  params: Promise<{ tenantId: string; executionId: string }>;
}) {
  const { tenantId, executionId } = await params;
  let meta = { display_name: tenantId };
  let logs: unknown[] = [];
  let err: string | null = null;
  try {
    const o = await apiGet<{ display_name: string }>(
      `/admin/tenants/${tenantId}/overview`,
    );
    meta = o;
    logs = await apiGet<unknown[]>(
      `/admin/tenants/${tenantId}/executions/${executionId}/logs`,
    );
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name={meta.display_name} />
      <div className="mb-4 text-sm text-muted">
        <Link href={`/admin/tenants/${tenantId}/jobs`} className="font-medium text-accent-link hover:text-ink hover:underline">
          ← All jobs
        </Link>
        <span className="mx-2 text-border">/</span>
        <span className="font-mono text-xs text-ink">{executionId}</span>
      </div>
      <h2 className="font-display mb-2 text-2xl font-light text-ink">Step log</h2>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && <JsonBlock data={logs} />}
    </div>
  );
}
