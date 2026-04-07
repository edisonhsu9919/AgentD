"use client";

import { useEffect, useRef, useCallback } from "react";
import type { HtmlSandboxContent } from "@/lib/types";

interface SandboxFrameProps {
  content: HtmlSandboxContent;
  onSubmit: (data: Record<string, unknown>) => void;
}

/**
 * Renders an html_sandbox payload inside a sandboxed iframe.
 * Listens for postMessage from the iframe and calls onSubmit
 * when a valid panel_submit message arrives.
 */
export default function SandboxFrame({ content, onSubmit }: SandboxFrameProps) {
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const handleMessage = useCallback(
    (event: MessageEvent) => {
      // Validate origin: only accept messages from our iframe
      if (
        iframeRef.current &&
        event.source !== iframeRef.current.contentWindow
      ) {
        return;
      }

      const data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.type !== "panel_submit") return;

      // Validate interaction_id matches current content
      if (data.interaction_id !== content.interaction_id) return;

      onSubmit(data.data || {});
    },
    [content.interaction_id, onSubmit],
  );

  useEffect(() => {
    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, [handleMessage]);

  const sandboxAttr = content.permissions.length > 0
    ? content.permissions.join(" ")
    : "allow-scripts";

  return (
    <iframe
      ref={iframeRef}
      srcDoc={content.html}
      sandbox={sandboxAttr}
      className="w-full border-0"
      style={{ height: content.height || 400, minHeight: 200 }}
      title="Panel App"
    />
  );
}
