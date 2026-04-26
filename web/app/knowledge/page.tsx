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
} from "lucide-react";

export default function KnowledgePage() {
  const {
    docs,
    isLoading,
    error,
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
    <div className="flex h-full min-h-0 overflow-hidden">
      {/* Main list area */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden px-6 py-4">
        {/* Search bar */}
        <div className="mx-auto flex w-full max-w-6xl items-center gap-3 pb-4">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-full bg-bg-primary/65 px-4 py-2.5 shadow-[0_12px_32px_rgba(42,41,51,0.04)]">
            <Search size={15} className="text-text-secondary" />
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search by title or tags..."
              className="min-w-0 flex-1 bg-transparent text-sm text-text-primary outline-none placeholder:text-text-secondary/45"
            />
            {searchInput && (
              <button
                onClick={() => setSearchInput("")}
                className="rounded-full px-2 text-sm text-text-secondary transition hover:bg-white/70 hover:text-text-primary"
              >
                &times;
              </button>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="min-h-0 flex-1 overflow-hidden">
          <div className="mx-auto h-full max-w-6xl overflow-y-auto px-1 pb-2">
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
              <div className="space-y-3">
                <div className="px-1 text-[10px] text-text-secondary">
                  {docs.length} document{docs.length !== 1 ? "s" : ""}
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
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
      role="button"
      aria-pressed={isSelected}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect();
      }}
      className="group flex h-[190px] cursor-pointer flex-col rounded-[24px] bg-white/72 p-4 shadow-[0_12px_28px_rgba(42,41,51,0.045)] transition duration-200 ease-out hover:-translate-y-0.5 hover:bg-white/90 hover:shadow-[0_18px_42px_rgba(42,41,51,0.09)]"
    >
      <div className="flex min-h-0 flex-1 items-start gap-3">
        {/* Icon */}
        <div className="mt-0.5 flex h-10 w-10 shrink-0 items-center justify-center rounded-[16px] bg-accent/10">
          <FileText size={17} className="text-accent" />
        </div>

        {/* Content */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="line-clamp-2 text-sm font-semibold leading-snug text-text-primary">
            {doc.title}
          </div>

          {doc.description && (
            <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-text-secondary">
              {doc.description}
            </p>
          )}

          <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
            {/* Kind badge */}
            <span className="rounded-full bg-bg-tertiary/80 px-2 py-0.5 text-[10px] text-text-secondary">
              {doc.kind}
            </span>

            {/* Permission */}
            <span className={`flex items-center gap-0.5 rounded-full px-2 py-0.5 text-[10px] ${
              doc.permission === "public"
                ? "bg-success/10 text-success"
                : "bg-bg-tertiary/80 text-text-secondary"
            }`}>
              {doc.permission === "public" ? <Unlock size={9} /> : <Lock size={9} />}
              {doc.permission}
            </span>

            {/* Tags */}
            {doc.tags && doc.tags.length > 0 && (
              <div className="flex min-w-0 items-center gap-1">
                <Tag size={9} className="text-text-secondary/40" />
                {doc.tags.slice(0, 3).map((t, i) => (
                  <span key={i} className="rounded-full bg-accent/10 px-1.5 py-0.5 text-[9px] text-accent">
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
              <span className="rounded-full bg-bg-tertiary/80 px-2 py-0.5 text-[10px] text-text-secondary">
                {new Date(doc.created_at).toLocaleDateString()}
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={(e) => {
              e.stopPropagation();
              onSelect();
            }}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-accent/10 hover:text-accent"
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
              className="rounded-full p-1.5 text-text-secondary/40 opacity-0 transition hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
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
