"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import { Bot, ArrowLeft } from "lucide-react";
import Link from "next/link";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const { hydrate, fetchMe } = useAuthStore();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  const currentToken = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);

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

  // Gate: admin only
  useEffect(() => {
    if (ready && user && user.role !== "admin") {
      router.replace("/chat");
    }
  }, [ready, user, router]);

  if (!ready || !user || user.role !== "admin") {
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
          <Bot size={20} className="text-accent" />
          <span className="text-sm font-semibold">AgentD Admin</span>
        </div>
        <Link
          href="/chat"
          className="flex items-center gap-1.5 text-xs text-text-secondary transition hover:text-text-primary"
        >
          <ArrowLeft size={14} />
          Back to Chat
        </Link>
      </div>

      {/* Sub-navigation */}
      <div className="flex border-b border-border bg-bg-secondary px-4">
        <Link
          href="/admin"
          className={`px-3 py-2 text-xs font-medium transition ${
            pathname === "/admin" || pathname?.startsWith("/admin/users")
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Users
        </Link>
        <Link
          href="/admin/settings"
          className={`px-3 py-2 text-xs font-medium transition ${
            pathname?.startsWith("/admin/settings")
              ? "border-b-2 border-accent text-accent"
              : "text-text-secondary hover:text-text-primary"
          }`}
        >
          Settings
        </Link>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-auto">{children}</div>
    </div>
  );
}
