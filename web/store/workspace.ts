import { create } from "zustand";
import { apiFetch, apiFetchRaw } from "@/lib/api";
import type { FileNode, FileMeta } from "@/lib/types";

interface WorkspaceState {
  fileTree: FileNode[];
  selectedFile: string | null;
  fileMeta: FileMeta | null;
  isLoading: boolean;

  fetchTree: (sessionId: string) => Promise<void>;
  selectFile: (sessionId: string, path: string) => Promise<void>;
  clearSelection: () => void;
  uploadFiles: (
    sessionId: string,
    files: File[],
    targetDir?: string,
  ) => Promise<void>;
  downloadFile: (sessionId: string, path: string) => Promise<void>;
}

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  fileTree: [],
  selectedFile: null,
  fileMeta: null,
  isLoading: false,

  fetchTree: async (sessionId: string) => {
    try {
      const tree = await apiFetch<FileNode[]>(
        `/sessions/${sessionId}/workspace/tree`,
      );
      set({ fileTree: tree });
    } catch {
      set({ fileTree: [] });
    }
  },

  selectFile: async (sessionId: string, path: string) => {
    set({ selectedFile: path, fileMeta: null });
    try {
      const meta = await apiFetch<FileMeta>(
        `/sessions/${sessionId}/workspace/meta?path=${encodeURIComponent(path)}`,
      );
      set({ fileMeta: meta });
    } catch {
      // ignore
    }
  },

  clearSelection: () => {
    set({ selectedFile: null, fileMeta: null });
  },

  uploadFiles: async (
    sessionId: string,
    files: File[],
    targetDir?: string,
  ) => {
    set({ isLoading: true });
    try {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      if (targetDir) formData.append("target_dir", targetDir);

      await apiFetch(`/sessions/${sessionId}/workspace/upload`, {
        method: "POST",
        body: formData,
      });
    } finally {
      set({ isLoading: false });
    }
  },

  downloadFile: async (sessionId: string, path: string) => {
    const res = await apiFetchRaw(
      `/sessions/${sessionId}/workspace/download?path=${encodeURIComponent(path)}`,
    );
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = path.split("/").pop() || "download";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },
}));
