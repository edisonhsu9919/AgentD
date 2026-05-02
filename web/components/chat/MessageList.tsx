"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useChatStore } from "@/store/chat";
import { useTaskPlanStore } from "@/store/taskPlan";
import MessageBubble from "./MessageBubble";
import SummaryDivider from "./SummaryDivider";
import ToolCallBlock from "./ToolCallBlock";
import MessageMarkdown from "./MessageMarkdown";
import { ArrowDown, Loader2, Clock, ChevronRight, Wrench } from "lucide-react";
import type { Message, SessionStatus, StreamingTimelineItem } from "@/lib/types";

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
            正在思考...
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
        <span>查看思考过程</span>
      </button>
      {showContent && (
        <div className="ml-1 mt-1 max-h-40 overflow-y-auto border-l border-border pl-3 text-xs leading-relaxed text-text-secondary/40 italic">
          {content}
        </div>
      )}
    </div>
  );
}

function TranscriptRow({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="flex w-full justify-start py-2">
      <div className="min-w-0 max-w-[min(100%,56rem)] space-y-2.5 chat-copy">
        {children}
      </div>
    </div>
  );
}

export default function MessageList() {
  const messages = useChatStore((s) => s.messages);
  const streamingTimeline = useChatStore((s) => s.streamingTimeline);
  const status = useChatStore((s) => s.status);
  const plan = useTaskPlanStore((s) => s.plan);

  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const initializedSessionRef = useRef<string | null>(null);
  const isNearBottomRef = useRef(true);
  const [showJumpToBottom, setShowJumpToBottom] = useState(false);
  const hasTaskPlan = !!plan.task.title || plan.steps.length > 0;

  const activeSessionId = messages[0]?.session_id ?? null;

  const scrollViewportToBottom = (behavior: ScrollBehavior = "auto") => {
    const scrollArea = scrollAreaRef.current;
    if (!scrollArea) return;
    scrollArea.scrollTo({
      top: scrollArea.scrollHeight,
      behavior,
    });
  };

  const jumpToBottom = (behavior: ScrollBehavior = "auto") => {
    scrollViewportToBottom(behavior);
    isNearBottomRef.current = true;
    setShowJumpToBottom(false);
  };

  const updateNearBottomState = () => {
    const scrollArea = scrollAreaRef.current;
    if (!scrollArea) return;
    const distanceFromBottom =
      scrollArea.scrollHeight - scrollArea.scrollTop - scrollArea.clientHeight;
    const nearBottom = distanceFromBottom < 96;
    isNearBottomRef.current = nearBottom;
    setShowJumpToBottom(!nearBottom);
  };

  useLayoutEffect(() => {
    if (!activeSessionId) return;
    if (initializedSessionRef.current !== activeSessionId) {
      initializedSessionRef.current = activeSessionId;
      scrollViewportToBottom("auto");
      isNearBottomRef.current = true;
      const timer = window.setTimeout(() => setShowJumpToBottom(false), 0);
      return () => window.clearTimeout(timer);
    }
  }, [activeSessionId, messages.length]);

  const latestStreamingTick = useMemo(() => {
    const last = streamingTimeline.at(-1);
    return last?.updatedAt ?? 0;
  }, [streamingTimeline]);

  useEffect(() => {
    if (isNearBottomRef.current) {
      scrollViewportToBottom("smooth");
      return;
    }
    const timer = window.setTimeout(() => setShowJumpToBottom(true), 0);
    return () => window.clearTimeout(timer);
  }, [streamingTimeline.length, latestStreamingTick, status]);

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

  // Build knowledge sources map: for each assistant message that follows
  // knowledge tool calls, collect the search results as structured sources.
  // Key = message id, Value = array of KnowledgeSearchResult.
  const knowledgeSourcesMap = useMemo(() => {
    const map = new Map<string, import("@/lib/types").KnowledgeSearchResult[]>();
    let pendingSources: import("@/lib/types").KnowledgeSearchResult[] = [];

    for (const msg of messages) {
      if (msg.role === "tool") {
        // Extract knowledge sources from tool_result parts
        for (const part of msg.parts) {
          if (part.type !== "tool_result") continue;
          const info = toolInfoMap.get(part.tool_call_id);
          if (!info || !info.tool_name.startsWith("knowledge_")) continue;
          if (info.tool_name !== "knowledge_search" || part.is_error) continue;
          try {
            const parsed = JSON.parse(part.output);
            if (parsed.results && Array.isArray(parsed.results)) {
              pendingSources.push(...parsed.results);
            }
          } catch { /* ignore parse errors */ }
        }
      } else if (msg.role === "assistant" && pendingSources.length > 0) {
        // Deduplicate by doc_id
        const seen = new Set<string>();
        const deduped = pendingSources.filter((s) => {
          if (seen.has(s.doc_id)) return false;
          seen.add(s.doc_id);
          return true;
        });
        map.set(msg.id, deduped);
        pendingSources = [];
      }
    }
    return map;
  }, [messages, toolInfoMap]);

  const hasRunningTool = streamingTimeline.some(
    (item) => item.kind === "tool" && item.status === "running",
  );

  // Show streaming row when: has ordered content OR agent is running/queued.
  const hasStreaming =
    streamingTimeline.length > 0 ||
    status === "running";

  const renderedRows = useMemo(() => {
    const rows: Array<
      | { type: "summary"; key: string; message: Message }
      | { type: "message"; key: string; message: Message }
      | { type: "tool-group"; key: string; messages: Message[]; toolCount: number }
    > = [];

    let index = 0;
    while (index < messages.length) {
      const message = messages[index];

      if (message.is_summary) {
        rows.push({ type: "summary", key: message.id, message });
        index += 1;
        continue;
      }

      if (!isToolHistoryOnlyMessage(message)) {
        rows.push({ type: "message", key: message.id, message });
        index += 1;
        continue;
      }

      const group: Message[] = [];
      let cursor = index;
      while (cursor < messages.length && isToolHistoryOnlyMessage(messages[cursor])) {
        group.push(messages[cursor]);
        cursor += 1;
      }

      const hasConclusionAfterGroup =
        cursor < messages.length &&
        !messages[cursor].is_summary &&
        !isToolHistoryOnlyMessage(messages[cursor]);

      if (hasConclusionAfterGroup) {
        rows.push({
          type: "tool-group",
          key: `${group[0].id}-group`,
          messages: group,
          toolCount: group.reduce(
            (count, item) =>
              count +
              item.parts.filter(
                (part) => part.type === "tool_call" || part.type === "tool_result",
              ).length,
            0,
          ),
        });
      } else {
        for (const item of group) {
          rows.push({ type: "message", key: item.id, message: item });
        }
      }

      index = cursor;
    }

    return rows;
  }, [messages]);

  return (
    <div className="relative min-h-0 min-w-0 flex-1 overflow-hidden">
      <div className="pointer-events-none absolute inset-x-0 top-0 z-10 h-8 bg-gradient-to-b from-background via-background/80 to-transparent" />
      <div className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-10 bg-gradient-to-t from-background via-background/80 to-transparent" />
      <div
        ref={scrollAreaRef}
        onScroll={updateNearBottomState}
        className="h-full overflow-y-auto px-2 py-1 md:px-4"
      >
        <div
          className={`mx-auto flex w-full max-w-[1080px] flex-col gap-3 pb-4 pt-2 ${hasTaskPlan ? "pt-24 md:pt-28" : ""}`}
        >
      {/* Persisted messages — tool-only runs collapse only after a later conclusion appears */}
      {renderedRows.map((row, rowIndex) => {
        const rowKey = `${row.key || row.type}-${rowIndex}`;

        if (row.type === "summary") {
          return <SummaryDivider key={rowKey} message={row.message} />;
        }

        if (row.type === "tool-group") {
          return (
            <CollapsedToolHistoryGroup key={rowKey} toolCount={row.toolCount}>
              {row.messages.map((message, messageIndex) => (
                <MessageBubble
                  key={`${message.id || message.seq || "message"}-${messageIndex}`}
                  message={message}
                  toolInfoMap={toolInfoMap}
                  knowledgeSources={knowledgeSourcesMap.get(message.id)}
                />
              ))}
            </CollapsedToolHistoryGroup>
          );
        }

        return (
          <MessageBubble
            key={rowKey}
            message={row.message}
            toolInfoMap={toolInfoMap}
            knowledgeSources={knowledgeSourcesMap.get(row.message.id)}
          />
        );
      })}

      {/* Queued indicator */}
      {status === "queued" && !hasStreaming && (
        <TranscriptRow>
          <div className="flex items-center gap-2 text-[12px] text-yellow-500">
            <Clock size={14} className="animate-pulse text-yellow-400" />
            <span>
              已进入队列，正在等待 worker 接手...
            </span>
          </div>
        </TranscriptRow>
      )}

      {/* Streaming content (not yet persisted) */}
      {hasStreaming && (
        <TranscriptRow>
          <div className="min-w-0 space-y-2.5">
            {(status === "running" || status === "queued") && (
              <div className="flex items-center gap-2 text-[11px] text-text-secondary">
                {status === "running" ? (
                  <RunningBars />
                ) : (
                  <>
                    <Loader2
                      size={12}
                      className="animate-spin text-accent"
                    />
                    <span>排队中</span>
                  </>
                )}
              </div>
            )}

            {streamingTimeline.length === 0 && status === "running" && !hasRunningTool && (
              <ThinkingBlock isActive content="" />
            )}

            {streamingTimeline.map((item, index) => (
              <StreamingTimelineBlock
                key={item.kind === "tool" ? `stream-tool:${item.tool_call_id}` : item.id}
                item={item}
                isLast={index === streamingTimeline.length - 1}
                status={status}
              />
            ))}

            {/* Typing cursor — shown when running and no tool is actively executing */}
            {status === "running" && !hasRunningTool && (
              <span className="typing-cursor inline-block text-accent">
                ▌
              </span>
            )}
          </div>
        </TranscriptRow>
      )}

      <div ref={bottomRef} />
        </div>
      </div>
      {showJumpToBottom && (
        <button
          type="button"
          onClick={() => jumpToBottom("smooth")}
          className="absolute bottom-4 left-1/2 z-30 inline-flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full bg-white/92 text-text-secondary shadow-[0_14px_36px_rgba(42,41,51,0.14)] backdrop-blur transition hover:bg-white hover:text-text-primary"
          title="回到最新消息"
        >
          <ArrowDown size={15} />
        </button>
      )}
    </div>
  );
}

function RunningBars() {
  return (
    <div className="flow-running-bars flex items-end gap-1">
      {Array.from({ length: 5 }).map((_, index) => (
        <span
          key={index}
          className="h-3 w-2 rounded-full bg-accent/20"
          style={{ animationDelay: `${index * 0.14}s` }}
        />
      ))}
    </div>
  );
}

function StreamingTimelineBlock({
  item,
  isLast,
  status,
}: {
  item: StreamingTimelineItem;
  isLast: boolean;
  status: SessionStatus;
}) {
  if (item.kind === "reasoning") {
    return (
      <ThinkingBlock
        isActive={status === "running" && isLast}
        content={item.content}
      />
    );
  }

  if (item.kind === "tool") {
    return (
      <ToolCallBlock
        toolName={item.tool_name}
        input={item.input}
        output={item.output}
        isError={item.is_error}
        status={item.status}
        autoCollapseOnComplete={false}
      />
    );
  }

  const parsed = parseStreamingThinking(item.content);
  return (
    <div className="space-y-2">
      {(parsed.thinkingText || parsed.isThinking) && (
        <ThinkingBlock
          isActive={status === "running" && isLast && parsed.isThinking}
          content={parsed.thinkingText}
        />
      )}
      {parsed.formalText && (
        <div className="chat-prose">
          <MessageMarkdown>{parsed.formalText}</MessageMarkdown>
        </div>
      )}
    </div>
  );
}

function isToolHistoryOnlyMessage(message: Message) {
  return (
    !message.is_summary &&
    message.role !== "user" &&
    message.parts.some(
      (part) => part.type === "tool_call" || part.type === "tool_result",
    ) &&
    message.parts.every(
      (part) =>
        part.type === "tool_call" ||
        part.type === "tool_result" ||
        part.type === "reasoning",
    )
  );
}

function CollapsedToolHistoryGroup({
  children,
  toolCount,
}: {
  children: React.ReactNode;
  toolCount: number;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="space-y-2 py-1">
      <button
        onClick={() => setOpen((prev) => !prev)}
        className="flex items-center gap-2 text-[12px] text-text-secondary transition hover:text-text-primary"
      >
        <ChevronRight
          size={12}
          className={`transition-transform ${open ? "rotate-90" : ""}`}
        />
        <Wrench size={12} />
        <span>工具记录</span>
        <span className="text-[11px] text-text-secondary/70">{toolCount}</span>
      </button>
      {open && <div className="space-y-1.5">{children}</div>}
    </div>
  );
}
