"use client";

import {
  X,
  Tag,
  Hash,
  FileText,
  Folder,
  File,
  CheckCircle,
  XCircle,
  Download,
  Trash2,
  Loader2,
  AlertTriangle,
} from "lucide-react";
import SkillIcon from "@/components/shared/SkillIcon";
import MessageMarkdown from "@/components/chat/MessageMarkdown";
import type { SquareDetailResponse, SquareTreeNode } from "@/lib/types";

interface SkillDetailDrawerProps {
  detail: SquareDetailResponse | null;
  loading: boolean;
  onClose: () => void;
  /** Whether the current user has this skill enabled (user/admin read-only mode) */
  isEnabled?: boolean;
  /** Square mode: callback when user selects a different version */
  onVersionChange?: (name: string, version: string) => void;
  /** Square mode: install callback with selected_skill_id */
  onInstall?: (skillId: string) => void;
  /** Square mode: uninstall callback with selected_skill_id */
  onUninstall?: (skillId: string) => void;
  /** Square mode: action in progress */
  actionLoading?: boolean;
  /** Square mode: action error message */
  actionError?: string | null;
  /** Admin mode: global delete callback (deletes skill from entire system) */
  onDeleteGlobal?: (skillId: string) => Promise<void>;
}

function TreeNodeItem({ node, depth = 0 }: { node: SquareTreeNode; depth?: number }) {
  return (
    <>
      <div
        className="flex items-center gap-1.5 py-0.5 text-xs text-text-secondary"
        style={{ paddingLeft: `${depth * 14}px` }}
      >
        {node.type === "dir" ? (
          <Folder size={12} className="text-accent/70" />
        ) : (
          <File size={12} />
        )}
        <span className={node.type === "dir" ? "text-text-primary" : ""}>
          {node.name}
        </span>
      </div>
      {node.children?.map((child) => (
        <TreeNodeItem key={child.path} node={child} depth={depth + 1} />
      ))}
    </>
  );
}

export default function SkillDetailDrawer({
  detail,
  loading,
  onClose,
  isEnabled,
  onVersionChange,
  onInstall,
  onUninstall,
  actionLoading,
  actionError,
  onDeleteGlobal,
}: SkillDetailDrawerProps) {
  const isSquareMode = !!(onInstall || onUninstall);

  if (loading) {
    return (
      <div className="flex h-full flex-col rounded-[18px] bg-white/96 shadow-[0_24px_70px_rgba(42,41,51,0.12)] backdrop-blur">
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-xs font-medium text-text-secondary">
            Loading...
          </span>
          <button
            onClick={onClose}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-white/70"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex flex-1 items-center justify-center">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full flex-col rounded-[18px] bg-white/96 shadow-[0_24px_70px_rgba(42,41,51,0.12)] backdrop-blur">
        <div className="flex items-center justify-between px-4 py-3">
          <span className="text-xs font-medium text-text-secondary">
            Skill Detail
          </span>
          <button
            onClick={onClose}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-white/70"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex flex-1 items-center justify-center text-xs text-text-secondary">
          Detail not available
        </div>
      </div>
    );
  }

  // Install/uninstall button logic (J3 brief §5.1)
  const viewingOtherVersion =
    detail.installed && detail.installed_version !== detail.selected_version;
  const actionControls = isSquareMode ? (
    <div className="ml-auto flex shrink-0 items-center gap-2">
      {!detail.installed ? (
        <button
          onClick={() => onInstall?.(detail.selected_skill_id)}
          disabled={actionLoading || detail.enabled === false}
          className="inline-flex h-8 w-[72px] items-center justify-center gap-1 rounded-full bg-accent text-[11px] font-medium text-white shadow-[0_12px_24px_rgba(139,92,246,0.16)] transition hover:bg-accent/90 disabled:opacity-50"
        >
          {actionLoading ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Download size={12} />
          )}
          安装
        </button>
      ) : (
        <button
          onClick={() => onUninstall?.(detail.selected_skill_id)}
          disabled={actionLoading}
          className="inline-flex h-8 w-[72px] items-center justify-center gap-1 rounded-full bg-warning/70 text-[11px] font-medium text-warning-foreground transition hover:bg-warning disabled:opacity-50"
        >
          {actionLoading ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Trash2 size={12} />
          )}
          卸载
        </button>
      )}

      {onDeleteGlobal && (
        <button
          onClick={() => {
            const ok = window.confirm(
              `Delete "${detail.name}" from system?\n\nThis will:\n- Remove the skill from the system catalog\n- Uninstall it from ALL users\n- This action cannot be undone`,
            );
            if (ok) onDeleteGlobal(detail.selected_skill_id);
          }}
          disabled={actionLoading}
          className="inline-flex h-8 w-[72px] items-center justify-center gap-1 rounded-full bg-danger/10 text-[11px] font-medium text-danger transition hover:bg-danger/20 disabled:opacity-50"
        >
          <Trash2 size={12} />
          删除
        </button>
      )}
    </div>
  ) : null;

  return (
    <div className="flex h-full flex-col rounded-[18px] bg-white/96 shadow-[0_24px_70px_rgba(42,41,51,0.12)] backdrop-blur">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex min-w-0 items-center gap-2.5">
          <SkillIcon icon={detail.icon} skillName={detail.name} size={30} iconSize={14} />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-text-primary">{detail.name}</div>
            <div className="text-[10px] text-text-secondary">Skill detail</div>
          </div>
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded-full bg-bg-primary p-1.5 text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 space-y-4 overflow-y-auto px-4 pb-4">
        {/* Meta */}
        <div className="space-y-3 rounded-[16px] bg-bg-primary/55 p-3">
          <p className="text-xs text-text-secondary">{detail.description}</p>

          {/* Version selector (Square mode) or static version badge */}
          {isSquareMode && detail.versions.length > 1 ? (
            <div className="flex flex-wrap items-center gap-2">
              <select
                value={detail.selected_version}
                onChange={(e) =>
                  onVersionChange?.(detail.name, e.target.value)
                }
                className="rounded-full bg-white/70 px-2.5 py-1 text-[11px] text-text-primary outline-none transition focus:bg-white/90 focus:shadow-[0_0_0_2px_rgba(139,92,246,0.16)]"
              >
                {detail.versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}
                    {detail.installed_version === v.version ? " (installed)" : ""}
                  </option>
                ))}
              </select>
              <span className="flex items-center gap-0.5 rounded-full bg-white/70 px-2 py-1 text-[10px] text-text-secondary">
                <Hash size={9} />
                {detail.usage_count_total} uses
              </span>
              {actionControls}
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2 text-[10px]">
              <span className="flex items-center gap-1 rounded-full bg-white/70 px-2 py-0.5 text-text-secondary">
                v{detail.selected_version}
              </span>
              {detail.versions.length > 1 && (
                <span className="text-text-secondary">
                  ({detail.versions.length} versions)
                </span>
              )}
              <span className="flex items-center gap-0.5 rounded-full bg-white/70 px-2 py-0.5 text-text-secondary">
                <Hash size={9} />
                {detail.usage_count_total} uses
              </span>
              {actionControls}
            </div>
          )}

          {/* Tags */}
          {detail.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {detail.tags.map((tag) => (
                <span
                  key={tag}
                  className="flex items-center gap-0.5 rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent"
                >
                  <Tag size={8} />
                  {tag}
                </span>
              ))}
            </div>
          )}

          {/* Enabled / disabled status */}
          {isSquareMode ? (
            detail.enabled === false && (
              <div className="flex items-center gap-1.5 text-xs">
                <XCircle size={12} className="text-danger" />
                <span className="text-danger">Disabled by admin</span>
              </div>
            )
          ) : (
            isEnabled !== undefined && (
              <div className="flex items-center gap-1.5 text-xs">
                {isEnabled ? (
                  <>
                    <CheckCircle size={12} className="text-success" />
                    <span className="text-success">Enabled</span>
                  </>
                ) : (
                  <>
                    <XCircle size={12} className="text-danger" />
                    <span className="text-danger">Disabled</span>
                  </>
                )}
              </div>
            )
          )}

          {/* Install / Uninstall status (Square mode) */}
          {isSquareMode && (
            <div className="space-y-1.5">
              {/* Case: viewing other version while one is installed */}
              {viewingOtherVersion && (
                <div className="flex items-center gap-1.5 rounded-[12px] bg-warning/10 px-2 py-1.5 text-[11px] text-warning">
                  <AlertTriangle size={12} />
                  <span>
                    当前已安装 v{detail.installed_version}，请先卸载。
                  </span>
                </div>
              )}

              {/* Action error */}
              {actionError && (
                <div className="flex items-center gap-1.5 rounded-[12px] bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
                  <XCircle size={12} className="shrink-0" />
                  <span>{actionError}</span>
                </div>
              )}

            </div>
          )}
        </div>

        {/* README */}
        {detail.readme_content && (
          <div>
            <h4 className="mb-1.5 flex items-center gap-1 text-xs font-medium text-text-secondary">
              <FileText size={12} />
              SKILL.md
            </h4>
            <div className="max-h-[420px] overflow-auto rounded-[16px] bg-bg-primary/55 p-3">
              <div className="chat-prose">
                <MessageMarkdown>{detail.readme_content}</MessageMarkdown>
              </div>
            </div>
          </div>
        )}

        {/* Package tree */}
        {detail.tree.length > 0 && (
          <div>
            <h4 className="mb-1.5 flex items-center gap-1 text-xs font-medium text-text-secondary">
              <Folder size={12} />
              Package Tree
            </h4>
            <div className="rounded-[16px] bg-bg-primary/55 p-3">
              {detail.tree.map((node) => (
                <TreeNodeItem key={node.path} node={node} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
