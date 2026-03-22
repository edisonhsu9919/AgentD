"use client";

import { useEffect, useState } from "react";
import { Puzzle } from "lucide-react";
import { API_URL } from "@/lib/constants";
import { getToken } from "@/lib/api";

function isEmojiIcon(icon: string): boolean {
  if (!icon) return false;
  if (icon.includes("/") || icon.includes("\\")) return false;
  if (/\.\w{2,4}$/.test(icon)) return false;
  return true;
}

function isFileIcon(icon: string): boolean {
  if (!icon) return false;
  return icon.includes("/") || /\.\w{2,4}$/.test(icon);
}

interface SkillIconProps {
  icon: string;
  skillName: string;
  size?: number;
  iconSize?: number;
}

export default function SkillIcon({
  icon,
  skillName,
  size = 28,
  iconSize = 14,
}: SkillIconProps) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!isFileIcon(icon) || failed) return;

    let cancelled = false;
    const token = getToken();
    const url = `${API_URL}/skills/square/${encodeURIComponent(skillName)}/icon`;

    fetch(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => {
        if (!res.ok) throw new Error("not ok");
        return res.blob();
      })
      .then((blob) => {
        if (!cancelled) setBlobUrl(URL.createObjectURL(blob));
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });

    return () => {
      cancelled = true;
    };
  }, [icon, skillName, failed]);

  // Clean up blob URL on unmount
  useEffect(() => {
    return () => {
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [blobUrl]);

  const containerClass =
    "flex shrink-0 items-center justify-center rounded bg-bg-tertiary text-text-secondary";

  if (isEmojiIcon(icon)) {
    return (
      <div className={containerClass} style={{ width: size, height: size }}>
        <span style={{ fontSize: iconSize }}>{icon}</span>
      </div>
    );
  }

  if (isFileIcon(icon) && blobUrl) {
    return (
      <div
        className="flex shrink-0 items-center justify-center rounded bg-bg-tertiary overflow-hidden"
        style={{ width: size, height: size }}
      >
        <img
          src={blobUrl}
          alt={`${skillName} icon`}
          className="h-full w-full object-cover"
        />
      </div>
    );
  }

  // Fallback (loading or failed)
  return (
    <div className={containerClass} style={{ width: size, height: size }}>
      <Puzzle size={iconSize} />
    </div>
  );
}
