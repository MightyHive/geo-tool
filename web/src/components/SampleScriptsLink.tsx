import { ArrowRight } from "lucide-react";
import { mentionsSampleScriptArtifact } from "../lib/sampleScriptsArtifacts";
import { cn } from "../lib/utils";

/** Compact CTA to the Workshop Sample scripts section. */
export function SampleScriptsLinkButton({
  onNavigate,
  className,
  label = "Open sample scripts",
}: {
  onNavigate: (sectionId: string) => void;
  className?: string;
  label?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onNavigate("sample-scripts")}
      className={cn(
        "inline-flex items-center gap-1 font-semibold text-violet-700 underline decoration-violet-300 underline-offset-2 hover:text-violet-900 focus:outline-none focus:ring-2 focus:ring-violet-400",
        className,
      )}
    >
      {label}
      <ArrowRight className="h-3 w-3" />
    </button>
  );
}

/**
 * Renders prose and, when it mentions robots.txt / llms.txt / JSON-LD,
 * appends a Sample scripts navigation link.
 */
export function TextWithSampleScriptsLink({
  text,
  onNavigate,
  className,
}: {
  text: string;
  onNavigate?: (sectionId: string) => void;
  className?: string;
}) {
  const showLink = Boolean(onNavigate && mentionsSampleScriptArtifact(text));
  return (
    <span className={className}>
      {text}
      {showLink ? (
        <>
          {" "}
          <SampleScriptsLinkButton onNavigate={onNavigate!} className="text-[11px]" />
        </>
      ) : null}
    </span>
  );
}
