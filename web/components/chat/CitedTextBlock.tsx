"use client";

import { useState, useMemo, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import { FileText, ExternalLink, BookOpen } from "lucide-react";
import { usePanelStore } from "@/store/panel";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import type { SourceRefItem } from "@/lib/types";

interface CitedTextBlockProps {
  text: string;
  sources: SourceRefItem[];
}

/**
 * Renders assistant text with inline citation superscripts and a source list footer.
 * Similar to Perplexity / ChatGPT with search — [1][2] in the text become clickable
 * superscript badges, and sources are listed below the text with hover cards.
 */
export default function CitedTextBlock({ text, sources }: CitedTextBlockProps) {
  // Build citation index map: doc_id → 1-based number
  const citationMap = useMemo(() => {
    const map = new Map<string, number>();
    sources.forEach((s, i) => map.set(s.doc_id, i + 1));
    return map;
  }, [sources]);

  // Replace [N] patterns with interactive superscript markers in the rendered output.
  // Since ReactMarkdown renders HTML, we inject custom components for citation marks.
  // Strategy: use a remark/rehype plugin or post-process. Simplest: custom component
  // that wraps ReactMarkdown and replaces [1], [2] etc with superscript spans.

  return (
    <div>
      {/* Text with inline citation marks */}
      <div className="prose prose-invert prose-sm max-w-none">
        <CitedMarkdown text={text} sourceCount={sources.length} sources={sources} />
      </div>

      {/* Source list — always visible, not collapsed */}
      {sources.length > 0 && (
        <div className="mt-3 border-t border-border/30 pt-2">
          <div className="mb-1.5 flex items-center gap-1.5">
            <BookOpen size={11} className="text-accent" />
            <span className="text-[10px] font-medium text-text-secondary">
              Sources
            </span>
          </div>
          <div className="space-y-1">
            {sources.map((source, idx) => (
              <SourceItem key={source.doc_id} source={source} index={idx + 1} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Renders markdown text with [N] citation references replaced by
 * clickable superscript badges.
 */
function CitedMarkdown({ text, sourceCount, sources }: { text: string; sourceCount: number; sources: SourceRefItem[] }) {
  // Split the text on [N] patterns where N is 1..sourceCount
  // and replace them with superscript elements
  const parts = useMemo(() => {
    if (sourceCount === 0) return [{ type: "text" as const, content: text }];

    // Match [1], [2], [1][2], etc. — only numbers within range
    const pattern = /\[(\d+)\]/g;
    const result: Array<{ type: "text"; content: string } | { type: "cite"; num: number }> = [];
    let lastIndex = 0;
    let match: RegExpExecArray | null;

    while ((match = pattern.exec(text)) !== null) {
      const num = parseInt(match[1], 10);
      if (num < 1 || num > sourceCount) continue;

      if (match.index > lastIndex) {
        result.push({ type: "text", content: text.slice(lastIndex, match.index) });
      }
      result.push({ type: "cite", num });
      lastIndex = match.index + match[0].length;
    }

    if (lastIndex < text.length) {
      result.push({ type: "text", content: text.slice(lastIndex) });
    }

    return result.length > 0 ? result : [{ type: "text" as const, content: text }];
  }, [text, sourceCount]);

  return (
    <>
      {parts.map((part, i) =>
        part.type === "cite" ? (
          <InlineCiteBadge key={i} num={part.num} sources={sources} />
        ) : (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>
            {part.content}
          </ReactMarkdown>
        ),
      )}
    </>
  );
}

/**
 * Inline citation badge — clickable superscript [N] in the text body.
 * Hover shows source title + evidence excerpt, click opens panel preview.
 */
function InlineCiteBadge({ num, sources }: { num: number; sources: SourceRefItem[] }) {
  const [hovered, setHovered] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const badgeRef = useRef<HTMLButtonElement>(null);
  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);
  const source = sources[num - 1]; // 1-based index

  const handleMouseEnter = useCallback(() => {
    setHovered(true);
    if (badgeRef.current) {
      const rect = badgeRef.current.getBoundingClientRect();
      setPos({
        top: rect.top - 4,
        left: rect.left + rect.width / 2,
      });
    }
  }, []);

  return (
    <span className="inline-block">
      <button
        ref={badgeRef}
        onClick={() => source && openKnowledgeSource(source.doc_id)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={() => setHovered(false)}
        className="ml-0.5 mr-0.5 inline-flex h-4 w-4 cursor-pointer items-center justify-center rounded-full bg-accent/20 text-[9px] font-bold text-accent align-super transition hover:bg-accent/40"
      >
        {num}
      </button>

      {/* Hover card — portal to body with fixed positioning to escape overflow clipping */}
      {hovered && source && pos && typeof document !== "undefined" &&
        createPortal(
          <div
            className="fixed z-[100] w-60 -translate-x-1/2 rounded-lg border border-border bg-bg-secondary p-2 shadow-lg"
            style={{ top: pos.top, left: pos.left, transform: "translate(-50%, -100%)" }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            <div className="mb-1 text-[10px] font-medium text-text-secondary">
              {source.title}
            </div>
            {source.evidence_excerpt && (
              <div className="rounded bg-bg-primary/50 px-2 py-1">
                <p className="text-[10px] text-text-primary line-clamp-3">{source.evidence_excerpt}</p>
              </div>
            )}
            <div className="mt-1 text-[9px] text-accent">Click to preview</div>
          </div>,
          document.body,
        )}
    </span>
  );
}

function SourceItem({ source, index }: { source: SourceRefItem; index: number }) {
  const [hovered, setHovered] = useState(false);
  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={() => openKnowledgeSource(source.doc_id)}
        className="flex w-full items-center gap-2 rounded px-2 py-1 text-left transition hover:bg-bg-tertiary/50"
      >
        {/* Index badge */}
        <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/20 text-[9px] font-bold text-accent">
          {index}
        </span>
        <FileText size={12} className="shrink-0 text-text-secondary" />
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs text-text-primary">{source.title}</div>
          {source.kind && (
            <div className="truncate text-[10px] text-text-secondary">{source.kind}</div>
          )}
        </div>
        <ExternalLink size={10} className="shrink-0 text-text-secondary/40" />
      </button>

      {/* Hover card */}
      {hovered && source.evidence_excerpt && (
        <div className="absolute bottom-full left-0 z-50 mb-1 w-72 rounded-lg border border-border bg-bg-secondary p-2.5 shadow-lg">
          <div className="mb-1.5 flex items-center gap-1.5">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/20 text-[9px] font-bold text-accent">
              {index}
            </span>
            <span className="text-[10px] font-medium text-text-secondary">
              {source.title}
            </span>
          </div>
          <div className="rounded bg-bg-primary/50 px-2 py-1">
            <p className="text-[10px] text-text-primary line-clamp-4">{source.evidence_excerpt}</p>
          </div>
        </div>
      )}
    </div>
  );
}
