"use client";

import { useState } from "react";
import type { Part, SubtaskResultPart } from "@/lib/types";
import ToolCallBlock from "./ToolCallBlock";
import KnowledgeSourceList from "./KnowledgeSourceList";
import MessageMarkdown from "./MessageMarkdown";
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
  role?: "user" | "assistant" | "tool";
  /** Map of tool_call_id → tool_name, built from sibling tool_call parts */
  toolNameMap?: Map<string, string>;
  /** Cross-message map of tool_call_id → {tool_name, input}, built from all messages */
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
}

export default function MessagePart({ part, role = "assistant", toolNameMap, toolInfoMap }: MessagePartProps) {
  switch (part.type) {
    case "text": {
      const cleaned = stripThinkTags(part.content);
      if (!cleaned) return null;
      if (role === "user") {
        return (
          <div className="chat-prose">
            <MessageMarkdown>{cleaned}</MessageMarkdown>
          </div>
        );
      }
      return (
        <div className="chat-prose">
          <MessageMarkdown>{cleaned}</MessageMarkdown>
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
        <div className="my-1 rounded-[16px] bg-bg-tertiary/50 px-3 py-2 text-[12px] text-text-secondary">
          上下文已压缩，本轮回收 {part.tokens_saved} tokens
        </div>
      );

    case "reasoning":
      return <ReasoningBlock content={part.content} />;

    case "error":
      return (
        <div className="my-1 rounded-[16px] bg-danger/10 px-3 py-2 text-[12px] text-danger">
          错误（{part.code}）：{part.message}
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
    <div className={`my-2 rounded-[16px] p-3 space-y-2 ${
      isFailed
        ? "bg-danger/5"
        : "bg-purple-500/5"
    }`}>
      <div className="flex items-center gap-2">
        <GitBranch size={14} className={isFailed ? "text-danger" : "text-purple-400"} />
        <span className={`text-xs font-medium ${isFailed ? "text-danger" : "text-purple-400"}`}>
          {isFailed ? "子任务失败" : "子任务完成"}
        </span>
        {part.title && (
          <span className="text-xs text-text-primary">&mdash; {part.title}</span>
        )}
      </div>

      {part.summary && (
        <p className="whitespace-pre-wrap text-[12px] text-text-primary">{part.summary}</p>
      )}

      {part.child_session_id && (
        <a
          href={`/chat?s=${part.child_session_id}`}
          className="inline-flex items-center gap-1 text-[10px] text-accent transition hover:underline"
        >
          <ExternalLink size={10} />
          查看子会话
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
        <span>查看思考过程</span>
      </button>
      {show && (
        <div className="ml-1 mt-1 max-h-40 overflow-y-auto pl-3 text-[12px] leading-6 text-text-secondary/40 italic">
          {content}
        </div>
      )}
    </div>
  );
}
