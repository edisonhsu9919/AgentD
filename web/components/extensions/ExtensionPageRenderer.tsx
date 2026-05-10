"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Boxes, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useAuthStore } from "@/store/auth";
import { useExtensionStore } from "@/store/extensions";
import ExtensionInfoPanel from "./ExtensionInfoPanel";
import ExtensionSearchTable from "./ExtensionSearchTable";
import type {
  DomainExtensionItem,
  ExtensionPageSchema,
} from "@/lib/types";
import ClauseLibraryPage from "./ClauseLibraryPage";

export default function ExtensionPageRenderer({ name }: { name: string }) {
  const extensions = useExtensionStore((s) => s.extensions);
  const loading = useExtensionStore((s) => s.loading);
  const loaded = useExtensionStore((s) => s.loaded);
  const error = useExtensionStore((s) => s.error);
  const fetchExtensions = useExtensionStore((s) => s.fetchExtensions);
  const user = useAuthStore((s) => s.user);

  const [schema, setSchema] = useState<ExtensionPageSchema | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  useEffect(() => {
    fetchExtensions();
  }, [fetchExtensions]);

  const extension = useMemo(
    () => extensions.find((item) => item.name === name),
    [extensions, name],
  );

  useEffect(() => {
    if (!extension?.frontend?.page_schema_endpoint) return;
    if (extension.status !== "enabled") return;
    if (extension.visibility === "admin" && user?.role !== "admin") return;

    let cancelled = false;
    const loadSchema = async () => {
      setSchemaLoading(true);
      setSchemaError(null);
      setSchema(null);
      try {
        const data = await apiFetch<ExtensionPageSchema>(
          normalizeSchemaEndpoint(extension.frontend!.page_schema_endpoint),
        );
        if (cancelled) return;
        if (!isExtensionPageSchema(data)) {
          throw new Error("无法识别扩展页面 schema");
        }
        setSchema(data);
      } catch (err) {
        if (cancelled) return;
        setSchemaError(
          err instanceof Error ? err.message : "扩展页面加载失败",
        );
      } finally {
        if (!cancelled) setSchemaLoading(false);
      }
    };

    loadSchema();
    return () => {
      cancelled = true;
    };
  }, [extension, user?.role]);

  if (loading && !loaded) {
    return <ExtensionLoading />;
  }

  if (error && !extension) {
    return (
      <ExtensionError
        title="扩展列表加载失败"
        description={error}
        onRetry={() => fetchExtensions(true)}
      />
    );
  }

  if (!extension) {
    return (
      <ExtensionError
        title="扩展不存在"
        description={`没有找到名为 ${name} 的扩展。`}
      />
    );
  }

  if (extension.visibility === "admin" && user?.role !== "admin") {
    return (
      <ExtensionError
        title="无权访问该扩展"
        description="该扩展仅管理员可见。"
        extension={extension}
      />
    );
  }

  if (extension.status === "error") {
    return (
      <ExtensionError
        title={`${extension.display_name} 暂不可用`}
        description="该扩展当前处于错误状态，请稍后重试或联系管理员检查后端扩展。"
        extension={extension}
      />
    );
  }

  if (extension.status === "disabled") {
    return (
      <ExtensionError
        title={`${extension.display_name} 已停用`}
        description="该扩展当前未启用。"
        extension={extension}
      />
    );
  }

  if (extension.name === "agentd-insurance-clauses") {
    return <ClauseLibraryPage />;
  }

  if (!extension.frontend?.page_schema_endpoint) {
    return (
      <ExtensionError
        title="扩展未提供页面入口"
        description="该扩展没有声明 frontend.page_schema_endpoint。"
        extension={extension}
      />
    );
  }

  if (schemaLoading) return <ExtensionLoading extension={extension} />;

  if (schemaError || !schema) {
    return (
      <ExtensionError
        title="扩展页面加载失败"
        description={schemaError || "没有获得可渲染的页面 schema。"}
        extension={extension}
      />
    );
  }

  if (schema.kind === "info_panel") {
    return <ExtensionInfoPanel schema={schema} />;
  }

  if (schema.kind === "search_table") {
    return <ExtensionSearchTable schema={schema} />;
  }

  return (
    <ExtensionError
      title="暂不支持的扩展页面"
      description="当前前端无法渲染该扩展页面类型。"
      extension={extension}
    />
  );
}

function normalizeSchemaEndpoint(endpoint: string) {
  if (endpoint.startsWith("/api/")) return endpoint.slice(4);
  if (endpoint.startsWith("/")) return endpoint;
  return `/${endpoint}`;
}

function isExtensionPageSchema(value: unknown): value is ExtensionPageSchema {
  if (!value || typeof value !== "object") return false;
  const schema = value as { kind?: unknown };
  if (schema.kind === "info_panel") return true;
  if (schema.kind === "search_table") return true;
  return false;
}

function ExtensionLoading({
  extension,
}: {
  extension?: DomainExtensionItem;
}) {
  return (
    <div className="flex h-full items-center justify-center px-6 py-10">
      <div className="flex items-center gap-3 rounded-full bg-white/72 px-4 py-3 text-sm text-text-secondary shadow-[0_14px_36px_rgba(42,41,51,0.06)]">
        <RefreshCw size={15} className="animate-spin text-accent" />
        正在加载{extension?.display_name || "扩展"}...
      </div>
    </div>
  );
}

function ExtensionError({
  title,
  description,
  extension,
  onRetry,
}: {
  title: string;
  description: string;
  extension?: DomainExtensionItem;
  onRetry?: () => void;
}) {
  return (
    <div className="mx-auto flex h-full w-full max-w-5xl items-center justify-center px-6 py-10">
      <section className="w-full rounded-[28px] bg-white/72 p-6 shadow-[0_18px_48px_rgba(42,41,51,0.055)]">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[18px] bg-danger/10 text-danger">
            <AlertTriangle size={20} />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-text-secondary/60">
              Extension Page
            </p>
            <h1 className="mt-2 text-xl font-semibold tracking-[-0.02em] text-text-primary">
              {title}
            </h1>
            <p className="mt-3 text-sm leading-6 text-text-secondary">
              {description}
            </p>
            {extension && (
              <div className="mt-5 flex flex-wrap gap-2 text-[11px] text-text-secondary">
                <span className="rounded-full bg-bg-primary px-2.5 py-1">
                  {extension.name}
                </span>
                <span className="rounded-full bg-bg-primary px-2.5 py-1">
                  v{extension.version}
                </span>
                <span className="rounded-full bg-bg-primary px-2.5 py-1">
                  {extension.status}
                </span>
              </div>
            )}
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="mt-5 inline-flex h-10 items-center gap-2 rounded-full bg-bg-primary px-4 text-sm font-medium text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
              >
                <Boxes size={14} />
                重新加载
              </button>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
