import Link from "next/link";
import { AdminNav } from "@/components/AdminNav";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-40 border-b border-border-subtle bg-surface/95 backdrop-blur-md">
        <div className="mx-auto max-w-[1200px] px-4 py-6 sm:px-6 lg:px-8">
          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-[0.63rem] font-medium uppercase tracking-[0.7px] text-muted">
                AIWorks
              </p>
              <h1 className="font-display mt-2 text-[1.75rem] font-light leading-tight tracking-[-0.02em] text-ink">
                Platform admin
              </h1>
              <p className="mt-2 max-w-xl text-[0.94rem] leading-[1.6] tracking-body-lg text-ink-secondary">
                Control plane registries, policies, and per-tenant content.
              </p>
            </div>
            <Link href="/admin/tenants" className="btn-pill-primary shrink-0">
              Tenant workspaces →
            </Link>
          </div>
          <div className="mt-8">
            <AdminNav />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-[1200px] px-4 py-10 sm:px-6 lg:px-8">
        {children}
      </main>
    </div>
  );
}
