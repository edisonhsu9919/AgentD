"use client";

import { useState } from "react";
import {
  BookOpen,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  FileText,
  Hash,
  History,
  Lock,
  MessageSquare,
  Package,
  Shield,
  Sparkles,
  User as UserIcon,
  X,
} from "lucide-react";
import MessageMarkdown from "@/components/chat/MessageMarkdown";
import PanelShell from "@/components/panel/PanelShell";
import SkillIcon from "@/components/shared/SkillIcon";
import SkillDetailDrawer from "@/components/user/SkillDetailDrawer";
import { usePanelStore } from "@/store/panel";
import type {
  KnowledgeDocItem,
  Message,
  Session,
  SquareDetailResponse,
  UserProfile,
  UserSkillItem,
} from "@/lib/types";

interface UserWorkspaceOverviewProps {
  profile: UserProfile;
  skills: UserSkillItem[];
  knowledgeDocs: KnowledgeDocItem[];
  sessions: Session[];
  sessionsTotal?: number;
  sessionsPage?: number;
  skillsLoading?: boolean;
  knowledgeLoading?: boolean;
  sessionsLoading?: boolean;
  selectedSkill: string | null;
  skillDetail: SquareDetailResponse | null;
  skillDetailLoading: boolean;
  selectedSessionId?: string | null;
  sessionMessages?: Message[];
  sessionMessagesLoading?: boolean;
  onSelectSkill: (name: string) => void;
  onClearSkillDetail: () => void;
  onToggleSkill?: (name: string, enabled: boolean) => void;
  onSelectSession?: (id: string) => void;
  onPageChange?: (page: number) => void;
  eyebrow?: string;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "暂无";
  return new Date(value).toLocaleDateString();
}

function formatDateTime(value: string | null | undefined) {
  if (!value) return "暂无";
  const d = new Date(value);
  return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

function roleLabel(role: UserProfile["role"]) {
  return role === "admin" ? "管理员" : "用户";
}

function statusLabel(status: Session["status"]) {
  const labels: Record<string, string> = {
    idle: "空闲",
    running: "运行中",
    error: "异常",
    waiting_permission: "待确认",
  };
  return labels[status] ?? status;
}

function EmptyList({ icon: Icon, text }: { icon: typeof Package; text: string }) {
  return (
    <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 text-xs text-text-secondary">
      <Icon size={22} className="text-text-secondary/35" />
      <span>{text}</span>
    </div>
  );
}

function SectionCard({
  title,
  count,
  icon: Icon,
  children,
}: {
  title: string;
  count?: number;
  icon: typeof UserIcon;
  children: React.ReactNode;
}) {
  return (
    <section className="flex min-h-[260px] min-w-0 flex-col rounded-[24px] bg-white/72 p-3 shadow-[0_12px_28px_rgba(42,41,51,0.045)]">
      <div className="mb-2 flex items-center justify-between gap-3 px-1">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-[13px] bg-accent/10 text-accent">
            <Icon size={15} />
          </span>
          <h2 className="truncate text-sm font-semibold text-text-primary">{title}</h2>
        </div>
        {typeof count === "number" && (
          <span className="rounded-full bg-bg-primary px-2 py-1 text-[10px] text-text-secondary">
            {count}
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-1 pb-1">
        {children}
      </div>
    </section>
  );
}

function UserInfoSection({ profile }: { profile: UserProfile }) {
  return (
    <SectionCard title="用户基本信息" icon={profile.role === "admin" ? Shield : UserIcon}>
      <div className="space-y-2">
        <div className="rounded-[18px] bg-bg-primary/65 p-3">
          <div className="text-lg font-semibold tracking-[-0.04em] text-text-primary">
            {profile.username}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5 text-[10px]">
            <span className="rounded-full bg-accent/10 px-2 py-0.5 text-accent">
              {roleLabel(profile.role)}
            </span>
            <span className="rounded-full bg-white/70 px-2 py-0.5 text-text-secondary">
              {profile.is_active ? "账号启用" : "账号停用"}
            </span>
          </div>
        </div>
        <InfoRow label="部门" value={profile.department || "未填写"} />
        <InfoRow label="员工 ID" value={profile.employee_id || "未填写"} />
        <InfoRow label="创建时间" value={formatDate(profile.created_at)} icon={<Calendar size={11} />} />
        <InfoRow label="Workspace" value={profile.workspace} />
      </div>
    </SectionCard>
  );
}

function InfoRow({
  label,
  value,
  icon,
}: {
  label: string;
  value: string;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-[14px] px-2 py-1.5 text-xs">
      <span className="flex shrink-0 items-center gap-1.5 text-text-secondary">
        {icon}
        {label}
      </span>
      <span className="min-w-0 truncate text-right font-medium text-text-primary">
        {value}
      </span>
    </div>
  );
}

function SkillListSection({
  skills,
  loading,
  selectedSkill,
  onOpenSkill,
  onToggleSkill,
}: {
  skills: UserSkillItem[];
  loading?: boolean;
  selectedSkill: string | null;
  onOpenSkill: (name: string) => void;
  onToggleSkill?: (name: string, enabled: boolean) => void;
}) {
  if (loading) return <LoadingList />;
  if (skills.length === 0) return <EmptyList icon={Package} text="暂无已安装技能" />;

  return (
    <div className="space-y-1">
      {skills.map((skill) => (
        <button
          key={skill.name}
          onClick={() => onOpenSkill(skill.name)}
          className={`flex w-full items-center gap-3 rounded-[16px] px-3 py-2.5 text-left outline-none transition hover:bg-bg-primary/75 focus-visible:shadow-[0_0_0_2px_rgba(139,92,246,0.16)] ${
            selectedSkill === skill.name ? "bg-accent/10" : ""
          }`}
        >
          <SkillIcon icon={skill.icon} skillName={skill.name} size={28} iconSize={13} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-xs font-medium text-text-primary">{skill.name}</span>
              <span className="text-[10px] text-text-secondary">v{skill.version}</span>
              {!skill.is_enabled && (
                <span className="rounded-full bg-danger/10 px-2 py-0.5 text-[10px] text-danger">
                  已禁用
                </span>
              )}
            </div>
            <div className="mt-0.5 flex items-center gap-3 text-[10px] text-text-secondary">
              <span className="flex items-center gap-0.5">
                <Hash size={9} />
                {skill.usage_count}
              </span>
              <span className="flex items-center gap-0.5">
                <Clock size={9} />
                {formatDateTime(skill.last_used_at)}
              </span>
            </div>
          </div>
          {onToggleSkill && (
            <span
              onClick={(e) => {
                e.stopPropagation();
                onToggleSkill(skill.name, !skill.is_enabled);
              }}
              className={`shrink-0 rounded-full px-2 py-1 text-[10px] ${
                skill.is_enabled
                  ? "bg-success/10 text-success"
                  : "bg-bg-primary text-text-secondary"
              }`}
            >
              {skill.is_enabled ? "启用中" : "启用"}
            </span>
          )}
        </button>
      ))}
    </div>
  );
}

function KnowledgeListSection({
  docs,
  loading,
  onOpenDoc,
}: {
  docs: KnowledgeDocItem[];
  loading?: boolean;
  onOpenDoc: (docId: string) => void;
}) {
  if (loading) return <LoadingList />;
  if (docs.length === 0) return <EmptyList icon={BookOpen} text="暂无用户上传知识库" />;

  return (
    <div className="space-y-1">
      {docs.map((doc) => (
        <button
          key={doc.doc_id}
          onClick={() => onOpenDoc(doc.doc_id)}
          className="flex w-full items-start gap-3 rounded-[16px] px-3 py-2.5 text-left outline-none transition hover:bg-bg-primary/75 focus-visible:shadow-[0_0_0_2px_rgba(139,92,246,0.16)]"
        >
          <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-[13px] bg-accent/10 text-accent">
            <FileText size={14} />
          </span>
          <div className="min-w-0 flex-1">
            <div className="line-clamp-1 text-xs font-medium text-text-primary">
              {doc.title}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-text-secondary">
              <span className="rounded-full bg-white/70 px-2 py-0.5">{doc.kind}</span>
              <span className="flex items-center gap-0.5 rounded-full bg-white/70 px-2 py-0.5">
                <Lock size={9} />
                {doc.permission}
              </span>
              <span>{formatDate(doc.created_at)}</span>
            </div>
            {doc.description && (
              <p className="mt-1 line-clamp-1 text-[11px] text-text-secondary">
                {doc.description}
              </p>
            )}
          </div>
        </button>
      ))}
    </div>
  );
}

function SessionListSection({
  sessions,
  total,
  page,
  loading,
  selectedSessionId,
  onOpenSession,
  onPageChange,
}: {
  sessions: Session[];
  total: number;
  page: number;
  loading?: boolean;
  selectedSessionId?: string | null;
  onOpenSession?: (id: string) => void;
  onPageChange?: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / 20));

  if (loading && sessions.length === 0) return <LoadingList />;
  if (sessions.length === 0) return <EmptyList icon={History} text="暂无会话历史" />;

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        {sessions.map((session) => (
          <button
            key={session.id}
            onClick={() => onOpenSession?.(session.id)}
            className={`w-full rounded-[16px] px-3 py-2.5 text-left outline-none transition hover:bg-bg-primary/75 focus-visible:shadow-[0_0_0_2px_rgba(139,92,246,0.16)] ${
              selectedSessionId === session.id ? "bg-accent/10" : ""
            }`}
          >
            <div className="truncate text-xs font-medium text-text-primary">
              {session.title || "未命名会话"}
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-text-secondary">
              <span className="rounded-full bg-white/70 px-2 py-0.5">
                {statusLabel(session.status)}
              </span>
              <span>{formatDateTime(session.updated_at || session.created_at)}</span>
              {session.token_usage.total > 0 && (
                <span>{session.token_usage.total.toLocaleString()} tokens</span>
              )}
            </div>
          </button>
        ))}
      </div>

      {totalPages > 1 && onPageChange && (
        <div className="flex items-center justify-center gap-2 pt-1">
          <button
            onClick={() => onPageChange(page - 1)}
            disabled={page <= 1}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-primary disabled:opacity-30"
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-[10px] text-text-secondary">
            {page} / {totalPages}
          </span>
          <button
            onClick={() => onPageChange(page + 1)}
            disabled={page >= totalPages}
            className="rounded-full p-1.5 text-text-secondary transition hover:bg-bg-primary disabled:opacity-30"
          >
            <ChevronRight size={14} />
          </button>
        </div>
      )}
    </div>
  );
}

function LoadingList() {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center">
      <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
  );
}

function SessionPreviewDrawer({
  open,
  title,
  messages,
  loading,
  onClose,
}: {
  open: boolean;
  title: string;
  messages: Message[];
  loading?: boolean;
  onClose: () => void;
}) {
  return (
    <>
      <div
        className={`fixed inset-0 z-30 bg-[rgba(42,41,51,0.08)] backdrop-blur-[1px] transition-opacity duration-300 ease-out ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        onClick={onClose}
      />
      <div
        className={`fixed inset-y-3 right-3 z-40 flex w-[min(46vw,820px)] min-w-[360px] max-w-[820px] transform-gpu flex-col rounded-[18px] bg-white/97 shadow-[0_28px_90px_rgba(42,41,51,0.14)] backdrop-blur transition-transform duration-[460ms] ease-[cubic-bezier(0.2,0.8,0.2,1)] will-change-transform max-md:inset-x-3 max-md:w-auto max-md:min-w-0 ${
          open
            ? "translate-x-0"
            : "pointer-events-none translate-x-[calc(100%+1.25rem)]"
        }`}
      >
        <div className="flex items-center justify-between px-4 py-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-text-primary">{title}</div>
            <div className="text-[10px] text-text-secondary">只读会话预览</div>
          </div>
          <button
            onClick={onClose}
            className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-bg-primary text-text-secondary transition hover:bg-bg-tertiary hover:text-text-primary"
            title="关闭"
          >
            <X size={15} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5">
          {loading ? (
            <LoadingList />
          ) : messages.length === 0 ? (
            <EmptyList icon={MessageSquare} text="暂无消息内容" />
          ) : (
            <div className="space-y-5">
              {messages.map((message) => (
                <ReadOnlyMessage key={message.id} message={message} />
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function ReadOnlyMessage({ message }: { message: Message }) {
  const text = message.parts
    .map((part) => {
      if (part.type === "text") return part.content;
      if (part.type === "tool_call") return `工具调用：${part.tool_name}`;
      if (part.type === "tool_result") return part.is_error ? "工具执行失败" : "工具执行完成";
      if (part.type === "compaction") return part.summary;
      return "";
    })
    .filter(Boolean)
    .join("\n\n");

  return (
    <div className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[min(100%,56rem)] text-[14px] leading-7 ${
          message.role === "user"
            ? "rounded-[18px] bg-bg-primary/85 px-4 py-3 shadow-[0_12px_30px_rgba(42,41,51,0.04)]"
            : ""
        }`}
      >
        <div className="mb-1 text-[10px] text-text-secondary">
          {message.role === "user" ? "用户" : message.role === "assistant" ? "Agent" : "工具"}
          <span className="ml-2">{formatDateTime(message.created_at)}</span>
        </div>
        <div className="chat-prose">
          <MessageMarkdown>{text || "空消息"}</MessageMarkdown>
        </div>
      </div>
    </div>
  );
}

export default function UserWorkspaceOverview({
  profile,
  skills,
  knowledgeDocs,
  sessions,
  sessionsTotal,
  sessionsPage = 1,
  skillsLoading,
  knowledgeLoading,
  sessionsLoading,
  selectedSkill,
  skillDetail,
  skillDetailLoading,
  selectedSessionId,
  sessionMessages = [],
  sessionMessagesLoading,
  onSelectSkill,
  onClearSkillDetail,
  onToggleSkill,
  onSelectSession,
  onPageChange,
  eyebrow = "个人中心",
}: UserWorkspaceOverviewProps) {
  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false);
  const [sessionDrawerOpen, setSessionDrawerOpen] = useState(false);
  const openKnowledgeSource = usePanelStore((s) => s.openKnowledgeSource);
  const totalSessions = sessionsTotal ?? sessions.length;
  const activeSession = sessions.find((session) => session.id === selectedSessionId);

  const openSkill = (name: string) => {
    setSkillDrawerOpen(true);
    onSelectSkill(name);
  };

  const closeSkill = () => {
    setSkillDrawerOpen(false);
    window.setTimeout(onClearSkillDetail, 460);
  };

  const openSession = (id: string) => {
    setSessionDrawerOpen(true);
    onSelectSession?.(id);
  };

  const closeSession = () => {
    setSessionDrawerOpen(false);
  };

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-[1440px] flex-col overflow-hidden px-6 py-6">
      <div className="mb-4 space-y-1.5">
        <div className="page-eyebrow">{eyebrow}</div>
        <h1 className="text-[22px] font-semibold tracking-[-0.03em] text-text-primary">
          {profile.username}
        </h1>
        <p className="max-w-2xl text-xs leading-6 text-text-secondary">
          用户资料、已安装技能、上传知识库与会话历史的统一概览。
        </p>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto pr-2 lg:grid-cols-2">
        <UserInfoSection profile={profile} />
        <SectionCard title="用户安装的 Skill" count={skills.length} icon={Sparkles}>
          <SkillListSection
            skills={skills}
            loading={skillsLoading}
            selectedSkill={selectedSkill}
            onOpenSkill={openSkill}
            onToggleSkill={onToggleSkill}
          />
        </SectionCard>
        <SectionCard title="用户上传的知识库" count={knowledgeDocs.length} icon={Database}>
          <KnowledgeListSection
            docs={knowledgeDocs}
            loading={knowledgeLoading}
            onOpenDoc={openKnowledgeSource}
          />
        </SectionCard>
        <SectionCard title="用户会话历史" count={totalSessions} icon={History}>
          <SessionListSection
            sessions={sessions}
            total={totalSessions}
            page={sessionsPage}
            loading={sessionsLoading}
            selectedSessionId={selectedSessionId}
            onOpenSession={openSession}
            onPageChange={onPageChange}
          />
        </SectionCard>
      </div>

      <div
        className={`fixed inset-0 z-30 bg-[rgba(42,41,51,0.08)] backdrop-blur-[1px] transition-opacity duration-300 ease-out ${
          skillDrawerOpen && selectedSkill
            ? "opacity-100"
            : "pointer-events-none opacity-0"
        }`}
        onClick={closeSkill}
      />
      <div
        className={`fixed inset-y-3 right-3 z-40 flex w-[min(46vw,820px)] min-w-[360px] max-w-[820px] transform-gpu flex-col transition-transform duration-[460ms] ease-[cubic-bezier(0.2,0.8,0.2,1)] will-change-transform max-md:inset-x-3 max-md:w-auto max-md:min-w-0 ${
          skillDrawerOpen && selectedSkill
            ? "translate-x-0"
            : "pointer-events-none translate-x-[calc(100%+1.25rem)]"
        }`}
      >
        {selectedSkill && (
          <SkillDetailDrawer
            detail={skillDetail}
            loading={skillDetailLoading}
            onClose={closeSkill}
            isEnabled={skills.find((skill) => skill.name === selectedSkill)?.is_enabled}
          />
        )}
      </div>

      <SessionPreviewDrawer
        open={sessionDrawerOpen && Boolean(selectedSessionId)}
        title={activeSession?.title || "会话预览"}
        messages={sessionMessages}
        loading={sessionMessagesLoading}
        onClose={closeSession}
      />

      <PanelShell sessionId="" />
    </div>
  );
}
