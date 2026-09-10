import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ExternalLink,
  GripVertical,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import {
  fetchTopicContentSamples,
  generateTopicContentSample,
} from "../api/client";
import type {
  ContentOutline,
  ContentOutlineSection,
  ContentOutlineStructure,
  TopicContentSample,
  TopicContentSamplesResponse,
} from "../types";
import { cn } from "../lib/utils";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const POLL_INTERVAL_MS = 2_500;

type EditorSection = ContentOutlineSection & { editorKey: string };

let editorKey = 0;
function nextEditorKey(): string {
  editorKey += 1;
  return `outline-section-${editorKey}`;
}

function toEditorSections(sections: ContentOutlineSection[]): EditorSection[] {
  return sections.map((section) => ({ ...section, editorKey: nextEditorKey() }));
}

export function shouldHydrateOutline({
  hydratedTopic,
  hydratedVersion,
  nextTopic,
  nextVersion,
  dirty,
}: {
  hydratedTopic: string;
  hydratedVersion: string;
  nextTopic: string;
  nextVersion: string;
  dirty: boolean;
}): boolean {
  if (hydratedTopic === nextTopic && hydratedVersion === nextVersion) return false;
  return !(hydratedTopic === nextTopic && dirty);
}

export function canRegenerateSections(
  sections: Array<Pick<ContentOutlineSection, "heading">>,
): boolean {
  return sections.length > 0 && sections.every((section) => section.heading.trim().length > 0);
}

function visibilityLabel(visibility: number | null): string {
  return visibility == null ? "Not measured" : `${Math.round(visibility)}% visibility`;
}

function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "The content outline could not be loaded.";
  try {
    const parsed = JSON.parse(error.message) as { detail?: string };
    return parsed.detail || error.message;
  } catch {
    return error.message;
  }
}

function statusLabel(topic: TopicContentSample): string {
  if (topic.status === "queued") return "Queued";
  if (topic.status === "running") return "Generating";
  if (topic.status === "error") return "Needs retry";
  if (topic.outline) return "Outline ready";
  return "Not generated";
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6" aria-label="Loading content outline topics" aria-busy="true">
      <div className="h-7 w-64 animate-pulse rounded bg-gray-200" />
      <div className="h-4 w-full max-w-xl animate-pulse rounded bg-gray-200/80" />
      <div className="grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <div className="space-y-2 border-y border-gray-200 py-3">
          {[0, 1, 2, 3].map((item) => (
            <div key={item} className="h-16 animate-pulse rounded-lg bg-white/70" />
          ))}
        </div>
        <div className="space-y-3 border-t border-gray-200 pt-5">
          <div className="h-8 w-3/4 animate-pulse rounded bg-white/80" />
          <div className="h-20 animate-pulse rounded-lg bg-white/70" />
          <div className="h-32 animate-pulse rounded-lg bg-white/70" />
        </div>
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="border-y border-gray-200 bg-white/50 px-6 py-12 text-center">
      <h3 className="text-base font-semibold text-[#0d0d0d]">Prompt evidence is required</h3>
      <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-gray-500">
        Run visibility probes first. The generator uses topic-level responses, citations, and
        brand evidence to create an auditable outline.
      </p>
    </div>
  );
}

export function ContentOutlineGeneratorSection({
  auditDirOrSlug,
}: {
  auditDirOrSlug: string;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<TopicContentSamplesResponse | null>(null);
  const [selectedTopic, setSelectedTopic] = useState("");
  const [draft, setDraft] = useState<ContentOutline | null>(null);
  const [sections, setSections] = useState<EditorSection[]>([]);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);
  const [requesting, setRequesting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const headingRefs = useRef<Array<HTMLInputElement | null>>([]);
  const hydratedTopicRef = useRef("");
  const hydratedVersionRef = useRef("");

  const load = useCallback(async (signal?: AbortSignal) => {
    const response = await fetchTopicContentSamples(auditDirOrSlug, { signal });
    setData(response);
    setLoadError(null);
    return response;
  }, [auditDirOrSlug]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setData(null);
    setSelectedTopic("");
    setLoadError(null);
    load(controller.signal)
      .catch((error) => {
        if (!controller.signal.aborted) setLoadError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load]);

  const topics = useMemo(
    () => [...(data?.topics ?? [])].sort((left, right) => {
      const leftSuggested = left.suggested ? 0 : 1;
      const rightSuggested = right.suggested ? 0 : 1;
      if (leftSuggested !== rightSuggested) return leftSuggested - rightSuggested;
      const leftVisibility = left.visibility ?? Number.POSITIVE_INFINITY;
      const rightVisibility = right.visibility ?? Number.POSITIVE_INFINITY;
      return leftVisibility - rightVisibility || left.topic.localeCompare(right.topic);
    }),
    [data],
  );

  useEffect(() => {
    if (!topics.length) return;
    const requested = searchParams.get("topic");
    const validRequested = requested && topics.some((item) => item.topic === requested)
      ? requested
      : "";
    const preferred = topics.find((item) => item.suggested)?.topic ?? topics[0].topic;
    setSelectedTopic((current) => {
      if (current && topics.some((item) => item.topic === current)) return current;
      return validRequested || preferred;
    });
  }, [searchParams, topics]);

  const selected = useMemo(
    () => topics.find((item) => item.topic === selectedTopic) ?? null,
    [selectedTopic, topics],
  );

  useEffect(() => {
    const version = selected?.updated_at ?? "";
    if (!shouldHydrateOutline({
      hydratedTopic: hydratedTopicRef.current,
      hydratedVersion: hydratedVersionRef.current,
      nextTopic: selectedTopic,
      nextVersion: version,
      dirty,
    })) return;
    const outline = selected?.outline ?? null;
    setDraft(outline);
    setSections(toEditorSections(outline?.sections ?? []));
    setDirty(false);
    hydratedTopicRef.current = selectedTopic;
    hydratedVersionRef.current = version;
  }, [selectedTopic, selected?.outline, selected?.updated_at, dirty]);

  useEffect(() => {
    setActionError(selected?.error ?? null);
  }, [selectedTopic, selected?.error]);

  const hasActiveJob = Boolean(data?.topics.some((item) => ACTIVE_STATUSES.has(item.status)));
  useEffect(() => {
    if (!hasActiveJob) return;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      load(controller.signal)
        .then((response) => {
          const current = response.topics.find((item) => item.topic === selectedTopic);
          if (!controller.signal.aborted && current?.status !== "error") {
            setActionError(null);
          }
        })
        .catch((error) => {
          if (!controller.signal.aborted) setActionError(errorMessage(error));
        });
    }, POLL_INTERVAL_MS);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [hasActiveJob, load, selectedTopic]);

  const chooseTopic = (topic: string) => {
    if (topic === selectedTopic) return;
    if (dirty && !window.confirm("Discard unsaved outline changes and switch topic?")) return;
    setSelectedTopic(topic);
    const next = new URLSearchParams(searchParams);
    next.set("topic", topic);
    setSearchParams(next, { replace: true });
  };

  const runGeneration = async (refresh: boolean, structure?: ContentOutlineStructure) => {
    if (!selectedTopic || requesting) return;
    setRequesting(true);
    setActionError(null);
    try {
      const response = await generateTopicContentSample(auditDirOrSlug, {
        topic: selectedTopic,
        refresh,
        ...(structure ? { structure } : {}),
      });
      setData(response);
      setDirty(false);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setRequesting(false);
    }
  };

  const updateSection = (
    index: number,
    field: "heading" | "instructions",
    value: string,
  ) => {
    setSections((current) =>
      current.map((section, itemIndex) =>
        itemIndex === index ? { ...section, [field]: value } : section
      )
    );
    setDirty(true);
  };

  const moveSection = (from: number, to: number) => {
    if (to < 0 || to >= sections.length || from === to) return;
    setSections((current) => {
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
    setDirty(true);
    window.requestAnimationFrame(() => headingRefs.current[to]?.focus());
  };

  const addSection = () => {
    const index = sections.length;
    setSections((current) => [
      ...current,
      {
        editorKey: nextEditorKey(),
        heading: "",
        level: 2,
        instructions: "",
        sample_copy: "",
        evidence_opportunities: [],
      },
    ]);
    setDirty(true);
    window.requestAnimationFrame(() => headingRefs.current[index]?.focus());
  };

  const removeSection = (index: number) => {
    setSections((current) => current.filter((_, itemIndex) => itemIndex !== index));
    setDirty(true);
  };

  const onDrop = (event: DragEvent<HTMLDivElement>, targetIndex: number) => {
    event.preventDefault();
    if (draggedIndex != null) moveSection(draggedIndex, targetIndex);
    setDraggedIndex(null);
  };

  const structure = (): ContentOutlineStructure => ({
    title: draft?.title,
    meta_description: draft?.meta_description,
    audience: draft?.audience,
    intent: draft?.intent,
    sections: sections.map(({ heading, level, instructions }) => ({
      heading,
      level,
      instructions,
    })),
  });
  const hasInvalidSections = !canRegenerateSections(sections);

  if (loading) return <LoadingSkeleton />;

  if (loadError && !data) {
    return (
      <div className="border-y border-red-200 bg-red-50 px-5 py-6" role="alert">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-600" />
          <div>
            <p className="text-sm font-semibold text-red-900">Could not load content topics</p>
            <p className="mt-1 text-sm text-red-700">{loadError}</p>
            <button
              type="button"
              onClick={() => {
                setLoading(true);
                load()
                  .catch((error) => setLoadError(errorMessage(error)))
                  .finally(() => setLoading(false));
              }}
              className="mt-3 rounded-md text-sm font-semibold text-red-800 underline underline-offset-2 focus:outline-none focus:ring-2 focus:ring-red-500"
            >
              Try again
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (!data?.has_probe_data || !topics.length) {
    return (
      <div className="space-y-6">
        <header>
          <h2 className="text-xl font-bold text-[#0d0d0d]">Content outline generator</h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-gray-500">
            Turn visibility evidence into an editable, source-aware content brief.
          </p>
        </header>
        <EmptyState />
      </div>
    );
  }

  const active = selected && ACTIVE_STATUSES.has(selected.status);

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold text-[#0d0d0d]">Content outline generator</h2>
          <p className="mt-1 max-w-3xl text-sm leading-relaxed text-gray-500">
            Build an evidence-led brief for low-visibility topics, then refine its structure.
          </p>
        </div>
        <p className="text-xs text-gray-400">{topics.length} tested topics</p>
      </header>

      <div className="grid items-start gap-6 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <aside aria-label="Topics ordered by visibility">
          <div className="mb-2 flex items-center justify-between px-1">
            <h3 className="text-[11px] font-bold uppercase tracking-wider text-gray-500">Topics</h3>
            <span className="text-[10px] text-gray-400">Low visibility first</span>
          </div>
          <ol className="overflow-hidden rounded-xl border border-gray-200 bg-white divide-y divide-gray-100">
            {topics.map((topic) => {
              const isSelected = topic.topic === selectedTopic;
              const isActive = ACTIVE_STATUSES.has(topic.status);
              return (
                <li key={topic.topic}>
                  <button
                    type="button"
                    onClick={() => chooseTopic(topic.topic)}
                    aria-current={isSelected ? "true" : undefined}
                    className={cn(
                      "w-full px-4 py-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500",
                      isSelected ? "bg-gray-950 text-white" : "hover:bg-gray-50",
                    )}
                  >
                    <span className="flex items-start justify-between gap-2">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold">{topic.topic}</span>
                        <span className={cn(
                          "mt-1 block text-[11px]",
                          isSelected ? "text-gray-300" : "text-gray-500",
                        )}>
                          {visibilityLabel(topic.visibility)} · {topic.evidence_count} evidence
                        </span>
                      </span>
                      {topic.suggested ? (
                        <span className={cn(
                          "shrink-0 rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide",
                          isSelected ? "bg-white/15 text-white" : "bg-amber-50 text-amber-700",
                        )}>
                          Low visibility
                        </span>
                      ) : null}
                    </span>
                    <span className={cn(
                      "mt-2 flex items-center gap-1.5 text-[10px] font-medium",
                      isSelected ? "text-gray-300" : "text-gray-400",
                    )}>
                      {isActive ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                      {topic.status === "done" && topic.outline
                        ? <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                        : null}
                      {topic.status === "error" ? <AlertCircle className="h-3 w-3 text-red-500" /> : null}
                      {statusLabel(topic)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </aside>

        <main className="min-w-0 border-t border-gray-300 pt-5" aria-live="polite">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">
                Selected topic
              </p>
              <h3 className="mt-1 text-lg font-bold text-[#0d0d0d]">{selected?.topic}</h3>
              <p className="mt-1 text-xs text-gray-500">
                {visibilityLabel(selected?.visibility ?? null)}
                {" · "}
                {selected?.mention_count ?? 0} brand mentions
                {selected?.response_count != null ? ` across ${selected.response_count} responses` : ""}
                {" · "}
                {selected?.evidence_count ?? 0} evidence items
              </p>
            </div>
            {draft ? (
              <button
                type="button"
                onClick={() => void runGeneration(true, structure())}
                disabled={requesting || active || !sections.length || hasInvalidSections}
                className="inline-flex items-center gap-2 rounded-lg bg-[#0d0d0d] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {requesting || active
                  ? <Loader2 className="h-4 w-4 animate-spin" />
                  : <RefreshCw className="h-4 w-4" />}
                {active ? "Generating…" : "Regenerate with structure"}
              </button>
            ) : null}
          </div>

          {actionError ? (
            <div className="mt-5 flex items-start justify-between gap-4 border-y border-red-200 bg-red-50 px-4 py-3" role="alert">
              <div className="flex gap-2 text-sm text-red-800">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{actionError}</span>
              </div>
              <button
                type="button"
                onClick={() => void runGeneration(Boolean(draft), draft ? structure() : undefined)}
                className="shrink-0 rounded-sm text-xs font-bold text-red-800 underline underline-offset-2 focus:outline-none focus:ring-2 focus:ring-red-500"
              >
                Retry
              </button>
            </div>
          ) : null}

          {active && !draft ? (
            <div className="mt-6 space-y-3" aria-busy="true">
              <div className="flex items-center gap-2 text-sm font-semibold text-gray-700">
                <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                {selected?.status === "queued" ? "Outline queued" : "Generating evidence-led outline"}
              </div>
              <div className="h-20 animate-pulse rounded-lg bg-white/70" />
              <div className="h-36 animate-pulse rounded-lg bg-white/70" />
            </div>
          ) : !draft ? (
            <div className="mt-6 border-y border-gray-200 bg-white/50 px-6 py-10 text-center">
              <h4 className="text-base font-semibold text-[#0d0d0d]">No outline generated yet</h4>
              <p className="mx-auto mt-2 max-w-lg text-sm leading-relaxed text-gray-500">
                Generate a first draft using the {selected?.evidence_count ?? 0} evidence items
                collected for this topic.
              </p>
              <button
                type="button"
                onClick={() => void runGeneration(false)}
                disabled={requesting}
                className="mt-5 inline-flex items-center gap-2 rounded-lg bg-[#0d0d0d] px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2 disabled:opacity-50"
              >
                {requesting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                Generate outline
              </button>
            </div>
          ) : (
            <div className="mt-6 space-y-8">
              <section aria-labelledby="outline-summary-heading">
                <h4 id="outline-summary-heading" className="sr-only">Outline summary</h4>
                <dl className="grid gap-x-6 gap-y-4 border-y border-gray-200 py-5 sm:grid-cols-2">
                  <div className="sm:col-span-2">
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Suggested title</dt>
                    <dd className="mt-1 text-base font-semibold text-[#0d0d0d]">{draft.title}</dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Meta description</dt>
                    <dd className="mt-1 max-w-3xl text-sm leading-relaxed text-gray-700">{draft.meta_description}</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Audience</dt>
                    <dd className="mt-1 text-sm text-gray-700">{draft.audience}</dd>
                  </div>
                  <div>
                    <dt className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Intent</dt>
                    <dd className="mt-1 text-sm text-gray-700">{draft.intent}</dd>
                  </div>
                </dl>
              </section>

              <section aria-labelledby="outline-sections-heading">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <h4 id="outline-sections-heading" className="text-sm font-bold text-[#0d0d0d]">
                      Page structure
                    </h4>
                    <p className="mt-1 text-xs text-gray-500">
                      Edit headings and instructions. Use the controls or drag handles to reorder.
                    </p>
                  </div>
                  {dirty ? (
                    <span className="text-[11px] font-medium text-amber-700">Unsaved structure</span>
                  ) : null}
                </div>

                <div className="space-y-3">
                  {sections.map((section, index) => (
                    <div
                      key={section.editorKey}
                      onDragOver={(event) => event.preventDefault()}
                      onDrop={(event) => onDrop(event, index)}
                      className={cn(
                        "grid gap-3 rounded-xl border border-gray-200 bg-white p-4 sm:grid-cols-[auto_minmax(0,1fr)_auto]",
                        draggedIndex === index && "opacity-50",
                      )}
                    >
                      <button
                        type="button"
                        draggable
                        onDragStart={() => setDraggedIndex(index)}
                        onDragEnd={() => setDraggedIndex(null)}
                        aria-label={`Drag section ${index + 1} to reorder`}
                        className="mt-7 hidden h-8 w-6 cursor-grab items-center justify-center rounded-md text-gray-300 hover:bg-gray-100 hover:text-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 sm:inline-flex"
                      >
                        <GripVertical className="h-4 w-4" />
                      </button>
                      <div className="min-w-0 space-y-3">
                        <div className="grid gap-3 sm:grid-cols-[5rem_minmax(0,1fr)]">
                          <label className="text-xs font-semibold text-gray-600">
                            Level
                            <select
                              value={section.level}
                              onChange={(event) => {
                                const level = Number(event.target.value) as 2 | 3;
                                setSections((current) => current.map((item, itemIndex) =>
                                  itemIndex === index ? { ...item, level } : item
                                ));
                                setDirty(true);
                              }}
                              className="mt-1 w-full rounded-lg border border-gray-300 bg-white px-2 py-2 text-sm text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                              aria-label={`Heading level for section ${index + 1}`}
                            >
                              <option value={2}>H2</option>
                              <option value={3}>H3</option>
                            </select>
                          </label>
                          <label className="text-xs font-semibold text-gray-600">
                            Heading
                            <input
                              ref={(node) => { headingRefs.current[index] = node; }}
                              value={section.heading}
                              onChange={(event) => updateSection(index, "heading", event.target.value)}
                              className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                              aria-label={`Heading for section ${index + 1}`}
                            />
                          </label>
                        </div>
                        <label className="block text-xs font-semibold text-gray-600">
                          Writer instructions
                          <textarea
                            value={section.instructions}
                            onChange={(event) => updateSection(index, "instructions", event.target.value)}
                            rows={3}
                            className="mt-1 w-full resize-y rounded-lg border border-gray-300 px-3 py-2 text-sm leading-relaxed text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
                            aria-label={`Writer instructions for section ${index + 1}`}
                          />
                        </label>
                        {section.sample_copy ? (
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">Sample copy</p>
                            <p className="mt-1 text-sm leading-relaxed text-gray-600">{section.sample_copy}</p>
                          </div>
                        ) : null}
                        {section.evidence_opportunities?.length ? (
                          <div>
                            <p className="text-[10px] font-bold uppercase tracking-wider text-gray-400">
                              Evidence opportunities
                            </p>
                            <ul className="mt-1.5 space-y-1">
                              {section.evidence_opportunities.map((opportunity, itemIndex) => (
                                <li key={itemIndex} className="text-xs leading-relaxed text-gray-600">
                                  • {typeof opportunity === "string"
                                    ? opportunity
                                    : opportunity.description}
                                </li>
                              ))}
                            </ul>
                          </div>
                        ) : null}
                      </div>
                      <div className="flex items-start gap-1 sm:flex-col">
                        <button
                          type="button"
                          onClick={() => moveSection(index, index - 1)}
                          disabled={index === 0}
                          aria-label={`Move section ${index + 1} up`}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-25"
                        >
                          <ArrowUp className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => moveSection(index, index + 1)}
                          disabled={index === sections.length - 1}
                          aria-label={`Move section ${index + 1} down`}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-500 hover:bg-gray-100 focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-25"
                        >
                          <ArrowDown className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => removeSection(index)}
                          aria-label={`Remove section ${index + 1}`}
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:bg-red-50 hover:text-red-700 focus:outline-none focus:ring-2 focus:ring-red-500"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={addSection}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-md px-2 py-1.5 text-xs font-semibold text-blue-700 hover:bg-blue-50 focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add section
                </button>
                {hasInvalidSections ? (
                  <p className="mt-2 text-xs font-medium text-red-700" role="alert">
                    Add a heading to every section before regenerating.
                  </p>
                ) : null}
              </section>

              {draft.faqs.length ? (
                <section aria-labelledby="outline-faq-heading">
                  <h4 id="outline-faq-heading" className="text-sm font-bold text-[#0d0d0d]">FAQs</h4>
                  <ol className="mt-3 divide-y divide-gray-200 border-y border-gray-200">
                    {draft.faqs.map((faq, index) => (
                      <li key={`${faq.question}-${index}`} className="py-3">
                        <p className="text-sm font-semibold text-gray-900">{faq.question}</p>
                        {faq.answer || faq.guidance ? (
                          <p className="mt-1 text-xs leading-relaxed text-gray-600">
                            {faq.answer || faq.guidance}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ol>
                </section>
              ) : null}

              {draft.internal_links.length ? (
                <section aria-labelledby="outline-links-heading">
                  <h4 id="outline-links-heading" className="text-sm font-bold text-[#0d0d0d]">
                    Internal links
                  </h4>
                  <ul className="mt-3 divide-y divide-gray-200 border-y border-gray-200">
                    {draft.internal_links.map((link, index) => (
                      <li key={`${link.target_url}-${index}`} className="flex flex-wrap items-start justify-between gap-3 py-3">
                        <div>
                          <p className="text-sm font-semibold text-gray-800">{link.anchor_text}</p>
                          {link.rationale ? <p className="mt-1 text-xs text-gray-500">{link.rationale}</p> : null}
                        </div>
                        {/^https?:\/\//i.test(link.target_url) ? (
                          <a
                            href={link.target_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex max-w-full items-center gap-1 truncate rounded-sm text-xs font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                          >
                            {link.target_url} <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        ) : (
                          <code className="max-w-full truncate rounded bg-gray-100 px-2 py-1 text-xs text-gray-700">
                            {link.target_url}
                          </code>
                        )}
                      </li>
                    ))}
                  </ul>
                </section>
              ) : null}

              <section aria-labelledby="outline-provenance-heading">
                <h4 id="outline-provenance-heading" className="text-sm font-bold text-[#0d0d0d]">
                  Provenance
                </h4>
                {draft.provenance.length ? (
                  <ul className="mt-3 space-y-2 text-xs text-gray-600">
                    {draft.provenance.map((source, index) => (
                      <li key={`${source.url || source.title || source.source}-${index}`} className="flex gap-2">
                        <span className="font-bold text-gray-400">{index + 1}.</span>
                        <span>
                          {source.url ? (
                            <a
                              href={source.url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              {source.title || source.source || source.url}
                            </a>
                          ) : (
                            <span className="font-semibold text-gray-800">
                              {source.title || source.source || "Audit evidence"}
                            </span>
                          )}
                          {source.detail ? `: ${source.detail}` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-2 text-xs text-gray-500">
                    This outline was generated from the topic evidence recorded in the audit.
                  </p>
                )}
              </section>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
