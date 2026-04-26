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
    vlmConfig,
    diagnostics,
    diagnosticsLoading,
    editingConfig,
    isCreating,
    editorLoading,
    editorError,
    fetchHealth,
    fetchConfigs,
    fetchRuntimeConfig,
    fetchVLMConfig,
    fetchDiagnostics,
    openCreateEditor,
    openEditEditor,
    closeEditor,
    createConfig,
    updateConfig,
    deleteConfig,
    enableConfig,
    disableConfig,
    setDefaultConfig,
    unsetDefaultConfig,
  } = useSettingsStore();

  useEffect(() => {
    fetchHealth();
    fetchConfigs();
    fetchRuntimeConfig();
    fetchVLMConfig();
  }, [fetchHealth, fetchConfigs, fetchRuntimeConfig, fetchVLMConfig]);

  const showEditor = isCreating || editingConfig !== null;

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-[1440px] overflow-hidden px-6 py-6">
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <div className="mb-4 space-y-1.5">
          <div className="page-eyebrow">后台 / 设置</div>
          <h1 className="text-[22px] font-semibold tracking-[-0.03em] text-text-primary">
            运行时与模型配置
          </h1>
          <p className="max-w-2xl text-xs leading-6 text-text-secondary">
            查看服务状态，管理默认模型，并在需要时展开诊断信息。
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto pr-2">
          <div className="space-y-5 pb-2">
            <RuntimeStatus health={health} loading={healthLoading} vlmConfig={vlmConfig} />

            <ModelConfigList
              configs={configs}
              loading={configsLoading}
              onNew={openCreateEditor}
              onEdit={openEditEditor}
              onDelete={deleteConfig}
              onEnable={enableConfig}
              onDisable={disableConfig}
              onSetDefault={setDefaultConfig}
              onUnsetDefault={unsetDefaultConfig}
            />

            <DiagnosticsPanel
              diagnostics={diagnostics}
              loading={diagnosticsLoading}
              onFetch={fetchDiagnostics}
            />
          </div>
        </div>
      </div>

      {showEditor && (
        <div className="fixed inset-y-3 right-3 z-50 w-[min(92vw,420px)]">
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
