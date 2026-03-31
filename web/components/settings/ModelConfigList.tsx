"use client";

import { Plus, Edit2, Star, Power, PowerOff, Eye } from "lucide-react";
import type { ModelConfig } from "@/lib/types";

interface ModelConfigListProps {
  configs: ModelConfig[];
  loading: boolean;
  onNew: () => void;
  onEdit: (config: ModelConfig) => void;
  onEnable: (id: string) => void;
  onDisable: (id: string) => void;
  onSetDefault: (id: string) => void;
}

export default function ModelConfigList({
  configs,
  loading,
  onNew,
  onEdit,
  onEnable,
  onDisable,
  onSetDefault,
}: ModelConfigListProps) {
  return (
    <div className="rounded-lg border border-border bg-bg-secondary p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-medium text-text-secondary">
          Model Configurations
        </h3>
        <button
          onClick={onNew}
          className="flex items-center gap-1 rounded bg-accent px-2.5 py-1 text-[11px] font-medium text-white transition hover:bg-accent/90"
        >
          <Plus size={12} />
          New
        </button>
      </div>

      {loading && configs.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : configs.length === 0 ? (
        <p className="py-6 text-center text-xs text-text-secondary">
          No model configurations yet
        </p>
      ) : (
        <div className="space-y-2">
          {configs.map((config) => (
            <div
              key={config.id}
              className={`flex items-center justify-between rounded-lg border p-3 transition ${
                config.is_enabled
                  ? "border-border bg-bg-primary"
                  : "border-border/50 bg-bg-primary/50 opacity-60"
              }`}
            >
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="truncate text-xs font-medium text-text-primary">
                    {config.name}
                  </span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                      config.model_type === "vlm"
                        ? "bg-purple-500/10 text-purple-400"
                        : "bg-blue-500/10 text-blue-400"
                    }`}
                  >
                    {config.model_type === "vlm" ? "VLM" : "LLM"}
                  </span>
                  {config.model_type === "vlm" && (
                    <span className="flex items-center gap-0.5 rounded bg-purple-500/10 px-1.5 py-0.5 text-[10px] text-purple-400">
                      <Eye size={9} />
                      Vision
                    </span>
                  )}
                  {config.is_default && (
                    <span className="flex items-center gap-0.5 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] font-medium text-accent">
                      <Star size={9} />
                      Default
                    </span>
                  )}
                  {!config.is_enabled && (
                    <span className="rounded bg-danger/10 px-1.5 py-0.5 text-[10px] text-danger">
                      Disabled
                    </span>
                  )}
                </div>
                <div className="flex flex-wrap gap-x-4 text-[10px] text-text-secondary">
                  <span>{config.provider_type}</span>
                  <span>{config.model_id}</span>
                  <span className="max-w-[200px] truncate">
                    {config.base_url}
                  </span>
                  {config.timeout_seconds && (
                    <span>timeout: {config.timeout_seconds}s</span>
                  )}
                </div>
              </div>

              <div className="ml-3 flex shrink-0 items-center gap-1">
                {!config.is_default && config.is_enabled && (
                  <button
                    onClick={() => onSetDefault(config.id)}
                    className="rounded p-1.5 text-text-secondary transition hover:bg-accent/10 hover:text-accent"
                    title="Set as Default"
                  >
                    <Star size={13} />
                  </button>
                )}
                {config.is_enabled ? (
                  <button
                    onClick={() => onDisable(config.id)}
                    className="rounded p-1.5 text-text-secondary transition hover:bg-warning/10 hover:text-warning"
                    title="Disable"
                  >
                    <PowerOff size={13} />
                  </button>
                ) : (
                  <button
                    onClick={() => onEnable(config.id)}
                    className="rounded p-1.5 text-text-secondary transition hover:bg-success/10 hover:text-success"
                    title="Enable"
                  >
                    <Power size={13} />
                  </button>
                )}
                <button
                  onClick={() => onEdit(config)}
                  className="rounded p-1.5 text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
                  title="Edit"
                >
                  <Edit2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
