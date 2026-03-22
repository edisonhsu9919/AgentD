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
      <div className="space-y-0.5 rounded border border-border bg-bg-primary p-2">
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
    <div className="rounded-lg border border-border bg-bg-secondary p-4 space-y-3">
      <button
        onClick={handleToggle}
        className="flex w-full items-center gap-2 text-left"
      >
        {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <Stethoscope size={13} className="text-text-secondary" />
        <span className="text-xs font-medium text-text-secondary">
          Diagnostics
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

            <DiagSection title="Model">
              <DiagRow label="Source" value={diagnostics.model.source} />
              <DiagRow label="Name" value={diagnostics.model.name} />
              <DiagRow label="Model ID" value={diagnostics.model.model_id} />
              <DiagRow label="Base URL" value={diagnostics.model.base_url} />
              <DiagRow
                label="API Key"
                value={diagnostics.model.api_key_masked}
              />
            </DiagSection>

            <DiagSection title="Config Summary">
              <DiagRow
                label="Total"
                value={diagnostics.config_summary.total_configs.toString()}
              />
              <DiagRow
                label="Enabled"
                value={diagnostics.config_summary.enabled_configs.toString()}
              />
              <DiagRow
                label="Default"
                value={diagnostics.config_summary.default_config || "\u2014"}
              />
            </DiagSection>

            <DiagSection title="Environment Fallback">
              <DiagRow
                label="LLM URL"
                value={diagnostics.env_fallback.local_llm_url}
              />
              <DiagRow
                label="Default Model"
                value={diagnostics.env_fallback.default_model_id}
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
            Unable to fetch diagnostics
          </p>
        ))}
    </div>
  );
}
