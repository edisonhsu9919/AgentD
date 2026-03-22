"use client";

import { CheckCircle, XCircle, AlertTriangle, Database, Cpu } from "lucide-react";
import type { HealthResponse } from "@/lib/types";

interface RuntimeStatusProps {
  health: HealthResponse | null;
  loading: boolean;
}

export default function RuntimeStatus({ health, loading }: RuntimeStatusProps) {
  if (loading && !health) {
    return (
      <div className="rounded-lg border border-border bg-bg-secondary p-4">
        <h3 className="mb-3 text-xs font-medium text-text-secondary">
          Runtime Status
        </h3>
        <div className="flex items-center justify-center py-6">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      </div>
    );
  }

  if (!health) {
    return (
      <div className="rounded-lg border border-border bg-bg-secondary p-4">
        <h3 className="mb-3 text-xs font-medium text-text-secondary">
          Runtime Status
        </h3>
        <p className="text-xs text-text-secondary">
          Unable to fetch health status
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-bg-secondary p-4 space-y-3">
      <h3 className="text-xs font-medium text-text-secondary">
        Runtime Status
      </h3>

      {/* Key status cards */}
      <div className="grid grid-cols-3 gap-3">
        {/* Ready */}
        <div
          className={`rounded-lg border p-3 ${
            health.ready
              ? "border-success/30 bg-success/5"
              : "border-danger/30 bg-danger/5"
          }`}
        >
          <div className="mb-1 flex items-center gap-1.5">
            {health.ready ? (
              <CheckCircle size={13} className="text-success" />
            ) : (
              <XCircle size={13} className="text-danger" />
            )}
            <span className="text-[10px] font-medium text-text-secondary">
              Ready
            </span>
          </div>
          <span
            className={`text-sm font-semibold ${
              health.ready ? "text-success" : "text-danger"
            }`}
          >
            {health.ready ? "Yes" : "No"}
          </span>
          {!health.ready && health.degraded_reason && (
            <p className="mt-0.5 text-[10px] text-danger">
              {health.degraded_reason}
            </p>
          )}
        </div>

        {/* Schema */}
        <div
          className={`rounded-lg border p-3 ${
            health.schema_ok
              ? "border-success/30 bg-success/5"
              : "border-danger/30 bg-danger/5"
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
              Expected: {health.schema_expected}
            </p>
          )}
        </div>

        {/* Runtime Model */}
        <div
          className={`rounded-lg border p-3 ${
            health.runtime_model_source === "db_default"
              ? "border-success/30 bg-success/5"
              : "border-warning/30 bg-warning/5"
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
              Model
            </span>
          </div>
          <span className="block truncate text-sm font-semibold text-text-primary">
            {health.runtime_model?.name || "\u2014"}
          </span>
          <p className="mt-0.5 text-[10px] text-text-secondary">
            {health.runtime_model_source === "env_fallback"
              ? "env fallback"
              : "db default"}
          </p>
        </div>
      </div>

      {/* Detail row */}
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-[10px] text-text-secondary">
        <span>
          Version:{" "}
          <strong className="text-text-primary">{health.version}</strong>
        </span>
        <span>
          Instance:{" "}
          <strong className="text-text-primary">{health.instance_id}</strong>
        </span>
        <span>
          PID: <strong className="text-text-primary">{health.pid}</strong>
        </span>
        <span>
          Started:{" "}
          <strong className="text-text-primary">
            {new Date(health.started_at).toLocaleString()}
          </strong>
        </span>
        {health.runtime_model && (
          <>
            <span>
              Model ID:{" "}
              <strong className="text-text-primary">
                {health.runtime_model.model_id}
              </strong>
            </span>
            <span>
              Provider:{" "}
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
