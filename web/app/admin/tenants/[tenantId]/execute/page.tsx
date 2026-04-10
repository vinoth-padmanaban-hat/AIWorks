"use client";

import { TenantNav } from "@/components/TenantNav";
import { useParams } from "next/navigation";
import { useState } from "react";

const API =
  typeof window !== "undefined"
    ? process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000"
    : "http://127.0.0.1:8000";

type StepResult = {
  step_id: string;
  skill_id: string;
  status: string;
  output: Record<string, unknown>;
  error: string | null;
};

type ExecResult = {
  execution_id: string;
  tenant_id: string;
  status: string;
  goal: string;
  plan: Record<string, unknown>[];
  steps: StepResult[];
  cost: { tokens_in: number; tokens_out: number; cost_usd: number };
  error: string | null;
};

export default function ExecutePage() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const [goal, setGoal] = useState(
    "Scrape configured sources, create newsletter articles with product references for review"
  );
  const [skillIds, setSkillIds] = useState("content_curation");
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<ExecResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const body: Record<string, unknown> = {
        tenant_id: tenantId,
        goal,
      };
      if (skillIds.trim()) {
        body.skill_ids = skillIds
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      }
      const res = await fetch(`${API}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(JSON.stringify(data, null, 2));
      } else {
        setResult(data as ExecResult);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  };

  const inputClass =
    "w-full rounded-xl border border-border bg-surface px-3 py-2.5 text-sm leading-relaxed tracking-body text-ink placeholder:text-muted/50 focus:border-ink focus:outline-none focus:ring-2 focus:ring-focus";

  return (
    <div>
      <TenantNav tenantId={tenantId} name="Tenant" />
      <h1 className="font-display mb-4 text-2xl font-light text-ink">Execute goal</h1>

      <div className="panel-surface rounded-2xl p-5">
        <label className="mb-1 block text-sm font-medium text-ink-secondary">Goal</label>
        <textarea
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          rows={3}
          className={inputClass}
          placeholder="Describe what the coworker should do..."
        />

        <label className="mb-1 mt-4 block text-sm font-medium text-ink-secondary">
          Skill IDs (comma-separated, leave empty for planner to decide)
        </label>
        <input
          value={skillIds}
          onChange={(e) => setSkillIds(e.target.value)}
          className={inputClass}
          placeholder="content_curation, content_ingestion"
        />

        <button
          type="button"
          onClick={run}
          disabled={running || !goal.trim()}
          className="btn-pill-primary mt-5 text-sm disabled:opacity-50"
        >
          {running ? "Running..." : "Execute"}
        </button>
      </div>

      {error && (
        <pre className="mt-4 overflow-x-auto rounded-2xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
          {error}
        </pre>
      )}

      {result && (
        <div className="mt-6 space-y-4">
          <div className="panel-surface rounded-2xl p-4">
            <div className="flex items-center gap-3">
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                  result.status === "SUCCESS"
                    ? "bg-emerald-50 text-emerald-900"
                    : result.status === "ERROR"
                    ? "bg-red-50 text-red-800"
                    : "bg-amber-50 text-amber-900"
                }`}
              >
                {result.status}
              </span>
              <span className="font-mono text-xs text-muted">
                {result.execution_id}
              </span>
            </div>

            {result.cost && (
              <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
                <span>Tokens: {result.cost.tokens_in} in / {result.cost.tokens_out} out</span>
                <span>Cost: ${result.cost.cost_usd?.toFixed(6)}</span>
              </div>
            )}
          </div>

          {result.plan.length > 0 && (
            <div className="panel-surface rounded-2xl p-4">
              <h3 className="mb-2 text-sm font-medium text-ink">Plan</h3>
              <div className="space-y-1">
                {result.plan.map((step, i) => (
                  <div
                    key={i}
                    className="flex gap-2 text-xs font-mono text-muted"
                  >
                    <span className="text-ink">{i + 1}.</span>
                    <span>{step.skill_id as string}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.steps.length > 0 && (
            <div className="panel-surface rounded-2xl p-4">
              <h3 className="mb-2 text-sm font-medium text-ink">Results</h3>
              {result.steps.map((step) => (
                <div
                  key={step.step_id}
                  className="border-b border-border/60 py-2 last:border-0"
                >
                  <div className="flex items-center gap-2 text-sm">
                    <span className="font-mono text-ink">
                      {step.skill_id}
                    </span>
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${
                        step.status === "SUCCESS"
                          ? "bg-emerald-50 text-emerald-900"
                          : "bg-red-50 text-red-800"
                      }`}
                    >
                      {step.status}
                    </span>
                  </div>
                  {step.error && (
                    <p className="mt-1 text-xs text-red-800">{step.error}</p>
                  )}
                  {step.output &&
                    Object.keys(step.output).length > 0 && (
                      <pre className="mt-2 max-h-40 overflow-auto rounded-xl border border-border bg-surface-muted p-2 text-xs text-muted">
                        {JSON.stringify(step.output, null, 2)}
                      </pre>
                    )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
