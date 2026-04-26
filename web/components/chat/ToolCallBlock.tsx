"use client";

import { useEffect, useRef, useState } from "react";
import {
  Terminal,
  FileText,
  FilePen,
  FileEdit,
  Puzzle,
  Code,
  FolderTree,
  Search,
  FileSearch,
  ListTodo,
  ListChecks,
  ChevronDown,
  ChevronRight,
  CheckCircle,
  XCircle,
  Loader2,
  File,
  Folder,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Icon map — covers all 10 backend tools
// ---------------------------------------------------------------------------
const toolIcons: Record<string, React.ElementType> = {
  bash: Terminal,
  file_read: FileText,
  file_write: FilePen,
  file_edit: FileEdit,
  list_dir: FolderTree,
  glob: FileSearch,
  grep: Search,
  skill: Puzzle,
  planning: ListTodo,
  todo_update: ListChecks,
};

// ---------------------------------------------------------------------------
// Input summary — one-line description of what the tool is doing
// ---------------------------------------------------------------------------
function getInputSummary(
  toolName: string,
  input: Record<string, unknown>,
): string {
  switch (toolName) {
    case "bash":
      return (input.command as string) || JSON.stringify(input);
    case "file_read":
    case "file_write":
    case "file_edit":
      return (input.path as string) || JSON.stringify(input);
    case "list_dir":
      return (input.path as string) || ".";
    case "glob":
      return (input.pattern as string) || JSON.stringify(input);
    case "grep":
      return (input.pattern as string) || JSON.stringify(input);
    case "skill": {
      const action = (input.action as string) || "";
      const name = (input.name as string) || "";
      return name ? `${action} ${name}` : action || JSON.stringify(input);
    }
    case "planning":
      return (input.title as string) || "Create plan";
    case "todo_update":
      return (input.step_id as string) || JSON.stringify(input);
    default:
      return JSON.stringify(input);
  }
}

// ---------------------------------------------------------------------------
// Output summary — one-line result description for collapsed header
// ---------------------------------------------------------------------------
function getOutputSummary(
  toolName: string,
  output?: string,
  isError?: boolean,
): string {
  if (!output) return "";
  if (isError) return "Error";

  const lines = output.split("\n").filter(Boolean);

  switch (toolName) {
    case "bash": {
      if (lines.length === 0) return "";
      if (lines.length === 1) {
        const t = output.trim();
        return t.length > 40 ? t.slice(0, 40) + "…" : t;
      }
      return `${lines.length} lines`;
    }
    case "file_read":
      return `${lines.length} lines`;
    case "file_write": {
      const t = output.trim();
      return t.length > 40 ? t.slice(0, 40) + "…" : t;
    }
    case "file_edit": {
      const t = output.trim();
      return t.length > 40 ? t.slice(0, 40) + "…" : t;
    }
    case "list_dir":
      if (output === "(empty directory)") return "Empty";
      return `${lines.length} items`;
    case "glob":
      if (output === "No files matched.") return "No matches";
      return `${lines.filter((l) => !l.startsWith("...")).length} files`;
    case "grep":
      if (output === "No matches found.") return "No matches";
      return `${lines.filter((l) => !l.startsWith("...")).length} matches`;
    case "skill": {
      const first = lines[0] || "";
      return first.length > 40 ? first.slice(0, 40) + "…" : first;
    }
    case "planning":
      return "Plan created";
    case "todo_update":
      return "Updated";
    default:
      return "";
  }
}

// ---------------------------------------------------------------------------
// Structured output renderers
// ---------------------------------------------------------------------------

/** list_dir: tree-formatted output with folder/file icons */
function ListDirOutput({ output }: { output: string }) {
  const lines = output.split("\n").filter(Boolean);
  return (
    <div className="mt-1 max-h-60 overflow-auto rounded bg-bg-secondary p-2 text-xs">
      {lines.map((line, i) => {
        const isDir = line.trimEnd().endsWith("/");
        return (
          <div key={i} className="flex items-center gap-1 py-px font-mono">
            {isDir ? (
              <Folder size={11} className="shrink-0 text-yellow-400" />
            ) : (
              <File size={11} className="shrink-0 text-text-secondary" />
            )}
            <span className={isDir ? "text-yellow-400" : ""}>
              {line}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/** glob: newline-separated file paths */
function GlobOutput({ output }: { output: string }) {
  const lines = output.split("\n").filter(Boolean);
  return (
    <div className="mt-1 max-h-60 overflow-auto rounded bg-bg-secondary p-2 text-xs">
      {lines.map((line, i) => {
        // Cap message line (e.g. "... (N total, showing first 200)")
        if (line.startsWith("...")) {
          return (
            <div key={i} className="py-px text-text-secondary italic">
              {line}
            </div>
          );
        }
        return (
          <div key={i} className="flex items-center gap-1 py-px font-mono">
            <File size={11} className="shrink-0 text-text-secondary" />
            <span>{line}</span>
          </div>
        );
      })}
    </div>
  );
}

/** grep: path:line_number: content format */
function GrepOutput({ output }: { output: string }) {
  const lines = output.split("\n").filter(Boolean);
  return (
    <div className="mt-1 max-h-60 overflow-auto rounded bg-bg-secondary p-2 text-xs">
      {lines.map((line, i) => {
        // Cap message
        if (line.startsWith("...")) {
          return (
            <div key={i} className="py-px text-text-secondary italic">
              {line}
            </div>
          );
        }
        // Parse path:line_number: content
        const match = line.match(/^(.+?):(\d+):\s?(.*)$/);
        if (match) {
          const [, filePath, lineNum, content] = match;
          return (
            <div key={i} className="flex gap-1 py-px font-mono">
              <span className="shrink-0 text-accent">{filePath}</span>
              <span className="shrink-0 text-text-secondary">:{lineNum}:</span>
              <span className="truncate">{content}</span>
            </div>
          );
        }
        // No match message
        return (
          <div key={i} className="py-px text-text-secondary">
            {line}
          </div>
        );
      })}
    </div>
  );
}

/** file_edit: compact edit summary */
function FileEditOutput({ output }: { output: string }) {
  return (
    <div className="mt-1 flex items-center gap-2 rounded bg-bg-secondary px-2 py-1.5 text-xs">
      <CheckCircle size={12} className="shrink-0 text-success" />
      <span>{output}</span>
    </div>
  );
}

/** Picks the right structured renderer or falls back to raw pre */
function StructuredOutput({
  toolName,
  output,
  isError,
}: {
  toolName: string;
  output: string;
  isError?: boolean;
}) {
  // Errors always use raw display
  if (isError) {
    return (
      <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-danger/10 p-2 text-xs text-danger">
        {output}
      </pre>
    );
  }

  // "No matches" / "No files matched" / empty — just show text
  if (!output || output === "(empty directory)" || output === "No matches found." || output === "No files matched.") {
    return (
      <div className="mt-1 rounded bg-bg-secondary px-2 py-1.5 text-xs text-text-secondary italic">
        {output || "(empty)"}
      </div>
    );
  }

  switch (toolName) {
    case "list_dir":
      return <ListDirOutput output={output} />;
    case "glob":
      return <GlobOutput output={output} />;
    case "grep":
      return <GrepOutput output={output} />;
    case "file_edit":
      return <FileEditOutput output={output} />;
    default:
      return (
        <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-all rounded bg-bg-secondary p-2 text-xs">
          {output}
        </pre>
      );
  }
}

// ---------------------------------------------------------------------------
// ToolCallBlock — main component
// ---------------------------------------------------------------------------

interface ToolCallBlockProps {
  toolName: string;
  input: Record<string, unknown>;
  output?: string;
  isError?: boolean;
  status?: string;
  autoCollapseOnComplete?: boolean;
}

export default function ToolCallBlock({
  toolName,
  input,
  output,
  isError,
  status,
  autoCollapseOnComplete = true,
}: ToolCallBlockProps) {
  const isRunning = status === "running" || status === "pending";
  const [expanded, setExpanded] = useState(isRunning);
  const completionHandledRef = useRef(false);
  const Icon = toolIcons[toolName] || Code;
  const isExpanded = expanded || isRunning || !autoCollapseOnComplete;

  useEffect(() => {
    if (!autoCollapseOnComplete) {
      completionHandledRef.current = false;
      return;
    }

    if (isRunning) {
      completionHandledRef.current = false;
      return;
    }

    if (completionHandledRef.current) return;
    if (!status && output === undefined) return;

    const timer = window.setTimeout(() => {
      setExpanded(false);
      completionHandledRef.current = true;
    }, 900);

    return () => {
      window.clearTimeout(timer);
    };
  }, [autoCollapseOnComplete, isRunning, output, status]);

  const statusIcon =
    isRunning ? (
      <Loader2 size={12} className="animate-spin text-accent" />
    ) : isError ? (
      <XCircle size={12} className="text-danger" />
    ) : (
      <CheckCircle size={12} className="text-success" />
    );

  const outputSummary = getOutputSummary(toolName, output, isError);

  return (
    <div className="my-1.5">
      <button
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-2 py-1 text-left text-[12px] text-text-secondary transition hover:text-text-primary"
      >
        {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-bg-primary/80">
          <Icon size={12} className="text-text-secondary" />
        </div>
        <span className="truncate font-mono text-[12px] font-medium text-text-primary">
          {toolName}
        </span>
        <span className="flex-1" />
        {outputSummary && (
          <span className="shrink-0 text-[11px] text-text-secondary/60">
            {outputSummary}
          </span>
        )}
        {statusIcon}
      </button>

      {isExpanded && (
        <div className="ml-7 mt-1 rounded-[16px] bg-bg-primary/70 px-3 py-2.5">
          {/* Input section */}
          <div className="mb-2">
            <span className="text-[11px] text-text-secondary">Input</span>
            <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded-[12px] bg-bg-secondary p-2 text-[11px]">
              {JSON.stringify(input, null, 2)}
            </pre>
          </div>

          {/* Output section — structured or fallback */}
          {output !== undefined && (
            <div>
              <span className="text-[11px] text-text-secondary">Output</span>
              <StructuredOutput
                toolName={toolName}
                output={output}
                isError={isError}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
