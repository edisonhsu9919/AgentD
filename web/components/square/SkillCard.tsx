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
      aria-pressed={selected}
      tabIndex={0}
      onClick={() => onSelect(card.name)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(card.name);
      }}
      className="flex h-[190px] cursor-pointer flex-col gap-3 rounded-[24px] bg-white/72 p-4 shadow-[0_12px_28px_rgba(42,41,51,0.045)] transition duration-200 ease-out hover:-translate-y-0.5 hover:bg-white/90 hover:shadow-[0_18px_42px_rgba(42,41,51,0.09)]"
    >
      {/* Top row: icon + name + status */}
      <div className="flex items-start gap-2.5">
        <SkillIcon icon={card.icon} skillName={card.name} size={40} iconSize={17} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-text-primary">{card.name}</span>
            {card.installed && (
              <span className="shrink-0 rounded-full bg-success/10 px-1.5 py-0.5 text-[10px] font-medium text-success">
                Installed
              </span>
            )}
            {card.enabled === false && (
              <span className="shrink-0 rounded-full bg-danger/10 px-1.5 py-0.5 text-[10px] font-medium text-danger">
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
      <p className="line-clamp-3 flex-1 text-xs leading-relaxed text-text-secondary">
        {card.description || "No description"}
      </p>

      {/* Footer: stats */}
      <div className="mt-auto flex flex-wrap items-center gap-2 text-[10px] text-text-secondary">
        <span className="flex items-center gap-0.5 rounded-full bg-bg-tertiary/80 px-2 py-0.5">
          <Hash size={9} />
          {card.usage_count_total} uses
        </span>
        <span className="rounded-full bg-bg-tertiary/80 px-2 py-0.5">v{card.latest_version}</span>
        {card.available_versions.length > 1 && (
          <span className="rounded-full bg-bg-tertiary/80 px-2 py-0.5">{card.available_versions.length} versions</span>
        )}
      </div>
    </div>
  );
}
