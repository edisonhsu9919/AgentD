import { API_URL } from "./constants";

// ---------------------------------------------------------------------------
// Token helpers (localStorage)
// ---------------------------------------------------------------------------

const TOKEN_KEY = "agentd_token";
const REFRESH_KEY = "agentd_refresh_token";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem(TOKEN_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

export class ApiRequestError extends Error {
  code: string;
  status: number;
  details?: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

let isRefreshing = false;
let refreshPromise: Promise<boolean> | null = null;

async function tryRefresh(): Promise<boolean> {
  if (isRefreshing && refreshPromise) return refreshPromise;

  isRefreshing = true;
  refreshPromise = (async () => {
    const rt = getRefreshToken();
    if (!rt) return false;

    try {
      const res = await fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: rt }),
      });
      if (!res.ok) return false;

      const json = await res.json();
      const newToken = json.data?.access_token;
      if (!newToken) return false;

      localStorage.setItem(TOKEN_KEY, newToken);
      return true;
    } catch {
      return false;
    } finally {
      isRefreshing = false;
      refreshPromise = null;
    }
  })();

  return refreshPromise;
}

/**
 * Authenticated fetch wrapper.
 * - Auto-injects Bearer token
 * - On 401: tries refresh once, retries original request
 * - On failure to refresh: redirects to /login
 * - Parses { data, meta } or { error } envelope
 */
export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const url = `${API_URL}${path}`;

  const makeHeaders = (): Record<string, string> => {
    const h: Record<string, string> = {};
    const token = getToken();
    if (token) h["Authorization"] = `Bearer ${token}`;
    if (
      options.method &&
      options.method !== "GET" &&
      !(options.body instanceof FormData)
    ) {
      h["Content-Type"] = "application/json";
    }
    return h;
  };

  const doFetch = async (): Promise<Response> => {
    return fetch(url, {
      ...options,
      headers: { ...makeHeaders(), ...(options.headers as Record<string, string> || {}) },
    });
  };

  let res = await doFetch();

  // 401 → try refresh once
  if (res.status === 401) {
    const refreshed = await tryRefresh();
    if (refreshed) {
      res = await doFetch();
    } else {
      clearTokens();
      if (typeof window !== "undefined") {
        window.location.href = "/login";
      }
      throw new ApiRequestError(401, "UNAUTHORIZED", "Session expired");
    }
  }

  // Parse response
  const json = await res.json();

  if (!res.ok) {
    const err = json.error || { code: "UNKNOWN", message: res.statusText };
    throw new ApiRequestError(res.status, err.code, err.message, err.details);
  }

  return json.data as T;
}

/**
 * Fetch that returns raw Response (for binary downloads).
 */
export async function apiFetchRaw(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const url = `${API_URL}${path}`;
  const token = getToken();
  const headers: Record<string, string> = {
    ...(options.headers as Record<string, string> || {}),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(url, { ...options, headers });

  if (res.status === 401) {
    clearTokens();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiRequestError(401, "UNAUTHORIZED", "Session expired");
  }

  if (!res.ok) {
    throw new ApiRequestError(res.status, "FETCH_ERROR", res.statusText);
  }

  return res;
}
