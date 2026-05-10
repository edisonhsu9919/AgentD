"use client";

import type { UserProfile } from "@/lib/types";

interface SkillPickerProps {
  skills: UserProfile["installed_skills"];
  loadedSkillNames?: Set<string>;
  onInsert: (text: string) => void;
}

export default function SkillPicker({ skills, loadedSkillNames, onInsert }: SkillPickerProps) {
  if (skills.length === 0) return null;

  return (
    <div className="space-y-0.5">
      {skills.map((skill) => (
        <button
          key={skill.name}
          onClick={() => onInsert(`/skill load ${skill.name}`)}
          className="flex w-full items-center gap-2 px-1 py-1.5 text-left text-sm text-text-secondary transition hover:text-text-primary"
          title={`加载 ${skill.name}`}
        >
          <span className="truncate">{skill.name}</span>
          {loadedSkillNames?.has(skill.name) && (
            <span className="ml-auto rounded-full bg-accent/10 px-1.5 py-0.5 text-[10px] text-accent">
              已加载
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
