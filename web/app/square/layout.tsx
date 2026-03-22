"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { Bot, ArrowLeft, Store } from "lucide-react";
import Link from "next/link";

export default function SquareLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const { hydrate, fetchMe } = useAuthStore();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  const currentToken = useAuthStore((s) => s.token);

  useEffect(() => {
    if (currentToken === null && typeof window !== "undefined") {
      const stored = localStorage.getItem("agentd_token");
      if (!stored) {
        router.replace("/login");
        return;
      }
    }
    if (currentToken) {
      fetchMe().then(() => setReady(true));
    }
  }, [currentToken, fetchMe, router]);

  if (!ready) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      {/* Top bar */}
      <div className="flex items-center justify-between border-b border-border bg-bg-secondary px-4 py-3">
        <div className="flex items-center gap-3">
          <Store size={20} className="text-accent" />
          <span className="text-sm font-semibold">Skill Square</span>
        </div>
        <Link
          href="/chat"
          className="flex items-center gap-1.5 text-xs text-text-secondary transition hover:text-text-primary"
        >
          <ArrowLeft size={14} />
          Back to Chat
        </Link>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden">{children}</div>
    </div>
  );
}
