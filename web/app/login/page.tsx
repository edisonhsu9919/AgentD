"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/store/auth";
import AgentDLogoReveal from "@/components/brand/AgentDLogoReveal";
import LoginInteractiveBackdrop from "@/components/login/LoginInteractiveBackdrop";
import { ArrowRight } from "lucide-react";

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
    <div className="relative min-h-screen overflow-hidden bg-background text-foreground">
      <LoginInteractiveBackdrop />

      <div className="relative z-10 mx-auto grid min-h-screen w-full max-w-[1680px] grid-cols-1 gap-10 px-6 py-8 lg:grid-cols-[1.08fr_0.92fr] lg:items-center lg:px-16 xl:px-24">
        <section className="flex min-w-0 flex-col justify-center py-8 lg:py-16">
          <div className="max-w-[42rem] space-y-10">
            <AgentDLogoReveal className="h-16 w-auto max-w-[min(25rem,82vw)] md:h-[76px]" />
            <div className="space-y-5">
              <h1 className="page-title text-[clamp(2.85rem,5vw,4.8rem)] leading-[0.98] tracking-[-0.045em]">
                把复杂工作
                <br />
                交付成清晰结果
              </h1>
              <p className="max-w-xl text-sm leading-7 text-text-secondary md:text-[15px]">
                面向企业知识、长链路执行与多轮协作的智能工作台。
              </p>
            </div>
          </div>
        </section>

        <section className="flex min-w-0 items-center justify-center py-6 lg:justify-end lg:py-16">
          <form
            onSubmit={handleSubmit}
            className="login-auth-card w-full max-w-[29rem] space-y-8 px-7 py-9 md:px-12 md:py-14"
          >
            <div className="space-y-2">
              <h2 className="text-[1.95rem] font-semibold tracking-[-0.045em]">
                登录 AgentD
              </h2>
              <p className="text-sm leading-7 text-text-secondary">
                使用企业账号进入智能工作区
              </p>
            </div>

            {error && (
              <div className="rounded-[20px] bg-danger/10 px-4 py-3 text-sm text-danger">
                {error}
              </div>
            )}

            <div className="space-y-5">
              <div>
                <label className="font-caption mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-text-secondary">
                  企业邮箱 / 用户名
                </label>
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="field-input"
                  placeholder="name@company.com"
                  autoFocus
                  required
                />
              </div>

              <div>
                <label className="font-caption mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-text-secondary">
                  密码
                </label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="field-input"
                  placeholder="输入密码"
                  required
                />
              </div>
            </div>

            <button
              type="submit"
              disabled={isLoading || !username || !password}
              className="btn-primary w-full px-5 py-4 text-base font-medium"
            >
              {isLoading ? "正在登录..." : "进入工作台"}
              {!isLoading && <ArrowRight size={18} />}
            </button>
          </form>
        </section>
      </div>
    </div>
  );
}
