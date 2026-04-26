"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

interface CopyButtonProps {
  text: string;
  label?: string;
  title?: string;
  className?: string;
}

export default function CopyButton({
  text,
  label,
  title = "复制",
  className = "",
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title={copied ? "已复制" : title}
      className={`inline-flex items-center gap-1.5 rounded-full text-text-secondary/55 transition hover:bg-bg-tertiary/70 hover:text-text-primary ${className}`}
    >
      {copied ? <Check size={12} /> : <Copy size={12} />}
      {label && <span>{copied ? "已复制" : label}</span>}
    </button>
  );
}
