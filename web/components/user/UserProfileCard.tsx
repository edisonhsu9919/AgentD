"use client";

import {
  User as UserIcon,
  Shield,
  Building2,
  BadgeCheck,
  Calendar,
} from "lucide-react";
import type { UserProfile } from "@/lib/types";

interface UserProfileCardProps {
  profile: UserProfile;
}

export default function UserProfileCard({ profile }: UserProfileCardProps) {
  return (
    <div className="rounded-lg border border-border bg-bg-secondary p-5">
      {/* Avatar + name */}
      <div className="mb-4 flex items-center gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent/20 text-accent">
          {profile.role === "admin" ? (
            <Shield size={20} />
          ) : (
            <UserIcon size={20} />
          )}
        </div>
        <div>
          <h2 className="text-sm font-semibold">{profile.username}</h2>
          <span
            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${
              profile.role === "admin"
                ? "bg-accent/20 text-accent"
                : "bg-bg-tertiary text-text-secondary"
            }`}
          >
            {profile.role === "admin" ? <Shield size={10} /> : <UserIcon size={10} />}
            {profile.role}
          </span>
        </div>
      </div>

      {/* Info rows */}
      <div className="space-y-2 text-xs">
        {profile.department && (
          <div className="flex items-center gap-2 text-text-secondary">
            <Building2 size={13} />
            <span className="text-text-primary">{profile.department}</span>
          </div>
        )}
        {profile.employee_id && (
          <div className="flex items-center gap-2 text-text-secondary">
            <BadgeCheck size={13} />
            <span className="text-text-primary">{profile.employee_id}</span>
          </div>
        )}
        <div className="flex items-center gap-2 text-text-secondary">
          <Calendar size={13} />
          <span className="text-text-primary">
            Joined {new Date(profile.created_at).toLocaleDateString()}
          </span>
        </div>
      </div>
    </div>
  );
}
