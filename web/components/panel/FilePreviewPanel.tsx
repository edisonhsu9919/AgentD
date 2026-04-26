"use client";

import { useEffect, useState } from "react";
import { usePanelStore } from "@/store/panel";
import { useSettingsStore } from "@/store/settings";
import { useWorkspaceStore } from "@/store/workspace";
import { apiFetch, apiFetchRaw } from "@/lib/api";
import { showToast } from "@/components/ui/Toast";
import type { FileMeta, InspectResult } from "@/lib/types";
import {
  Download,
  FileText,
  File,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Eye,
  EyeOff,
  FileSpreadsheet,
  Presentation,
  Mail,
  BookOpen,
  Table,
  Layers,
  FolderInput,
  Loader2,
} from "lucide-react";
import MessageMarkdown from "@/components/chat/MessageMarkdown";

interface FilePreviewPanelProps {
  sessionId: string;
}

export default function FilePreviewPanel({ sessionId }: FilePreviewPanelProps) {
  const filePreviewPath = usePanelStore((s) => s.filePreviewPath);
  const fileInspect = usePanelStore((s) => s.fileInspect);
  const fileInspectLoading = usePanelStore((s) => s.fileInspectLoading);
  const knowledgeDocContent = usePanelStore((s) => s.knowledgeDocContent);
  const knowledgeDocTitle = usePanelStore((s) => s.knowledgeDocTitle);
  const downloadFile = useWorkspaceStore((s) => s.downloadFile);
  const vlmConfig = useSettingsStore((s) => s.vlmConfig);
  const fetchVLMConfig = useSettingsStore((s) => s.fetchVLMConfig);

  const isKnowledgePreview = filePreviewPath?.startsWith("knowledge:");

  const [fileMeta, setFileMeta] = useState<FileMeta | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [imageScale, setImageScale] = useState(1);
  const [importing, setImporting] = useState(false);

  // Importable file types
  const IMPORTABLE_EXTS = new Set(["pdf", "docx", "pptx", "txt", "md"]);
  const currentExt = filePreviewPath?.split(".").pop()?.toLowerCase() || "";
  const canImport = !isKnowledgePreview && IMPORTABLE_EXTS.has(currentExt);

  const startImportDraft = usePanelStore((s) => s.startImportDraft);

  const handleStartKnowledgeImport = async () => {
    if (!filePreviewPath || isKnowledgePreview) return;
    const ok = window.confirm(
      "导入知识库\n\n系统会提取文件内容并生成元数据草稿，确认继续吗？",
    );
    if (!ok) return;

    setImporting(true);
    try {
      await startImportDraft(sessionId, filePreviewPath);
    } catch {
      showToast("error", "启动导入失败");
    } finally {
      setImporting(false);
    }
  };

  useEffect(() => {
    if (!vlmConfig) fetchVLMConfig();
  }, [vlmConfig, fetchVLMConfig]);

  // Fetch file meta + content when path changes
  useEffect(() => {
    setTextContent(null);
    setFileMeta(null);
    setImageScale(1);
    if (imageUrl) {
      URL.revokeObjectURL(imageUrl);
      setImageUrl(null);
    }
    if (pdfUrl) {
      URL.revokeObjectURL(pdfUrl);
      setPdfUrl(null);
    }

    if (!filePreviewPath) return;
    // Knowledge preview is handled entirely by panel store (openKnowledgeSource)
    if (filePreviewPath.startsWith("knowledge:")) return;

    let cancelled = false;

    const load = async () => {
      setLoading(true);
      try {
        // Fetch file meta
        const meta = await apiFetch<FileMeta>(
          `/sessions/${sessionId}/workspace/meta?path=${encodeURIComponent(filePreviewPath)}`,
        );
        if (cancelled) return;
        setFileMeta(meta);

        // Fetch content based on preview mode
        if (meta.preview_mode === "text") {
          const resp = await apiFetch<{ path: string; content: string }>(
            `/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(filePreviewPath)}&mode=text`,
          );
          if (!cancelled) setTextContent(resp.content);
        } else if (meta.preview_mode === "image") {
          const res = await apiFetchRaw(
            `/sessions/${sessionId}/workspace/download?path=${encodeURIComponent(filePreviewPath)}`,
          );
          const blob = await res.blob();
          if (!cancelled) setImageUrl(URL.createObjectURL(blob));
        } else if (meta.preview_mode === "pdf") {
          const res = await apiFetchRaw(
            `/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(filePreviewPath)}&mode=binary`,
          );
          const blob = await res.blob();
          const pdfBlob = blob.type === "application/pdf"
            ? blob
            : new Blob([blob], { type: "application/pdf" });
          if (!cancelled) setPdfUrl(URL.createObjectURL(pdfBlob));
        }
      } catch {
        // non-fatal
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    load();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filePreviewPath, sessionId]);

  // Cleanup blob URL on unmount
  useEffect(() => {
    return () => {
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      if (pdfUrl) URL.revokeObjectURL(pdfUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!filePreviewPath) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        No file selected
      </div>
    );
  }

  // Knowledge preview mode
  if (isKnowledgePreview) {
    return (
      <div className="flex h-full flex-col">
        <div className="flex items-center justify-between px-4 py-2">
          <div className="flex items-center gap-2 text-[10px] text-text-secondary">
            <BookOpen size={12} className="text-accent" />
            <span>{knowledgeDocTitle || "Knowledge Document"}</span>
            {fileInspect?.path && (
              <span className="text-text-secondary/50">{fileInspect.path}</span>
            )}
          </div>
          {fileInspect?.metadata?.raw_available === "true" && fileInspect.metadata.source_file && (
            <button
              onClick={async () => {
                try {
                  const res = await apiFetchRaw(
                    `/knowledge/raw/${encodeURIComponent(fileInspect.metadata!.source_file!)}`,
                  );
                  const blob = await res.blob();
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = fileInspect.metadata!.source_file!;
                  document.body.appendChild(a);
                  a.click();
                  document.body.removeChild(a);
                  URL.revokeObjectURL(url);
                } catch { /* ignore */ }
              }}
              className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-tertiary/70"
              title="Download original file"
            >
              <Download size={13} />
            </button>
          )}
        </div>
        <div className="flex-1 overflow-auto p-4">
          {fileInspectLoading ? (
            <div className="flex items-center justify-center py-12">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            </div>
          ) : knowledgeDocContent ? (
            <div className="space-y-4">
              {/* Metadata card */}
              {fileInspect?.metadata && (
                <div className="space-y-1.5 rounded-[14px] bg-bg-primary/55 p-3 text-xs">
                  {fileInspect.metadata.description && (
                    <p className="text-text-secondary">{fileInspect.metadata.description}</p>
                  )}
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-text-secondary/70">
                    {fileInspect.metadata.tags && (
                      <span>Tags: {fileInspect.metadata.tags}</span>
                    )}
                    {fileInspect.metadata.author && (
                      <span>Author: {fileInspect.metadata.author}</span>
                    )}
                    {fileInspect.metadata.source_file && (
                      <span>Source: {fileInspect.metadata.source_file}</span>
                    )}
                  </div>
                </div>
              )}
              {/* Markdown content */}
              <div className="chat-prose">
                <MessageMarkdown>{knowledgeDocContent}</MessageMarkdown>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center py-12 text-xs text-text-secondary">
              Unable to load document content
            </div>
          )}
        </div>
      </div>
    );
  }

  const ext = filePreviewPath.split(".").pop()?.toLowerCase() || "";
  const isMarkdown = ext === "md" || ext === "mdx";
  const fileName = filePreviewPath.split("/").pop() || filePreviewPath;

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-3 px-4 py-2">
        <div className="flex min-w-0 items-center gap-2 text-[10px] text-text-secondary">
          <FileText size={12} className="shrink-0 text-accent" />
          <span className="truncate text-[11px] font-medium text-text-primary" title={filePreviewPath}>
            {fileName}
          </span>
          {fileMeta && (
            <>
              <span className="shrink-0 text-text-secondary/60">{fileMeta.extension || ext}</span>
              <span className="shrink-0 text-text-secondary/60">{formatSize(fileMeta.size)}</span>
            </>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {fileMeta?.preview_mode === "image" && imageUrl && (
            <>
              <button
                onClick={() => setImageScale((s) => Math.max(0.25, s - 0.25))}
                className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-tertiary/70"
                title="Zoom out"
              >
                <ZoomOut size={13} />
              </button>
              <span className="text-[10px] text-text-secondary">
                {Math.round(imageScale * 100)}%
              </span>
              <button
                onClick={() => setImageScale((s) => Math.min(4, s + 0.25))}
                className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-tertiary/70"
                title="Zoom in"
              >
                <ZoomIn size={13} />
              </button>
              <button
                onClick={() => setImageScale(1)}
                className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-tertiary/70"
                title="Reset zoom"
              >
                <Maximize2 size={13} />
              </button>
            </>
          )}
          <button
            onClick={() => downloadFile(sessionId, filePreviewPath)}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-tertiary/70"
            title="Download"
          >
            <Download size={13} />
          </button>
          {canImport && (
            <button
              onClick={handleStartKnowledgeImport}
              disabled={importing}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] text-accent transition hover:bg-accent/10 disabled:opacity-50"
              title="Import to knowledge base"
            >
              {importing ? <Loader2 size={12} className="animate-spin" /> : <FolderInput size={12} />}
              <span className="hidden sm:inline">{importing ? "导入中..." : "导入"}</span>
            </button>
          )}
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto px-4 pb-4 pt-2">
        {loading || fileInspectLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          </div>
        ) : (
          <>
            {/* Text preview */}
            {fileMeta?.preview_mode === "text" && textContent !== null && (
              isMarkdown ? (
                <div className="chat-prose">
                  <MessageMarkdown>{textContent}</MessageMarkdown>
                </div>
              ) : (
                <pre className="whitespace-pre-wrap break-words text-xs text-text-primary font-mono">
                  {textContent}
                </pre>
              )
            )}

            {/* PDF preview with browser-native full-page viewer */}
            {fileMeta?.preview_mode === "pdf" && pdfUrl && (
              <PdfPreviewFrame url={pdfUrl} title={fileName} />
            )}

            {/* Image preview with zoom */}
            {fileMeta?.preview_mode === "image" && imageUrl && (
              <div className="flex flex-col items-center gap-2">
                <img
                  src={imageUrl}
                  alt={filePreviewPath}
                  className="max-w-full rounded transition-transform"
                  style={{ transform: `scale(${imageScale})`, transformOrigin: "top center" }}
                />
                <VLMHint available={vlmConfig?.available ?? null} />
              </div>
            )}

            {/* Inspectable file: structured card */}
            {fileInspect?.inspectable && fileMeta?.preview_mode !== "pdf" && (
              <InspectCard
                inspect={fileInspect}
                sessionId={sessionId}
                path={filePreviewPath}
                canImport={canImport}
                importing={importing}
                onImport={handleStartKnowledgeImport}
              />
            )}

            {/* PDF fallback */}
            {fileMeta?.preview_mode === "pdf" && !pdfUrl && (
              <DownloadPrompt
                icon={<File size={28} />}
                label="PDF Document"
                onDownload={() => downloadFile(sessionId, filePreviewPath)}
              />
            )}

            {/* Office: fallback download */}
            {fileMeta?.preview_mode === "office" && !fileInspect?.inspectable && (
              <DownloadPrompt
                icon={<FileSpreadsheet size={28} />}
                label="Office Document"
                onDownload={() => downloadFile(sessionId, filePreviewPath)}
              />
            )}

            {/* Binary / download-only */}
            {(fileMeta?.preview_mode === "binary" ||
              fileMeta?.preview_mode === "download" ||
              fileMeta?.download_only) &&
              !fileInspect?.inspectable && (
                <DownloadPrompt
                  icon={<File size={28} />}
                  label={fileMeta?.preview_mode === "binary" ? "Binary file" : "Cannot preview this file type"}
                  onDownload={() => downloadFile(sessionId, filePreviewPath)}
                />
              )}
          </>
        )}
      </div>
    </div>
  );
}

// --- Inspect Card ---

function InspectCard({ inspect, sessionId, path, canImport, importing, onImport }: {
  inspect: InspectResult; sessionId: string; path: string;
  canImport?: boolean; importing?: boolean; onImport?: () => void;
}) {
  let card: React.ReactNode = null;
  if (inspect.kind === "pdf") card = <PDFInspectCard inspect={inspect} sessionId={sessionId} path={path} />;
  else if (inspect.kind === "office" && inspect.office_kind === "docx") card = <DocxCard inspect={inspect} />;
  else if (inspect.kind === "office" && inspect.office_kind === "xlsx") card = <XlsxCard inspect={inspect} />;
  else if (inspect.kind === "office" && inspect.office_kind === "pptx") card = <PptxCard inspect={inspect} />;
  else if (inspect.kind === "email") card = <EmlCard inspect={inspect} />;
  else if (inspect.kind === "image") card = null;
  else card = (
    <div className="rounded-[14px] bg-bg-primary/55 p-4">
      <pre className="text-xs text-text-secondary">{JSON.stringify(inspect, null, 2)}</pre>
    </div>
  );

  return (
    <div className="space-y-3">
      {card}
      {canImport && onImport && (
        <button
          onClick={onImport}
          disabled={importing}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-accent/10 px-4 py-2 text-xs font-medium text-accent transition hover:bg-accent/14 disabled:opacity-50"
        >
          {importing ? <Loader2 size={14} className="animate-spin" /> : <FolderInput size={14} />}
          {importing ? "导入中..." : "导入知识库"}
        </button>
      )}
    </div>
  );
}

function PDFInspectCard({ inspect, sessionId, path }: { inspect: InspectResult; sessionId: string; path: string }) {
  const downloadFile = useWorkspaceStore((s) => s.downloadFile);
  const vlmConfig = useSettingsStore((s) => s.vlmConfig);

  return (
    <div className="space-y-3">
      <div className="space-y-3 rounded-[14px] bg-bg-primary/55 p-4">
        <div className="flex items-center gap-2">
          <File size={18} className="text-red-400" />
          <span className="text-sm font-medium text-text-primary">PDF Document</span>
          {inspect.pdf_kind && (
            <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
              inspect.pdf_kind === "text_pdf"
                ? "bg-green-500/10 text-green-400"
                : inspect.pdf_kind === "image_like_pdf"
                  ? "bg-purple-500/10 text-purple-400"
                  : "bg-yellow-500/10 text-yellow-400"
            }`}>
              {inspect.pdf_kind === "text_pdf" ? "Text PDF" : inspect.pdf_kind === "image_like_pdf" ? "Scanned" : "Mixed"}
            </span>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          {inspect.page_count != null && (
            <div><span className="text-text-secondary">Pages:</span> <strong className="text-text-primary">{inspect.page_count}</strong></div>
          )}
          {inspect.size_bytes != null && (
            <div><span className="text-text-secondary">Size:</span> <strong className="text-text-primary">{formatSize(inspect.size_bytes)}</strong></div>
          )}
        </div>

        {inspect.metadata && Object.values(inspect.metadata).some(Boolean) && (
          <div className="space-y-1 text-xs">
            {inspect.metadata.title && <div><span className="text-text-secondary">Title:</span> <span className="text-text-primary">{inspect.metadata.title}</span></div>}
            {inspect.metadata.author && <div><span className="text-text-secondary">Author:</span> <span className="text-text-primary">{inspect.metadata.author}</span></div>}
          </div>
        )}

        {inspect.text_sample && (
          <div className="rounded-[12px] bg-white/62 p-2">
            <div className="mb-1 text-[10px] font-medium text-text-secondary">Preview</div>
            <p className="text-xs text-text-primary line-clamp-6 whitespace-pre-wrap">{inspect.text_sample}</p>
          </div>
        )}

        {inspect.pdf_kind === "image_like_pdf" && (
          <VLMHint available={vlmConfig?.available ?? null} />
        )}

        <button
          onClick={() => downloadFile(sessionId, path)}
          className="w-full rounded bg-accent/20 px-3 py-1.5 text-xs text-accent transition hover:bg-accent/30"
        >
          Download to view full document
        </button>
      </div>
    </div>
  );
}

function PdfPreviewFrame({ url, title }: { url: string; title: string }) {
  return (
    <div className="flex h-full min-h-[640px] flex-col rounded-[14px] bg-bg-primary/55 p-2">
      <div className="flex min-h-0 flex-1 overflow-hidden rounded-[12px] bg-white shadow-[0_14px_42px_rgba(23,23,37,0.08)]">
        <iframe
          src={`${url}#toolbar=0&navpanes=0&scrollbar=1&view=FitH`}
          title={title}
          className="h-full min-h-[640px] w-full bg-white"
        />
      </div>
    </div>
  );
}

function DocxCard({ inspect }: { inspect: InspectResult }) {
  return (
    <div className="space-y-3 rounded-[14px] bg-bg-primary/55 p-4">
      <div className="flex items-center gap-2">
        <BookOpen size={18} className="text-blue-400" />
        <span className="text-sm font-medium text-text-primary">Word Document</span>
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs">
        {inspect.paragraph_count != null && (
          <div><span className="text-text-secondary">Paragraphs:</span> <strong className="text-text-primary">{inspect.paragraph_count}</strong></div>
        )}
        {inspect.heading_count != null && (
          <div><span className="text-text-secondary">Headings:</span> <strong className="text-text-primary">{inspect.heading_count}</strong></div>
        )}
        {inspect.table_count != null && (
          <div><span className="text-text-secondary">Tables:</span> <strong className="text-text-primary">{inspect.table_count}</strong></div>
        )}
      </div>

      {inspect.headings && inspect.headings.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium text-text-secondary">Structure</div>
          <ul className="space-y-0.5 text-xs text-text-primary">
            {inspect.headings.map((h, i) => (
              <li key={i} className="flex items-center gap-1.5">
                <span className="h-1 w-1 shrink-0 rounded-full bg-blue-400" />
                {h}
              </li>
            ))}
          </ul>
        </div>
      )}

      {inspect.text_sample && (
        <div className="rounded-[12px] bg-white/62 p-2">
          <div className="mb-1 text-[10px] font-medium text-text-secondary">Preview</div>
          <p className="text-xs text-text-primary line-clamp-6 whitespace-pre-wrap">{inspect.text_sample}</p>
        </div>
      )}
    </div>
  );
}

function XlsxCard({ inspect }: { inspect: InspectResult }) {
  return (
    <div className="space-y-3 rounded-[14px] bg-bg-primary/55 p-4">
      <div className="flex items-center gap-2">
        <Table size={18} className="text-green-400" />
        <span className="text-sm font-medium text-text-primary">Excel Spreadsheet</span>
        {inspect.sheet_count != null && (
          <span className="text-[10px] text-text-secondary">{inspect.sheet_count} sheet{inspect.sheet_count !== 1 ? "s" : ""}</span>
        )}
      </div>

      {inspect.sheets && inspect.sheets.map((sheet, si) => (
        <div key={si} className="space-y-2 rounded-[12px] bg-white/62 p-2">
          <div className="flex items-center gap-1.5">
            <Layers size={12} className="text-green-400" />
            <span className="text-xs font-medium text-text-primary">{sheet.name}</span>
            <span className="text-[10px] text-text-secondary">{sheet.dimensions}</span>
          </div>

          {sheet.header_row && sheet.header_row.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead>
                  <tr className="bg-bg-primary/45">
                    {sheet.header_row.map((h, i) => (
                      <th key={i} className="px-2 py-1 text-left font-medium text-text-secondary">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sheet.sample_rows?.map((row, ri) => (
                    <tr key={ri} className="odd:bg-white/30">
                      {row.map((cell, ci) => (
                        <td key={ci} className="px-2 py-0.5 text-text-primary">{cell}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function PptxCard({ inspect }: { inspect: InspectResult }) {
  return (
    <div className="space-y-3 rounded-[14px] bg-bg-primary/55 p-4">
      <div className="flex items-center gap-2">
        <Presentation size={18} className="text-orange-400" />
        <span className="text-sm font-medium text-text-primary">Presentation</span>
        {inspect.slide_count != null && (
          <span className="text-[10px] text-text-secondary">{inspect.slide_count} slides</span>
        )}
      </div>

      {inspect.slides && inspect.slides.map((slide) => (
        <div key={slide.number} className="flex gap-2 rounded-[12px] bg-white/62 p-2">
          <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded bg-orange-400/10 text-[10px] font-medium text-orange-400">
            {slide.number}
          </div>
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs font-medium text-text-primary">{slide.title || "Untitled"}</div>
            {slide.text_preview && (
              <p className="mt-0.5 text-[10px] text-text-secondary line-clamp-2">{slide.text_preview}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function EmlCard({ inspect }: { inspect: InspectResult }) {
  return (
    <div className="space-y-3 rounded-[14px] bg-bg-primary/55 p-4">
      <div className="flex items-center gap-2">
        <Mail size={18} className="text-cyan-400" />
        <span className="text-sm font-medium text-text-primary">Email</span>
      </div>

      <div className="space-y-1 text-xs">
        {inspect.subject && <div><span className="text-text-secondary">Subject:</span> <strong className="text-text-primary">{inspect.subject}</strong></div>}
        {inspect.from_addr && <div><span className="text-text-secondary">From:</span> <span className="text-text-primary">{inspect.from_addr}</span></div>}
        {inspect.to_addr && <div><span className="text-text-secondary">To:</span> <span className="text-text-primary">{inspect.to_addr}</span></div>}
        {inspect.date && <div><span className="text-text-secondary">Date:</span> <span className="text-text-primary">{inspect.date}</span></div>}
      </div>

      {inspect.body_preview && (
        <div className="rounded-[12px] bg-white/62 p-2">
          <p className="text-xs text-text-primary whitespace-pre-wrap line-clamp-8">{inspect.body_preview}</p>
        </div>
      )}

      {inspect.attachments && inspect.attachments.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] font-medium text-text-secondary">
            Attachments ({inspect.attachment_count})
          </div>
          <ul className="space-y-0.5">
            {inspect.attachments.map((a, i) => (
              <li key={i} className="flex items-center gap-1.5 text-xs text-text-primary">
                <FileText size={11} className="text-text-secondary" />
                {a.filename}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// --- Shared helpers ---

function VLMHint({ available }: { available: boolean | null }) {
  if (available === null) return null;
  return (
    <div className={`flex items-center gap-1.5 rounded px-2 py-1 text-[10px] ${
      available ? "bg-purple-500/10 text-purple-400" : "bg-bg-tertiary/50 text-text-secondary/50"
    }`}>
      {available ? <Eye size={11} /> : <EyeOff size={11} />}
      <span>{available ? "Vision understanding available" : "Vision understanding not available"}</span>
    </div>
  );
}

function DownloadPrompt({ icon, label, onDownload }: { icon: React.ReactNode; label: string; onDownload: () => void }) {
  return (
    <div className="flex flex-col items-center gap-3 py-12 text-text-secondary">
      {icon}
      <span className="text-xs">{label}</span>
      <button onClick={onDownload} className="rounded bg-accent/20 px-4 py-1.5 text-xs text-accent transition hover:bg-accent/30">
        Download to view
      </button>
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
