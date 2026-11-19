"use client";

/* Simple view (default) for everyday readers; Analyst view unlocks the detailed tools.
   The choice is a per-browser preference, stored in localStorage. */

import { createContext, ReactNode, useContext, useEffect, useState } from "react";

export type ViewMode = "simple" | "analyst";
const KEY = "georisk.view";
const Ctx = createContext<{ mode: ViewMode; setMode: (m: ViewMode) => void; ready: boolean }>({
  mode: "simple", setMode: () => {}, ready: false,
});

export function ViewProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ViewMode>("simple");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    try { if (localStorage.getItem(KEY) === "analyst") setModeState("analyst"); } catch { /* ignore */ }
    setReady(true);
  }, []);
  const setMode = (m: ViewMode) => {
    setModeState(m);
    try { localStorage.setItem(KEY, m); } catch { /* ignore */ }
  };
  return <Ctx.Provider value={{ mode, setMode, ready }}>{children}</Ctx.Provider>;
}

export const useView = () => useContext(Ctx);

export function ViewToggle() {
  const { mode, setMode } = useView();
  const analyst = mode === "analyst";
  return (
    <button type="button" role="switch" aria-checked={analyst} onClick={() => setMode(analyst ? "simple" : "analyst")}
      title="Analyst view shows scores, raw evidence, data sources and settings"
      className="inline-flex items-center gap-2 rounded-full border border-line px-2.5 py-1 text-xs text-ink2 hover:text-ink">
      <span className={`relative inline-block h-4 w-7 shrink-0 rounded-full transition ${analyst ? "bg-accent" : "bg-ink2/35"}`}>
        <span className={`absolute top-0.5 h-3 w-3 rounded-full bg-white shadow transition-all ${analyst ? "left-3.5" : "left-0.5"}`} />
      </span>
      Analyst view
    </button>
  );
}
