"use client";

import { useEffect } from "react";
import { useUserProfileStore } from "@/store/userProfile";
import UserWorkspaceOverview from "@/components/user/UserWorkspaceOverview";

export default function UserPage() {
  const {
    profile,
    isLoading,
    error,
    knowledgeDocs,
    knowledgeLoading,
    sessions,
    sessionsLoading,
    viewingSessionId,
    viewingMessages,
    viewingMessagesLoading,
    selectedSkill,
    skillDetail,
    skillDetailLoading,
    fetchProfile,
    fetchKnowledgeDocs,
    fetchSessions,
    viewSessionMessages,
    clearViewingSession,
    selectSkill,
    clearSkillDetail,
  } = useUserProfileStore();

  useEffect(() => {
    fetchProfile();
    fetchSessions();
  }, [fetchProfile, fetchSessions]);

  useEffect(() => {
    return () => {
      clearViewingSession();
      clearSkillDetail();
    };
  }, [clearViewingSession, clearSkillDetail]);

  useEffect(() => {
    if (profile) {
      fetchKnowledgeDocs();
    }
  }, [profile, fetchKnowledgeDocs]);

  if (isLoading || !profile) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        {error ? (
          <span className="text-danger">{error}</span>
        ) : (
          "正在加载用户信息..."
        )}
      </div>
    );
  }

  return (
    <UserWorkspaceOverview
      profile={profile}
      skills={profile.installed_skills}
      knowledgeDocs={knowledgeDocs}
      sessions={sessions}
      knowledgeLoading={knowledgeLoading}
      sessionsLoading={sessionsLoading}
      selectedSessionId={viewingSessionId}
      sessionMessages={viewingMessages}
      sessionMessagesLoading={viewingMessagesLoading}
      selectedSkill={selectedSkill}
      skillDetail={skillDetail}
      skillDetailLoading={skillDetailLoading}
      onSelectSkill={selectSkill}
      onClearSkillDetail={clearSkillDetail}
      onSelectSession={viewSessionMessages}
      eyebrow="个人中心"
    />
  );
}
