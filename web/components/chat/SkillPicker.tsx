"use client";

import type { UserProfile } from "@/lib/types";

interface SkillPickerProps {
  skills: UserProfile["installed_skills"];
  onInsert: (text: string) => void;
}

export default function SkillPicker({ skills, onInsert }: SkillPickerProps) {
  if (skills.length === 0) return null;

  return (
    <div className="space-y-0.5">
      {skills.map((skill) => (
        <button
          key={skill.name}
          onClick={() => onInsert(`使用${skill.name}技能……`)}
          className="flex w-full items-center px-1 py-1.5 text-left text-sm text-text-secondary transition hover:text-text-primary"
          title={`使用 ${skill.name}`}
        >
          <span className="truncate">{skill.name}</span>
        </button>
      ))}
    </div>
  );
}
