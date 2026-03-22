"use client";

import { Hash } from "lucide-react";
import SkillIcon from "@/components/shared/SkillIcon";
import type { SquareCardItem } from "@/lib/types";

interface SkillCardProps {
  card: SquareCardItem;
  selected: boolean;
  onSelect: (name: string) => void;
}

export default function SkillCard({ card, selected, onSelect }: SkillCardProps) {
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(card.name)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(card.name);
      }}
      className={`flex cursor-pointer flex-col gap-2 rounded-lg border p-3 transition ${
        selected
          ? "border-accent/40 bg-accent/5"
          : "border-border hover:border-accent/20 hover:bg-bg-tertiary/30"
      }`}
    >
      {/* Top row: icon + name + status */}
      <div className="flex items-start gap-2.5">
        <SkillIcon icon={card.icon} skillName={card.name} size={36} iconSize={16} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-xs font-semibold">{card.name}</span>
            {card.installed && (
              <span className="shrink-0 rounded bg-success/10 px-1.5 py-0.5 text-[10px] font-medium text-success">
                Installed
              </span>
            )}
            {card.enabled === false && (
              <span className="shrink-0 rounded bg-danger/10 px-1.5 py-0.5 text-[10px] font-medium text-danger">
                Disabled
              </span>
            )}
          </div>
          {card.installed && card.installed_version && (
            <span className="text-[10px] text-text-secondary">
              v{card.installed_version}
            </span>
          )}
        </div>
      </div>

      {/* Description */}
      <p className="line-clamp-2 text-[11px] leading-relaxed text-text-secondary">
        {card.description || "No description"}
      </p>

      {/* Footer: stats */}
      <div className="flex items-center gap-3 text-[10px] text-text-secondary">
        <span className="flex items-center gap-0.5">
          <Hash size={9} />
          {card.usage_count_total} uses
        </span>
        <span>v{card.latest_version}</span>
        {card.available_versions.length > 1 && (
          <span>{card.available_versions.length} versions</span>
        )}
      </div>
    </div>
  );
}
