"use client";

import { useEffect, useState } from "react";
import { useWorkspaceStore } from "@/store/workspace";
import { apiFetch, apiFetchRaw } from "@/lib/api";
import type { FileMeta } from "@/lib/types";
import {
  X,
  Download,
  FileText,
  Image as ImageIcon,
  FileSpreadsheet,
  File,
} from "lucide-react";

interface FilePreviewProps {
  sessionId: string;
}

export default function FilePreview({ sessionId }: FilePreviewProps) {
  const selectedFile = useWorkspaceStore((s) => s.selectedFile);
  const fileMeta = useWorkspaceStore((s) => s.fileMeta);
  const clearSelection = useWorkspaceStore((s) => s.clearSelection);
  const downloadFile = useWorkspaceStore((s) => s.downloadFile);

  const [textContent, setTextContent] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  // Fetch preview content when fileMeta changes
  useEffect(() => {
    setTextContent(null);
    if (imageUrl) {
      URL.revokeObjectURL(imageUrl);
      setImageUrl(null);
    }

    if (!fileMeta || !fileMeta.is_previewable || !selectedFile) return;

    let cancelled = false;

    const fetchContent = async () => {
      setLoading(true);
      try {
        if (fileMeta.preview_mode === "text") {
          const resp = await apiFetch<{ path: string; content: string }>(
            `/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(selectedFile)}&mode=text`,
          );
          if (!cancelled) setTextContent(resp.content);
        } else if (fileMeta.preview_mode === "image") {
          const res = await apiFetchRaw(
            `/sessions/${sessionId}/workspace/download?path=${encodeURIComponent(selectedFile)}`,
          );
          const blob = await res.blob();
          if (!cancelled) setImageUrl(URL.createObjectURL(blob));
        }
      } catch {
        // preview error is non-fatal
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchContent();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileMeta, selectedFile, sessionId]);

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!selectedFile || !fileMeta) return null;

  const modeIcon = () => {
    switch (fileMeta.preview_mode) {
      case "text":
        return <FileText size={14} />;
      case "image":
        return <ImageIcon size={14} />;
      case "office":
        return <FileSpreadsheet size={14} />;
      default:
        return <File size={14} />;
    }
  };

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex h-full flex-col border-l border-border bg-bg-secondary">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-1.5 min-w-0">
          {modeIcon()}
          <span className="truncate text-xs font-medium">{fileMeta.name}</span>
          <span className="shrink-0 text-xs text-text-secondary">
            {formatSize(fileMeta.size)}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <button
            onClick={() => downloadFile(sessionId, selectedFile)}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
            title="Download"
          >
            <Download size={14} />
          </button>
          <button
            onClick={clearSelection}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary/50"
            title="Close"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto p-3">
        {loading && (
          <div className="flex items-center justify-center py-8 text-xs text-text-secondary">
            Loading preview...
          </div>
        )}

        {!loading && fileMeta.preview_mode === "text" && textContent !== null && (
          <pre className="whitespace-pre-wrap break-words text-xs text-text-primary">
            {textContent}
          </pre>
        )}

        {!loading && fileMeta.preview_mode === "image" && imageUrl && (
          <img
            src={imageUrl}
            alt={fileMeta.name}
            className="max-w-full rounded"
          />
        )}

        {!loading && fileMeta.preview_mode === "pdf" && (
          <div className="flex flex-col items-center gap-2 py-8 text-xs text-text-secondary">
            <File size={24} />
            <span>PDF preview not available</span>
            <button
              onClick={() => downloadFile(sessionId, selectedFile)}
              className="rounded bg-accent/20 px-3 py-1 text-accent transition hover:bg-accent/30"
            >
              Download to view
            </button>
          </div>
        )}

        {!loading && fileMeta.preview_mode === "office" && (
          <div className="flex flex-col items-center gap-2 py-8 text-xs text-text-secondary">
            <FileSpreadsheet size={24} />
            <span>Office document</span>
            <button
              onClick={() => downloadFile(sessionId, selectedFile)}
              className="rounded bg-accent/20 px-3 py-1 text-accent transition hover:bg-accent/30"
            >
              Download to view
            </button>
          </div>
        )}

        {!loading &&
          (fileMeta.preview_mode === "binary" ||
            fileMeta.preview_mode === "download" ||
            fileMeta.download_only ||
            !fileMeta.is_previewable) && (
            <div className="flex flex-col items-center gap-2 py-8 text-xs text-text-secondary">
              <File size={24} />
              <span>
                {fileMeta.preview_mode === "binary"
                  ? "Binary file"
                  : "Cannot preview this file type"}
              </span>
              <button
                onClick={() => downloadFile(sessionId, selectedFile)}
                className="rounded bg-accent/20 px-3 py-1 text-accent transition hover:bg-accent/30"
              >
                Download
              </button>
            </div>
          )}
      </div>
    </div>
  );
}
