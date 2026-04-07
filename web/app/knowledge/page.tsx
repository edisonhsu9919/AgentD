"use client";

import { useEffect, useState, useCallback } from "react";
import { useKnowledgeStore } from "@/store/knowledge";
import { usePanelStore } from "@/store/panel";
import { useAuthStore } from "@/store/auth";
import { showToast } from "@/components/ui/Toast";
import PanelShell from "@/components/panel/PanelShell";
import type { KnowledgeDocItem } from "@/lib/types";
import {
  Search,
  FileText,
  Trash2,
  BookOpen,
  Tag,
  Lock,
  Unlock,
  ExternalLink,
  Loader2,
  Download,
} from "lucide-react";
import { apiFetchRaw } from "@/lib/api";

export default function KnowledgePage() {
  const {
    docs,
    isLoading,
    error,
    searchQuery,
    selectedDocId,
    fetchDocs,
    setSearchQuery,
    selectDoc,
    deleteDoc,
  } = useKnowledgeStore();

  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);
  const user = useAuthStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  // Fetch docs on mount
  useEffect(() => {
    fetchDocs();
  }, [fetchDocs]);

  // Debounced search
  const [searchInput, setSearchInput] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearchQuery(searchInput);
      fetchDocs(searchInput);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, setSearchQuery, fetchDocs]);

  const handleDelete = useCallback(
    async (doc: KnowledgeDocItem) => {
      const ok = window.confirm(
        `Delete "${doc.title}"?\n\nThis will permanently remove both the knowledge document and its original source file.`,
      );
      if (!ok) return;
      try {
        await deleteDoc(doc.doc_id);
        showToast("info", `Deleted "${doc.title}"`);
      } catch {
        showToast("error", "Failed to delete document");
      }
    },
    [deleteDoc],
  );

  const handleSelect = useCallback(
    (doc: KnowledgeDocItem) => {
      selectDoc(doc.doc_id);
      openKnowledgeSource(doc.doc_id);
    },
    [selectDoc, openKnowledgeSource],
  );

  return (
    <div className="flex h-full">
      {/* Main list area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Search bar */}
        <div className="border-b border-border px-4 py-3">
          <div className="mx-auto flex max-w-2xl items-center gap-2 rounded-lg border border-border bg-bg-primary px-3 py-2">
            <Search size={14} className="text-text-secondary" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by title or tags..."
              className="flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-secondary/50"
            />
            {searchInput && (
              <button
                onClick={() => setSearchInput("")}
                className="text-text-secondary hover:text-text-primary"
              >
                &times;
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          <div className="mx-auto max-w-2xl">
            {isLoading && docs.length === 0 ? (
              <div className="flex items-center justify-center py-12">
                <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
              </div>
            ) : error ? (
              <div className="py-8 text-center text-xs text-danger">{error}</div>
            ) : docs.length === 0 ? (
              <div className="flex flex-col items-center gap-3 py-12 text-center">
                <BookOpen size={28} className="text-text-secondary/30" />
                <p className="text-sm text-text-secondary">
                  {searchInput ? "No matching documents" : "No knowledge documents yet"}
                </p>
                <p className="text-xs text-text-secondary/60">
                  Import files from chat to build your knowledge base.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="mb-2 text-[10px] text-text-secondary">
                  {docs.length} document{docs.length !== 1 ? "s" : ""}
                </div>
                {docs.map((doc) => (
                  <KnowledgeCard
                    key={doc.doc_id}
                    doc={doc}
                    isSelected={doc.doc_id === selectedDocId}
                    canDelete={isAdmin || doc.owner === user?.id}
                    onSelect={() => handleSelect(doc)}
                    onDelete={() => handleDelete(doc)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Right panel for knowledge preview */}
      <PanelShell sessionId="" />
    </div>
  );
}

function KnowledgeCard({
  doc,
  isSelected,
  canDelete,
  onSelect,
  onDelete,
}: {
  doc: KnowledgeDocItem;
  isSelected: boolean;
  canDelete: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`group rounded-lg border p-3 transition ${
        isSelected
          ? "border-accent/30 bg-accent/5"
          : "border-border bg-bg-secondary hover:border-border/80"
      }`}
    >
      <div className="flex items-start gap-3">
        {/* Icon */}
        <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded bg-accent/10">
          <FileText size={16} className="text-accent" />
        </div>

        {/* Content */}
        <div className="min-w-0 flex-1">
          <button
            onClick={onSelect}
            className="text-left"
          >
            <div className="text-sm font-medium text-text-primary hover:text-accent transition">
              {doc.title}
            </div>
          </button>

          {doc.description && (
            <p className="mt-0.5 text-xs text-text-secondary line-clamp-2">
              {doc.description}
            </p>
          )}

          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {/* Kind badge */}
            <span className="rounded bg-bg-tertiary px-1.5 py-0.5 text-[10px] text-text-secondary">
              {doc.kind}
            </span>

            {/* Permission */}
            <span className={`flex items-center gap-0.5 text-[10px] ${
              doc.permission === "public" ? "text-green-400" : "text-text-secondary/50"
            }`}>
              {doc.permission === "public" ? <Unlock size={9} /> : <Lock size={9} />}
              {doc.permission}
            </span>

            {/* Tags */}
            {doc.tags && doc.tags.length > 0 && (
              <div className="flex items-center gap-1">
                <Tag size={9} className="text-text-secondary/40" />
                {doc.tags.slice(0, 3).map((t, i) => (
                  <span key={i} className="rounded bg-accent/10 px-1 py-0.5 text-[9px] text-accent">
                    {t}
                  </span>
                ))}
                {doc.tags.length > 3 && (
                  <span className="text-[9px] text-text-secondary">+{doc.tags.length - 3}</span>
                )}
              </div>
            )}

            {/* Date */}
            {doc.created_at && (
              <span className="text-[10px] text-text-secondary/40">
                {new Date(doc.created_at).toLocaleDateString()}
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={onSelect}
            className="rounded p-1 text-text-secondary transition hover:bg-accent/10 hover:text-accent"
            title="Preview"
          >
            <ExternalLink size={13} />
          </button>
          {canDelete && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
              className="rounded p-1 text-text-secondary/40 opacity-0 transition hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
              title="Delete"
            >
              <Trash2 size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
