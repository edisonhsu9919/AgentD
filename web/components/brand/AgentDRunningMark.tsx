import AgentDSignalLoopMark from "./AgentDSignalLoopMark";

interface AgentDRunningMarkProps {
  size?: number;
  className?: string;
}

export default function AgentDRunningMark({
  size = 18,
  className = "",
}: AgentDRunningMarkProps) {
  return (
    <span className={`brand-running inline-flex ${className}`}>
      <AgentDSignalLoopMark size={size} />
    </span>
  );
}
