"use client";

import { useState } from "react";
import type { Part } from "@/lib/types";
import ToolCallBlock from "./ToolCallBlock";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronRight } from "lucide-react";

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

    default:
      return null;
  }
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
