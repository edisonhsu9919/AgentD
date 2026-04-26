"use client";

import { useEffect, useState } from "react";
import { useAuthStore } from "@/store/auth";
import { useChatStore } from "@/store/chat";
import { useSessionStore } from "@/store/session";
import { useUserProfileStore } from "@/store/userProfile";
import { useWorkspaceStore } from "@/store/workspace";
import { usePanelStore } from "@/store/panel";
import SkillPicker from "@/components/chat/SkillPicker";
import SessionList from "@/components/session/SessionList";
import FileTree from "@/components/workspace/FileTree";
import UploadButton from "@/components/workspace/UploadButton";
import { MessageSquare, FolderTree } from "lucide-react";
import { showToast } from "@/components/ui/Toast";

type SidebarTab = "sessions" | "files";

export default function Sidebar() {
  const user = useAuthStore((s) => s.user);
  const insertToPrompt = useChatStore((s) => s.insertToPrompt);
  const [tab, setTab] = useState<SidebarTab>("sessions");
  const profile = useUserProfileStore((s) => s.profile);
  const fetchProfile = useUserProfileStore((s) => s.fetchProfile);

  const currentSessionId = useSessionStore((s) => s.currentSessionId);
  const fileTree = useWorkspaceStore((s) => s.fileTree);
  const fetchTree = useWorkspaceStore((s) => s.fetchTree);
  const deleteFile = useWorkspaceStore((s) => s.deleteFile);
  const openFilePreview = usePanelStore((s) => s.openFilePreview);
  const filePreviewPath = usePanelStore((s) => s.filePreviewPath);
  const clearPanel = usePanelStore((s) => s.clearPanel);
  const enabledSkills =
    profile?.installed_skills?.filter((skill) => skill.is_enabled) || [];

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const handleDeleteFile = async (path: string) => {
    if (!currentSessionId) return;
    const fileName = path.split("/").pop() || path;
    if (!window.confirm(`确认删除「${fileName}」吗？`)) return;
    try {
      await deleteFile(currentSessionId, path);
      // If panel is previewing this file, clear it
      if (filePreviewPath === path) {
        clearPanel();
      }
      await fetchTree(currentSessionId);
      showToast("info", `已删除 ${fileName}`);
    } catch {
      showToast("error", "删除文件失败");
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col border-r border-border bg-white/92 backdrop-blur">
      <div className="px-4 py-4">
        <div className="relative grid grid-cols-2 rounded-full bg-bg-primary p-1">
          <span
            className={`pointer-events-none absolute inset-y-1 left-1 w-[calc(50%-0.25rem)] rounded-full bg-white shadow-[0_12px_30px_rgba(42,41,51,0.08)] transition-transform duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] ${
              tab === "files" ? "translate-x-full" : ""
            }`}
          />
          <button
            onClick={() => setTab("sessions")}
            aria-label="会话"
            className={`relative z-10 flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-2.5 text-sm transition ${
              tab === "sessions"
                ? "text-text-primary"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            <MessageSquare size={13} />
            会话
          </button>
          <button
            onClick={() => setTab("files")}
            aria-label="文件"
            className={`relative z-10 flex flex-1 items-center justify-center gap-1.5 rounded-full px-3 py-2.5 text-sm transition ${
              tab === "files"
                ? "text-text-primary"
                : "text-text-secondary hover:text-text-primary"
            }`}
          >
            <FolderTree size={13} />
            文件
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <div className="min-h-0 flex-1">
          {tab === "sessions" && (
            <div className="h-full overflow-y-auto px-3 pb-3">
              <SessionList />
            </div>
          )}
          {tab === "files" && (
            <div className="h-full overflow-y-auto px-3 pb-3">
              {currentSessionId && (
                <div className="mb-3 flex items-center justify-end px-1">
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

        {enabledSkills.length > 0 && (
          <div className="max-h-[33%] shrink-0 border-t border-border px-4 py-4">
            <div className="h-full overflow-y-auto">
              <SkillPicker
                skills={enabledSkills}
                onInsert={insertToPrompt}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
