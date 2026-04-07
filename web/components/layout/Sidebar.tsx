"use client";

import { useState } from "react";
import { useAuthStore } from "@/store/auth";
import { useSessionStore } from "@/store/session";
import { useWorkspaceStore } from "@/store/workspace";
import { usePanelStore } from "@/store/panel";
import SessionList from "@/components/session/SessionList";
import FileTree from "@/components/workspace/FileTree";
import UploadButton from "@/components/workspace/UploadButton";
import { useChatStore } from "@/store/chat";
import { LogOut, Bot, MessageSquare, FolderTree, Users, UserCircle, Store, Settings, PanelRight, Wand2, BookOpen } from "lucide-react";
import Link from "next/link";
import { showToast } from "@/components/ui/Toast";
import SkillPicker from "@/components/chat/SkillPicker";

type SidebarTab = "sessions" | "files";

export default function Sidebar() {
  const { user, logout } = useAuthStore();
  const [tab, setTab] = useState<SidebarTab>("sessions");

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const fileTree = useWorkspaceStore((s) => s.fileTree);
  const fetchTree = useWorkspaceStore((s) => s.fetchTree);
  const deleteFile = useWorkspaceStore((s) => s.deleteFile);
  const openFilePreview = usePanelStore((s) => s.openFilePreview);
  const togglePanel = usePanelStore((s) => s.togglePanel);
  const panelOpen = usePanelStore((s) => s.open);
  const filePreviewPath = usePanelStore((s) => s.filePreviewPath);
  const clearPanel = usePanelStore((s) => s.clearPanel);
  const insertToPrompt = useChatStore((s) => s.insertToPrompt);

  const handleDeleteFile = async (path: string) => {
    if (!currentSessionId) return;
    const fileName = path.split("/").pop() || path;
    if (!window.confirm(`Delete "${fileName}"?`)) return;
    try {
      await deleteFile(currentSessionId, path);
      // If panel is previewing this file, clear it
      if (filePreviewPath === path) {
        clearPanel();
      }
      await fetchTree(currentSessionId);
      showToast("info", `Deleted ${fileName}`);
    } catch {
      showToast("error", "Failed to delete file");
    }
  };

  return (
    <div className="flex h-full flex-col border-r border-border bg-bg-secondary">
      {/* Header */}
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Bot size={20} className="text-accent" />
        <span className="text-sm font-semibold">AgentD</span>
      </div>

      {/* Tab switcher */}
      <div className="flex border-b border-border">
        <button
          onClick={() => setTab("sessions")}
          className={`flex flex-1 items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition ${
            tab === "sessions"
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          <MessageSquare size={13} />
          Sessions
        </button>
        <button
          onClick={() => setTab("files")}
          className={`flex flex-1 items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium transition ${
            tab === "files"
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          <FolderTree size={13} />
          Files
        </button>
      </div>

      {/* Tab content */}
      <div className="flex min-h-0 flex-1 flex-col">
        {tab === "sessions" && (
          <>
            {/* Upper: Session list */}
            <div className="flex-1 overflow-y-auto px-2 py-2">
              <SessionList />
            </div>
            {/* Lower: Skill list (persistent) */}
            <div className="border-t border-border">
              <div className="flex items-center gap-1.5 px-3 py-1.5">
                <Wand2 size={11} className="text-text-secondary" />
                <span className="text-[10px] font-medium text-text-secondary">Skills</span>
              </div>
              <div className="max-h-36 overflow-y-auto px-2 pb-2">
                <SkillPicker onInsert={insertToPrompt} />
              </div>
            </div>
          </>
        )}
        {tab === "files" && (
          <div className="flex-1 overflow-y-auto px-2 py-2">
            {currentSessionId && (
              <div className="mb-2 flex items-center justify-end px-1">
                <UploadButton sessionId={currentSessionId} />
              </div>
            )}
            <FileTree
              tree={fileTree}
              selectedPath={filePreviewPath}
              onSelect={(path) => {
                if (currentSessionId) openFilePreview(currentSessionId, path);
              }}
              onDelete={(path) => handleDeleteFile(path)}
            />
          </div>
        )}
      </div>

      {/* User footer */}
      <div className="flex items-center justify-between border-t border-border px-4 py-3">
        <span className="text-xs text-text-secondary">
          {user?.username || "—"}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={togglePanel}
            className={`transition ${panelOpen ? "text-accent" : "text-text-secondary hover:text-accent"}`}
            title="Toggle work panel"
          >
            <PanelRight size={14} />
          </button>
          <Link
            href="/square"
            className="text-text-secondary transition hover:text-accent"
            title="Skill Square"
          >
            <Store size={14} />
          </Link>
          <Link
            href="/knowledge"
            className="text-text-secondary transition hover:text-accent"
            title="Knowledge Hub"
          >
            <BookOpen size={14} />
          </Link>
          <Link
            href="/user"
            className="text-text-secondary transition hover:text-accent"
            title="My Profile"
          >
            <UserCircle size={14} />
          </Link>
          {user?.role === "admin" && (
            <>
              <Link
                href="/admin"
                className="text-text-secondary transition hover:text-accent"
                title="User Management"
              >
                <Users size={14} />
              </Link>
              <Link
                href="/admin/settings"
                className="text-text-secondary transition hover:text-accent"
                title="Settings"
              >
                <Settings size={14} />
              </Link>
            </>
          )}
          <button
            onClick={logout}
            className="text-text-secondary transition hover:text-danger"
            title="Sign out"
          >
            <LogOut size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
