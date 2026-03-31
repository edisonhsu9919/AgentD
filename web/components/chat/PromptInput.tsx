"use client";

import { useState, KeyboardEvent } from "react";
import { useChatStore } from "@/store/chat";
import { useSessionStore } from "@/store/session";
import { useTaskPlanStore } from "@/store/taskPlan";
import { Send, Square } from "lucide-react";
import { ApiRequestError } from "@/lib/api";

interface PromptInputProps {
  sessionId: string;
}

export default function PromptInput({ sessionId }: PromptInputProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const status = useChatStore((s) => s.status);
  const sendPrompt = useChatStore((s) => s.sendPrompt);
  const cancelTask = useChatStore((s) => s.cancelTask);
  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);
  const clearTaskPlan = useTaskPlanStore((s) => s.clearTaskPlan);

  const canSend = status === "idle" || status === "error";
  const canAbort = status === "running" || status === "queued" || status === "waiting";

  const handleSend = async () => {
    if (!text.trim() || !canSend || !sessionId) return;

    const content = text.trim();
    setText("");
    setError(null);
    setSending(true);

    // Optimistic: update session store status so header badge shows "running"
    updateSessionStatus(sessionId, "running");

    try {
      await sendPrompt(sessionId, content);
    } catch (err) {
      // Revert session status on error
      updateSessionStatus(sessionId, "idle");
      if (err instanceof ApiRequestError && err.status === 409) {
        setError(err.message);
      } else {
        setError("Failed to send message");
      }
      setText(content); // Restore text on error
    } finally {
      setSending(false);
    }
  };

  const handleAbort = async () => {
    try {
      await cancelTask(sessionId);
      clearTaskPlan();
      updateSessionStatus(sessionId, "idle");
    } catch {
      // ignore
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Ignore Enter during IME composition (Chinese, Japanese, Korean input)
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="border-t border-border px-4 py-3">
      {error && (
        <div className="mb-2 rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
          {error}
        </div>
      )}
      <div className="flex items-end gap-2">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={
            canSend
              ? "Type a message..."
              : status === "queued"
                ? "Queued — waiting for worker..."
                : status === "waiting"
                  ? "Waiting for permission approval..."
                  : "Agent is running..."
          }
          disabled={!canSend || sending}
          rows={1}
          className="flex-1 resize-none rounded border border-border bg-bg-primary px-3 py-2 text-sm text-text-primary outline-none focus:border-accent disabled:opacity-50"
          style={{ minHeight: "40px", maxHeight: "120px" }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = "auto";
            target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
          }}
        />

        {canAbort ? (
          <button
            onClick={handleAbort}
            className="rounded bg-danger px-3 py-2 text-sm text-white transition hover:bg-danger/80"
            title="Stop"
          >
            <Square size={16} />
          </button>
        ) : (
          <button
            onClick={handleSend}
            disabled={!canSend || !text.trim() || sending}
            className="rounded bg-accent px-3 py-2 text-sm text-white transition hover:bg-accent-hover disabled:opacity-50"
            title="Send"
          >
            <Send size={16} />
          </button>
        )}
      </div>
    </div>
  );
}
