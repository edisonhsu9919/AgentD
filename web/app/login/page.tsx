"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";

export default function LoginPage() {
  const router = useRouter();
  const { login, isLoading, error } = useAuthStore();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await login(username, password);
      router.push("/chat");
    } catch {
      // error is set in store
    }
  };

  return (
    <div className="flex h-screen items-center justify-center">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm space-y-6 rounded-lg bg-bg-secondary p-8"
      >
        <div className="text-center">
          <h1 className="text-2xl font-bold text-text-primary">AgentD</h1>
          <p className="mt-1 text-sm text-text-secondary">
            Enterprise AI Agent Platform
          </p>
        </div>

        {error && (
          <div className="rounded bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-sm text-text-secondary">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full rounded border border-border bg-bg-primary px-3 py-2 text-text-primary outline-none focus:border-accent"
              autoFocus
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-sm text-text-secondary">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded border border-border bg-bg-primary px-3 py-2 text-text-primary outline-none focus:border-accent"
              required
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={isLoading || !username || !password}
          className="w-full rounded bg-accent py-2 text-sm font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
        >
          {isLoading ? "Signing in..." : "Sign in"}
        </button>
      </form>
    </div>
  );
}
