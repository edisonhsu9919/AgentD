"use client";

import { useEffect } from "react";
import { useUserProfileStore } from "@/store/userProfile";
import SkillIcon from "@/components/shared/SkillIcon";

interface SkillPickerProps {
  onInsert: (text: string) => void;
}

export default function SkillPicker({ onInsert }: SkillPickerProps) {
  const profile = useUserProfileStore((s) => s.profile);
  const fetchProfile = useUserProfileStore((s) => s.fetchProfile);

  // Always fetch on mount to stay in sync with filesystem truth
  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  const skills = profile?.installed_skills?.filter((s) => s.is_enabled) || [];

  if (skills.length === 0) return null;

  return (
    <div className="space-y-0.5">
      {skills.map((skill) => (
        <button
          key={skill.name}
          onClick={() => onInsert(`请使用 ${skill.name} skill `)}
          className="flex w-full items-center gap-2 rounded px-2 py-1 text-left transition hover:bg-bg-tertiary/50"
          title={`Use ${skill.name}`}
        >
          <SkillIcon icon={skill.icon} skillName={skill.name} size={20} iconSize={12} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-xs text-text-primary">
              {skill.name}
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}
