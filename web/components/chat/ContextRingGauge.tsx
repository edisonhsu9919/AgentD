"use client";

import { useState } from "react";

interface ContextRingGaugeProps {
  ratio: number;
  promptTokens: number;
  windowLimit: number;
}

export default function ContextRingGauge({
  ratio,
  promptTokens,
  windowLimit,
}: ContextRingGaugeProps) {
  const [showTooltip, setShowTooltip] = useState(false);

  const size = 22;
  const strokeWidth = 2.5;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - Math.min(ratio, 1));

  const color =
    ratio > 0.75
      ? "var(--color-danger)"
      : ratio > 0.5
        ? "#facc15"
        : "var(--color-success)";

  const pct = (ratio * 100).toFixed(1);

  return (
    <div
      className="relative inline-flex items-center"
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <svg
        width={size}
        height={size}
        className="shrink-0"
        style={{ transform: "rotate(-90deg)" }}
      >
        {/* Background track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="var(--color-bg-tertiary)"
          strokeWidth={strokeWidth}
        />
        {/* Fill arc */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          className="transition-all duration-500"
        />
      </svg>

      <span
        className="ml-1.5 text-[10px] font-mono"
        style={{ color }}
      >
        {pct}%
      </span>

      {/* Tooltip */}
      {showTooltip && (
        <div className="absolute left-1/2 top-full z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap rounded bg-bg-primary border border-border px-2.5 py-1.5 text-[10px] font-mono text-text-secondary shadow-lg">
          Prompt {promptTokens.toLocaleString()} / {windowLimit.toLocaleString()} ({pct}%)
        </div>
      )}
    </div>
  );
}
