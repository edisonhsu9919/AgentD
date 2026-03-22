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
}: SkillDetailDrawerProps) {
  const isSquareMode = !!(onInstall || onUninstall);

  if (loading) {
    return (
      <div className="flex h-full flex-col border-l border-border bg-bg-secondary">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-xs font-medium text-text-secondary">
            Loading...
          </span>
          <button
            onClick={onClose}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
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
      <div className="flex h-full flex-col border-l border-border bg-bg-secondary">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <span className="text-xs font-medium text-text-secondary">
            Skill Detail
          </span>
          <button
            onClick={onClose}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
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
  const viewingInstalledVersion =
    detail.installed && detail.installed_version === detail.selected_version;
  const viewingOtherVersion =
    detail.installed && detail.installed_version !== detail.selected_version;

  return (
    <div className="flex h-full flex-col border-l border-border bg-bg-secondary">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <SkillIcon icon={detail.icon} skillName={detail.name} size={24} iconSize={12} />
          <span className="truncate text-xs font-medium">{detail.name}</span>
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
        >
          <X size={14} />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto p-3 space-y-4">
        {/* Meta */}
        <div className="space-y-2">
          <p className="text-xs text-text-secondary">{detail.description}</p>

          {/* Version selector (Square mode) or static version badge */}
          {isSquareMode && detail.versions.length > 1 ? (
            <div className="flex items-center gap-2">
              <select
                value={detail.selected_version}
                onChange={(e) =>
                  onVersionChange?.(detail.name, e.target.value)
                }
                className="rounded border border-border bg-bg-primary px-2 py-1 text-[11px] text-text-primary outline-none focus:border-accent"
              >
                {detail.versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    v{v.version}
                    {detail.installed_version === v.version ? " (installed)" : ""}
                  </option>
                ))}
              </select>
              <span className="flex items-center gap-0.5 text-[10px] text-text-secondary">
                <Hash size={9} />
                {detail.usage_count_total} uses
              </span>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2 text-[10px]">
              <span className="flex items-center gap-1 rounded bg-bg-tertiary px-1.5 py-0.5 text-text-secondary">
                v{detail.selected_version}
              </span>
              {detail.versions.length > 1 && (
                <span className="text-text-secondary">
                  ({detail.versions.length} versions)
                </span>
              )}
              <span className="flex items-center gap-0.5 text-text-secondary">
                <Hash size={9} />
                {detail.usage_count_total} uses
              </span>
            </div>
          )}

          {/* Tags */}
          {detail.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {detail.tags.map((tag) => (
                <span
                  key={tag}
                  className="flex items-center gap-0.5 rounded bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent"
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

          {/* Install / Uninstall button (Square mode) */}
          {isSquareMode && (
            <div className="space-y-1.5">
              {/* Case: viewing other version while one is installed */}
              {viewingOtherVersion && (
                <div className="flex items-center gap-1.5 rounded bg-warning/10 px-2 py-1.5 text-[11px] text-warning">
                  <AlertTriangle size={12} />
                  <span>
                    v{detail.installed_version} is currently installed. Uninstall it first.
                  </span>
                </div>
              )}

              {!detail.installed ? (
                // Not installed → Install
                <button
                  onClick={() => onInstall?.(detail.selected_skill_id)}
                  disabled={actionLoading || detail.enabled === false}
                  className="flex w-full items-center justify-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent/90 disabled:opacity-50"
                >
                  {actionLoading ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Download size={13} />
                  )}
                  Install
                </button>
              ) : (
                // Installed → Uninstall (whether same or different version)
                <button
                  onClick={() => onUninstall?.(detail.selected_skill_id)}
                  disabled={actionLoading}
                  className="flex w-full items-center justify-center gap-1.5 rounded bg-danger/10 px-3 py-1.5 text-xs font-medium text-danger transition hover:bg-danger/20 disabled:opacity-50"
                >
                  {actionLoading ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Trash2 size={13} />
                  )}
                  {viewingOtherVersion
                    ? `Uninstall v${detail.installed_version}`
                    : "Uninstall"}
                </button>
              )}

              {/* Action error */}
              {actionError && (
                <div className="flex items-center gap-1.5 rounded bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
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
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-border bg-bg-primary p-2.5 text-[11px] text-text-primary">
              {detail.readme_content}
            </pre>
          </div>
        )}

        {/* Package tree */}
        {detail.tree.length > 0 && (
          <div>
            <h4 className="mb-1.5 flex items-center gap-1 text-xs font-medium text-text-secondary">
              <Folder size={12} />
              Package Tree
            </h4>
            <div className="rounded border border-border bg-bg-primary p-2">
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
