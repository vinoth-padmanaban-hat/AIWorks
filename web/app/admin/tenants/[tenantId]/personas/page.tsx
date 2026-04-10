import { JsonBlock } from "@/components/JsonBlock";
import { TenantNav } from "@/components/TenantNav";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function TenantPersonasPage({
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
    rows = await apiGet<unknown[]>(`/admin/tenants/${tenantId}/personas`);
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div>
      <TenantNav tenantId={tenantId} name={meta.display_name} />
      <h2 className="font-display mb-2 text-2xl font-light text-ink">Personas for this tenant</h2>
      <p className="mb-3 text-sm leading-relaxed tracking-body text-ink-secondary">
        Stored in the control plane. Call{" "}
        <code className="code-inline">POST /ingestion/run/&lt;tenant_id&gt;</code>{" "}
        with optional query param{" "}
        <code className="code-inline">persona_id=&lt;uuid&gt;</code>{" "}
        or omit it to use the tenant&apos;s default persona.
      </p>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && <JsonBlock data={rows} />}
    </div>
  );
}
