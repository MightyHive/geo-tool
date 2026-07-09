/**
 * PlatformLogo — renders the logo image for an AI platform.
 * Falls back to a coloured initial badge if the image fails to load.
 */

const PLATFORM_META: Record<string, { label: string; logo: string; color: string }> = {
  gemini: {
    label: "Gemini",
    logo: "/logos/gemini.png",
    color: "#4285F4",
  },
  openai: {
    label: "ChatGPT",
    logo: "/logos/openai.png",
    color: "#10a37f",
  },
  claude: {
    label: "Claude",
    logo: "/logos/claude.png",
    color: "#D97706",
  },
  google_aio: {
    label: "Google AI",
    logo: "/logos/google-ai.png",
    color: "#EA4335",
  },
};

export { PLATFORM_META };

interface PlatformLogoProps {
  platform: string;
  /** Size in px — used for both width and height. Default 20. */
  size?: number;
  className?: string;
}

export function PlatformLogo({ platform, size = 20, className = "" }: PlatformLogoProps) {
  const meta = PLATFORM_META[platform];
  if (!meta) {
    return (
      <span
        className={`inline-flex items-center justify-center rounded-full text-white text-[9px] font-bold shrink-0 ${className}`}
        style={{ width: size, height: size, background: "#6b7280", fontSize: size * 0.45 }}
        title={platform}
      >
        {platform.slice(0, 1).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={meta.logo}
      alt={meta.label}
      title={meta.label}
      width={size}
      height={size}
      className={`rounded-sm object-contain shrink-0 ${className}`}
      onError={(e) => {
        const el = e.currentTarget as HTMLImageElement;
        el.style.display = "none";
        const fallback = document.createElement("span");
        fallback.style.cssText = `display:inline-flex;align-items:center;justify-content:center;width:${size}px;height:${size}px;border-radius:4px;background:${meta.color};color:#fff;font-size:${Math.round(size * 0.45)}px;font-weight:700;flex-shrink:0`;
        fallback.textContent = meta.label.slice(0, 1);
        el.parentNode?.insertBefore(fallback, el.nextSibling);
      }}
    />
  );
}

/** A row of platform logos with tooltips — replaces coloured-chip rows. */
export function PlatformLogoRow({
  platforms,
  size = 18,
}: {
  platforms: string[];
  size?: number;
}) {
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {platforms.map((p) => (
        <PlatformLogo key={p} platform={p} size={size} />
      ))}
    </div>
  );
}

/** Competitor favicon helper with text fallback. */
export function CompetitorFavicon({
  name,
  website,
  size = 14,
}: {
  name: string;
  website?: string;
  size?: number;
}) {
  const domain = website
    ? website.replace(/^https?:\/\//, "").split("/")[0]
    : null;

  if (!domain) {
    return (
      <span
        className="inline-flex items-center justify-center rounded-sm bg-gray-200 text-gray-500 font-bold shrink-0"
        style={{ width: size, height: size, fontSize: size * 0.6 }}
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
      onError={(e) => {
        const el = e.currentTarget as HTMLImageElement;
        el.style.display = "none";
      }}
    />
  );
}
