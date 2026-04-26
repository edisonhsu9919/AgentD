"use client";

import { Plus, Edit2, Star, Power, PowerOff, Eye, Trash2 } from "lucide-react";
import type { ModelConfig } from "@/lib/types";

type ActionHandler = (id: string) => void | Promise<void>;

interface ModelConfigListProps {
  configs: ModelConfig[];
  loading: boolean;
  onNew: () => void;
  onEdit: (config: ModelConfig) => void;
  onDelete: ActionHandler;
  onEnable: ActionHandler;
  onDisable: ActionHandler;
  onSetDefault: ActionHandler;
  onUnsetDefault: ActionHandler;
}

export default function ModelConfigList({
  configs,
  loading,
  onNew,
  onEdit,
  onDelete,
  onEnable,
  onDisable,
  onSetDefault,
  onUnsetDefault,
}: ModelConfigListProps) {
  const handleSetDefault = async (config: ModelConfig) => {
    if (!config.is_enabled) {
      await onEnable(config.id);
    }
    await onSetDefault(config.id);
  };

  const handleDelete = async (config: ModelConfig) => {
    const confirmed = window.confirm(
      `删除模型配置「${config.name}」？如果它是当前默认模型，运行时会回退到可用配置或环境变量。`,
    );
    if (confirmed) {
      await onDelete(config.id);
    }
  };

  return (
    <div className="rounded-[28px] bg-bg-primary/42 p-2 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
      <div className="flex items-center justify-between px-2 py-2">
        <div>
          <h3 className="text-sm font-medium text-text-primary">模型配置</h3>
          <p className="mt-1 text-[11px] text-text-secondary">
            默认模型按 LLM / VLM 类型分别生效；未配置时回退到环境变量。
          </p>
        </div>
        <button
          onClick={onNew}
          className="inline-flex min-w-[104px] items-center justify-center gap-1.5 whitespace-nowrap rounded-full bg-accent px-3.5 py-2 text-xs font-medium text-white shadow-sm transition hover:bg-accent/90"
        >
          <Plus size={12} />
          新建配置
        </button>
      </div>

      {loading && configs.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : configs.length === 0 ? (
        <p className="py-8 text-center text-xs text-text-secondary">
          暂无模型配置
        </p>
      ) : (
        <div className="space-y-2">
          {configs.map((config) => (
            <div
              key={config.id}
              className={`flex items-center justify-between rounded-[22px] p-4 shadow-[0_8px_24px_rgba(15,23,42,0.04)] transition duration-200 hover:-translate-y-0.5 hover:bg-white/92 ${
                config.is_enabled
                  ? "bg-white/75"
                  : "bg-white/50 opacity-65"
              }`}
            >
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="truncate text-xs font-medium text-text-primary">
                    {config.name}
                  </span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                      config.model_type === "vlm"
                        ? "bg-purple-500/10 text-purple-400"
                        : "bg-blue-500/10 text-blue-400"
                    }`}
                  >
                    {config.model_type === "vlm" ? "VLM" : "LLM"}
                  </span>
                  {config.model_type === "vlm" && (
                    <span className="flex items-center gap-0.5 rounded-full bg-purple-500/10 px-2 py-0.5 text-[10px] text-purple-400">
                      <Eye size={9} />
                      Vision
                    </span>
                  )}
                  {config.is_default && (
                    <span className="flex items-center gap-0.5 rounded-full bg-accent/10 px-2 py-0.5 text-[10px] font-medium text-accent">
                      <Star size={9} />
                      当前默认
                    </span>
                  )}
                  {!config.is_enabled && (
                    <span className="rounded-full bg-danger/10 px-2 py-0.5 text-[10px] text-danger">
                      已禁用
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
                    <span>超时：{config.timeout_seconds}s</span>
                  )}
                </div>
              </div>

              <div className="ml-3 flex shrink-0 items-center gap-1">
                {!config.is_default && (
                  <button
                    onClick={() => handleSetDefault(config)}
                    className="inline-flex items-center gap-1 rounded-full bg-accent/10 px-2.5 py-1.5 text-[11px] font-medium text-accent transition hover:bg-accent/20"
                    title={config.is_enabled ? "设为默认" : "启用并设为默认"}
                  >
                    <Star size={13} />
                    设为默认
                  </button>
                )}
                {config.is_default && (
                  <button
                    onClick={() => onUnsetDefault(config.id)}
                    className="inline-flex items-center gap-1 rounded-full bg-bg-primary px-2.5 py-1.5 text-[11px] font-medium text-text-secondary transition hover:bg-accent/10 hover:text-accent"
                    title="取消默认，保留启用状态"
                  >
                    <Star size={13} />
                    取消默认
                  </button>
                )}
                {config.is_enabled ? (
                  <button
                    onClick={() => onDisable(config.id)}
                    className="rounded-full p-1.5 text-text-secondary transition hover:bg-warning/10 hover:text-warning"
                    title="禁用"
                  >
                    <PowerOff size={13} />
                  </button>
                ) : (
                  <button
                    onClick={() => onEnable(config.id)}
                    className="rounded-full p-1.5 text-text-secondary transition hover:bg-success/10 hover:text-success"
                    title="启用"
                  >
                    <Power size={13} />
                  </button>
                )}
                <button
                  onClick={() => onEdit(config)}
                  className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-primary hover:text-text-primary"
                  title="编辑"
                >
                  <Edit2 size={13} />
                </button>
                <button
                  onClick={() => handleDelete(config)}
                  className="rounded-full p-1.5 text-text-secondary transition hover:bg-danger/10 hover:text-danger"
                  title="删除"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
