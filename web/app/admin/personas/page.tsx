import { JsonBlock } from "@/components/JsonBlock";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function PersonasPage() {
  let rows: unknown[] = [];
  let err: string | null = null;
  try {
    rows = await apiGet<unknown[]>("/admin/platform/personas");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="space-y-4">
      <h2 className="font-display text-2xl font-light text-ink">Persona Store</h2>
      <p className="text-sm leading-relaxed tracking-body text-ink-secondary">
        Control plane table <code className="code-inline">personas</code>
        — each tenant can have many; one may be marked <code className="code-inline">is_default</code>{" "}
        for ingestion when <code className="code-inline">persona_id</code> is omitted.
      </p>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && <JsonBlock data={rows} />}
    </div>
  );
}
