import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type {
  DomainExtensionItem,
  DomainExtensionsResponse,
} from "@/lib/types";

interface ExtensionState {
  extensions: DomainExtensionItem[];
  loading: boolean;
  loaded: boolean;
  error: string | null;
  fetchExtensions: (force?: boolean) => Promise<void>;
  getExtensionByName: (name: string) => DomainExtensionItem | undefined;
}

export const useExtensionStore = create<ExtensionState>((set, get) => ({
  extensions: [],
  loading: false,
  loaded: false,
  error: null,

  fetchExtensions: async (force = false) => {
    const state = get();
    if (!force && (state.loading || state.loaded)) return;

    set({ loading: true, error: null });
    try {
      const data = await apiFetch<DomainExtensionsResponse>("/extensions");
      set({
        extensions: Array.isArray(data.extensions) ? data.extensions : [],
        loading: false,
        loaded: true,
        error: null,
      });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load extensions";
      set({
        extensions: [],
        loading: false,
        loaded: true,
        error: message,
      });
    }
  },

  getExtensionByName: (name: string) =>
    get().extensions.find((extension) => extension.name === name),
}));
