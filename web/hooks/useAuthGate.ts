"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";

interface UseAuthGateOptions {
  requireAdmin?: boolean;
  fallbackPath?: string;
}

export default function useAuthGate({
  requireAdmin = false,
  fallbackPath = "/chat",
}: UseAuthGateOptions = {}) {
  const router = useRouter();
  const { hydrate, fetchMe } = useAuthStore();
  const token = useAuthStore((s) => s.token);
  const user = useAuthStore((s) => s.user);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  useEffect(() => {
    let cancelled = false;

    const resolveAuth = async () => {
      if (token === null && typeof window !== "undefined") {
        const stored = localStorage.getItem("agentd_token");
        if (!stored) {
          router.replace("/login");
          return;
        }
      }

      if (!token) return;

      await fetchMe();
      if (!cancelled) {
        setReady(true);
      }
    };

    resolveAuth();

    return () => {
      cancelled = true;
    };
  }, [token, fetchMe, router]);

  useEffect(() => {
    if (ready && requireAdmin && user && user.role !== "admin") {
      router.replace(fallbackPath);
    }
  }, [ready, requireAdmin, user, router, fallbackPath]);

  return {
    ready: ready && (!requireAdmin || user?.role === "admin"),
    user,
  };
}
