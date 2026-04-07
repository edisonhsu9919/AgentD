"use client";

import { useState } from "react";
import type { Part, SubtaskResultPart, SourceRefsPart } from "@/lib/types";
import ToolCallBlock from "./ToolCallBlock";
import KnowledgeSourceList from "./KnowledgeSourceList";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronRight, GitBranch, ExternalLink } from "lucide-react";

/**
 * Strip <think>...</think> blocks from LLM output.
 * Returns the visible text content.
 */
function stripThinkTags(content: string): string {
  return content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}

interface MessagePartProps {
  part: Part;
  /** Map of tool_call_id → tool_name, built from sibling tool_call parts */
  toolNameMap?: Map<string, string>;
  /** Cross-message map of tool_call_id → {tool_name, input}, built from all messages */
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
}

export default function MessagePart({ part, toolNameMap, toolInfoMap }: MessagePartProps) {
  switch (part.type) {
    case "text": {
      const cleaned = stripThinkTags(part.content);
      if (!cleaned) return null;
      return (
        <div className="prose prose-invert prose-sm max-w-none">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleaned}</ReactMarkdown>
        </div>
      );
    }

    case "tool_call":
      return (
        <ToolCallBlock
          toolName={part.tool_name}
          input={part.input}
          status={part.status}
        />
      );

    case "tool_result": {
      // Resolve tool identity: cross-message map (covers tool_call in AIMessage →
      // tool_result in ToolMessage) > same-message sibling map > fallback
      const crossInfo = toolInfoMap?.get(part.tool_call_id);
      const resolvedToolName =
        crossInfo?.tool_name || toolNameMap?.get(part.tool_call_id) || "result";
      const resolvedInput = crossInfo?.input || {};
      return (
        <ToolCallBlock
          toolName={resolvedToolName}
          input={resolvedInput}
          output={part.output}
          isError={part.is_error}
          status="completed"
        />
      );
    }

    case "compaction":
      return (
        <div className="my-1 rounded bg-bg-tertiary/50 px-3 py-2 text-xs text-text-secondary">
          Context compacted — saved {part.tokens_saved} tokens
        </div>
      );

    case "reasoning":
      return <ReasoningBlock content={part.content} />;

    case "error":
      return (
        <div className="my-1 rounded bg-danger/10 px-3 py-2 text-sm text-danger">
          Error ({part.code}): {part.message}
        </div>
      );

    case "subtask_result":
      return <SubtaskResultCard part={part} />;

    case "source_refs":
      return <KnowledgeSourceList sourceRefs={part.sources} />;

    default:
      return null;
  }
}

/** Sub-task result card — rendered for subtask_result parts */
function SubtaskResultCard({ part }: { part: SubtaskResultPart }) {
  const isFailed = part.status === "failed";

  return (
    <div className={`my-2 rounded-lg border p-3 space-y-2 ${
      isFailed
        ? "border-danger/20 bg-danger/5"
        : "border-purple-500/20 bg-purple-500/5"
    }`}>
      <div className="flex items-center gap-2">
        <GitBranch size={14} className={isFailed ? "text-danger" : "text-purple-400"} />
        <span className={`text-xs font-medium ${isFailed ? "text-danger" : "text-purple-400"}`}>
          {isFailed ? "Sub-task Failed" : "Sub-task Completed"}
        </span>
        {part.title && (
          <span className="text-xs text-text-primary">&mdash; {part.title}</span>
        )}
      </div>

      {part.summary && (
        <p className="text-xs text-text-primary whitespace-pre-wrap">{part.summary}</p>
      )}

      {part.child_session_id && (
        <a
          href={`/chat?s=${part.child_session_id}`}
          className="inline-flex items-center gap-1 text-[10px] text-accent transition hover:underline"
        >
          <ExternalLink size={10} />
          View child session
        </a>
      )}
    </div>
  );
}

/** Collapsible reasoning block for persisted messages */
function ReasoningBlock({ content }: { content: string }) {
  const [show, setShow] = useState(false);
  if (!content) return null;

  return (
    <div className="mb-1">
      <button
        onClick={() => setShow(!show)}
        className="flex items-center gap-1.5 text-[11px] text-text-secondary/50 transition hover:text-text-secondary"
      >
        <ChevronRight
          size={10}
          className={`transition-transform ${show ? "rotate-90" : ""}`}
        />
        <span>Thought for a moment</span>
      </button>
      {show && (
        <div className="ml-1 mt-1 max-h-40 overflow-y-auto border-l border-border pl-3 text-xs leading-relaxed text-text-secondary/40 italic">
          {content}
        </div>
      )}
    </div>
  );
}
