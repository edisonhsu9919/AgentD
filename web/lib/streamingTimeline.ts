import type { StreamingTimelineItem, StreamingToolCall } from "@/lib/types";

function makeId(prefix: string, seq: number) {
  return `stream-${prefix}:${seq}`;
}

export function appendTimelineText(
  timeline: StreamingTimelineItem[],
  text: string,
  seq: number,
  now = Date.now(),
) {
  if (!text) return { timeline, nextSeq: seq };
  const last = timeline.at(-1);
  if (last?.kind === "text") {
    return {
      timeline: [
        ...timeline.slice(0, -1),
        { ...last, content: last.content + text, updatedAt: now },
      ],
      nextSeq: seq,
    };
  }

  return {
    timeline: [
      ...timeline,
      {
        id: makeId("text", seq),
        kind: "text" as const,
        content: text,
        createdAt: now,
        updatedAt: now,
      },
    ],
    nextSeq: seq + 1,
  };
}

export function appendTimelineReasoning(
  timeline: StreamingTimelineItem[],
  text: string,
  seq: number,
  now = Date.now(),
) {
  if (!text) return { timeline, nextSeq: seq };
  const last = timeline.at(-1);
  if (last?.kind === "reasoning") {
    return {
      timeline: [
        ...timeline.slice(0, -1),
        { ...last, content: last.content + text, updatedAt: now },
      ],
      nextSeq: seq,
    };
  }

  return {
    timeline: [
      ...timeline,
      {
        id: makeId("reasoning", seq),
        kind: "reasoning" as const,
        content: text,
        createdAt: now,
        updatedAt: now,
      },
    ],
    nextSeq: seq + 1,
  };
}

export function upsertTimelineToolStart(
  timeline: StreamingTimelineItem[],
  toolCall: StreamingToolCall,
  seq: number,
  now = Date.now(),
) {
  const index = timeline.findIndex(
    (item) =>
      item.kind === "tool" && item.tool_call_id === toolCall.tool_call_id,
  );
  if (index >= 0) {
    return {
      timeline: timeline.map((item, itemIndex) =>
        itemIndex === index && item.kind === "tool"
          ? { ...item, ...toolCall, updatedAt: now }
          : item,
      ),
      nextSeq: seq,
    };
  }

  return {
    timeline: [
      ...timeline,
      {
        id: makeId(`tool:${toolCall.tool_call_id || seq}`, seq),
        kind: "tool" as const,
        ...toolCall,
        createdAt: now,
        updatedAt: now,
      },
    ],
    nextSeq: seq + 1,
  };
}

export function upsertTimelineToolResult(
  timeline: StreamingTimelineItem[],
  result: {
    tool_call_id: string;
    tool_name: string;
    output: string;
    is_error: boolean;
  },
  seq: number,
  now = Date.now(),
) {
  const status = result.is_error ? "error" as const : "completed" as const;
  const index = timeline.findIndex(
    (item) =>
      item.kind === "tool" && item.tool_call_id === result.tool_call_id,
  );

  if (index >= 0) {
    return {
      timeline: timeline.map((item, itemIndex) =>
        itemIndex === index && item.kind === "tool"
          ? {
              ...item,
              tool_name: item.tool_name || result.tool_name,
              status,
              output: result.output,
              is_error: result.is_error,
              updatedAt: now,
            }
          : item,
      ),
      nextSeq: seq,
    };
  }

  return {
    timeline: [
      ...timeline,
      {
        id: makeId(`orphan-tool:${result.tool_call_id || seq}`, seq),
        kind: "tool" as const,
        tool_call_id: result.tool_call_id,
        tool_name: result.tool_name || "unknown_tool",
        input: {},
        status,
        output: result.output,
        is_error: result.is_error,
        createdAt: now,
        updatedAt: now,
      },
    ],
    nextSeq: seq + 1,
  };
}
