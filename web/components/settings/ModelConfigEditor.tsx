"use client";

import { useState, useEffect } from "react";
import { X, Loader2, XCircle } from "lucide-react";
import type {
  ModelConfig,
  ModelConfigCreate,
  ModelConfigUpdate,
} from "@/lib/types";

interface ModelConfigEditorProps {
  config: ModelConfig | null;
  isCreating: boolean;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onCreate: (data: ModelConfigCreate) => void;
  onUpdate: (id: string, data: ModelConfigUpdate) => void;
}

export default function ModelConfigEditor({
  config,
  isCreating,
  loading,
  error,
  onClose,
  onCreate,
  onUpdate,
}: ModelConfigEditorProps) {
  const [name, setName] = useState("");
  const [modelType, setModelType] = useState<"llm" | "vlm">("llm");
  const [providerType, setProviderType] = useState("openai_compatible");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [apiKeyChanged, setApiKeyChanged] = useState(false);
  const [modelId, setModelId] = useState("");
  const [isEnabled, setIsEnabled] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [contextWindow, setContextWindow] = useState("");

  useEffect(() => {
    if (config) {
      setName(config.name);
      setModelType(config.model_type || "llm");
      setProviderType(config.provider_type);
      setBaseUrl(config.base_url);
      setApiKey("");
      setApiKeyChanged(false);
      setModelId(config.model_id);
      setIsEnabled(config.is_enabled);
      setTimeoutSeconds(config.timeout_seconds?.toString() || "");
      setContextWindow(config.context_window?.toString() || "");
    } else {
      setName("");
      setModelType("llm");
      setProviderType("openai_compatible");
      setBaseUrl("");
      setApiKey("");
      setApiKeyChanged(false);
      setModelId("");
      setIsEnabled(true);
      setTimeoutSeconds("");
      setContextWindow("");
    }
  }, [config, isCreating]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const timeout = timeoutSeconds ? parseInt(timeoutSeconds, 10) : null;
    const ctxWindow = contextWindow ? parseInt(contextWindow, 10) : null;

    if (isCreating) {
      const data: ModelConfigCreate = {
        name,
        model_type: modelType,
        provider_type: providerType,
        base_url: baseUrl,
        model_id: modelId,
        is_enabled: isEnabled,
        timeout_seconds: timeout,
        context_window: ctxWindow,
      };
      if (apiKey) data.api_key = apiKey;
      onCreate(data);
    } else if (config) {
      const data: ModelConfigUpdate = {};
      if (name !== config.name) data.name = name;
      if (modelType !== (config.model_type || "llm"))
        data.model_type = modelType;
      if (providerType !== config.provider_type)
        data.provider_type = providerType;
      if (baseUrl !== config.base_url) data.base_url = baseUrl;
      if (apiKeyChanged && apiKey) data.api_key = apiKey;
      if (modelId !== config.model_id) data.model_id = modelId;
      if (isEnabled !== config.is_enabled) data.is_enabled = isEnabled;
      const newTimeout = timeoutSeconds
        ? parseInt(timeoutSeconds, 10)
        : null;
      if (newTimeout !== config.timeout_seconds)
        data.timeout_seconds = newTimeout;
      if (ctxWindow !== config.context_window)
        data.context_window = ctxWindow;
      onUpdate(config.id, data);
    }
  };

  const inputClass =
    "w-full rounded border border-border bg-bg-primary px-2.5 py-1.5 text-xs text-text-primary outline-none placeholder:text-text-secondary focus:border-accent";

  return (
    <div className="flex h-full flex-col border-l border-border bg-bg-secondary">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <span className="text-xs font-medium">
          {isCreating ? "New Model Config" : "Edit Model Config"}
        </span>
        <button
          onClick={onClose}
          className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
        >
          <X size={14} />
        </button>
      </div>

      {/* Form */}
      <form
        onSubmit={handleSubmit}
        className="flex-1 overflow-y-auto p-3 space-y-3"
      >
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Name *
          </label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            maxLength={128}
            placeholder="e.g. GPT-4o Production"
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Model Type *
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setModelType("llm")}
              className={`flex-1 rounded border px-2.5 py-1.5 text-xs font-medium transition ${
                modelType === "llm"
                  ? "border-blue-500 bg-blue-500/10 text-blue-400"
                  : "border-border bg-bg-primary text-text-secondary hover:border-border/80"
              }`}
            >
              LLM
            </button>
            <button
              type="button"
              onClick={() => setModelType("vlm")}
              className={`flex-1 rounded border px-2.5 py-1.5 text-xs font-medium transition ${
                modelType === "vlm"
                  ? "border-purple-500 bg-purple-500/10 text-purple-400"
                  : "border-border bg-bg-primary text-text-secondary hover:border-border/80"
              }`}
            >
              VLM
            </button>
          </div>
          <p className="mt-0.5 text-[10px] text-text-secondary/60">
            {modelType === "vlm"
              ? "Vision Language Model \u2014 for image understanding"
              : "Large Language Model \u2014 main session model"}
          </p>
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Provider Type
          </label>
          <input
            type="text"
            value={providerType}
            onChange={(e) => setProviderType(e.target.value)}
            maxLength={32}
            placeholder="openai_compatible"
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Base URL *
          </label>
          <input
            type="text"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            required
            maxLength={512}
            placeholder="https://api.openai.com/v1"
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            API Key
          </label>
          {config && !apiKeyChanged && (
            <p className="mb-1 text-[10px] text-text-secondary">
              Current:{" "}
              <code className="text-text-primary">{config.api_key_masked}</code>
            </p>
          )}
          <input
            type="password"
            value={apiKey}
            onChange={(e) => {
              setApiKey(e.target.value);
              if (!apiKeyChanged) setApiKeyChanged(true);
            }}
            maxLength={512}
            placeholder={config ? "Enter new key to change" : "sk-..."}
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Model ID *
          </label>
          <input
            type="text"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            required
            maxLength={128}
            placeholder="gpt-4o"
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Timeout (seconds)
          </label>
          <input
            type="number"
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(e.target.value)}
            min={1}
            max={600}
            placeholder="Optional (1-600)"
            className={inputClass}
          />
        </div>

        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            Context Window
          </label>
          <input
            type="number"
            value={contextWindow}
            onChange={(e) => setContextWindow(e.target.value)}
            min={1}
            placeholder="Optional max context tokens, e.g. 32768"
            className={inputClass}
          />
        </div>

        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="mc_is_enabled"
            checked={isEnabled}
            onChange={(e) => setIsEnabled(e.target.checked)}
            className="rounded border-border"
          />
          <label htmlFor="mc_is_enabled" className="text-xs text-text-primary">
            Enabled
          </label>
        </div>

        {error && (
          <div className="flex items-center gap-1.5 rounded bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
            <XCircle size={12} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="submit"
          disabled={loading || !name || !baseUrl || !modelId}
          className="flex w-full items-center justify-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
        >
          {loading && <Loader2 size={13} className="animate-spin" />}
          {isCreating ? "Create" : "Save Changes"}
        </button>
      </form>
    </div>
  );
}
