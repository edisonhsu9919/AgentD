"use client";

import { useState } from "react";
import type { FileNode } from "@/lib/types";
import {
  File,
  Folder,
  FolderOpen,
  ChevronRight,
  ChevronDown,
  Trash2,
} from "lucide-react";

interface FileTreeNodeProps {
  node: FileNode;
  depth: number;
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onDelete?: (path: string) => void;
}

function FileTreeNode({
  node,
  depth,
  selectedPath,
  onSelect,
  onDelete,
}: FileTreeNodeProps) {
  const [expanded, setExpanded] = useState(depth < 2);
  const isDir = node.type === "dir";
  const isSelected = node.path === selectedPath;

  if (isDir) {
    return (
      <div>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex w-full items-center gap-1.5 rounded-[16px] px-2 py-2 text-sm text-text-secondary transition hover:bg-bg-primary hover:text-text-primary"
          style={{ paddingLeft: `${depth * 12 + 8}px` }}
        >
          {expanded ? (
            <ChevronDown size={12} />
          ) : (
            <ChevronRight size={12} />
          )}
          {expanded ? (
            <FolderOpen size={14} className="text-accent" />
          ) : (
            <Folder size={14} className="text-accent" />
          )}
          <span className="truncate">{node.name}</span>
        </button>
        {expanded && node.children && (
          <div>
            {node.children.map((child) => (
              <FileTreeNode
                key={child.path}
                node={child}
                depth={depth + 1}
                selectedPath={selectedPath}
                onSelect={onSelect}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  return (
    <div
      className={`group flex w-full items-center rounded text-sm transition ${
        isSelected
          ? "bg-accent/8 text-accent"
          : "text-text-secondary hover:bg-bg-primary hover:text-text-primary"
      }`}
      style={{ paddingLeft: `${depth * 12 + 20}px` }}
    >
      <button
        onClick={() => onSelect(node.path)}
        className="flex min-w-0 flex-1 items-center gap-1.5 py-1"
      >
        <File size={14} />
        <span className="truncate">{node.name}</span>
      </button>
      {onDelete && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onDelete(node.path);
          }}
          className="mr-1 shrink-0 rounded-full p-1 opacity-0 transition hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
          title="删除文件"
        >
          <Trash2 size={12} />
        </button>
      )}
    </div>
  );
}

interface FileTreeProps {
  tree: FileNode[];
  selectedPath: string | null;
  onSelect: (path: string) => void;
  onDelete?: (path: string) => void;
}

export default function FileTree({
  tree,
  selectedPath,
  onSelect,
  onDelete,
}: FileTreeProps) {
  if (tree.length === 0) {
    return (
      <div className="px-2 py-5 text-center text-sm text-text-secondary/70">
        当前会话还没有文件。
      </div>
    );
  }

  return (
    <div className="space-y-0.5">
      {tree.map((node) => (
        <FileTreeNode
          key={node.path}
          node={node}
          depth={0}
          selectedPath={selectedPath}
          onSelect={onSelect}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
