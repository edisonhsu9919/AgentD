type AgentDSignalLoopMarkProps = {
  size?: number;
  className?: string;
  graphiteColor?: string;
  accentColor?: string;
};

export default function AgentDSignalLoopMark({
  size = 20,
  className = "",
  graphiteColor = "#1C1F27",
  accentColor = "#705CFF",
}: AgentDSignalLoopMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="AgentD logo"
    >
      <path
        d="M30 26H54C66.1503 26 76 35.8497 76 48C76 60.1503 66.1503 70 54 70H46V58H54C59.5228 58 64 53.5228 64 48C64 42.4772 59.5228 38 54 38H30V26Z"
        fill={graphiteColor}
      />
      <path
        d="M70 74H46C33.8497 74 24 64.1503 24 52C24 39.8497 33.8497 30 46 30H54V42H46C40.4772 42 36 46.4772 36 52C36 57.5228 40.4772 62 46 62H70V74Z"
        fill={accentColor}
      />
    </svg>
  );
}
