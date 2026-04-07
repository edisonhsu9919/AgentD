"use client";

import { useMemo } from "react";
import type { Message, KnowledgeSearchResult, SourceRefItem } from "@/lib/types";
import MessagePart from "./MessagePart";
import KnowledgeSourceList from "./KnowledgeSourceList";
import CitedTextBlock from "./CitedTextBlock";
import { User, Bot, Wrench } from "lucide-react";

const roleConfig = {
  user: {
    icon: User,
    label: "You",
    align: "justify-end" as const,
    bubble: "bg-accent/10 border-accent/20",
  },
  assistant: {
    icon: Bot,
    label: "Agent",
    align: "justify-start" as const,
    bubble: "bg-bg-secondary border-border",
  },
  tool: {
    icon: Wrench,
    label: "Tool",
    align: "justify-start" as const,
    bubble: "bg-bg-secondary border-border",
  },
};

export default function MessageBubble({
  message,
  toolInfoMap,
  knowledgeSources,
  sessionId,
}: {
  message: Message;
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
  knowledgeSources?: KnowledgeSearchResult[];
  sessionId?: string;
}) {
  const config = roleConfig[message.role] || roleConfig.assistant;
  const Icon = config.icon;

  // Build tool_call_id → tool_name map so tool_result parts can resolve their tool name
  const toolNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const part of message.parts) {
      if (part.type === "tool_call") {
        map.set(part.tool_call_id, part.tool_name);
      }
    }
    return map;
  }, [message.parts]);

  return (
    <div className={`flex ${config.align} mb-4`}>
      <div
        className={`min-w-0 max-w-[85%] rounded-lg border px-4 py-3 ${config.bubble}`}
      >
        {/* Header */}
        <div className="mb-1 flex items-center gap-1.5">
          <Icon size={12} className="text-text-secondary" />
          <span className="text-xs font-medium text-text-secondary">
            {config.label}
          </span>
        </div>

        {/* Parts — with cited text merging */}
        <div className="space-y-1">
          <PartsRenderer
            parts={message.parts}
            toolNameMap={toolNameMap}
            toolInfoMap={toolInfoMap}
            knowledgeSources={knowledgeSources}
          />
        </div>
      </div>
    </div>
  );
}

/**
 * Smart parts renderer: detects when a message has both text + source_refs
 * and merges them into a CitedTextBlock (inline citations + bottom source list).
 * Falls back to standard per-part rendering otherwise.
 */
function PartsRenderer({
  parts,
  toolNameMap,
  toolInfoMap,
  knowledgeSources,
}: {
  parts: Message["parts"];
  toolNameMap: Map<string, string>;
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
  knowledgeSources?: KnowledgeSearchResult[];
}) {
  // Check if this message has both text and source_refs parts
  const sourceRefsPart = parts.find((p) => p.type === "source_refs");
  const textParts = parts.filter((p) => p.type === "text");
  const hasCitedContent = sourceRefsPart && textParts.length > 0;

  if (hasCitedContent) {
    // Merge all text content and render with inline citations
    const combinedText = textParts
      .map((p) => (p.type === "text" ? p.content : ""))
      .join("\n\n");
    const sources = (sourceRefsPart as import("@/lib/types").SourceRefsPart).sources;

    return (
      <>
        {/* Render non-text/non-source_refs parts first (reasoning, tool_call, etc.) */}
        {parts.map((part, i) => {
          if (part.type === "text" || part.type === "source_refs") return null;
          return <MessagePart key={i} part={part} toolNameMap={toolNameMap} toolInfoMap={toolInfoMap} />;
        })}
        {/* Then render the cited text block */}
        <CitedTextBlock text={combinedText} sources={sources} />
      </>
    );
  }

  // Standard rendering — no citation merging
  return (
    <>
      {parts.map((part, i) => (
        <MessagePart key={i} part={part} toolNameMap={toolNameMap} toolInfoMap={toolInfoMap} />
      ))}
      {/* Fallback knowledge sources from tool results */}
      {knowledgeSources &&
        knowledgeSources.length > 0 &&
        !parts.some((p) => p.type === "source_refs") && (
          <KnowledgeSourceList searchResults={knowledgeSources} />
        )}
    </>
  );
}
