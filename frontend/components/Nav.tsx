"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getToken, onTokenChange, setToken } from "@/lib/api";

const ITEMS = [
  { href: "/", label: "Today" },
  { href: "/early-warning", label: "Early warning" },
];

function useSignedIn() {
  const [signed, setSigned] = useState(false);
  useEffect(() => {
    const f = () => setSigned(Boolean(getToken()));
    f();
    return onTokenChange(f);
  }, []);
  return signed;
}

function AdminLink({ onClick }: { onClick?: () => void }) {
  const signed = useSignedIn();
  const path = usePathname();
  return (
    <span className="inline-flex items-center gap-1">
      <Link href="/admin" onClick={onClick}
        className={`rounded-md px-2.5 py-1.5 text-sm ${path.startsWith("/admin") ? "bg-accent/10 text-accent" : "text-ink2 hover:text-ink"}`}>
        Admin
      </Link>
      {signed && <button className="text-xs text-muted hover:text-ink" onClick={() => setToken("")}>Log out</button>}
    </span>
  );
}

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
              </Link>
            );
          })}
          <div className="mt-2 flex items-center gap-2 md:hidden"><AdminLink onClick={() => setOpen(false)} /><ThemeToggle /></div>
        </nav>
        <div className="hidden items-center gap-2 md:ml-auto md:flex"><AdminLink /><ThemeToggle /></div>
      </div>
    </header>
  );
}
