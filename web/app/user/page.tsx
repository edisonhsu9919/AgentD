"use client";

import { useEffect } from "react";
import { useUserProfileStore } from "@/store/userProfile";
import UserProfileCard from "@/components/user/UserProfileCard";
import UserSkillList from "@/components/user/UserSkillList";
import SkillDetailDrawer from "@/components/user/SkillDetailDrawer";

export default function UserPage() {
  const {
    profile,
    isLoading,
    error,
    selectedSkill,
    skillDetail,
    skillDetailLoading,
    fetchProfile,
    selectSkill,
    clearSkillDetail,
  } = useUserProfileStore();

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  if (isLoading || !profile) {
    return (
      <div className="flex h-full items-center justify-center text-xs text-text-secondary">
        {error ? (
          <span className="text-danger">{error}</span>
        ) : (
          "Loading profile..."
        )}
      </div>
    );
  }

  // Find the selected skill's enabled state from profile
  const selectedSkillItem = profile.installed_skills.find(
    (s) => s.name === selectedSkill,
  );

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto max-w-2xl space-y-6">
          <UserProfileCard profile={profile} />

          <div>
            <h3 className="mb-3 text-sm font-semibold">
              Installed Skills
              <span className="ml-2 text-xs font-normal text-text-secondary">
                {profile.installed_skills.length}
              </span>
            </h3>
            <UserSkillList
              skills={profile.installed_skills}
              selectedSkill={selectedSkill}
              onSelectSkill={selectSkill}
            />
          </div>
        </div>
      </div>

      {/* Skill detail drawer */}
      {selectedSkill && (
        <div className="w-80 shrink-0">
          <SkillDetailDrawer
            detail={skillDetail}
            loading={skillDetailLoading}
            onClose={clearSkillDetail}
            isEnabled={selectedSkillItem?.is_enabled}
          />
        </div>
      )}
    </div>
  );
}
