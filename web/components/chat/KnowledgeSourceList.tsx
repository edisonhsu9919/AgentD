"use client";

import { useState } from "react";
import { BookOpen, FileText, ExternalLink, ChevronDown, ChevronRight } from "lucide-react";
import { usePanelStore } from "@/store/panel";
import type { SourceRefItem, KnowledgeSearchResult } from "@/lib/types";

/** Unified source item that works with both source_refs (primary) and search results (fallback) */
interface SourceDisplayItem {
  doc_id: string;
  title: string;
  kind: string;
  evidence_excerpt: string;
}

interface KnowledgeSourceListProps {
  /** Primary: from assistant message source_refs part */
  sourceRefs?: SourceRefItem[];
  /** Fallback: from knowledge_search tool results */
  searchResults?: KnowledgeSearchResult[];
}

export default function KnowledgeSourceList({ sourceRefs, searchResults }: KnowledgeSourceListProps) {
  const [expanded, setExpanded] = useState(false);

  // Prefer source_refs (structured, from assistant message), fallback to search results
  const items: SourceDisplayItem[] = sourceRefs
    ? sourceRefs.map((s) => ({
        doc_id: s.doc_id,
        title: s.title,
        kind: s.kind,
        evidence_excerpt: s.evidence_excerpt || "",
      }))
    : (searchResults || []).map((s) => ({
        doc_id: s.doc_id,
        title: s.title,
        kind: s.kind,
        evidence_excerpt: s.excerpts?.[0]?.text || "",
      }));

  if (items.length === 0) return null;

  return (
    <div className="mt-2 rounded-lg border border-border/50 bg-bg-primary/30">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center gap-1.5 px-3 py-1.5 text-left"
      >
        {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <BookOpen size={12} className="text-accent" />
        <span className="text-[11px] font-medium text-text-secondary">
          Sources ({items.length})
        </span>
      </button>
      {expanded && (
        <div className="border-t border-border/30 px-2 py-1.5 space-y-1">
          {items.map((item) => (
            <KnowledgeSourceItem key={item.doc_id} item={item} />
          ))}
        </div>
      )}
    </div>
  );
}

function KnowledgeSourceItem({ item }: { item: SourceDisplayItem }) {
  const [hovered, setHovered] = useState(false);
  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={() => openKnowledgeSource(item.doc_id)}
        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left transition hover:bg-bg-tertiary/50"
      >
        <FileText size={12} className="shrink-0 text-text-secondary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs text-text-primary">{item.title}</div>
          {item.kind && (
            <div className="truncate text-[10px] text-text-secondary">{item.kind}</div>
          )}
        </div>
        <ExternalLink size={10} className="shrink-0 text-text-secondary/40" />
      </button>

      {/* Hover card */}
      {hovered && item.evidence_excerpt && (
        <div className="absolute bottom-full left-0 z-50 mb-1 w-72 rounded-lg border border-border bg-bg-secondary p-2.5 shadow-lg">
          <div className="mb-1.5 text-[10px] font-medium text-text-secondary">
            {item.title}
          </div>
          <div className="rounded bg-bg-primary/50 px-2 py-1">
            <p className="text-[10px] text-text-primary line-clamp-4">{item.evidence_excerpt}</p>
          </div>
        </div>
      )}
    </div>
  );
}
