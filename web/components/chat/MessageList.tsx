"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "@/store/chat";
import MessageBubble from "./MessageBubble";
import SummaryDivider from "./SummaryDivider";
import ToolCallBlock from "./ToolCallBlock";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, Loader2, Clock, ChevronRight } from "lucide-react";

/**
 * Parse streaming content into thinking vs formal text.
 * Merges ALL <think>...</think> blocks (possibly multiple per turn)
 * into a single thinking buffer so subsequent reasoning chunks
 * don't leak into the formal text area.
 */
function parseStreamingThinking(content: string): {
  isThinking: boolean;
  thinkingText: string;
  formalText: string;
} {
  const openTag = "<think>";
  const closeTag = "</think>";

  // Fast path: no <think> tag at all
  if (content.indexOf(openTag) === -1) {
    // Might be a partial <think> tag still being typed
    if (/^<(?:t(?:h(?:i(?:n(?:k>?)?)?)?)?)?$/.test(content.trim())) {
      return { isThinking: true, thinkingText: "", formalText: "" };
    }
    return { isThinking: false, thinkingText: "", formalText: content };
  }

  // Iterate through all <think>...</think> blocks, merging them
  const thinkingParts: string[] = [];
  const formalParts: string[] = [];
  let remaining = content;

  while (remaining.length > 0) {
    const openIdx = remaining.indexOf(openTag);

    if (openIdx === -1) {
      // No more <think> tags — rest is formal text
      const trimmed = remaining.trim();
      if (trimmed) formalParts.push(trimmed);
      break;
    }

    // Text before <think> is formal
    const before = remaining.slice(0, openIdx).trim();
    if (before) formalParts.push(before);

    const afterOpen = openIdx + openTag.length;
    const closeIdx = remaining.indexOf(closeTag, afterOpen);

    if (closeIdx === -1) {
      // Unclosed <think> — actively thinking
      thinkingParts.push(remaining.slice(afterOpen));
      return {
        isThinking: true,
        thinkingText: thinkingParts.join("\n"),
        formalText: formalParts.join("\n"),
      };
    }

    // Complete <think>...</think> block
    thinkingParts.push(remaining.slice(afterOpen, closeIdx));
    remaining = remaining.slice(closeIdx + closeTag.length);
  }

  return {
    isThinking: false,
    thinkingText: thinkingParts.join("\n"),
    formalText: formalParts.join("\n"),
  };
}

/**
 * Thinking display layer — temporary UI for reasoning content.
 * Active: pulsing indicator + tail of thinking content.
 * Done: collapsible toggle to review thinking.
 */
function ThinkingBlock({
  isActive,
  content,
}: {
  isActive: boolean;
  content: string;
}) {
  const [showContent, setShowContent] = useState(false);

  if (isActive) {
    const tail =
      content.length > 200 ? "…" + content.slice(-200) : content;
    return (
      <div className="mb-2">
        <div className="flex items-center gap-2 text-xs">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
          <span className="animate-pulse font-medium text-text-secondary">
            Thinking...
          </span>
        </div>
        {content && (
          <div className="mt-1 max-h-20 overflow-hidden text-xs leading-relaxed text-text-secondary/40 italic">
            {tail}
          </div>
        )}
      </div>
    );
  }

  if (!content) return null;

  return (
    <div className="mb-2">
      <button
        onClick={() => setShowContent(!showContent)}
        className="flex items-center gap-1.5 text-[11px] text-text-secondary/50 transition hover:text-text-secondary"
      >
        <ChevronRight
          size={10}
          className={`transition-transform ${showContent ? "rotate-90" : ""}`}
        />
        <span>Thought for a moment</span>
      </button>
      {showContent && (
        <div className="ml-1 mt-1 max-h-40 overflow-y-auto border-l border-border pl-3 text-xs leading-relaxed text-text-secondary/40 italic">
          {content}
        </div>
      )}
    </div>
  );
}

export default function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const streamingDraft = useChatStore((s) => s.streamingDraft);
  const streamingThinking = useChatStore((s) => s.streamingThinking);
  const streamingToolCalls = useChatStore((s) => s.streamingToolCalls);
  const status = useChatStore((s) => s.status);

  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingDraft, streamingThinking, streamingToolCalls]);

  // Build cross-message tool_call_id → {tool_name, input} map so tool_result parts
  // in ToolMessages (role: "tool") can resolve their tool identity from the matching
  // tool_call in the AIMessage (role: "assistant") — they're in different messages.
  const toolInfoMap = useMemo(() => {
    const map = new Map<string, { tool_name: string; input: Record<string, unknown> }>();
    for (const msg of messages) {
      for (const part of msg.parts) {
        if (part.type === "tool_call") {
          map.set(part.tool_call_id, { tool_name: part.tool_name, input: part.input });
        }
      }
    }
    return map;
  }, [messages]);

  // Parse <think> tags from streamingDraft (defense-in-depth if backend filter leaks)
  const parsed = streamingDraft
    ? parseStreamingThinking(streamingDraft)
    : { isThinking: false, thinkingText: "", formalText: "" };

  // Merge thinking from both sources:
  // 1. streamingThinking — from reasoning_delta SSE events
  // 2. parsed.thinkingText — from <think> tags in text_delta (fallback)
  const thinkingContent = [streamingThinking, parsed.thinkingText]
    .filter(Boolean)
    .join("\n");

  const hasRunningTool = streamingToolCalls.some(
    (tc) => tc.status === "running",
  );
  const hasThinkingContent = !!thinkingContent;

  // Active thinking: content arriving without formal text,
  // or gap detection (running, no output, no running tool)
  const isActivelyThinking =
    (hasThinkingContent && !parsed.formalText) ||
    parsed.isThinking ||
    (status === "running" &&
      !streamingDraft &&
      !streamingThinking &&
      !hasRunningTool);

  const showThinking = isActivelyThinking || hasThinkingContent;
  const formalText = parsed.formalText;

  // Show streaming bubble when: has content OR agent is running
  const hasStreaming =
    streamingDraft ||
    streamingThinking ||
    streamingToolCalls.length > 0 ||
    status === "running";

  return (
    <div className="min-w-0 flex-1 overflow-y-auto px-4 py-4">
      {/* Persisted messages — is_summary renders as SummaryDivider inline */}
      {messages.map((msg) =>
        msg.is_summary ? (
          <SummaryDivider key={msg.id} message={msg} />
        ) : (
          <MessageBubble key={msg.id} message={msg} toolInfoMap={toolInfoMap} />
        ),
      )}

      {/* Queued indicator */}
      {status === "queued" && !hasStreaming && (
        <div className="mb-4 flex justify-start">
          <div className="flex items-center gap-2 rounded-lg border border-yellow-500/30 bg-yellow-500/5 px-4 py-3">
            <Clock size={14} className="animate-pulse text-yellow-400" />
            <span className="text-sm text-yellow-400">
              Queued — waiting for worker...
            </span>
          </div>
        </div>
      )}

      {/* Streaming content (not yet persisted) */}
      {hasStreaming && (
        <div className="mb-4 flex justify-start">
          <div className="min-w-0 max-w-[85%] rounded-lg border border-border bg-bg-secondary px-4 py-3">
            <div className="mb-1 flex items-center gap-1.5">
              <Bot size={12} className="text-text-secondary" />
              <span className="text-xs font-medium text-text-secondary">
                Agent
              </span>
              {(status === "running" || status === "queued") && (
                <Loader2
                  size={12}
                  className="animate-spin text-accent"
                />
              )}
            </div>

            {/* Thinking block — from reasoning_delta, <think> tags, or gap detection */}
            {showThinking && (
              <ThinkingBlock
                isActive={isActivelyThinking}
                content={thinkingContent}
              />
            )}

            {/* Streaming tool calls */}
            {streamingToolCalls.map((tc) => (
              <ToolCallBlock
                key={tc.tool_call_id}
                toolName={tc.tool_name}
                input={tc.input}
                output={tc.output}
                isError={tc.is_error}
                status={tc.status}
              />
            ))}

            {/* Formal text (after thinking) */}
            {formalText && (
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {formalText}
                </ReactMarkdown>
              </div>
            )}

            {/* Typing cursor — shown when running and no tool is actively executing */}
            {status === "running" && !hasRunningTool && (
              <span className="typing-cursor inline-block text-accent">
                ▌
              </span>
            )}
          </div>
        </div>
      )}

      <div ref={bottomRef} />
    </div>
  );
}
