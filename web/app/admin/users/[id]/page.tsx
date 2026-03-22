"use client";

import { useEffect, useState, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { useAdminStore } from "@/store/admin";
import UserProfileCard from "@/components/user/UserProfileCard";
import UserSkillList from "@/components/user/UserSkillList";
import SkillDetailDrawer from "@/components/user/SkillDetailDrawer";
import MessagePart from "@/components/chat/MessagePart";
import {
  ArrowLeft,
  Puzzle,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
  Eye,
  Bot,
  User as UserIcon,
  Wrench,
} from "lucide-react";
import type { Session, Message } from "@/lib/types";

// ---------------------------------------------------------------------------
// Session list sub-component
// ---------------------------------------------------------------------------

function SessionListPanel({
  sessions,
  total,
  page,
  loading,
  selectedId,
  onSelect,
  onPageChange,
}: {
  sessions: Session[];
  total: number;
  page: number;
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / 20));

  if (loading && sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-xs text-text-secondary">
        Loading sessions...
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-8 text-xs text-text-secondary">
        <MessageSquare size={24} />
        <span>No sessions found</span>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {sessions.map((s) => (
        <button
          key={s.id}
          onClick={() => onSelect(s.id)}
          className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left transition ${
            selectedId === s.id
              ? "border-accent/40 bg-accent/5"
              : "border-transparent hover:bg-bg-tertiary/50"
          }`}
        >
          <MessageSquare size={13} className="mt-0.5 shrink-0 text-text-secondary" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-xs font-medium">
              {s.title || "Untitled"}
            </p>
            <div className="mt-0.5 flex items-center gap-2 text-[10px] text-text-secondary">
              <span className={`rounded px-1 py-0.5 ${
                s.status === "idle" ? "bg-bg-tertiary" :
                s.status === "running" ? "bg-accent/20 text-accent" :
                s.status === "error" ? "bg-danger/20 text-danger" :
                "bg-bg-tertiary"
              }`}>
                {s.status}
              </span>
              <span>{new Date(s.created_at).toLocaleDateString()}</span>
              {s.token_usage.total > 0 && (
                <span>{s.token_usage.total.toLocaleString()} tokens</span>
              )}
            </div>
          </div>
        </button>
      ))}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="rounded p-0.5 text-text-secondary hover:bg-bg-tertiary disabled:opacity-30"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-[10px] text-text-secondary">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            className="rounded p-0.5 text-text-secondary hover:bg-bg-tertiary disabled:opacity-30"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Read-only message viewer
// ---------------------------------------------------------------------------

const roleConfig = {
  user: { icon: UserIcon, label: "User", bubble: "bg-accent/10 border-accent/20" },
  assistant: { icon: Bot, label: "Agent", bubble: "bg-bg-secondary border-border" },
  tool: { icon: Wrench, label: "Tool", bubble: "bg-bg-secondary border-border" },
};

function ReadOnlyMessage({ message }: { message: Message }) {
  const config = roleConfig[message.role] || roleConfig.assistant;
  const Icon = config.icon;

  const toolNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const part of message.parts) {
      if (part.type === "tool_call") {
        map.set(part.tool_call_id, part.tool_name);
      }
    }
    return map;
  }, [message.parts]);

  return (
    <div className={`flex ${message.role === "user" ? "justify-end" : "justify-start"} mb-3`}>
      <div className={`min-w-0 max-w-[85%] rounded-lg border px-3 py-2 ${config.bubble}`}>
        <div className="mb-1 flex items-center gap-1.5">
          <Icon size={11} className="text-text-secondary" />
          <span className="text-[10px] font-medium text-text-secondary">{config.label}</span>
        </div>
        <div className="space-y-1">
          {message.parts.map((part, i) => (
            <MessagePart key={i} part={part} toolNameMap={toolNameMap} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SessionMessageViewer({
  messages,
  loading,
}: {
  messages: Message[];
  loading: boolean;
}) {
  if (loading) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        Loading messages...
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        No messages in this session
      </div>
    );
  }

  return (
    <div className="space-y-1 p-3">
      {/* Read-only banner */}
      <div className="mb-3 flex items-center gap-1.5 rounded border border-border bg-bg-tertiary/50 px-3 py-1.5 text-[10px] text-text-secondary">
        <Eye size={11} />
        Read-only — viewing session history
      </div>

      {messages.map((msg) => (
        <ReadOnlyMessage key={msg.id} message={msg} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main admin user detail page
// ---------------------------------------------------------------------------

type DetailTab = "skills" | "sessions";

export default function AdminUserDetailPage() {
  const params = useParams();
  const userId = params.id as string;

  const {
    userDetail,
    userDetailLoading,
    fetchUserDetail,
    userSkills,
    userSkillsLoading,
    fetchUserSkills,
    toggleUserSkill,
    selectedSkill,
    skillDetail,
    skillDetailLoading,
    selectSkill,
    clearSkillDetail,
    userSessions,
    userSessionsTotal,
    userSessionsPage,
    userSessionsLoading,
    fetchUserSessions,
    viewingSessionId,
    viewingMessages,
    viewingMessagesLoading,
    viewSessionMessages,
    clearViewingSession,
  } = useAdminStore();

  const [tab, setTab] = useState<DetailTab>("skills");

  useEffect(() => {
    fetchUserDetail(userId);
    fetchUserSkills(userId);
    fetchUserSessions(userId);
  }, [userId, fetchUserDetail, fetchUserSkills, fetchUserSessions]);

  // Clean up on unmount
  useEffect(() => {
    return () => {
      clearSkillDetail();
      clearViewingSession();
    };
  }, [clearSkillDetail, clearViewingSession]);

  if (userDetailLoading || !userDetail) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        {userDetailLoading ? "Loading user..." : "User not found"}
      </div>
    );
  }

  // Find the selected skill's enabled state
  const selectedSkillItem = userSkills.find((s) => s.name === selectedSkill);

  return (
    <div className="flex h-full flex-col">
      {/* Back link + username */}
      <div className="flex items-center gap-3 border-b border-border px-6 py-3">
        <Link
          href="/admin"
          className="flex items-center gap-1 text-xs text-text-secondary transition hover:text-text-primary"
        >
          <ArrowLeft size={14} />
          Users
        </Link>
        <span className="text-xs text-text-secondary">/</span>
        <span className="text-xs font-medium">{userDetail.username}</span>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* Left column: profile + tab content */}
        <div className="flex flex-1 flex-col overflow-y-auto px-6 py-4">
          <div className="mx-auto w-full max-w-2xl space-y-4">
            <UserProfileCard profile={userDetail} />

            {/* Tab switcher */}
            <div className="flex border-b border-border">
              <button
                onClick={() => { setTab("skills"); clearViewingSession(); }}
                className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition ${
                  tab === "skills"
                    ? "border-b-2 border-accent text-accent"
                    : "text-text-secondary hover:text-text-primary"
                }`}
              >
                <Puzzle size={13} />
                Skills ({userSkills.length})
              </button>
              <button
                onClick={() => { setTab("sessions"); clearSkillDetail(); }}
                className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium transition ${
                  tab === "sessions"
                    ? "border-b-2 border-accent text-accent"
                    : "text-text-secondary hover:text-text-primary"
                }`}
              >
                <MessageSquare size={13} />
                Sessions ({userSessionsTotal})
              </button>
            </div>

            {/* Tab content */}
            {tab === "skills" && (
              <UserSkillList
                skills={userSkills}
                selectedSkill={selectedSkill}
                onSelectSkill={selectSkill}
                onToggleSkill={(name, enabled) =>
                  toggleUserSkill(userId, name, enabled)
                }
                loading={userSkillsLoading}
              />
            )}

            {tab === "sessions" && (
              <SessionListPanel
                sessions={userSessions}
                total={userSessionsTotal}
                page={userSessionsPage}
                loading={userSessionsLoading}
                selectedId={viewingSessionId}
                onSelect={(sid) => viewSessionMessages(userId, sid)}
                onPageChange={(p) => fetchUserSessions(userId, p)}
              />
            )}
          </div>
        </div>

        {/* Right drawer: skill detail or session messages */}
        {tab === "skills" && selectedSkill && (
          <div className="w-80 shrink-0 overflow-y-auto">
            <SkillDetailDrawer
              detail={skillDetail}
              loading={skillDetailLoading}
              onClose={clearSkillDetail}
              isEnabled={selectedSkillItem?.is_enabled}
            />
          </div>
        )}

        {tab === "sessions" && viewingSessionId && (
          <div className="w-96 shrink-0 overflow-y-auto border-l border-border">
            <SessionMessageViewer
              messages={viewingMessages}
              loading={viewingMessagesLoading}
            />
          </div>
        )}
      </div>
    </div>
  );
}
