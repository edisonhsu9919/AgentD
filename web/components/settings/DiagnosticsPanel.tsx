"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, Stethoscope } from "lucide-react";
import type { DiagnosticsData } from "@/lib/types";

interface DiagnosticsPanelProps {
  diagnostics: DiagnosticsData | null;
  loading: boolean;
  onFetch: () => void;
}

function DiagSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <h4 className="mb-1 text-[10px] font-medium uppercase tracking-wider text-text-secondary">
        {title}
      </h4>
      <div className="space-y-0.5 rounded-[16px] bg-white/70 p-2 shadow-[0_8px_20px_rgba(15,23,42,0.035)]">
        {children}
      </div>
    </div>
  );
}

function DiagRow({
  label,
  value,
  ok,
}: {
  label: string;
  value: string;
  ok?: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-[11px]">
      <span className="text-text-secondary">{label}</span>
      <span
        className={`font-mono ${
          ok === true
            ? "text-success"
            : ok === false
              ? "text-danger"
              : "text-text-primary"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

export default function DiagnosticsPanel({
  diagnostics,
  loading,
  onFetch,
}: DiagnosticsPanelProps) {
  const [expanded, setExpanded] = useState(false);

  const handleToggle = () => {
    if (!expanded && !diagnostics) {
      onFetch();
    }
    setExpanded(!expanded);
  };

  return (
    <div className="space-y-3 rounded-[24px] bg-bg-primary/42 p-3 shadow-[0_18px_44px_rgba(15,23,42,0.06)]">
      <button
        onClick={handleToggle}
        className="flex w-full items-center gap-2 rounded-[18px] px-2 py-2 text-left transition hover:bg-white/70"
      >
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Stethoscope size={13} className="text-text-secondary" />
        <span className="text-xs font-medium text-text-secondary">
          诊断信息
        </span>
      </button>

      {expanded &&
        (loading ? (
          <div className="flex items-center justify-center py-4">
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          </div>
        ) : diagnostics ? (
          <div className="space-y-3">
            <DiagSection title="Instance">
              <DiagRow
                label="Instance ID"
                value={diagnostics.instance.instance_id}
              />
              <DiagRow
                label="PID"
                value={diagnostics.instance.pid.toString()}
              />
              <DiagRow
                label="Started"
                value={new Date(
                  diagnostics.instance.started_at,
                ).toLocaleString()}
              />
              <DiagRow label="Version" value={diagnostics.instance.version} />
            </DiagSection>

            <DiagSection title="Schema">
              <DiagRow
                label="Version"
                value={diagnostics.schema.version || "\u2014"}
              />
              <DiagRow label="Expected" value={diagnostics.schema.expected} />
              <DiagRow
                label="OK"
                value={diagnostics.schema.ok ? "Yes" : "No"}
                ok={diagnostics.schema.ok}
              />
              <DiagRow
                label="DB Reachable"
                value={diagnostics.schema.db_reachable ? "Yes" : "No"}
                ok={diagnostics.schema.db_reachable}
              />
            </DiagSection>

            <DiagSection title="LLM Model">
              <DiagRow label="Source" value={diagnostics.model.source} />
              <DiagRow label="Name" value={diagnostics.model.name} />
              <DiagRow label="Model ID" value={diagnostics.model.model_id} />
              <DiagRow label="Base URL" value={diagnostics.model.base_url} />
              <DiagRow
                label="API Key"
                value={diagnostics.model.api_key_masked}
              />
              <DiagRow
                label="Context Window"
                value={diagnostics.model.context_window?.toLocaleString() ?? "\u2014"}
              />
            </DiagSection>

            <DiagSection title="VLM Model">
              <DiagRow
                label="Available"
                value={diagnostics.vlm.available ? "Yes" : "No"}
                ok={diagnostics.vlm.available}
              />
              {diagnostics.vlm.available && (
                <>
                  <DiagRow label="Source" value={diagnostics.vlm.source || "\u2014"} />
                  <DiagRow label="Name" value={diagnostics.vlm.name || "\u2014"} />
                  <DiagRow label="Model ID" value={diagnostics.vlm.model_id || "\u2014"} />
                  <DiagRow label="Base URL" value={diagnostics.vlm.base_url || "\u2014"} />
                  <DiagRow
                    label="API Key"
                    value={diagnostics.vlm.api_key_masked || "\u2014"}
                  />
                  <DiagRow
                    label="Vision"
                    value={diagnostics.vlm.supports_vision ? "Yes" : "No"}
                    ok={diagnostics.vlm.supports_vision}
                  />
                  <DiagRow
                    label="HTTP Image URL"
                    value={diagnostics.vlm.supports_http_image_url ? "Yes" : "No"}
                    ok={diagnostics.vlm.supports_http_image_url}
                  />
                  <DiagRow
                    label="Data URI Image"
                    value={diagnostics.vlm.supports_data_uri_image ? "Yes" : "No"}
                    ok={diagnostics.vlm.supports_data_uri_image}
                  />
                </>
              )}
            </DiagSection>

            <DiagSection title="Config Summary">
              <DiagRow
                label="Total"
                value={diagnostics.config_summary.total_configs.toString()}
              />
              <DiagRow
                label="LLM Configs"
                value={diagnostics.config_summary.llm_configs.toString()}
              />
              <DiagRow
                label="VLM Configs"
                value={diagnostics.config_summary.vlm_configs.toString()}
              />
              <DiagRow
                label="Enabled"
                value={diagnostics.config_summary.enabled_configs.toString()}
              />
              <DiagRow
                label="Default LLM"
                value={diagnostics.config_summary.default_llm || "\u2014"}
              />
              <DiagRow
                label="Default VLM"
                value={diagnostics.config_summary.default_vlm || "\u2014"}
              />
            </DiagSection>

            <DiagSection title="Environment Fallback">
              <DiagRow
                label="LLM URL"
                value={diagnostics.env_fallback.local_llm_url}
              />
              <DiagRow
                label="Default LLM ID"
                value={diagnostics.env_fallback.default_model_id}
              />
              <DiagRow
                label="VLM URL"
                value={diagnostics.env_fallback.local_vlm_url || "\u2014"}
              />
              <DiagRow
                label="Default VLM ID"
                value={diagnostics.env_fallback.default_vlm_id || "\u2014"}
              />
              <DiagRow
                label="Workspace Root"
                value={diagnostics.env_fallback.workspace_root}
              />
              <DiagRow
                label="DB Pool"
                value={`${diagnostics.env_fallback.db_pool_size} (+${diagnostics.env_fallback.db_max_overflow})`}
              />
              <DiagRow
                label="Debug"
                value={diagnostics.env_fallback.debug ? "Yes" : "No"}
              />
            </DiagSection>
          </div>
        ) : (
          <p className="text-xs text-text-secondary">
            暂时无法获取诊断信息
          </p>
        ))}
    </div>
  );
}
