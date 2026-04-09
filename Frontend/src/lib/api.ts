import { AUTH_STORAGE_KEY } from "@/hooks/use-auth";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://127.0.0.1:8000";

export const apiFetch = async (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers || {});
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as { token?: string };
      if (parsed?.token) headers.set("Authorization", `Bearer ${parsed.token}`);
    }
  } catch {
    // ignore auth storage parse issues
  }
  const response = await fetch(`${BACKEND_URL}${path}`, { ...init, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || payload.detail || `HTTP ${response.status}`);
  }
  return payload;
};
