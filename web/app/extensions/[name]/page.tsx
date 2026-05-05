"use client";

import { useParams } from "next/navigation";
import ExtensionPageRenderer from "@/components/extensions/ExtensionPageRenderer";

export default function ExtensionPage() {
  const params = useParams<{ name: string }>();
  const name = decodeURIComponent(params.name);

  return (
    <div className="flex h-full min-h-0 overflow-hidden">
      <div className="min-h-0 min-w-0 flex-1 overflow-auto">
        <ExtensionPageRenderer name={name} />
      </div>
    </div>
  );
}
