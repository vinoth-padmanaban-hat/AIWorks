"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

const links = [
  { href: "/admin", label: "Overview" },
  { href: "/admin/tenants", label: "Tenants" },
  { href: "/admin/skills", label: "Skills" },
  { href: "/admin/agents", label: "Agents" },
  { href: "/admin/policies", label: "Policies" },
  { href: "/admin/personas", label: "Personas" },
];

export function AdminNav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between lg:hidden">
        <span className="text-[15px] font-medium tracking-[0.15px] text-muted">
          Menu
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-controls="admin-nav-links"
          onClick={() => setOpen((v) => !v)}
          className="rounded-full border border-border bg-surface px-4 py-2 text-[15px] font-medium text-ink shadow-card transition hover:bg-surface-muted focus:outline-none focus-visible:ring-2 focus-visible:ring-focus focus-visible:ring-offset-2"
        >
          {open ? "Close" : "Menu"}
        </button>
      </div>
      <nav
        id="admin-nav-links"
        className={`${open ? "mt-4 flex" : "hidden lg:flex"} flex-col gap-1 border-b border-border-subtle pb-4 lg:mt-0 lg:flex-row lg:flex-wrap lg:gap-1 lg:border-b lg:pb-3`}
        aria-label="Platform admin"
      >
        {links.map((l) => {
          const active =
            l.href === "/admin"
              ? pathname === "/admin"
              : pathname === l.href || pathname.startsWith(`${l.href}/`);
          return (
            <Link
              key={l.href}
              href={l.href}
              onClick={() => setOpen(false)}
              className={`rounded-lg px-3 py-2 text-[15px] font-medium leading-[1.4] tracking-[0.15px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-focus ${
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
