"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  KeyboardEvent,
  type ReactNode,
} from "react";
import { useChatStore } from "@/store/chat";
import { useSessionStore } from "@/store/session";
import { useTaskPlanStore } from "@/store/taskPlan";
import { Bot, ChevronUp, Eye, Send, Square } from "lucide-react";
import { apiFetch, ApiRequestError } from "@/lib/api";
import type { RuntimeModelConfigData, VLMConfigResponse } from "@/lib/types";
import PolicySwitcher from "@/components/policy/PolicySwitcher";

interface PromptInputProps {
  sessionId: string;
  contextUsageRatio?: number | null;
  promptTokens?: number | null;
  windowLimit?: number | null;
}

export default function PromptInput({
  sessionId,
  contextUsageRatio,
  promptTokens,
  windowLimit,
}: PromptInputProps) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runtimeModel, setRuntimeModel] =
    useState<RuntimeModelConfigData | null>(null);
  const [vlmConfig, setVlmConfig] = useState<VLMConfigResponse | null>(null);
  const [modelMenuOpen, setModelMenuOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const status = useChatStore((s) => s.status);
  const sendPrompt = useChatStore((s) => s.sendPrompt);
  const runCommand = useChatStore((s) => s.runCommand);
  const cancelTask = useChatStore((s) => s.cancelTask);

  // Consume pending insert from skill picker
  const pendingInsert = useChatStore((s) => s.pendingInsert);
  const clearPendingInsert = useChatStore((s) => s.clearPendingInsert);
  useEffect(() => {
    if (pendingInsert) {
      setText((prev) => {
        const target = textareaRef.current;
        if (target && document.activeElement === target) {
          const start = target.selectionStart ?? prev.length;
          const end = target.selectionEnd ?? prev.length;
          return `${prev.slice(0, start)}${pendingInsert}${prev.slice(end)}`;
        }
        return prev.trim() ? `${prev}\n${pendingInsert}` : pendingInsert;
      });
      window.setTimeout(() => {
        textareaRef.current?.focus();
      }, 0);
      clearPendingInsert();
    }
  }, [pendingInsert, clearPendingInsert]);
  const updateSessionStatus = useSessionStore((s) => s.updateSessionStatus);
  const clearTaskPlan = useTaskPlanStore((s) => s.clearTaskPlan);

  useEffect(() => {
    let cancelled = false;
    async function fetchRuntimeModels() {
      try {
        const [llm, vlm] = await Promise.all([
          apiFetch<RuntimeModelConfigData>("/runtime/model-config"),
          apiFetch<VLMConfigResponse>("/runtime/vlm-config"),
        ]);
        if (!cancelled) {
          setRuntimeModel(llm);
          setVlmConfig(vlm);
        }
      } catch {
        if (!cancelled) {
          setRuntimeModel(null);
          setVlmConfig(null);
        }
      }
    }
    fetchRuntimeModels();
    return () => {
      cancelled = true;
    };
  }, []);

  const canSend = status === "idle" || status === "error";
  const canAbort = status === "running" || status === "queued" || status === "waiting";

  const handleSend = async () => {
    if (!text.trim() || !canSend || !sessionId) return;

    const content = text.trim();
    const isCommand = isSlashSkillLoadCommand(content);
    setText("");
    setError(null);
    setSending(true);

    // Optimistic: only model prompts enqueue a run and move session status.
    if (!isCommand) {
      updateSessionStatus(sessionId, "running");
    }

    try {
      if (isCommand) {
        await runCommand(sessionId, content);
      } else {
        await sendPrompt(sessionId, content);
      }
    } catch (err) {
      // Revert session status on error
      if (!isCommand) {
        updateSessionStatus(sessionId, "idle");
      }
      if (err instanceof ApiRequestError) {
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
    <div className="relative z-[70] bg-white/96 px-4 py-4 md:px-6">
      {error && (
        <div className="mb-3 rounded-[18px] bg-danger/10 px-4 py-3 text-sm text-danger">
          {error}
        </div>
      )}
      <div className="mx-auto w-full max-w-[1080px]">
        <div className="flex items-end gap-3">
          <div className="flex min-w-0 flex-1 flex-col rounded-[28px] bg-bg-primary px-4 py-3">
            <textarea
              ref={textareaRef}
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                canSend
                  ? "输入你的任务目标、约束或需要处理的文件内容..."
                  : status === "queued"
                    ? "任务已进入队列，正在等待 worker 接手..."
                    : status === "waiting"
                      ? "等待权限批准后继续执行..."
                      : status === "subtask_waiting"
                        ? "等待子任务完成后继续汇总结果..."
                  : "AgentD 正在执行中..."
              }
              disabled={!canSend || sending}
              rows={1}
              className="min-h-[42px] w-full resize-none border-0 bg-transparent px-0 py-0 text-[15px] leading-[1.65rem] text-text-primary outline-none placeholder:text-text-secondary/42 disabled:opacity-60"
              style={{ maxHeight: "200px" }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = "auto";
                target.style.height = `${Math.min(target.scrollHeight, 200)}px`;
              }}
            />

            <div className="mt-2 flex items-center justify-between gap-3 pt-2.5">
              <div className="flex min-w-0 items-center gap-3">
                <PolicySwitcher sessionId={sessionId} />
                <ContextUsageBars
                  ratio={contextUsageRatio}
                  promptTokens={promptTokens}
                  windowLimit={windowLimit}
                />
                <RuntimeModelPill
                  runtimeModel={runtimeModel}
                  vlmConfig={vlmConfig}
                  open={modelMenuOpen}
                  onToggle={() => setModelMenuOpen((value) => !value)}
                />
              </div>

              <div className="flex shrink-0 items-center justify-end">
                {canAbort ? (
                  <button
                    onClick={handleAbort}
                    className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-danger text-white transition hover:opacity-90"
                    title="停止"
                  >
                    <Square size={15} />
                  </button>
                ) : (
                  <div className="group relative shrink-0">
                    <button
                      onClick={handleSend}
                      disabled={!canSend || !text.trim() || sending}
                      className="inline-flex h-11 w-11 items-center justify-center rounded-full bg-white text-text-secondary shadow-[0_10px_24px_rgba(42,41,51,0.08)] transition hover:bg-white/90 hover:text-text-primary disabled:opacity-50"
                      title="发送"
                    >
                      <Send size={16} />
                    </button>
                    <div className="ui-tooltip pointer-events-none absolute bottom-[calc(100%+0.6rem)] right-0 hidden whitespace-nowrap group-hover:block">
                      Enter 发送，Shift + Enter 换行
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function isSlashSkillLoadCommand(value: string) {
  return /^\/skill\s+load(?:\s+|$)/i.test(value.trim());
}

function RuntimeModelPill({
  runtimeModel,
  vlmConfig,
  open,
  onToggle,
}: {
  runtimeModel: RuntimeModelConfigData | null;
  vlmConfig: VLMConfigResponse | null;
  open: boolean;
  onToggle: () => void;
}) {
  const llm = runtimeModel?.active_config ?? null;
  const llmName = llm?.name || llm?.model_id || "模型信息";
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [menuPosition, setMenuPosition] = useState<{
    left: number;
    bottom: number;
  } | null>(null);

  const updateMenuPosition = useCallback(() => {
    if (!buttonRef.current || typeof window === "undefined") return;

    const rect = buttonRef.current.getBoundingClientRect();
    const menuWidth = Math.min(380, window.innerWidth * 0.82);
    const margin = 16;
    const left = Math.min(
      Math.max(rect.left, margin),
      window.innerWidth - menuWidth - margin,
    );
    const bottom = Math.max(window.innerHeight - rect.top + 10, 96);

    setMenuPosition({ left, bottom });
  }, []);

  useEffect(() => {
    if (!open) return;

    updateMenuPosition();
    window.addEventListener("resize", updateMenuPosition);
    window.addEventListener("scroll", updateMenuPosition, true);
    return () => {
      window.removeEventListener("resize", updateMenuPosition);
      window.removeEventListener("scroll", updateMenuPosition, true);
    };
  }, [open, updateMenuPosition]);

  return (
    <div className="relative min-w-0 shrink">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => {
          updateMenuPosition();
          onToggle();
        }}
        className="flex h-8 max-w-[180px] items-center gap-1.5 rounded-full bg-white/55 px-2.5 text-[11px] text-text-secondary/80 transition hover:bg-white/85 hover:text-text-primary"
        title={llmName}
      >
        <Bot size={12} className="shrink-0 text-accent/75" />
        <span className="min-w-0 truncate">{llmName}</span>
        <ChevronUp
          size={12}
          className={`shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>

      {open && (
        <div
          className="fixed z-[140] w-[min(82vw,380px)] rounded-[18px] bg-white/97 p-3 shadow-[0_22px_60px_rgba(42,41,51,0.16)] backdrop-blur"
          style={
            menuPosition
              ? { left: menuPosition.left, bottom: menuPosition.bottom }
              : undefined
          }
        >
          <div className="mb-2 text-[11px] font-medium text-text-secondary">
            当前默认模型
          </div>
          <ModelInfoBlock
            icon={<Bot size={13} />}
            label="LLM"
            name={llm?.name || "未配置"}
            modelId={llm?.model_id || "—"}
            source={runtimeModel?.source || "unknown"}
            baseUrl={llm?.base_url || "—"}
            contextWindow={llm?.context_window}
          />
          <ModelInfoBlock
            icon={<Eye size={13} />}
            label="VLM"
            name={vlmConfig?.active_config?.name || (vlmConfig?.available ? "未命名视觉模型" : "未配置")}
            modelId={vlmConfig?.active_config?.model_id || "—"}
            source={vlmConfig?.source || "unavailable"}
            baseUrl={vlmConfig?.active_config?.base_url || "—"}
            visionFlags={
              vlmConfig?.active_config
                ? [
                    vlmConfig.active_config.supports_vision ? "vision" : null,
                    vlmConfig.active_config.supports_http_image_url ? "http image" : null,
                    vlmConfig.active_config.supports_data_uri_image ? "data URI" : null,
                  ].filter(Boolean) as string[]
                : []
            }
          />
        </div>
      )}
    </div>
  );
}

function ModelInfoBlock({
  icon,
  label,
  name,
  modelId,
  source,
  baseUrl,
  contextWindow,
  visionFlags = [],
}: {
  icon: ReactNode;
  label: string;
  name: string;
  modelId: string;
  source: string;
  baseUrl: string;
  contextWindow?: number | null;
  visionFlags?: string[];
}) {
  return (
    <div className="mb-2 last:mb-0 rounded-[14px] bg-bg-primary/65 p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent/10 text-accent">
          {icon}
        </span>
        <span className="text-xs font-semibold text-text-primary">{label}</span>
        <span className="ml-auto rounded-full bg-white/70 px-2 py-0.5 text-[10px] text-text-secondary">
          {source === "db_default" ? "数据库默认" : source === "env_fallback" ? "环境变量回退" : source}
        </span>
      </div>
      <div className="space-y-1 text-[11px]">
        <InfoLine label="名称" value={name} />
        <InfoLine label="Model ID" value={modelId} />
        <InfoLine label="Base URL" value={baseUrl} />
        {contextWindow != null && (
          <InfoLine label="Context" value={contextWindow.toLocaleString()} />
        )}
        {visionFlags.length > 0 && (
          <InfoLine label="能力" value={visionFlags.join(" · ")} />
        )}
      </div>
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-3">
      <span className="w-16 shrink-0 text-text-secondary">{label}</span>
      <span className="min-w-0 flex-1 break-all text-text-primary">{value}</span>
    </div>
  );
}

function ContextUsageBars({
  ratio,
  promptTokens,
  windowLimit,
}: {
  ratio?: number | null;
  promptTokens?: number | null;
  windowLimit?: number | null;
}) {
  const safeRatio = Math.max(0, Math.min(ratio ?? 0, 1));
  const filledCount = Math.max(1, Math.ceil(safeRatio * 5));
  const showTooltip = promptTokens != null && windowLimit != null;

  return (
    <div
      className="group relative flex items-center gap-1.5"
      title={
        showTooltip
          ? `Prompt ${promptTokens?.toLocaleString()} / ${windowLimit?.toLocaleString()}`
          : "上下文窗口占比"
      }
    >
      {Array.from({ length: 5 }).map((_, index) => {
        const filled = index < filledCount && safeRatio > 0;
        const colorClass = !filled
          ? "bg-bg-tertiary"
          : filledCount >= 5
            ? "bg-danger"
            : filledCount >= 4
              ? "bg-warning-foreground"
              : "bg-success";

        return (
          <span
            key={index}
            className={`h-4 w-2.5 rounded-full transition ${colorClass}`}
          />
        );
      })}
      {showTooltip && (
        <div className="ui-tooltip pointer-events-none absolute bottom-[calc(100%+0.6rem)] left-1/2 hidden -translate-x-1/2 whitespace-nowrap group-hover:block">
          Prompt {promptTokens?.toLocaleString()} / {windowLimit?.toLocaleString()} ({(safeRatio * 100).toFixed(1)}%)
        </div>
      )}
    </div>
  );
}
