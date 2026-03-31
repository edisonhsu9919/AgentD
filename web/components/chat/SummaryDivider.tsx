"use client";

import { useState, useMemo } from "react";
import type { Message } from "@/lib/types";
import { ChevronRight, Layers, Target, ListChecks, Wrench, FileText, ArrowRight } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface SummaryDividerProps {
  message: Message;
}

interface StructuredSummary {
  session_intent?: string;
  key_decisions?: string[];
  current_task_state?: string;
  active_skill?: string | null;
  important_artifacts?: string[];
  next_steps?: string[];
}

function tryParseStructuredSummary(text: string): StructuredSummary | null {
  // Try to extract JSON from the text (may be wrapped in markdown code fences)
  const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/) || text.match(/(\{[\s\S]*\})/);
  if (!jsonMatch) return null;

  try {
    const parsed = JSON.parse(jsonMatch[1]);
    if (typeof parsed === "object" && parsed !== null && "session_intent" in parsed) {
      return parsed as StructuredSummary;
    }
  } catch {
    // not valid JSON
  }
  return null;
}

function SummaryCard({ data }: { data: StructuredSummary }) {
  return (
    <div className="space-y-3">
      {/* Session Intent */}
      {data.session_intent && (
        <SummaryField
          icon={<Target size={11} />}
          label="Session Intent"
          content={data.session_intent}
        />
      )}

      {/* Current Task State */}
      {data.current_task_state && (
        <SummaryField
          icon={<ListChecks size={11} />}
          label="Current Task"
          content={data.current_task_state}
        />
      )}

      {/* Active Skill */}
      {data.active_skill && (
        <SummaryField
          icon={<Wrench size={11} />}
          label="Active Skill"
          content={data.active_skill}
        />
      )}

      {/* Key Decisions */}
      {data.key_decisions && data.key_decisions.length > 0 && (
        <SummaryListField
          label="Key Decisions"
          items={data.key_decisions}
        />
      )}

      {/* Important Artifacts */}
      {data.important_artifacts && data.important_artifacts.length > 0 && (
        <SummaryListField
          icon={<FileText size={11} />}
          label="Artifacts"
          items={data.important_artifacts}
        />
      )}

      {/* Next Steps */}
      {data.next_steps && data.next_steps.length > 0 && (
        <SummaryListField
          icon={<ArrowRight size={11} />}
          label="Next Steps"
          items={data.next_steps}
        />
      )}
    </div>
  );
}

function SummaryField({
  icon,
  label,
  content,
}: {
  icon: React.ReactNode;
  label: string;
  content: string;
}) {
  return (
    <div>
      <div className="mb-0.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-text-secondary/50">
        {icon}
        {label}
      </div>
      <div className="text-xs leading-relaxed text-text-secondary/80">{content}</div>
    </div>
  );
}

function SummaryListField({
  icon,
  label,
  items,
}: {
  icon?: React.ReactNode;
  label: string;
  items: string[];
}) {
  return (
    <div>
      <div className="mb-0.5 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-text-secondary/50">
        {icon}
        {label}
      </div>
      <ul className="ml-3 list-disc space-y-0.5 text-xs leading-relaxed text-text-secondary/80">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

export default function SummaryDivider({ message }: SummaryDividerProps) {
  const [expanded, setExpanded] = useState(false);

  // Extract summary text from message parts
  const summaryText = message.parts
    .filter((p) => p.type === "text")
    .map((p) => (p as { type: "text"; content: string }).content)
    .join("\n")
    // Strip the [Context Summary] prefix if present
    .replace(/^\[Context Summary\]\s*/i, "");

  // Try to parse as structured JSON, fallback to markdown
  const structured = useMemo(() => tryParseStructuredSummary(summaryText), [summaryText]);

  // Extract tokens_saved from compaction parts if available
  const compactionPart = message.parts.find((p) => p.type === "compaction");
  const tokensSaved =
    compactionPart && compactionPart.type === "compaction"
      ? compactionPart.tokens_saved
      : null;

  return (
    <div className="my-4">
      <div className="flex items-center gap-2">
        <div className="h-px flex-1 bg-border/60" />
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1.5 rounded-full border border-border/60 bg-bg-secondary/50 px-3 py-1 text-[11px] text-text-secondary transition hover:bg-bg-secondary hover:text-text-primary"
        >
          <Layers size={10} className="shrink-0" />
          <span>Context compacted</span>
          {tokensSaved != null && (
            <span className="text-text-secondary/60">
              — saved {tokensSaved.toLocaleString()} tokens
            </span>
          )}
          <ChevronRight
            size={10}
            className={`transition-transform ${expanded ? "rotate-90" : ""}`}
          />
        </button>
        <div className="h-px flex-1 bg-border/60" />
      </div>

      {expanded && summaryText && (
        <div className="mx-auto mt-2 max-w-[85%] rounded border border-border/40 bg-bg-secondary/30 px-4 py-3">
          {structured ? (
            <SummaryCard data={structured} />
          ) : (
            <>
              <div className="text-[10px] font-medium uppercase tracking-wider text-text-secondary/50 mb-1.5">
                Summary
              </div>
              <div className="prose prose-invert prose-sm max-w-none text-text-secondary/80">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {summaryText}
                </ReactMarkdown>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
