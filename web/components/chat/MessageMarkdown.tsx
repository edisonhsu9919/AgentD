"use client";

import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import CopyButton from "./CopyButton";

function extractText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(extractText).join("");
  if (typeof node === "object" && "props" in node) {
    const props = node.props as { children?: ReactNode };
    return extractText(props.children);
  }
  return "";
}

const markdownComponents: Components = {
  pre({ children }) {
    const text = extractText(children).replace(/\n$/, "");
    return (
      <div className="chat-code-block group/code">
        <CopyButton
          text={text}
          title="复制代码"
          className="chat-code-copy h-7 w-7 justify-center p-0"
        />
        <pre>{children}</pre>
      </div>
    );
  },
  blockquote({ children }) {
    const text = extractText(children).trim();
    return (
      <blockquote className="group/quote">
        <div className="chat-block-copy">
          <CopyButton
            text={text}
            label="复制"
            title="复制引用"
            className="px-2 py-1 text-[10px]"
          />
        </div>
        {children}
      </blockquote>
    );
  },
};

export default function MessageMarkdown({ children }: { children: string }) {
  return (
    <ReactMarkdown components={markdownComponents} remarkPlugins={[remarkGfm]}>
      {children}
    </ReactMarkdown>
  );
}
