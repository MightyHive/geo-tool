/**
 * PlatformLogo — renders the logo image for an AI platform.
 * Falls back to a coloured initial badge on load error.
 */
import { useState } from "react";

export const PLATFORM_META: Record<string, {
  label: string;
  logo: string;
  color: string;
  lightColor: string;
}> = {
  gemini: {
    label: "Gemini",
    logo: "/logos/gemini.png",
    color: "#1D4ED8",
    lightColor: "#93C5FD",
  },
  openai: {
    label: "OpenAI",
    logo: "/logos/chatgpt.png",
    color: "#374151",
    lightColor: "#D1D5DB",
  },
  claude: {
    label: "Claude",
    logo: "/logos/claude.png",
    color: "#C2410C",
    lightColor: "#FDBA74",
  },
  google_aio: {
    label: "Google AI",
    logo: "/logos/google-ai.png",
    color: "#15803D",
    lightColor: "#86EFAC",
  },
  copilot: {
    label: "Microsoft Copilot",
    logo: "/assets/logos/Copilot.png",
    color: "#2563EB",
    lightColor: "#93C5FD",
  },
};

// ── Single logo ───────────────────────────────────────────────────────────────

interface PlatformLogoProps {
  platform: string;
  /** Size in px for both width and height. Default 20. */
  size?: number;
  className?: string;
}

export function PlatformLogo({ platform, size = 20, className = "" }: PlatformLogoProps) {
  const [failed, setFailed] = useState(false);
  const meta = PLATFORM_META[platform];
  const color = meta?.color ?? "#6b7280";
  const label = meta?.label ?? platform;

  if (!meta || failed) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-md text-white font-bold shrink-0 ${className}`}
        style={{
          width: size,
          height: size,
          background: color,
          fontSize: Math.round(size * 0.45),
          minWidth: size,
        }}
        title={label}
      >
        {label.slice(0, 1).toUpperCase()}
      </span>
    );
  }

  return (
    <img
      src={meta.logo}
      alt={label}
      title={label}
      width={size}
      height={size}
      className={`rounded-sm object-contain shrink-0 ${className}`}
      onError={() => setFailed(true)}
    />
  );
}

// ── Row of logos (no labels) ──────────────────────────────────────────────────

export function PlatformLogoRow({
  platforms,
  size = 18,
}: {
  platforms?: string[] | null;
  size?: number;
}) {
  const list = platforms ?? [];
  if (!list.length) return <span className="text-xs text-gray-300">—</span>;
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {list.map((p) => (
        <PlatformLogo key={p} platform={p} size={size} />
      ))}
    </div>
  );
}

// ── Logo + label pill ─────────────────────────────────────────────────────────

interface PlatformBadgeProps {
  platform: string;
  size?: number;
  /** If true renders a subtle chip with bg tint; if false just logo+text inline */
  chip?: boolean;
}

export function PlatformBadge({ platform, size = 16, chip = false }: PlatformBadgeProps) {
  const meta = PLATFORM_META[platform];
  const label = meta?.label ?? platform;
  const color = meta?.color ?? "#6b7280";

  const inner = (
    <span className="flex items-center gap-1.5">
      <PlatformLogo platform={platform} size={size} />
      <span className="text-xs font-semibold">{label}</span>
    </span>
  );

  if (!chip) return inner;
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full"
      style={{ background: `${color}18`, color }}
    >
      <PlatformLogo platform={platform} size={size} />
      <span className="text-xs font-semibold">{label}</span>
    </span>
  );
}

// ── Competitor favicon ────────────────────────────────────────────────────────

export function CompetitorFavicon({
  name,
  website,
  size = 14,
}: {
  name: string;
  website?: string;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const domain = website
    ? website.replace(/^https?:\/\//, "").split("/")[0]
    : null;

  if (!domain || failed) {
    return (
      <span
        className="inline-flex items-center justify-center rounded-sm bg-gray-200 text-gray-500 font-bold shrink-0"
        style={{ width: size, height: size, fontSize: Math.round(size * 0.6) }}
      >
        {name.slice(0, 1).toUpperCase()}
      </span>
    );
  }

  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`}
      alt={name}
      title={name}
      width={size}
      height={size}
      className="rounded-sm object-contain shrink-0"
      onError={() => setFailed(true)}
    />
  );
}
