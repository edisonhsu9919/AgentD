"use client";

import {
  ToggleLeft,
  ToggleRight,
  Clock,
  Hash,
  Package,
} from "lucide-react";
import SkillIcon from "@/components/shared/SkillIcon";
import type { UserSkillItem } from "@/lib/types";

interface UserSkillListProps {
  skills: UserSkillItem[];
  selectedSkill: string | null;
  onSelectSkill: (name: string) => void;
  /** If provided, show toggle actions (admin mode) */
  onToggleSkill?: (name: string, enabled: boolean) => void;
  loading?: boolean;
}

function formatDate(iso: string | null): string {
  if (!iso) return "Never";
  const d = new Date(iso);
  return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function UserSkillList({
  skills,
  selectedSkill,
  onSelectSkill,
  onToggleSkill,
  loading,
}: UserSkillListProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-xs text-text-secondary">
        Loading skills...
      </div>
    );
  }

  if (skills.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-8 text-xs text-text-secondary">
        <Package size={24} />
        <span>No skills installed</span>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {skills.map((skill) => (
        <div
          key={skill.name}
          role="button"
          tabIndex={0}
          onClick={() => onSelectSkill(skill.name)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") onSelectSkill(skill.name); }}
          className={`flex w-full cursor-pointer items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition ${
            selectedSkill === skill.name
              ? "border-accent/40 bg-accent/5"
              : "border-transparent hover:bg-bg-tertiary/50"
          }`}
        >
          {/* Icon */}
          <div className="mt-0.5">
            <SkillIcon icon={skill.icon} skillName={skill.name} />
          </div>

          {/* Info */}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-xs font-medium">{skill.name}</span>
              <span className="shrink-0 text-[10px] text-text-secondary">
                v{skill.version}
              </span>
              {!skill.is_enabled && (
                <span className="shrink-0 rounded bg-danger/10 px-1.5 py-0.5 text-[10px] font-medium text-danger">
                  Disabled
                </span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-3 text-[10px] text-text-secondary">
              <span className="flex items-center gap-0.5">
                <Hash size={9} />
                {skill.usage_count}
              </span>
              <span className="flex items-center gap-0.5">
                <Clock size={9} />
                {formatDate(skill.last_used_at)}
              </span>
            </div>
          </div>

          {/* Toggle (admin mode only) */}
          {onToggleSkill && (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggleSkill(skill.name, !skill.is_enabled);
              }}
              className={`mt-0.5 shrink-0 transition ${
                skill.is_enabled ? "text-success" : "text-text-secondary"
              } hover:opacity-80`}
              title={skill.is_enabled ? "Disable skill" : "Enable skill"}
            >
              {skill.is_enabled ? (
                <ToggleRight size={18} />
              ) : (
                <ToggleLeft size={18} />
              )}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
