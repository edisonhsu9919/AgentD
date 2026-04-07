import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type {
  PanelType,
  PanelContent,
  PanelTab,
  InspectResult,
  TaskInstance,
  TaskKind,
  KnowledgeImportDraft,
  KnowledgeImportProgress,
} from "@/lib/types";

/** Fixed panel-type-level tabs. Tabs represent panel types, not file instances. */
const FIXED_TABS: PanelTab[] = [
  { id: "file_preview", type: "file_preview", title: "File Preview" },
  { id: "task_output", type: "task_output", title: "Task Output" },
  { id: "html_app", type: "html_app", title: "App" },
];

interface PanelState {
  // Visibility
  open: boolean;
  togglePanel: () => void;
  openPanel: () => void;
  closePanel: () => void;

  // Active panel (one of the 3 fixed types)
  activeType: PanelType | null;
  tabs: PanelTab[];

  // File preview (single-instance)
  filePreviewPath: string | null;
  fileInspect: InspectResult | null;
  fileInspectLoading: boolean;

  // Panel content (from SSE panel_update)
  panelContent: PanelContent | null;

  // Task instances (Phase P3 — multi-task support)
  taskList: TaskInstance[];
  activeTaskId: string | null;

  // Task output log (per-task, keyed by task_id)
  taskOutputLogs: Record<string, string[]>;

  // Knowledge source preview
  knowledgeDocContent: string | null;
  knowledgeDocTitle: string | null;

  // Knowledge import (Phase P6E)
  knowledgeImportDraft: KnowledgeImportDraft | null;
  knowledgeImportSourcePath: string | null;
  knowledgeImportTaskId: string | null;
  knowledgeImportStatus: "idle" | "drafting" | "form" | "submitting" | "processing" | "completed" | "failed";
  knowledgeImportProgress: KnowledgeImportProgress | null;
  knowledgeImportError: string | null;

  // Actions — file preview
  openFilePreview: (sessionId: string, path: string) => Promise<void>;
  openKnowledgeSource: (docId: string) => Promise<void>;

  // Actions — knowledge import
  startImportDraft: (sessionId: string, sourcePath: string) => Promise<void>;
  confirmImport: (sessionId: string) => Promise<void>;
  pollImportProgress: (taskId: string) => Promise<void>;
  resetImport: () => void;
  restoreImport: (sessionId: string) => Promise<void>;

  // Actions — panel navigation
  openTaskOutput: () => void;
  openHtmlApp: () => void;
  activateType: (type: PanelType) => void;
  setPanelContent: (type: PanelType, content: PanelContent) => void;
  setTabAttention: (type: PanelType, attention: boolean) => void;

  // Actions — task management (SSE + API reconcile)
  addTask: (task: TaskInstance) => void;
  updateTaskStatus: (taskId: string, status: TaskInstance["status"], extra?: Partial<TaskInstance>) => void;
  selectTask: (taskId: string) => void;
  appendTaskLog: (taskId: string, line: string) => void;
  fetchTasks: (sessionId: string) => Promise<void>;
  fetchTaskStdout: (sessionId: string, taskId: string) => Promise<void>;
  stopTask: (sessionId: string, taskId: string) => Promise<void>;
  hasRunningTasks: () => boolean;

  // Reset
  clearPanel: () => void;
}

export const usePanelStore = create<PanelState>((set, get) => ({
  open: false,
  activeType: null,
  tabs: FIXED_TABS,
  filePreviewPath: null,
  fileInspect: null,
  fileInspectLoading: false,
  panelContent: null,
  knowledgeDocContent: null,
  knowledgeDocTitle: null,
  knowledgeImportDraft: null,
  knowledgeImportSourcePath: null,
  knowledgeImportTaskId: null,
  knowledgeImportStatus: "idle" as const,
  knowledgeImportProgress: null,
  knowledgeImportError: null,
  taskList: [],
  activeTaskId: null,
  taskOutputLogs: {},

  togglePanel: () => set((s) => ({ open: !s.open })),
  openPanel: () => set({ open: true }),
  closePanel: () => set({ open: false }),

  openFilePreview: async (sessionId: string, path: string) => {
    set({
      open: true,
      activeType: "file_preview",
      filePreviewPath: path,
      fileInspect: null,
      fileInspectLoading: true,
    });

    try {
      const data = await apiFetch<InspectResult>(
        `/sessions/${sessionId}/workspace/inspect?path=${encodeURIComponent(path)}`,
      );
      if (get().filePreviewPath === path) {
        set({ fileInspect: data, fileInspectLoading: false });
      }
    } catch {
      if (get().filePreviewPath === path) {
        set({ fileInspectLoading: false });
      }
    }
  },

  openKnowledgeSource: async (docId: string) => {
    set({
      open: true,
      activeType: "file_preview",
      filePreviewPath: `knowledge:${docId}`,
      fileInspect: null,
      fileInspectLoading: true,
      knowledgeDocContent: null,
      knowledgeDocTitle: null,
    });

    try {
      // Resolve source metadata
      const source = await apiFetch<{
        doc_id: string;
        title: string;
        kind: string;
        description: string;
        tags: string[];
        author: string;
        source_file: string;
        raw_available: boolean;
        raw_path: string | null;
        knowledge_md_path: string;
      }>(`/knowledge/source/${encodeURIComponent(docId)}`);

      // If raw file available, fetch it for download but show md content for preview
      // Fetch the markdown content for display
      const doc = await apiFetch<{
        doc_id: string;
        title: string;
        content?: string;
      }>(`/knowledge/documents/${encodeURIComponent(docId)}?include_content=true`);

      if (get().filePreviewPath === `knowledge:${docId}`) {
        set({
          fileInspectLoading: false,
          knowledgeDocContent: doc.content || null,
          knowledgeDocTitle: source.title || doc.title || docId,
          fileInspect: {
            path: source.raw_available && source.raw_path ? source.raw_path : source.knowledge_md_path,
            kind: source.kind || "document",
            inspectable: true,
            size_bytes: doc.content ? new Blob([doc.content]).size : 0,
            // Stash source metadata for download
            metadata: {
              doc_id: docId,
              raw_available: source.raw_available ? "true" : "false",
              source_file: source.source_file || "",
              raw_path: source.raw_path || "",
              description: source.description || "",
              tags: (source.tags || []).join(", "),
              author: source.author || "",
            },
          },
        });
      }
    } catch {
      if (get().filePreviewPath === `knowledge:${docId}`) {
        set({ fileInspectLoading: false });
      }
    }
  },

  openTaskOutput: () => {
    set({ open: true, activeType: "task_output" });
  },

  openHtmlApp: () => {
    set({ open: true, activeType: "html_app" });
  },

  activateType: (type: PanelType) => {
    set((s) => ({
      open: true,
      activeType: type,
      tabs: s.tabs.map((t) =>
        t.id === type ? { ...t, attention: false } : t,
      ),
    }));
  },

  setPanelContent: (type: PanelType, content: PanelContent) => {
    const { activeType } = get();

    // html_app is a required interaction — always auto-open and switch
    if (type === "html_app") {
      set({ open: true, activeType: type, panelContent: content });
      return;
    }

    // Other types: don't auto-switch if user is on a different tab
    if (activeType && activeType !== type) {
      set((s) => ({
        panelContent: content,
        tabs: s.tabs.map((t) =>
          t.id === type ? { ...t, attention: true } : t,
        ),
      }));
    } else {
      set({ open: true, activeType: type, panelContent: content });
    }
  },

  setTabAttention: (type: PanelType, attention: boolean) => {
    set((s) => ({
      tabs: s.tabs.map((t) =>
        t.id === type ? { ...t, attention } : t,
      ),
    }));
  },

  // --- Task management (SSE-driven) ---

  addTask: (task: TaskInstance) => {
    set((s) => {
      const exists = s.taskList.some((t) => t.task_id === task.task_id);
      if (exists) return s;

      const newList = [task, ...s.taskList];
      const newLogs = { ...s.taskOutputLogs, [task.task_id]: [] };

      // Auto-select if first task, and mark attention if not viewing task_output
      const shouldAutoSelect = s.activeTaskId === null;
      const shouldAttention = s.activeType !== "task_output";

      return {
        taskList: newList,
        activeTaskId: shouldAutoSelect ? task.task_id : s.activeTaskId,
        taskOutputLogs: newLogs,
        tabs: shouldAttention
          ? s.tabs.map((t) =>
              t.id === "task_output" ? { ...t, attention: true } : t,
            )
          : s.tabs,
      };
    });
  },

  updateTaskStatus: (taskId, status, extra) => {
    set((s) => {
      const newList = s.taskList.map((t) =>
        t.task_id === taskId
          ? { ...t, status, updated_at: new Date().toISOString(), ...extra }
          : t,
      );

      // Mark attention on completed/failed if not viewing task_output
      const shouldAttention =
        (status === "completed" || status === "failed") &&
        s.activeType !== "task_output";

      return {
        taskList: newList,
        tabs: shouldAttention
          ? s.tabs.map((t) =>
              t.id === "task_output" ? { ...t, attention: true } : t,
            )
          : s.tabs,
      };
    });
  },

  selectTask: (taskId: string) => {
    set({ activeTaskId: taskId });
  },

  appendTaskLog: (taskId: string, line: string) => {
    set((s) => ({
      taskOutputLogs: {
        ...s.taskOutputLogs,
        [taskId]: [...(s.taskOutputLogs[taskId] || []), line],
      },
    }));
  },

  fetchTasks: async (sessionId: string) => {
    try {
      // API returns { data: [...] } with `id` field (not `task_id`)
      const items = await apiFetch<Array<Record<string, unknown>>>(
        `/sessions/${sessionId}/tasks`,
      );
      const tasks: TaskInstance[] = items.map((t) => ({
        task_id: (t.id as string) || "",
        session_id: sessionId,
        task_kind: (t.task_kind as TaskKind) || "process",
        blocking_mode: (t.blocking_mode as TaskInstance["blocking_mode"]) || "detached",
        status: (t.status as TaskInstance["status"]) || "queued",
        title: (t.title as string) || "",
        command: (t.command as string) || "",
        spawned_by_tool: (t.spawned_by_tool as string) || "",
        tool_call_id: (t.tool_call_id as string) || "",
        child_session_id: (t.child_session_id as string) || null,
        pid: (t.pid as number) || null,
        artifact_root: (t.artifact_root as string) || "",
        stdout_path: (t.stdout_path as string) || "",
        stderr_path: (t.stderr_path as string) || "",
        error: (t.error as string) || null,
        result_summary: (t.result_summary as string) || null,
        created_at: (t.created_at as string) || "",
        updated_at: (t.updated_at as string) || "",
      }));

      const { activeTaskId } = get();
      set({
        taskList: tasks,
        activeTaskId: activeTaskId && tasks.some((t) => t.task_id === activeTaskId)
          ? activeTaskId
          : tasks[0]?.task_id || null,
      });

      // Mark attention if any running task and not viewing task_output
      const { activeType } = get();
      if (tasks.some((t) => t.status === "running") && activeType !== "task_output") {
        set((s) => ({
          tabs: s.tabs.map((t) =>
            t.id === "task_output" ? { ...t, attention: true } : t,
          ),
        }));
      }
    } catch {
      // silent
    }
  },

  fetchTaskStdout: async (sessionId: string, taskId: string) => {
    try {
      const data = await apiFetch<{ stdout: string }>(
        `/sessions/${sessionId}/tasks/${taskId}/stdout?tail=500`,
      );
      if (data.stdout) {
        const lines = data.stdout.split("\n");
        set((s) => ({
          taskOutputLogs: { ...s.taskOutputLogs, [taskId]: lines },
        }));
      }
    } catch {
      // silent
    }
  },

  stopTask: async (sessionId: string, taskId: string) => {
    await apiFetch(`/sessions/${sessionId}/tasks/${taskId}/stop`, {
      method: "POST",
    });
    // Refresh task list after stop
    await get().fetchTasks(sessionId);
  },

  hasRunningTasks: () => {
    return get().taskList.some(
      (t) => t.status === "running" || t.status === "queued" || t.status === "waiting",
    );
  },

  // --- Knowledge import actions ---

  startImportDraft: async (sessionId: string, sourcePath: string) => {
    set({
      knowledgeImportStatus: "drafting",
      knowledgeImportSourcePath: sourcePath,
      knowledgeImportDraft: null,
      knowledgeImportError: null,
    });

    try {
      const draft = await apiFetch<KnowledgeImportDraft>(
        `/knowledge/import-draft?session_id=${encodeURIComponent(sessionId)}&source_path=${encodeURIComponent(sourcePath)}`,
      );
      set({
        knowledgeImportDraft: draft,
        knowledgeImportStatus: "form",
        open: true,
        activeType: "html_app",
      });
    } catch {
      set({ knowledgeImportStatus: "failed", knowledgeImportError: "Failed to generate metadata draft" });
    }
  },

  confirmImport: async (sessionId: string) => {
    const { knowledgeImportDraft, knowledgeImportSourcePath } = get();
    if (!knowledgeImportDraft || !knowledgeImportSourcePath) return;

    set({ knowledgeImportStatus: "submitting", knowledgeImportError: null });

    try {
      const result = await apiFetch<{ task_id: string; status: string }>(
        "/knowledge/import",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: sessionId,
            source_path: knowledgeImportSourcePath,
            title: knowledgeImportDraft.title,
            description: knowledgeImportDraft.description,
            tags: Array.isArray(knowledgeImportDraft.tags)
              ? knowledgeImportDraft.tags.join(", ")
              : knowledgeImportDraft.tags,
            permission: "private",
          }),
        },
      );
      set({
        knowledgeImportTaskId: result.task_id,
        knowledgeImportStatus: "processing",
      });
    } catch {
      set({ knowledgeImportStatus: "failed", knowledgeImportError: "Failed to start import" });
    }
  },

  pollImportProgress: async (taskId: string) => {
    try {
      const progress = await apiFetch<KnowledgeImportProgress>(
        `/knowledge/import/${encodeURIComponent(taskId)}`,
      );
      set({ knowledgeImportProgress: progress });

      if (progress.status === "completed") {
        set({ knowledgeImportStatus: "completed" });
      } else if (progress.status === "failed") {
        set({ knowledgeImportStatus: "failed", knowledgeImportError: progress.error || "Import failed" });
      }
    } catch {
      // Polling failure is non-fatal — will retry on next tick
    }
  },

  resetImport: () => {
    set({
      knowledgeImportDraft: null,
      knowledgeImportSourcePath: null,
      knowledgeImportTaskId: null,
      knowledgeImportStatus: "idle",
      knowledgeImportProgress: null,
      knowledgeImportError: null,
    });
  },

  restoreImport: async (sessionId: string) => {
    try {
      const tasks = await apiFetch<KnowledgeImportProgress[]>(
        `/knowledge/imports?session_id=${encodeURIComponent(sessionId)}`,
      );
      // Find the most recent active (non-completed) import
      const active = tasks.find((t) => t.status === "extracting" || t.status === "committing");
      if (active) {
        set({
          knowledgeImportTaskId: active.task_id,
          knowledgeImportStatus: "processing",
          knowledgeImportProgress: active,
          open: true,
          activeType: "html_app",
        });
      }
    } catch {
      // silent
    }
  },

  clearPanel: () => {
    set({
      activeType: null,
      tabs: FIXED_TABS,
      filePreviewPath: null,
      fileInspect: null,
      fileInspectLoading: false,
      panelContent: null,
      knowledgeDocContent: null,
      knowledgeDocTitle: null,
      knowledgeImportDraft: null,
      knowledgeImportSourcePath: null,
      knowledgeImportTaskId: null,
      knowledgeImportStatus: "idle",
      knowledgeImportProgress: null,
      knowledgeImportError: null,
      taskList: [],
      activeTaskId: null,
      taskOutputLogs: {},
    });
  },
}));
