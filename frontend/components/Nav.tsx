"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/geopolitics", label: "Geopolitics" },
  { href: "/economy", label: "Economy", planned: true },
  { href: "/news", label: "News & Events" },
  { href: "/alerts", label: "Alerts" },
  { href: "/datasets", label: "Datasets & Models" },
  { href: "/settings", label: "Settings" },
];

type Theme = "system" | "light" | "dark";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");
  useEffect(() => {
    try { setTheme((localStorage.getItem("georisk.theme") as Theme) || "system"); } catch { /* ignore */ }
  }, []);
  const apply = (t: Theme) => {
    setTheme(t);
    try { localStorage.setItem("georisk.theme", t); } catch { /* ignore */ }
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  };
  const next: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
  return (
    <button className="btn text-xs" onClick={() => apply(next[theme])} aria-label="Toggle theme">
      {theme === "dark" ? "☾ Dark" : theme === "light" ? "☀ Light" : "◐ System"}
    </button>
  );
}

export default function Nav() {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/95 backdrop-blur">
      <div className="mx-auto flex max-w-[1500px] items-center gap-4 px-4 py-2.5">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-sm text-white">G</span>
          GeoRisk
        </Link>
        <button className="btn ml-auto md:hidden" onClick={() => setOpen(!open)} aria-expanded={open}>Menu</button>
        <nav className={`${open ? "flex" : "hidden"} absolute left-0 right-0 top-full flex-col gap-1 border-b border-line bg-surface p-3 md:static md:flex md:flex-row md:border-0 md:bg-transparent md:p-0`}>
          {ITEMS.map((i) => {
            const active = i.href === "/" ? path === "/" : path.startsWith(i.href);
            return (
              <Link key={i.href} href={i.href} onClick={() => setOpen(false)}
                className={`rounded-md px-2.5 py-1.5 text-sm ${active ? "bg-accent/10 text-accent" : "text-ink2 hover:text-ink"}`}>
                {i.label}
                {i.planned && <span className="ml-1 rounded bg-line px-1 text-[10px] uppercase text-muted">planned</span>}
              </Link>
            );
          })}
        </nav>
        <div className="hidden md:ml-auto md:block"><ThemeToggle /></div>
      </div>
    </header>
  );
}
