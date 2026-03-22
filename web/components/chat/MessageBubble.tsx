"use client";

import { useMemo } from "react";
import type { Message } from "@/lib/types";
import MessagePart from "./MessagePart";
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
}: {
  message: Message;
  toolInfoMap?: Map<string, { tool_name: string; input: Record<string, unknown> }>;
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

        {/* Parts */}
        <div className="space-y-1">
          {message.parts.map((part, i) => (
            <MessagePart key={i} part={part} toolNameMap={toolNameMap} toolInfoMap={toolInfoMap} />
          ))}
        </div>
      </div>
    </div>
  );
}
