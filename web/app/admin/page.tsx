"use client";

import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useAdminStore } from "@/store/admin";
import { useAuthStore } from "@/store/auth";
import {
  Plus,
  Shield,
  User as UserIcon,
  ToggleLeft,
  ToggleRight,
  ChevronLeft,
  ChevronRight,
  KeyRound,
  Building2,
  BadgeCheck,
  Puzzle,
} from "lucide-react";
import type { User } from "@/lib/types";

// ---------------------------------------------------------------------------
// Create User Dialog
// ---------------------------------------------------------------------------

function CreateUserDialog({ onClose }: { onClose: () => void }) {
  const createUser = useAdminStore((s) => s.createUser);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"user" | "admin">("user");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await createUser({ username, password, role });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create user");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <form
        onSubmit={handleSubmit}
        className="surface-card w-full max-w-md space-y-5 px-6 py-6"
      >
        <h3 className="text-lg font-semibold">创建用户</h3>

        {error && (
          <div className="rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
            {error}
          </div>
        )}

        <div>
          <label className="font-caption mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-text-secondary">
            用户名
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="field-input"
            minLength={2}
            maxLength={64}
            required
            autoFocus
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
            minLength={6}
            required
          />
        </div>

        <div>
          <label className="font-caption mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-text-secondary">
            角色
          </label>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as "user" | "admin")}
            className="field-input"
          >
            <option value="user">成员</option>
            <option value="admin">管理员</option>
          </select>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary px-4 py-2 text-sm"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {submitting ? "创建中..." : "创建用户"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reset Password Dialog
// ---------------------------------------------------------------------------

function ResetPasswordDialog({
  user,
  onClose,
}: {
  user: User;
  onClose: () => void;
}) {
  const updateUser = useAdminStore((s) => s.updateUser);
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await updateUser(user.id, { password });
      onClose();
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to reset password",
      );
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <form
        onSubmit={handleSubmit}
        className="surface-card w-full max-w-md space-y-5 px-6 py-6"
      >
        <h3 className="text-lg font-semibold">
          重置密码：{user.username}
        </h3>

        {error && (
          <div className="rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
            {error}
          </div>
        )}

        <div>
          <label className="font-caption mb-2 block text-xs font-medium uppercase tracking-[0.14em] text-text-secondary">
            新密码
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="field-input"
            minLength={6}
            required
            autoFocus
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="btn-secondary px-4 py-2 text-sm"
          >
            取消
          </button>
          <button
            type="submit"
            disabled={submitting || !password}
            className="btn-primary px-4 py-2 text-sm font-medium disabled:opacity-50"
          >
            {submitting ? "保存中..." : "重置密码"}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// User Row
// ---------------------------------------------------------------------------

function UserRow({
  user,
  isSelf,
  onResetPassword,
  onViewDetail,
}: {
  user: {
    id: string;
    username: string;
    role: "admin" | "user";
    is_active: boolean;
    department: string | null;
    employee_id: string | null;
    created_at: string;
    installed_skill_count: number;
  };
  isSelf: boolean;
  onResetPassword: (user: User) => void;
  onViewDetail: (userId: string) => void;
}) {
  const updateUser = useAdminStore((s) => s.updateUser);
  const [busy, setBusy] = useState(false);
  const cellClass = "bg-white/76 px-4 py-3 text-sm transition group-hover:bg-white/92";
  const mutedCellClass =
    "bg-white/76 px-4 py-3 text-xs text-text-secondary transition group-hover:bg-white/92";

  const toggleActive = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isSelf) return;
    setBusy(true);
    try {
      await updateUser(user.id, { is_active: !user.is_active });
    } finally {
      setBusy(false);
    }
  };

  const toggleRole = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (isSelf) return;
    setBusy(true);
    try {
      await updateUser(user.id, {
        role: user.role === "admin" ? "user" : "admin",
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr
      onClick={() => onViewDetail(user.id)}
      className="group cursor-pointer"
    >
      <td className={`${cellClass} rounded-l-[18px] font-medium text-text-primary`}>
        {user.username}
      </td>
      <td className={cellClass}>
        <button
          onClick={toggleRole}
          disabled={busy || isSelf}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium transition ${
            user.role === "admin"
              ? "bg-accent/12 text-accent"
              : "bg-bg-tertiary/80 text-text-secondary"
          } ${isSelf ? "cursor-default opacity-70" : "hover:opacity-80"}`}
          title={isSelf ? "不能修改自己的角色" : "切换角色"}
        >
          {user.role === "admin" ? (
            <Shield size={11} />
          ) : (
            <UserIcon size={11} />
          )}
          {user.role === "admin" ? "管理员" : "成员"}
        </button>
      </td>
      <td className={mutedCellClass}>
        {user.department || "—"}
      </td>
      <td className={mutedCellClass}>
        {user.employee_id || "—"}
      </td>
      <td className={cellClass}>
        <button
          onClick={toggleActive}
          disabled={busy || isSelf}
          className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium transition ${
            user.is_active
              ? "bg-success/10 text-success"
              : "bg-bg-tertiary/80 text-text-secondary"
          } ${isSelf ? "cursor-default opacity-70" : "hover:opacity-80"}`}
          title={
            isSelf
              ? "不能停用自己"
              : user.is_active
                ? "停用"
                : "启用"
          }
        >
          {user.is_active ? (
            <ToggleRight size={16} />
          ) : (
            <ToggleLeft size={16} />
          )}
          {user.is_active ? "启用中" : "已停用"}
        </button>
      </td>
      <td className={cellClass}>
        <span className="inline-flex items-center gap-1 rounded-full bg-bg-tertiary/70 px-2.5 py-1 text-xs text-text-secondary">
          <Puzzle size={11} />
          {user.installed_skill_count}
        </span>
      </td>
      <td className={mutedCellClass}>
        {new Date(user.created_at).toLocaleDateString()}
      </td>
      <td className={`${cellClass} rounded-r-[18px]`}>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onResetPassword(user as unknown as User);
          }}
          className="inline-flex items-center gap-1 rounded-full bg-bg-tertiary/70 px-2.5 py-1 text-xs text-text-secondary transition hover:bg-accent/10 hover:text-accent"
          title="重置密码"
        >
          <KeyRound size={11} />
          重置
        </button>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const router = useRouter();
  const { users, total, page, pageSize, isLoading, error, fetchUsers } =
    useAdminStore();
  const currentUser = useAuthStore((s) => s.user);

  const [showCreate, setShowCreate] = useState(false);
  const [resetTarget, setResetTarget] = useState<User | null>(null);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-[1440px] flex-col overflow-hidden px-6 py-6">
      <div className="mb-4 flex shrink-0 flex-wrap items-end justify-between gap-4">
        <div className="space-y-1">
          <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-text-secondary/65">
            后台 / 用户
          </div>
          <h1 className="text-[22px] font-semibold tracking-[-0.02em] text-text-primary">
            用户管理
          </h1>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-xs leading-5 text-text-secondary">
              管理账号、角色、启停状态与技能安装概览。
            </p>
            <span className="pill pill-muted">{total} 个账号</span>
          </div>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="btn-primary px-5 py-3 text-sm font-medium"
        >
          <Plus size={14} />
          创建用户
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 shrink-0 rounded-[18px] bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="min-h-0 flex-1 overflow-hidden rounded-[28px] bg-bg-primary/42 p-2 shadow-[0_18px_46px_rgba(42,41,51,0.055)]">
        <div className="h-full overflow-y-auto pr-1">
          <table className="w-full border-separate border-spacing-y-2">
            <thead className="sticky top-0 z-10">
              <tr className="bg-bg-primary/92 text-left text-xs font-medium text-text-secondary backdrop-blur">
                <th className="rounded-l-[16px] px-4 py-3">用户名</th>
                <th className="px-4 py-3">角色</th>
                <th className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1">
                    <Building2 size={11} />
                    部门
                  </span>
                </th>
                <th className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1">
                    <BadgeCheck size={11} />
                    工号
                  </span>
                </th>
                <th className="px-4 py-2.5">状态</th>
                <th className="px-4 py-2.5">技能数</th>
                <th className="px-4 py-2.5">创建时间</th>
                <th className="rounded-r-[16px] px-4 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && users.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="rounded-[20px] bg-white/72 px-4 py-10 text-center text-xs text-text-secondary"
                  >
                    加载中...
                  </td>
                </tr>
              ) : users.length === 0 ? (
                <tr>
                  <td
                    colSpan={8}
                    className="rounded-[20px] bg-white/72 px-4 py-10 text-center text-xs text-text-secondary"
                  >
                    暂无用户
                  </td>
                </tr>
              ) : (
                users.map((u) => (
                  <UserRow
                    key={u.id}
                    user={u}
                    isSelf={u.id === currentUser?.id}
                    onResetPassword={setResetTarget}
                    onViewDetail={(id) => router.push(`/admin/users/${id}`)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex shrink-0 items-center justify-center gap-3">
          <button
            onClick={() => fetchUsers(page - 1, pageSize)}
            disabled={page <= 1}
            className="rounded-full bg-bg-primary p-2 text-text-secondary transition hover:bg-bg-tertiary disabled:opacity-30"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-xs text-text-secondary">
            第 {page} / {totalPages} 页
          </span>
          <button
            onClick={() => fetchUsers(page + 1, pageSize)}
            disabled={page >= totalPages}
            className="rounded-full bg-bg-primary p-2 text-text-secondary transition hover:bg-bg-tertiary disabled:opacity-30"
          >
            <ChevronRight size={16} />
          </button>
        </div>
      )}

      {/* Dialogs */}
      {showCreate && (
        <CreateUserDialog onClose={() => setShowCreate(false)} />
      )}
      {resetTarget && (
        <ResetPasswordDialog
          user={resetTarget}
          onClose={() => setResetTarget(null)}
        />
      )}
    </div>
  );
}
