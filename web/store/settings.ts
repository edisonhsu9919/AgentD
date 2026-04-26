import { create } from "zustand";
import { apiFetch, ApiRequestError } from "@/lib/api";
import { API_URL } from "@/lib/constants";
import type {
  HealthResponse,
  ModelConfig,
  ModelConfigCreate,
  ModelConfigUpdate,
  RuntimeModelConfigData,
  VLMConfigResponse,
  DiagnosticsData,
} from "@/lib/types";

interface SettingsState {
  // Health
  health: HealthResponse | null;
  healthLoading: boolean;
  fetchHealth: () => Promise<void>;

  // Model configs
  configs: ModelConfig[];
  configsLoading: boolean;
  fetchConfigs: () => Promise<void>;

  // Runtime model config
  runtimeConfig: RuntimeModelConfigData | null;
  fetchRuntimeConfig: () => Promise<void>;

  // VLM config
  vlmConfig: VLMConfigResponse | null;
  fetchVLMConfig: () => Promise<void>;

  // Diagnostics
  diagnostics: DiagnosticsData | null;
  diagnosticsLoading: boolean;
  fetchDiagnostics: () => Promise<void>;

  // Editor state
  editingConfig: ModelConfig | null;
  isCreating: boolean;
  editorLoading: boolean;
  editorError: string | null;
  openCreateEditor: () => void;
  openEditEditor: (config: ModelConfig) => void;
  closeEditor: () => void;

  // CRUD actions
  createConfig: (data: ModelConfigCreate) => Promise<void>;
  updateConfig: (id: string, data: ModelConfigUpdate) => Promise<void>;
  deleteConfig: (id: string) => Promise<void>;
  enableConfig: (id: string) => Promise<void>;
  disableConfig: (id: string) => Promise<void>;
  setDefaultConfig: (id: string) => Promise<void>;
  unsetDefaultConfig: (id: string) => Promise<void>;

  // Refresh health + configs + runtime together
  refreshStatus: () => Promise<void>;
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  health: null,
  healthLoading: false,
  configs: [],
  configsLoading: false,
  runtimeConfig: null,
  vlmConfig: null,
  diagnostics: null,
  diagnosticsLoading: false,
  editingConfig: null,
  isCreating: false,
  editorLoading: false,
  editorError: null,

  fetchHealth: async () => {
    set({ healthLoading: true });
    try {
      const baseUrl = API_URL.replace(/\/api$/, "");
      const res = await fetch(`${baseUrl}/health`);
      const data: HealthResponse = await res.json();
      set({ health: data, healthLoading: false });
    } catch {
      set({ healthLoading: false });
    }
  },

  fetchConfigs: async () => {
    set({ configsLoading: true });
    try {
      const data = await apiFetch<ModelConfig[]>("/admin/model-configs");
      set({ configs: data, configsLoading: false });
    } catch {
      set({ configsLoading: false });
    }
  },

  fetchRuntimeConfig: async () => {
    try {
      const data = await apiFetch<RuntimeModelConfigData>(
        "/admin/runtime/model-config",
      );
      set({ runtimeConfig: data });
    } catch {
      // silent
    }
  },

  fetchVLMConfig: async () => {
    try {
      const data = await apiFetch<VLMConfigResponse>(
        "/admin/runtime/vlm-config",
      );
      set({ vlmConfig: data });
    } catch {
      // silent
    }
  },

  fetchDiagnostics: async () => {
    set({ diagnosticsLoading: true });
    try {
      const data = await apiFetch<DiagnosticsData>(
        "/admin/runtime/diagnostics",
      );
      set({ diagnostics: data, diagnosticsLoading: false });
    } catch {
      set({ diagnosticsLoading: false });
    }
  },

  openCreateEditor: () => {
    set({ editingConfig: null, isCreating: true, editorError: null });
  },

  openEditEditor: (config: ModelConfig) => {
    set({ editingConfig: config, isCreating: false, editorError: null });
  },

  closeEditor: () => {
    set({ editingConfig: null, isCreating: false, editorError: null });
  },

  createConfig: async (data: ModelConfigCreate) => {
    set({ editorLoading: true, editorError: null });
    try {
      await apiFetch<ModelConfig>("/admin/model-configs", {
        method: "POST",
        body: JSON.stringify(data),
      });
      set({ editorLoading: false, isCreating: false, editingConfig: null });
      await get().refreshStatus();
    } catch (err) {
      const message =
        err instanceof ApiRequestError ? err.message : "Create failed";
      set({ editorLoading: false, editorError: message });
    }
  },

  updateConfig: async (id: string, data: ModelConfigUpdate) => {
    set({ editorLoading: true, editorError: null });
    try {
      await apiFetch<ModelConfig>(`/admin/model-configs/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
      });
      set({ editorLoading: false, isCreating: false, editingConfig: null });
      await get().refreshStatus();
    } catch (err) {
      const message =
        err instanceof ApiRequestError ? err.message : "Update failed";
      set({ editorLoading: false, editorError: message });
    }
  },

  deleteConfig: async (id: string) => {
    try {
      await apiFetch<{ deleted: boolean; id: string }>(`/admin/model-configs/${id}`, {
        method: "DELETE",
      });
      set((state) => ({
        editingConfig:
          state.editingConfig?.id === id ? null : state.editingConfig,
        isCreating: state.editingConfig?.id === id ? false : state.isCreating,
      }));
      await get().refreshStatus();
    } catch {
      // silent
    }
  },

  enableConfig: async (id: string) => {
    try {
      await apiFetch<ModelConfig>(`/admin/model-configs/${id}/enable`, {
        method: "POST",
      });
      await get().refreshStatus();
    } catch {
      // silent
    }
  },

  disableConfig: async (id: string) => {
    try {
      await apiFetch<ModelConfig>(`/admin/model-configs/${id}/disable`, {
        method: "POST",
      });
      await get().refreshStatus();
    } catch {
      // silent
    }
  },

  setDefaultConfig: async (id: string) => {
    try {
      await apiFetch<ModelConfig>(`/admin/model-configs/${id}/set-default`, {
        method: "POST",
      });
      await get().refreshStatus();
    } catch {
      // silent
    }
  },

  unsetDefaultConfig: async (id: string) => {
    try {
      await apiFetch<ModelConfig>(`/admin/model-configs/${id}/unset-default`, {
        method: "POST",
      });
      await get().refreshStatus();
    } catch {
      // silent
    }
  },

  refreshStatus: async () => {
    const { fetchHealth, fetchConfigs, fetchRuntimeConfig, fetchVLMConfig } = get();
    await Promise.all([fetchHealth(), fetchConfigs(), fetchRuntimeConfig(), fetchVLMConfig()]);
  },
}));
