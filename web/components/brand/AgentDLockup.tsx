import Image from "next/image";

interface AgentDLockupProps {
  className?: string;
  alt?: string;
}

export default function AgentDLockup({
  className = "",
  alt = "AgentD",
}: AgentDLockupProps) {
  return (
    <Image
      src="/brand/final/agentd-signal-loop-lockup.svg"
      alt={alt}
      width={420}
      height={100}
      className={className}
    />
  );
}
