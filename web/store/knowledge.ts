import { create } from "zustand";
import { apiFetch } from "@/lib/api";
import type { KnowledgeDocItem } from "@/lib/types";

interface KnowledgeState {
  docs: KnowledgeDocItem[];
  isLoading: boolean;
  error: string | null;
  searchQuery: string;

  // Selected doc for panel preview
  selectedDocId: string | null;

  fetchDocs: (query?: string) => Promise<void>;
  setSearchQuery: (query: string) => void;
  selectDoc: (docId: string | null) => void;
  deleteDoc: (docId: string) => Promise<void>;
}

export const useKnowledgeStore = create<KnowledgeState>((set, get) => ({
  docs: [],
  isLoading: false,
  error: null,
  searchQuery: "",
  selectedDocId: null,

  fetchDocs: async (query?: string) => {
    set({ isLoading: true, error: null });
    try {
      const q = query ?? get().searchQuery;
      const params = q ? `?q=${encodeURIComponent(q)}` : "";
      const docs = await apiFetch<KnowledgeDocItem[]>(
        `/knowledge/documents${params}`,
      );
      set({ docs, isLoading: false });
    } catch {
      set({ isLoading: false, error: "Failed to load knowledge documents" });
    }
  },

  setSearchQuery: (query: string) => {
    set({ searchQuery: query });
  },

  selectDoc: (docId: string | null) => {
    set({ selectedDocId: docId });
  },

  deleteDoc: async (docId: string) => {
    await apiFetch(`/knowledge/documents/${encodeURIComponent(docId)}`, {
      method: "DELETE",
    });
    // Refresh list and clear selection if deleted doc was selected
    const { selectedDocId, fetchDocs } = get();
    if (selectedDocId === docId) {
      set({ selectedDocId: null });
    }
    await fetchDocs();
  },
}));
