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
        className="w-full max-w-sm space-y-4 rounded-lg bg-bg-secondary p-6"
      >
        <h3 className="text-sm font-semibold">Create User</h3>

        {error && (
          <div className="rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
            {error}
          </div>
        )}

        <div>
          <label className="mb-1 block text-xs text-text-secondary">
            Username
          </label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="w-full rounded border border-border bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            minLength={2}
            maxLength={64}
            required
            autoFocus
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-text-secondary">
            Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-border bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            minLength={6}
            required
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-text-secondary">Role</label>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value as "user" | "admin")}
            className="w-full rounded border border-border bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
          >
            <option value="user">User</option>
            <option value="admin">Admin</option>
          </select>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-xs text-text-secondary transition hover:bg-bg-tertiary"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !username || !password}
            className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
          >
            {submitting ? "Creating..." : "Create"}
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
        className="w-full max-w-sm space-y-4 rounded-lg bg-bg-secondary p-6"
      >
        <h3 className="text-sm font-semibold">
          Reset Password: {user.username}
        </h3>

        {error && (
          <div className="rounded bg-danger/10 px-3 py-1.5 text-xs text-danger">
            {error}
          </div>
        )}

        <div>
          <label className="mb-1 block text-xs text-text-secondary">
            New Password
          </label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-border bg-bg-primary px-3 py-1.5 text-sm text-text-primary outline-none focus:border-accent"
            minLength={6}
            required
            autoFocus
          />
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded px-3 py-1.5 text-xs text-text-secondary transition hover:bg-bg-tertiary"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={submitting || !password}
            className="rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-hover disabled:opacity-50"
          >
            {submitting ? "Saving..." : "Reset Password"}
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
      className="cursor-pointer border-b border-border last:border-b-0 hover:bg-bg-tertiary/30"
    >
      <td className="px-4 py-2.5 text-sm">{user.username}</td>
      <td className="px-4 py-2.5">
        <button
          onClick={toggleRole}
          disabled={busy || isSelf}
          className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition ${
            user.role === "admin"
              ? "bg-accent/20 text-accent"
              : "bg-bg-tertiary text-text-secondary"
          } ${isSelf ? "cursor-default opacity-70" : "hover:opacity-80"}`}
          title={isSelf ? "Cannot change own role" : "Toggle role"}
        >
          {user.role === "admin" ? (
            <Shield size={11} />
          ) : (
            <UserIcon size={11} />
          )}
          {user.role}
        </button>
      </td>
      <td className="px-4 py-2.5 text-xs text-text-secondary">
        {user.department || "—"}
      </td>
      <td className="px-4 py-2.5 text-xs text-text-secondary">
        {user.employee_id || "—"}
      </td>
      <td className="px-4 py-2.5">
        <button
          onClick={toggleActive}
          disabled={busy || isSelf}
          className={`inline-flex items-center gap-1 text-xs transition ${
            user.is_active ? "text-success" : "text-text-secondary"
          } ${isSelf ? "cursor-default opacity-70" : "hover:opacity-80"}`}
          title={
            isSelf
              ? "Cannot deactivate yourself"
              : user.is_active
                ? "Deactivate"
                : "Activate"
          }
        >
          {user.is_active ? (
            <ToggleRight size={16} />
          ) : (
            <ToggleLeft size={16} />
          )}
          {user.is_active ? "Active" : "Inactive"}
        </button>
      </td>
      <td className="px-4 py-2.5">
        <span className="inline-flex items-center gap-1 text-xs text-text-secondary">
          <Puzzle size={11} />
          {user.installed_skill_count}
        </span>
      </td>
      <td className="px-4 py-2.5 text-xs text-text-secondary">
        {new Date(user.created_at).toLocaleDateString()}
      </td>
      <td className="px-4 py-2.5">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onResetPassword(user as unknown as User);
          }}
          className="inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
          title="Reset password"
        >
          <KeyRound size={11} />
          Reset
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
    <div className="mx-auto max-w-5xl px-6 py-6">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">User Management</h1>
          <p className="mt-0.5 text-xs text-text-secondary">
            {total} user{total !== 1 ? "s" : ""} total
          </p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-xs font-medium text-white transition hover:bg-accent-hover"
        >
          <Plus size={14} />
          Create User
        </button>
      </div>

      {/* Error */}
      {error && (
        <div className="mb-4 rounded bg-danger/10 px-3 py-2 text-xs text-danger">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border bg-bg-secondary text-left text-xs font-medium text-text-secondary">
              <th className="px-4 py-2.5">Username</th>
              <th className="px-4 py-2.5">Role</th>
              <th className="px-4 py-2.5">
                <span className="inline-flex items-center gap-1">
                  <Building2 size={11} />
                  Department
                </span>
              </th>
              <th className="px-4 py-2.5">
                <span className="inline-flex items-center gap-1">
                  <BadgeCheck size={11} />
                  Employee ID
                </span>
              </th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Skills</th>
              <th className="px-4 py-2.5">Created</th>
              <th className="px-4 py-2.5">Actions</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && users.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-xs text-text-secondary">
                  Loading...
                </td>
              </tr>
            ) : users.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-4 py-8 text-center text-xs text-text-secondary">
                  No users found
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

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-3">
          <button
            onClick={() => fetchUsers(page - 1, pageSize)}
            disabled={page <= 1}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary disabled:opacity-30"
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-xs text-text-secondary">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => fetchUsers(page + 1, pageSize)}
            disabled={page >= totalPages}
            className="rounded p-1 text-text-secondary transition hover:bg-bg-tertiary disabled:opacity-30"
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
