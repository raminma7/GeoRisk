"use client";

import useSWR, { SWRConfiguration } from "swr";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const TOKEN_STORAGE = "georisk.adminToken";
const listeners = new Set<() => void>();

/** Admin session token (set after signing in on /admin). */
export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setToken(v: string) {
  try {
    if (v) localStorage.setItem(TOKEN_STORAGE, v);
    else localStorage.removeItem(TOKEN_STORAGE);
  } catch {
    /* storage unavailable: the session lasts for this page only */
  }
  listeners.forEach((f) => f());
}

export function onTokenChange(f: () => void): () => void {
  listeners.add(f);
  return () => { listeners.delete(f); };
}

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(typeof body === "object" && body && "detail" in body ? String((body as { detail: unknown }).detail) : `HTTP ${status}`);
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "content-type": "application/json", ...(init.headers as Record<string, string>) };
  const token = getToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (res.status === 401 && token) setToken("");  // expired session: sign out
  if (!res.ok && res.status !== 501) throw new ApiError(res.status, body);
  return body as T;
}

export const post = <T,>(path: string, body: unknown) => api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });

export function useApi<T = unknown>(path: string | null, opts: SWRConfiguration = {}) {
  return useSWR<T>(path, (p: string) => api<T>(p), { revalidateOnFocus: false, ...opts });
}

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  });
  const s = u.toString();
  return s ? `?${s}` : "";
}
