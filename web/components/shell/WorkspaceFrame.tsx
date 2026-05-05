"use client";

import { usePathname } from "next/navigation";
import Sidebar from "@/components/layout/Sidebar";
import FullPageLoader from "@/components/ui/FullPageLoader";
import useAuthGate from "@/hooks/useAuthGate";
import AppChrome from "@/components/shell/AppChrome";

function ProtectedWorkspaceFrame({
  children,
  pathname,
}: {
  children: React.ReactNode;
  pathname: string;
}) {
  const isAdminRoute = pathname.startsWith("/admin");
  const isChatRoute = pathname.startsWith("/chat");
  const isScrollableRoute =
    pathname.startsWith("/admin") ||
    pathname.startsWith("/user") ||
    pathname.startsWith("/extensions");
  const { ready, user } = useAuthGate({
    requireAdmin: isAdminRoute,
  });

  if (!ready || (isAdminRoute && user?.role !== "admin")) {
    return <FullPageLoader />;
  }

  return (
    <AppChrome
      navItems={
        isAdminRoute
          ? [
              {
                href: "/admin",
                label: "用户管理",
                active:
                  pathname === "/admin" || pathname.startsWith("/admin/users"),
              },
              {
                href: "/admin/settings",
                label: "系统设置",
                active: pathname.startsWith("/admin/settings"),
              },
            ]
          : undefined
      }
      sidebar={isChatRoute ? <Sidebar /> : undefined}
      sidebarClassName="w-[20rem]"
      bodyClassName="flex min-h-0 flex-1 overflow-hidden"
      contentClassName={
        isScrollableRoute
          ? "min-h-0 min-w-0 flex-1 overflow-auto"
          : "min-h-0 min-w-0 flex-1 overflow-hidden"
      }
    >
      <div
        key={pathname}
        className="workspace-route-stage flex min-h-0 flex-1 flex-col overflow-hidden"
      >
        {children}
      </div>
    </AppChrome>
  );
}

export default function WorkspaceFrame({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname() || "/";
  const isWorkspaceRoute =
    pathname.startsWith("/chat") ||
    pathname.startsWith("/knowledge") ||
    pathname.startsWith("/extensions") ||
    pathname.startsWith("/square") ||
    pathname.startsWith("/user") ||
    pathname.startsWith("/admin");

  if (!isWorkspaceRoute) {
    return <>{children}</>;
  }

  return (
    <ProtectedWorkspaceFrame pathname={pathname}>
      {children}
    </ProtectedWorkspaceFrame>
  );
}
