import { JsonBlock } from "@/components/JsonBlock";
import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TenantTemplatesPage({
  params,
}: {
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  let meta = { display_name: tenantId };
  let rows: unknown[] = [];
  let err: string | null = null;
  try {
    const o = await apiGet<{ display_name: string }>(
      `/admin/tenants/${tenantId}/overview`,
    );
    meta = o;
    rows = await apiGet<unknown[]>(`/admin/tenants/${tenantId}/templates`);
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name={meta.display_name} />
      <h2 className="font-display mb-2 text-2xl font-light text-ink">Article format templates</h2>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && <JsonBlock data={rows} />}
    </div>
  );
}
