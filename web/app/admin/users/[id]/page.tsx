"use client";

import { useEffect } from "react";
import { useParams } from "next/navigation";
import { useAdminStore } from "@/store/admin";
import UserWorkspaceOverview from "@/components/user/UserWorkspaceOverview";

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
    userKnowledgeDocs,
    userKnowledgeLoading,
    fetchUserKnowledgeDocs,
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

  useEffect(() => {
    fetchUserDetail(userId);
    fetchUserSkills(userId);
    fetchUserKnowledgeDocs(userId);
    fetchUserSessions(userId);
  }, [
    userId,
    fetchUserDetail,
    fetchUserSkills,
    fetchUserKnowledgeDocs,
    fetchUserSessions,
  ]);

  useEffect(() => {
    return () => {
      clearSkillDetail();
      clearViewingSession();
    };
  }, [clearSkillDetail, clearViewingSession]);

  if (userDetailLoading || !userDetail) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        {userDetailLoading ? "正在加载用户..." : "未找到用户"}
      </div>
    );
  }

  return (
    <UserWorkspaceOverview
      profile={userDetail}
      skills={userSkills}
      skillsLoading={userSkillsLoading}
      knowledgeDocs={userKnowledgeDocs}
      knowledgeLoading={userKnowledgeLoading}
      sessions={userSessions}
      sessionsTotal={userSessionsTotal}
      sessionsPage={userSessionsPage}
      sessionsLoading={userSessionsLoading}
      selectedSkill={selectedSkill}
      skillDetail={skillDetail}
      skillDetailLoading={skillDetailLoading}
      selectedSessionId={viewingSessionId}
      sessionMessages={viewingMessages}
      sessionMessagesLoading={viewingMessagesLoading}
      onSelectSkill={selectSkill}
      onClearSkillDetail={clearSkillDetail}
      onToggleSkill={(name, enabled) => toggleUserSkill(userId, name, enabled)}
      onSelectSession={(sessionId) => viewSessionMessages(userId, sessionId)}
      onPageChange={(page) => fetchUserSessions(userId, page)}
      eyebrow="后台 / 用户详情"
    />
  );
}
