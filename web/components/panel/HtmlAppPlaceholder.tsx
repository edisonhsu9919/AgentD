"use client";

import { Globe } from "lucide-react";

export default function HtmlAppPlaceholder() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-lg bg-accent/10">
        <Globe size={24} className="text-accent/50" />
      </div>
      <div>
        <p className="text-sm font-medium text-text-secondary">
          Interactive App
        </p>
        <p className="mt-1 max-w-[260px] text-xs text-text-secondary/60">
          Skills with interactive UI will render here. This capability is
          coming in a future update.
        </p>
      </div>
    </div>
  );
}
