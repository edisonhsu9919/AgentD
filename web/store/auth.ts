import { create } from "zustand";
import { apiFetch, setTokens, clearTokens, getToken, getRefreshToken } from "@/lib/api";
import type { User, LoginResponse } from "@/lib/types";

interface AuthState {
  token: string | null;
  refreshToken: string | null;
  user: User | null;
  isLoading: boolean;
  error: string | null;

  hydrate: () => void;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  fetchMe: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  token: null,
  refreshToken: null,
  user: null,
  isLoading: false,
  error: null,

  hydrate: () => {
    const token = getToken();
    const refreshToken = getRefreshToken();
    set({ token, refreshToken });
  },

  login: async (username: string, password: string) => {
    set({ isLoading: true, error: null });
    try {
      const data = await apiFetch<LoginResponse>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setTokens(data.access_token, data.refresh_token);
      set({
        token: data.access_token,
        refreshToken: data.refresh_token,
        user: data.user,
        isLoading: false,
      });
    } catch (err) {
      const msg =
        err instanceof TypeError
          ? "无法连接到 AgentD 服务，请确认前后端运行正常"
          : err instanceof Error
            ? err.message
            : "登录失败";
      set({ isLoading: false, error: msg });
      throw err;
    }
  },

  logout: () => {
    clearTokens();
    set({ token: null, refreshToken: null, user: null });
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  },

  fetchMe: async () => {
    if (!get().token) return;
    try {
      const user = await apiFetch<User>("/auth/me");
      set({ user });
    } catch {
      // Token invalid — force logout
      get().logout();
    }
  },
}));
