import { Construction } from "lucide-react";

interface WipSectionProps {
  title: string;
  description?: string;
}

export function WipSection({ title, description }: WipSectionProps) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[40vh] text-center px-6">
      <div className="inline-flex h-14 w-14 items-center justify-center rounded-2xl bg-amber-50 mb-5">
        <Construction className="w-7 h-7 text-amber-500" />
      </div>
      <h2 className="text-xl font-semibold text-[#0d0d0d] mb-2">{title}</h2>
      <p className="text-sm text-gray-500 max-w-sm leading-relaxed">
        {description ?? "This section is in development. Check back soon."}
      </p>
      <span className="mt-4 inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-700">
        WIP
      </span>
    </div>
  );
}
