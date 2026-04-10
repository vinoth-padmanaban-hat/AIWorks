import { JsonBlock } from "@/components/JsonBlock";
import { apiGet } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function AgentsPage() {
  let rows: unknown[] = [];
  let err: string | null = null;
  try {
    rows = await apiGet<unknown[]>("/admin/platform/agents");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="space-y-4">
      <h2 className="font-display text-2xl font-light text-ink">Agent registry</h2>
      <p className="text-sm leading-relaxed tracking-body text-ink-secondary">
        Each agent includes <code className="code-inline">supported_skills</code>{" "}
        from the join table.
      </p>
      {err && <p className="text-sm text-red-800">{err}</p>}
      {!err && <JsonBlock data={rows} />}
    </div>
  );
}
