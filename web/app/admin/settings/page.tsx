"use client";

import { useEffect } from "react";
import { useSettingsStore } from "@/store/settings";
import RuntimeStatus from "@/components/settings/RuntimeStatus";
import ModelConfigList from "@/components/settings/ModelConfigList";
import ModelConfigEditor from "@/components/settings/ModelConfigEditor";
import DiagnosticsPanel from "@/components/settings/DiagnosticsPanel";

export default function SettingsPage() {
  const {
    health,
    healthLoading,
    configs,
    configsLoading,
    diagnostics,
    diagnosticsLoading,
    editingConfig,
    isCreating,
    editorLoading,
    editorError,
    fetchHealth,
    fetchConfigs,
    fetchRuntimeConfig,
    fetchDiagnostics,
    openCreateEditor,
    openEditEditor,
    closeEditor,
    createConfig,
    updateConfig,
    enableConfig,
    disableConfig,
    setDefaultConfig,
  } = useSettingsStore();

  useEffect(() => {
    fetchHealth();
    fetchConfigs();
    fetchRuntimeConfig();
  }, [fetchHealth, fetchConfigs, fetchRuntimeConfig]);

  const showEditor = isCreating || editingConfig !== null;

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        <div className="mx-auto max-w-4xl space-y-4">
          <RuntimeStatus health={health} loading={healthLoading} />

          <ModelConfigList
            configs={configs}
            loading={configsLoading}
            onNew={openCreateEditor}
            onEdit={openEditEditor}
            onEnable={enableConfig}
            onDisable={disableConfig}
            onSetDefault={setDefaultConfig}
          />

          <DiagnosticsPanel
            diagnostics={diagnostics}
            loading={diagnosticsLoading}
            onFetch={fetchDiagnostics}
          />
        </div>
      </div>

      {/* Editor drawer */}
      {showEditor && (
        <div className="w-96 shrink-0 overflow-y-auto">
          <ModelConfigEditor
            config={editingConfig}
            isCreating={isCreating}
            loading={editorLoading}
            error={editorError}
            onClose={closeEditor}
            onCreate={createConfig}
            onUpdate={updateConfig}
          />
        </div>
      )}
    </div>
  );
}
