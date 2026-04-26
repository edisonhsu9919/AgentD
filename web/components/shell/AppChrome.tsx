"use client";

import { useLayoutEffect, useMemo, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BookOpen,
  LogOut,
  MessageSquareText,
  Shield,
  Sparkles,
} from "lucide-react";
import AgentDLockup from "@/components/brand/AgentDLockup";
import { useAuthStore } from "@/store/auth";

export interface AppChromeNavItem {
  href: string;
  label: string;
  active?: boolean;
}

interface AppChromeProps {
  navItems?: AppChromeNavItem[];
  children: React.ReactNode;
  contentClassName?: string;
  bodyClassName?: string;
  sidebar?: React.ReactNode;
  sidebarClassName?: string;
  headerActions?: React.ReactNode;
}

export default function AppChrome({
  navItems,
  children,
  contentClassName = "overflow-auto",
  bodyClassName = "flex-1",
  sidebar,
  sidebarClassName = "w-[20rem]",
  headerActions,
}: AppChromeProps) {
  const pathname = usePathname();
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const userInitial = user?.username?.trim().charAt(0).toUpperCase() || "A";
  const navRef = useRef<HTMLDivElement | null>(null);
  const indicatorRef = useRef<HTMLSpanElement | null>(null);
  const itemRefs = useRef<Record<string, HTMLAnchorElement | null>>({});
  const subNavRef = useRef<HTMLDivElement | null>(null);
  const subIndicatorRef = useRef<HTMLSpanElement | null>(null);
  const subItemRefs = useRef<Record<string, HTMLAnchorElement | null>>({});

  const primaryNavItems = useMemo(
    () => [
      {
        href: "/chat",
        label: "对话工作台",
        active: pathname?.startsWith("/chat"),
        icon: MessageSquareText,
      },
      {
        href: "/knowledge",
        label: "知识中心",
        active: pathname?.startsWith("/knowledge"),
        icon: BookOpen,
      },
      {
        href: "/square",
        label: "技能广场",
        active: pathname?.startsWith("/square"),
        icon: Sparkles,
      },
      ...(user?.role === "admin"
        ? [
            {
              href: "/admin",
              label: "后台管理",
              active: pathname?.startsWith("/admin"),
              icon: Shield,
            },
          ]
        : []),
    ],
    [pathname, user?.role],
  );

  useLayoutEffect(() => {
    const activeItem = primaryNavItems.find((item) => item.active);
    const navEl = navRef.current;
    const indicatorEl = indicatorRef.current;
    const activeEl = activeItem ? itemRefs.current[activeItem.href] : null;

    if (!navEl || !indicatorEl) {
      return;
    }

    if (!activeEl) {
      indicatorEl.style.opacity = "0";
      return;
    }

    const navRect = navEl.getBoundingClientRect();
    const activeRect = activeEl.getBoundingClientRect();
    const x = activeRect.left - navRect.left;

    indicatorEl.style.opacity = "1";
    indicatorEl.style.width = `${activeRect.width}px`;
    indicatorEl.style.transform = `translateX(${x}px)`;
  }, [pathname, primaryNavItems]);

  useLayoutEffect(() => {
    const activeItem = navItems?.find((item) => item.active);
    const navEl = subNavRef.current;
    const indicatorEl = subIndicatorRef.current;
    const activeEl = activeItem ? subItemRefs.current[activeItem.href] : null;

    if (!navEl || !indicatorEl) {
      return;
    }

    if (!activeEl) {
      indicatorEl.style.opacity = "0";
      return;
    }

    const navRect = navEl.getBoundingClientRect();
    const activeRect = activeEl.getBoundingClientRect();
    const x = activeRect.left - navRect.left;

    indicatorEl.style.opacity = "1";
    indicatorEl.style.width = `${activeRect.width}px`;
    indicatorEl.style.transform = `translateX(${x}px)`;
  }, [pathname, navItems]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <header className="border-b border-border bg-white/90 backdrop-blur">
        <div className="mx-auto flex h-16 w-full max-w-[1600px] items-center justify-between gap-5 px-5 md:h-[4.5rem] md:px-8">
          <div className="flex min-w-0 items-center gap-4 md:gap-6">
            <Link
              href="/chat"
              className="transition-opacity hover:opacity-80"
              aria-label="AgentD"
            >
              <AgentDLockup className="h-16 w-auto md:h-[5rem]" />
            </Link>

            <nav
              ref={navRef}
              className="relative hidden min-w-0 items-center gap-1 md:flex"
            >
              <span
                ref={indicatorRef}
                className="pointer-events-none absolute top-1/2 h-11 -translate-y-1/2 rounded-full bg-[rgba(87,73,244,0.12)] shadow-[0_8px_22px_rgba(87,73,244,0.10)] transition-[transform,width,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
                style={{ opacity: 0, width: 0 }}
              />
              {primaryNavItems.map((item) => {
                const Icon = item.icon;

                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    ref={(node) => {
                      itemRefs.current[item.href] = node;
                    }}
                    className={`relative z-10 inline-flex h-11 w-[8.75rem] items-center justify-center gap-2 rounded-full px-4 text-[16px] leading-none transition ${
                      item.active
                        ? "font-medium text-accent"
                        : "text-text-secondary hover:text-foreground"
                    }`}
                  >
                    <Icon size={15} strokeWidth={1.9} />
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </nav>
          </div>

          <div className="flex items-center gap-3">
            {headerActions}
            {user && (
              <>
                <Link
                  href="/user"
                  className={`hidden h-11 w-11 items-center justify-center rounded-full border text-[16px] font-semibold shadow-[0_12px_32px_rgba(42,41,51,0.06)] transition md:flex ${
                    pathname?.startsWith("/user")
                      ? "border-accent/40 bg-accent/10 text-accent"
                      : "border-border bg-white text-text-primary hover:border-accent/30 hover:bg-accent/6"
                  }`}
                  title={user.username}
                  aria-label={user.username}
                >
                  {userInitial}
                </Link>
                <button
                  onClick={logout}
                  className="inline-flex items-center justify-center gap-2 rounded-full bg-bg-primary px-4 py-2 text-sm text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
                  title="退出登录"
                >
                  <LogOut size={14} />
                  退出
                </button>
              </>
            )}
          </div>
        </div>
      </header>

      {navItems && navItems.length > 0 && (
        <div className="bg-transparent">
          <div className="mx-auto flex w-full max-w-[1600px] items-center px-6 py-2.5 md:px-10">
            <nav
              ref={subNavRef}
              className="relative inline-flex items-center gap-1"
            >
              <span
                ref={subIndicatorRef}
                className="pointer-events-none absolute top-1/2 h-8 -translate-y-1/2 rounded-full bg-[rgba(87,73,244,0.10)] shadow-[0_8px_18px_rgba(87,73,244,0.08)] transition-[transform,width,opacity] duration-500 ease-[cubic-bezier(0.22,1,0.36,1)]"
                style={{ opacity: 0, width: 0 }}
              />
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  ref={(node) => {
                    subItemRefs.current[item.href] = node;
                  }}
                  className={`relative z-10 inline-flex h-8 w-[6.75rem] items-center justify-center rounded-full px-3 text-[13px] leading-none transition ${
                    item.active
                      ? "font-medium text-accent"
                      : "text-text-secondary hover:text-foreground"
                  }`}
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </div>
      )}

      <div className={`min-h-0 ${bodyClassName}`}>
        {sidebar && (
          <aside className={`min-h-0 shrink-0 ${sidebarClassName}`}>{sidebar}</aside>
        )}

        <main className={`flex min-h-0 min-w-0 flex-col ${contentClassName}`}>
          {children}
        </main>
      </div>
    </div>
  );
}
