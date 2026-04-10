"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

export function TenantNav({
  tenantId,
  name,
}: {
  tenantId: string;
  name: string;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const base = `/admin/tenants/${tenantId}`;
  const links = [
    { href: `${base}`, label: "Overview" },
    { href: `${base}/execute`, label: "Execute" },
    { href: `${base}/personas`, label: "Personas" },
    { href: `${base}/sources`, label: "Sources" },
    { href: `${base}/products`, label: "Products" },
    { href: `${base}/articles`, label: "Articles" },
    { href: `${base}/newsletters`, label: "Newsletters" },
    { href: `${base}/jobs`, label: "Jobs" },
    { href: `${base}/tags`, label: "Tags" },
    { href: `${base}/templates`, label: "Templates" },
    { href: `${base}/formatted`, label: "Formatted" },
  ];

  return (
    <div className="mb-8">
      <div className="mb-3 flex items-center gap-2 text-[0.94rem] font-medium tracking-body text-muted">
        <Link href="/admin/tenants" className="link-muted">
          ← Tenants
        </Link>
        <span className="text-border">/</span>
        <span className="text-ink">{name}</span>
      </div>
      <div className="lg:hidden">
        <button
          type="button"
          aria-expanded={open}
          aria-controls="tenant-nav-links"
          onClick={() => setOpen((v) => !v)}
          className="w-full rounded-full border border-border bg-surface px-4 py-2.5 text-left text-[15px] font-medium text-ink shadow-card focus:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          {open ? "Hide sections" : "Show tenant sections"}
        </button>
      </div>
      <nav
        id="tenant-nav-links"
        className={`-mx-1 mt-3 flex flex-wrap gap-1 border-b border-border-subtle pb-3 lg:mt-0 lg:flex lg:overflow-x-auto lg:pb-3 ${open ? "flex" : "hidden lg:flex"}`}
        aria-label="Tenant section"
      >
        {links.map((l) => {
          const active =
            l.href === base
              ? pathname === base || pathname === `${base}/`
              : pathname === l.href || pathname.startsWith(`${l.href}/`);
          return (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className={`shrink-0 rounded-lg px-3 py-2 text-[15px] font-medium leading-[1.4] tracking-[0.15px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-focus ${
                active
                  ? "bg-surface text-ink shadow-elev-outline"
                  : "text-muted hover:bg-surface-muted hover:text-ink"
              }`}
            >
              {l.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
