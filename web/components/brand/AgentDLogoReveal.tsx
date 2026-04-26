"use client";

import { useSyncExternalStore } from "react";
import Image from "next/image";
import AgentDLockup from "./AgentDLockup";

interface AgentDLogoRevealProps {
  className?: string;
  alt?: string;
}

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function subscribeToReducedMotion(onStoreChange: () => void) {
  const media = window.matchMedia(REDUCED_MOTION_QUERY);
  media.addEventListener("change", onStoreChange);
  return () => media.removeEventListener("change", onStoreChange);
}

function getReducedMotionSnapshot() {
  return typeof window !== "undefined"
    ? window.matchMedia(REDUCED_MOTION_QUERY).matches
    : false;
}

export default function AgentDLogoReveal({
  className = "",
  alt = "AgentD",
}: AgentDLogoRevealProps) {
  const reducedMotion = useSyncExternalStore(
    subscribeToReducedMotion,
    getReducedMotionSnapshot,
    () => false,
  );

  if (reducedMotion) {
    return <AgentDLockup className={className} alt={alt} />;
  }

  return (
    <Image
      src="/brand/final/agentd-signal-loop-motion-enter.svg"
      alt={alt}
      width={420}
      height={100}
      className={className}
    />
  );
}
