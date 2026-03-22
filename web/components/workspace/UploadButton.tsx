"use client";

import { useRef, useState } from "react";
import { useWorkspaceStore } from "@/store/workspace";
import { Upload, Loader2 } from "lucide-react";

interface UploadButtonProps {
  sessionId: string;
}

export default function UploadButton({ sessionId }: UploadButtonProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const uploadFiles = useWorkspaceStore((s) => s.uploadFiles);
  const fetchTree = useWorkspaceStore((s) => s.fetchTree);

  const handleChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const fileList = e.target.files;
    if (!fileList || fileList.length === 0) return;

    setUploading(true);
    try {
      await uploadFiles(sessionId, Array.from(fileList));
      await fetchTree(sessionId);
    } catch {
      // upload errors are non-fatal for UX
    } finally {
      setUploading(false);
      // Reset input so the same file can be re-selected
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleChange}
      />
      <button
        onClick={() => inputRef.current?.click()}
        disabled={uploading}
        className="flex items-center gap-1 rounded px-2 py-1 text-xs text-text-secondary transition hover:bg-bg-tertiary/50 disabled:opacity-50"
        title="Upload files"
      >
        {uploading ? (
          <Loader2 size={14} className="animate-spin" />
        ) : (
          <Upload size={14} />
        )}
        Upload
      </button>
    </>
  );
}
