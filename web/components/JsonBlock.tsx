"use client";

export function JsonBlock({ data }: { data: unknown }) {
  return (
    <pre className="overflow-x-auto rounded-[18px] border border-border bg-surface-muted p-4 font-mono text-[13px] leading-[1.85] text-ink-secondary shadow-elev-outline">
      {JSON.stringify(data, null, 2)}
    </pre>
  );
}
