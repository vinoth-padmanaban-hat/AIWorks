import { apiGet } from "@/lib/api";
import Link from "next/link";

export const dynamic = "force-dynamic";

type Summary = {
  tenant_count: number;
  skill_count: number;
  agent_count: number;
  policy_count: number;
  persona_count: number;
};

export default async function AdminHome() {
  let summary: Summary | null = null;
  let err: string | null = null;
  try {
    summary = await apiGet<Summary>("/admin/platform/summary");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  return (
    <div className="space-y-10">
      <section>
        <h2 className="font-display text-2xl font-light tracking-tight text-ink">
          Platform snapshot
        </h2>
        <p className="mt-2 text-sm leading-relaxed tracking-body text-ink-secondary">
          Aggregated counts from the control plane database (port 8000).
        </p>
        {err && (
          <p className="mt-4 rounded-2xl border border-amber-200/80 bg-amber-50 p-4 text-sm text-amber-950">
            Could not reach API: {err}
            <br />
            <span className="text-amber-900/80">
              Start the control plane:{" "}
              <code className="code-inline">uv run uvicorn app.main:app --reload</code>
            </span>
          </p>
        )}
        {summary && (
          <dl className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            <Stat label="Tenants" value={summary.tenant_count} href="/admin/tenants" />
            <Stat label="Personas" value={summary.persona_count} href="/admin/personas" />
            <Stat label="Skills" value={summary.skill_count} href="/admin/skills" />
            <Stat label="Agents" value={summary.agent_count} href="/admin/agents" />
            <Stat label="Policies" value={summary.policy_count} href="/admin/policies" />
          </dl>
        )}
      </section>
      <section className="panel-surface rounded-2xl p-6">
        <h3 className="font-display text-xl font-light text-ink">Views</h3>
        <ul className="mt-4 list-inside list-disc space-y-2 text-sm leading-relaxed tracking-body text-ink-secondary">
          <li>
            <strong className="font-medium text-ink">Platform</strong> — browse personas (Persona
            Store), skills, agents, and tenant policies.
          </li>
          <li>
            <strong className="font-medium text-ink">Tenant</strong> — pick a tenant to inspect
            sources, articles, ingestion jobs, tags, and format templates in that
            tenant&apos;s dedicated database.
          </li>
        </ul>
        <div className="mt-6">
          <Link href="/admin/tenants" className="text-sm font-medium text-ink underline-offset-4 hover:underline">
            Open tenant list →
          </Link>
        </div>
      </section>
    </div>
  );
}

function Stat({
  label,
  value,
  href,
}: {
  label: string;
  value: number;
  href: string;
}) {
  return (
    <div className="rounded-2xl border border-border bg-surface px-4 py-3 shadow-elev-outline">
      <dt className="text-[0.75rem] font-medium uppercase tracking-wide text-muted">{label}</dt>
      <dd className="mt-1 flex items-baseline justify-between">
        <span className="font-display text-3xl font-light tabular-nums text-ink">{value}</span>
        <Link href={href} className="text-xs font-medium text-accent-link hover:text-ink hover:underline">
          View
        </Link>
      </dd>
    </div>
  );
}
