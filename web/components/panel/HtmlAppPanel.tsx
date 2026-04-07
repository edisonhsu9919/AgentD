"use client";

import { useState, useEffect, useRef } from "react";
import {
  Globe,
  CheckCircle,
  XCircle,
  Loader2,
  FileText,
  Tag,
  Lock,
  Unlock,
  X,
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
      <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-accent/10">
        <Globe size={24} className="text-accent/50" />
      </div>
      <div>
        <p className="text-sm font-medium text-text-secondary">Knowledge Import</p>
        <p className="mt-1 max-w-[260px] text-xs text-text-secondary/60">
          Select a file in File Preview and click &ldquo;Import to Knowledge Base&rdquo; to get started.
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
      <p className="text-xs text-text-secondary">Generating metadata draft...</p>
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
  const updateDraft = usePanelStore((s) => s.knowledgeImportDraft);
  const setDraft = (fn: (d: import("@/lib/types").KnowledgeImportDraft) => import("@/lib/types").KnowledgeImportDraft) => {
    usePanelStore.setState((s) => ({
      knowledgeImportDraft: s.knowledgeImportDraft ? fn(s.knowledgeImportDraft) : null,
    }));
  };

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
          }
        : null,
    }));
    await onConfirm(sessionId);
  };

  const inputClass =
    "w-full rounded border border-border bg-bg-primary px-2.5 py-1.5 text-xs text-text-primary outline-none placeholder:text-text-secondary focus:border-accent";

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <FileText size={14} className="text-accent" />
        <span className="text-xs font-medium text-text-primary">Confirm Import Metadata</span>
        <span className="text-[10px] text-text-secondary">{draft.filename}</span>
      </div>

      {/* Form */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Title */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">Title *</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={100}
            placeholder="Document title"
            className={inputClass}
          />
        </div>

        {/* Description */}
        <div>
          <label className="mb-1 flex items-center justify-between text-[10px] font-medium text-text-secondary">
            <span>Description</span>
            <span className={description.length > maxDesc ? "text-danger" : ""}>
              {description.length} / {maxDesc}
            </span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value.slice(0, maxDesc))}
            rows={3}
            placeholder="Brief description of this document"
            className={`${inputClass} resize-none`}
          />
        </div>

        {/* Tags */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">
            <Tag size={10} className="mr-1 inline" />
            Tags (comma-separated)
          </label>
          <input
            type="text"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
            placeholder="e.g. finance, report, 2026"
            className={inputClass}
          />
        </div>

        {/* Permission */}
        <div>
          <label className="mb-1 block text-[10px] font-medium text-text-secondary">Permission</label>
          <div className="flex gap-2">
            <button
              onClick={() => setPermission("private")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded border px-3 py-1.5 text-xs transition ${
                permission === "private"
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-text-secondary hover:border-border/80"
              }`}
            >
              <Lock size={12} /> Private
            </button>
            <button
              onClick={() => setPermission("public")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded border px-3 py-1.5 text-xs transition ${
                permission === "public"
                  ? "border-accent bg-accent/10 text-accent"
                  : "border-border text-text-secondary hover:border-border/80"
              }`}
            >
              <Unlock size={12} /> Public
            </button>
          </div>
        </div>

        {/* File info */}
        <div className="rounded border border-border/50 bg-bg-primary/30 p-2 text-[10px] text-text-secondary">
          <div>Kind: {draft.kind}</div>
          <div>Size: {formatSize(draft.file_size)}</div>
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 border-t border-border px-4 py-3">
        <button
          onClick={() => usePanelStore.getState().resetImport()}
          className="rounded px-3 py-1.5 text-xs text-text-secondary transition hover:bg-bg-tertiary"
        >
          Cancel
        </button>
        <button
          onClick={handleConfirm}
          disabled={!title.trim()}
          className="flex flex-1 items-center justify-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
        >
          Confirm Import
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
      <p className="text-xs text-text-secondary">Starting import...</p>
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
          {progress?.phase || "Processing..."}
        </p>
        {progress?.filename && (
          <p className="mt-1 text-xs text-text-secondary">{progress.filename}</p>
        )}
        {progress?.content_chars != null && progress.content_chars > 0 && (
          <p className="mt-0.5 text-[10px] text-text-secondary">
            {progress.content_chars.toLocaleString()} characters extracted
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
        <p className="text-sm font-medium text-success">Import Complete</p>
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
            className="flex items-center gap-1.5 rounded bg-accent px-4 py-2 text-xs font-medium text-white transition hover:bg-accent/90"
          >
            <FileText size={13} />
            View Knowledge Document
          </button>
        )}
        {/* Secondary: view raw source file */}
        {progress?.raw_path && (
          <button
            onClick={() => openFilePreview(sessionId, progress.raw_path)}
            className="flex items-center gap-1 text-[10px] text-text-secondary transition hover:text-accent"
          >
            <ExternalLink size={10} />
            View original file
          </button>
        )}
        <button
          onClick={onClose}
          className="mt-1 rounded px-3 py-1 text-[10px] text-text-secondary/50 transition hover:bg-bg-tertiary"
        >
          Close
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
        <p className="text-sm font-medium text-danger">Import Failed</p>
        {error && <p className="mt-1 max-w-[300px] text-xs text-text-secondary">{error}</p>}
      </div>
      <div className="flex gap-2">
        <button
          onClick={onRetry}
          className="flex items-center gap-1 rounded bg-danger/10 px-3 py-1.5 text-xs text-danger transition hover:bg-danger/20"
        >
          <RefreshCw size={12} />
          Try Again
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
