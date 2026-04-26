"use client";

import { CheckCircle, XCircle, AlertTriangle, Database, Cpu, Eye } from "lucide-react";
import type { HealthResponse, VLMConfigResponse } from "@/lib/types";

interface RuntimeStatusProps {
  health: HealthResponse | null;
  loading: boolean;
  vlmConfig?: VLMConfigResponse | null;
}

export default function RuntimeStatus({ health, loading, vlmConfig }: RuntimeStatusProps) {
  if (loading && !health) {
    return (
      <div className="space-y-4 rounded-[28px] bg-bg-primary/42 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
        <h3 className="text-sm font-medium text-text-secondary">
          运行时状态
        </h3>
        <div className="flex items-center justify-center py-6">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      </div>
    );
  }

  if (!health) {
    return (
      <div className="space-y-4 rounded-[28px] bg-bg-primary/42 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
        <h3 className="text-sm font-medium text-text-secondary">
          运行时状态
        </h3>
        <p className="text-xs text-text-secondary">
          无法获取健康状态
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 rounded-[28px] bg-bg-primary/42 p-5 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
      <h3 className="text-sm font-medium text-text-secondary">
        运行时状态
      </h3>

      {/* Key status cards */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {/* Ready */}
        <div
          className={`rounded-[20px] p-3 shadow-[0_8px_24px_rgba(15,23,42,0.04)] ${
            health.ready
              ? "bg-success/5"
              : "bg-danger/5"
          }`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            {health.ready ? (
              <CheckCircle size={13} className="text-success" />
            ) : (
              <XCircle size={13} className="text-danger" />
            )}
            <span className="text-[10px] font-medium text-text-secondary">
              就绪
            </span>
          </div>
          <span
            className={`text-sm font-semibold ${
              health.ready ? "text-success" : "text-danger"
            }`}
          >
            {health.ready ? "是" : "否"}
          </span>
          {!health.ready && health.degraded_reason && (
            <p className="mt-0.5 text-[10px] text-danger">
              {health.degraded_reason}
            </p>
          )}
        </div>

        {/* Schema */}
        <div
          className={`rounded-[20px] p-3 shadow-[0_8px_24px_rgba(15,23,42,0.04)] ${
            health.schema_ok
              ? "bg-success/5"
              : "bg-danger/5"
          }`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            {health.schema_ok ? (
              <Database size={13} className="text-success" />
            ) : (
              <AlertTriangle size={13} className="text-danger" />
            )}
            <span className="text-[10px] font-medium text-text-secondary">
              Schema
            </span>
          </div>
          <span
            className={`text-sm font-semibold ${
              health.schema_ok ? "text-success" : "text-danger"
            }`}
          >
            {health.schema_version || "\u2014"}
          </span>
          {!health.schema_ok && (
            <p className="mt-0.5 text-[10px] text-danger">
              期望版本：{health.schema_expected}
            </p>
          )}
        </div>

        {/* LLM Model */}
        <div
          className={`rounded-[20px] p-3 shadow-[0_8px_24px_rgba(15,23,42,0.04)] ${
            health.runtime_model_source === "db_default"
              ? "bg-success/5"
              : "bg-warning/5"
          }`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            <Cpu
              size={13}
              className={
                health.runtime_model_source === "db_default"
                  ? "text-success"
                  : "text-warning"
              }
            />
            <span className="text-[10px] font-medium text-text-secondary">
              LLM 模型
            </span>
          </div>
          <span className="block truncate text-sm font-semibold text-text-primary">
            {health.runtime_model?.name || "\u2014"}
          </span>
          <p className="mt-0.5 text-[10px] text-text-secondary">
            {health.runtime_model_source === "env_fallback"
              ? "环境变量回退"
              : "数据库默认"}
          </p>
        </div>

        {/* VLM Model */}
        <div
          className={`rounded-[20px] p-3 shadow-[0_8px_24px_rgba(15,23,42,0.04)] ${
            vlmConfig?.available
              ? vlmConfig.source === "db_default"
                ? "bg-purple-500/5"
                : "bg-purple-500/5"
              : "bg-white/50"
          }`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            <Eye
              size={13}
              className={
                vlmConfig?.available ? "text-purple-400" : "text-text-secondary/40"
              }
            />
            <span className="text-[10px] font-medium text-text-secondary">
              VLM 模型
            </span>
          </div>
          {vlmConfig?.available && vlmConfig.active_config ? (
            <>
              <span className="block truncate text-sm font-semibold text-text-primary">
                {vlmConfig.active_config.name}
              </span>
              <p className="mt-0.5 text-[10px] text-text-secondary">
                {vlmConfig.source === "env_fallback" ? "环境变量回退" : "数据库默认"}
              </p>
            </>
          ) : (
            <>
              <span className="text-sm font-semibold text-text-secondary/40">
                未配置
              </span>
              <p className="mt-0.5 text-[10px] text-text-secondary/40">
                暂无视觉模型
              </p>
            </>
          )}
        </div>
      </div>

      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[10px] text-text-secondary">
        <span>
          版本：{" "}
          <strong className="text-text-primary">{health.version}</strong>
        </span>
        <span>
          实例：{" "}
          <strong className="text-text-primary">{health.instance_id}</strong>
        </span>
        <span>
          PID: <strong className="text-text-primary">{health.pid}</strong>
        </span>
        <span>
          启动时间：{" "}
          <strong className="text-text-primary">
            {new Date(health.started_at).toLocaleString()}
          </strong>
        </span>
        {health.runtime_model && (
          <>
            <span>
              Model ID：{" "}
              <strong className="text-text-primary">
                {health.runtime_model.model_id}
              </strong>
            </span>
            <span>
              Provider：{" "}
              <strong className="text-text-primary">
                {health.runtime_model.provider_type}
              </strong>
            </span>
          </>
        )}
      </div>
    </div>
  );
}
