"use client";

import { useMemo } from "react";
import type { Message, KnowledgeSearchResult } from "@/lib/types";
import MessagePart from "./MessagePart";
import KnowledgeSourceList from "./KnowledgeSourceList";
import CitedTextBlock from "./CitedTextBlock";
import CopyButton from "./CopyButton";

export default function MessageBubble({
  message,
  toolInfoMap,
  knowledgeSources,
}: {
  message: Message;
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
  knowledgeSources?: KnowledgeSearchResult[];
}) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";
  const copyText = getVisibleMessageText(message);
  const showMeta = copyText.length > 0 && !isTool;

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
    <article className={`flex w-full ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`min-w-0 ${isUser ? "max-w-[72%] rounded-[18px] bg-bg-primary/92 px-4 py-3 shadow-[0_12px_30px_rgba(42,41,51,0.04)]" : isTool ? "max-w-[min(100%,56rem)] pl-2" : "max-w-[min(100%,56rem)]"}`}
      >
        <PartsRenderer
          messageRole={message.role}
          parts={message.parts}
          toolNameMap={toolNameMap}
          toolInfoMap={toolInfoMap}
          knowledgeSources={knowledgeSources}
        />
        {showMeta && (
          <MessageMeta
            createdAt={message.created_at}
            copyText={copyText}
            align={isUser ? "right" : "left"}
          />
        )}
      </div>
    </article>
  );
}

function MessageMeta({
  createdAt,
  copyText,
  align,
}: {
  createdAt: string;
  copyText: string;
  align: "left" | "right";
}) {
  return (
    <div
      className={`mt-2 flex items-center gap-2 text-[10px] text-text-secondary/38 ${
        align === "right" ? "justify-end" : "justify-start"
      }`}
    >
      <time dateTime={createdAt}>{formatMessageTime(createdAt)}</time>
      <span className="h-1 w-1 rounded-full bg-text-secondary/20" />
      <CopyButton
        text={copyText}
        label="复制"
        title="复制本轮内容"
        className="px-1.5 py-0.5 text-[10px]"
      />
    </div>
  );
}

function formatMessageTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function getVisibleMessageText(message: Message) {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => (part.type === "text" ? stripThinkTags(part.content) : ""))
    .filter(Boolean)
    .join("\n\n")
    .trim();
}

function stripThinkTags(content: string): string {
  return content.replace(/<think>[\s\S]*?<\/think>/g, "").trim();
}

/**
 * Smart parts renderer: detects when a message has both text + source_refs
 * and merges them into a CitedTextBlock (inline citations + bottom source list).
 * Falls back to standard per-part rendering otherwise.
 */
function PartsRenderer({
  messageRole,
  parts,
  toolNameMap,
  toolInfoMap,
  knowledgeSources,
}: {
  messageRole: Message["role"];
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
          return (
            <MessagePart
              key={i}
              part={part}
              role={messageRole}
              toolNameMap={toolNameMap}
              toolInfoMap={toolInfoMap}
            />
          );
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
        <MessagePart
          key={i}
          part={part}
          role={messageRole}
          toolNameMap={toolNameMap}
          toolInfoMap={toolInfoMap}
        />
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
