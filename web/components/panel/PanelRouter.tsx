"use client";

import type { PanelType } from "@/lib/types";
import FilePreviewPanel from "./FilePreviewPanel";
import TaskOutputPanel from "./TaskOutputPanel";
import HtmlAppPanel from "./HtmlAppPanel";

interface PanelRouterProps {
  sessionId: string;
  activeType: PanelType;
}

export default function PanelRouter({ sessionId, activeType }: PanelRouterProps) {
  switch (activeType) {
    case "file_preview":
      return <FilePreviewPanel sessionId={sessionId} />;
    case "task_output":
      return <TaskOutputPanel sessionId={sessionId} />;
    case "html_app":
      return <HtmlAppPanel sessionId={sessionId} />;
    default:
      return null;
  }
}
