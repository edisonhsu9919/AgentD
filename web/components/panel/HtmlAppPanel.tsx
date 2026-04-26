"use client";

import { useState, useEffect } from "react";
import {
  Globe,
  CheckCircle,
  XCircle,
  Loader2,
  FileText,
  Tag,
  Lock,
  Unlock,
  RefreshCw,
  ExternalLink,
} from "lucide-react";
import { usePanelStore } from "@/store/panel";

interface HtmlAppPanelProps {
  sessionId: string;
}

export default function HtmlAppPanel({ sessionId }: HtmlAppPanelProps) {
  const status = usePanelStore((s) => s.knowledgeImportStatus);
  const draft = usePanelStore((s) => s.knowledgeImportDraft);
  const progress = usePanelStore((s) => s.knowledgeImportProgress);
  const taskId = usePanelStore((s) => s.knowledgeImportTaskId);
  const error = usePanelStore((s) => s.knowledgeImportError);
  const confirmImport = usePanelStore((s) => s.confirmImport);
  const pollImportProgress = usePanelStore((s) => s.pollImportProgress);
  const resetImport = usePanelStore((s) => s.resetImport);
  const restoreImport = usePanelStore((s) => s.restoreImport);

  // Restore active import on mount
  useEffect(() => {
    if (status === "idle") {
      restoreImport(sessionId);
    }
  }, [sessionId, status, restoreImport]);

  // Poll progress when processing
  useEffect(() => {
    if (status !== "processing" || !taskId) return;
    const interval = setInterval(() => {
      pollImportProgress(taskId);
    }, 2000);
    return () => clearInterval(interval);
  }, [status, taskId, pollImportProgress]);

  switch (status) {
    case "idle":
      return <EmptyState />;
    case "drafting":
      return <DraftingState />;
    case "form":
      return draft ? <FormState sessionId={sessionId} draft={draft} onConfirm={confirmImport} /> : <EmptyState />;
    case "submitting":
      return <SubmittingState />;
    case "processing":
      return <ProcessingState progress={progress} />;
    case "completed":
      return <CompletedState progress={progress} onClose={resetImport} sessionId={sessionId} />;
    case "failed":
      return <FailedState error={error} onRetry={resetImport} />;
    default:
      return <EmptyState />;
  }
}

// --- Empty state ---

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-[16px] bg-accent/10">
        <Globe size={24} className="text-accent/55" />
      </div>
      <div>
        <p className="text-sm font-medium text-text-primary">知识库导入</p>
        <p className="mt-1 max-w-[260px] text-xs text-text-secondary/60">
          在文件预览中选择可导入文件，然后点击“导入知识库”开始生成元数据草稿。
        </p>
      </div>
    </div>
  );
}

// --- Drafting state (loading draft from backend) ---

function DraftingState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
      <Loader2 size={24} className="animate-spin text-accent" />
      <p className="text-xs text-text-secondary">正在生成元数据草稿...</p>
    </div>
  );
}

// --- Form state ---

function FormState({
  sessionId,
  draft,
  onConfirm,
}: {
  sessionId: string;
  draft: import("@/lib/types").KnowledgeImportDraft;
  onConfirm: (sessionId: string) => Promise<void>;
}) {
  const [title, setTitle] = useState(draft.title);
  const [description, setDescription] = useState(draft.description);
  const [tags, setTags] = useState(Array.isArray(draft.tags) ? draft.tags.join(", ") : "");
  const [permission, setPermission] = useState<"public" | "private">("private");
  const maxDesc = draft.limits?.description_max_chars || 200;

  const handleConfirm = async () => {
    // Update draft in store before confirming
    usePanelStore.setState((s) => ({
      knowledgeImportDraft: s.knowledgeImportDraft
        ? {
            ...s.knowledgeImportDraft,
            title,
            description,
            tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
            permission,
          }
        : null,
    }));
    await onConfirm(sessionId);
  };

  const inputClass =
    "w-full rounded-[14px] bg-bg-primary/65 px-3 py-2 text-xs text-text-primary outline-none placeholder:text-text-secondary/45 transition focus:bg-white/72 focus:shadow-[0_0_0_2px_rgba(139,92,246,0.16)]";

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3">
        <FileText size={14} className="text-accent" />
        <span className="text-xs font-medium text-text-primary">确认导入信息</span>
        <span className="truncate text-[10px] text-text-secondary" title={draft.filename}>{draft.filename}</span>
      </div>

      {/* Form */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Title */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">标题 *</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={100}
            placeholder="文档标题"
            className={inputClass}
          />
        </div>

        {/* Description */}
        <div>
          <label className="mb-1 flex items-center justify-between text-[10px] font-medium text-text-secondary">
            <span>说明</span>
            <span className={description.length > maxDesc ? "text-danger" : ""}>
              {description.length} / {maxDesc}
            </span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value.slice(0, maxDesc))}
            rows={3}
            placeholder="简单描述这份文档的用途和内容"
            className={`${inputClass} resize-none`}
          />
        </div>

        {/* Tags */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            <Tag size={10} className="mr-1 inline" />
            标签（逗号分隔）
          </label>
          <input
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="例如：财务, 报告, 2026"
            className={inputClass}
          />
        </div>

        {/* Permission */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">权限</label>
          <div className="inline-flex rounded-[14px] bg-bg-primary/65 p-1">
            <button
              onClick={() => setPermission("private")}
              className={`flex items-center justify-center gap-1.5 rounded-[11px] px-3 py-1.5 text-xs transition ${
                permission === "private"
                  ? "bg-white/78 text-accent shadow-[0_8px_18px_rgba(23,23,37,0.06)]"
                  : "text-text-secondary hover:bg-white/45"
              }`}
            >
              <Lock size={12} /> 私有
            </button>
            <button
              onClick={() => setPermission("public")}
              className={`flex items-center justify-center gap-1.5 rounded-[11px] px-3 py-1.5 text-xs transition ${
                permission === "public"
                  ? "bg-white/78 text-accent shadow-[0_8px_18px_rgba(23,23,37,0.06)]"
                  : "text-text-secondary hover:bg-white/45"
              }`}
            >
              <Unlock size={12} /> 公开
            </button>
          </div>
        </div>

        {/* File info */}
        <div className="rounded-[14px] bg-bg-primary/55 p-3 text-[10px] text-text-secondary">
          <div>类型：{draft.kind}</div>
          <div>大小：{formatSize(draft.file_size)}</div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 py-3">
        <button
          onClick={() => usePanelStore.getState().resetImport()}
          className="rounded-full px-3 py-1.5 text-xs text-text-secondary transition hover:bg-bg-tertiary/70"
        >
          取消
        </button>
        <button
          onClick={handleConfirm}
          disabled={!title.trim()}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-full bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
        >
          确认导入
        </button>
      </div>
    </div>
  );
}

// --- Submitting state ---

function SubmittingState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6">
      <Loader2 size={24} className="animate-spin text-accent" />
      <p className="text-xs text-text-secondary">正在启动导入...</p>
    </div>
  );
}

// --- Processing state ---

function ProcessingState({ progress }: { progress: import("@/lib/types").KnowledgeImportProgress | null }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <Loader2 size={28} className="animate-spin text-accent" />
      <div>
        <p className="text-sm font-medium text-text-primary">
          {progress?.phase || "正在处理..."}
        </p>
        {progress?.filename && (
          <p className="mt-1 text-xs text-text-secondary">{progress.filename}</p>
        )}
        {progress?.content_chars != null && progress.content_chars > 0 && (
          <p className="mt-0.5 text-[10px] text-text-secondary">
            已提取 {progress.content_chars.toLocaleString()} 个字符
          </p>
        )}
      </div>
    </div>
  );
}

// --- Completed state ---

function CompletedState({
  progress,
  onClose,
  sessionId,
}: {
  progress: import("@/lib/types").KnowledgeImportProgress | null;
  onClose: () => void;
  sessionId: string;
}) {
  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);
  const openFilePreview = usePanelStore((s) => s.openFilePreview);

  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <CheckCircle size={32} className="text-success" />
      <div>
        <p className="text-sm font-medium text-success">导入完成</p>
        {progress?.title && (
          <p className="mt-1 text-xs text-text-primary">{progress.title}</p>
        )}
        {progress?.doc_id && (
          <p className="mt-0.5 text-[10px] text-text-secondary">doc_id: {progress.doc_id}</p>
        )}
      </div>
      <div className="flex flex-col items-center gap-2">
        {/* Primary: open knowledge document (Markdown) */}
        {progress?.doc_id && (
          <button
            onClick={() => openKnowledgeSource(progress.doc_id!)}
            className="flex items-center gap-1.5 rounded-full bg-accent px-4 py-2 text-xs font-medium text-white transition hover:bg-accent/90"
          >
            <FileText size={13} />
            查看知识文档
          </button>
        )}
        {/* Secondary: view raw source file */}
        {progress?.raw_path && (
          <button
            onClick={() => openFilePreview(sessionId, progress.raw_path)}
            className="flex items-center gap-1 rounded-full px-2 py-1 text-[10px] text-text-secondary transition hover:bg-bg-tertiary/70 hover:text-accent"
          >
            <ExternalLink size={10} />
            查看原始文件
          </button>
        )}
        <button
          onClick={onClose}
          className="mt-1 rounded-full px-3 py-1 text-[10px] text-text-secondary/50 transition hover:bg-bg-tertiary/70"
        >
          关闭
        </button>
      </div>
    </div>
  );
}

// --- Failed state ---

function FailedState({ error, onRetry }: { error: string | null; onRetry: () => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <XCircle size={32} className="text-danger" />
      <div>
        <p className="text-sm font-medium text-danger">导入失败</p>
        {error && <p className="mt-1 max-w-[300px] text-xs text-text-secondary">{error}</p>}
      </div>
      <div className="flex gap-2">
        <button
          onClick={onRetry}
          className="flex items-center gap-1 rounded-full bg-danger/10 px-3 py-1.5 text-xs text-danger transition hover:bg-danger/20"
        >
          <RefreshCw size={12} />
          重试
        </button>
      </div>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
