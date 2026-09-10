import { useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  BarChart3,
  Copy,
  Database,
  Plus,
  Save,
  Table2,
  Trash2,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchWorkshopDashboard,
  fetchWorkshopDashboardData,
  saveWorkshopDashboard,
} from "../api/client";
import {
  channelFilterLabel,
  createWidget,
  duplicateWidget,
  formatFieldLabel,
  moveWidget,
  platformLabel,
  prepareWidgetRows,
  type DashboardConfig,
  type DashboardDataset,
  type DashboardFilterChannel,
  type DashboardRow,
  type DashboardVisualization,
  type DashboardWidget,
} from "../lib/workshopDashboard";

const CHART_COLORS = ["#6366f1", "#0f766e", "#b45309", "#be123c", "#0369a1"];

function valueLabel(value: DashboardRow[string]): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, {
      maximumFractionDigits: 2,
    });
  }
  const text = String(value ?? "Unavailable");
  if (["gemini", "openai", "claude", "google_aio"].includes(text)) {
    return platformLabel(text);
  }
  return text;
}

function normalizeWidget(widget: DashboardWidget): DashboardWidget {
  return {
    ...widget,
    filter_text: widget.filter_text ?? "",
    filter_topic: widget.filter_topic ?? "",
    filter_channel: widget.filter_channel ?? "",
    filter_platform: widget.filter_platform ?? "",
  };
}

function normalizeConfig(config: DashboardConfig): DashboardConfig {
  return {
    ...config,
    widgets: config.widgets.map(normalizeWidget),
  };
}

function DashboardTable({
  rows,
  widget,
}: {
  rows: DashboardRow[];
  widget: DashboardWidget;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200">
      <table className="min-w-full divide-y divide-gray-200 text-sm">
        <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
          <tr>
            <th className="px-4 py-3">{formatFieldLabel(widget.dimension)}</th>
            <th className="px-4 py-3 text-right">{formatFieldLabel(widget.metric)}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100 bg-white">
          {rows.map((row, index) => (
            <tr key={`${String(row[widget.dimension])}-${index}`}>
              <td className="max-w-xl px-4 py-3 text-gray-800">
                {valueLabel(row[widget.dimension])}
              </td>
              <td className="px-4 py-3 text-right font-medium tabular-nums text-gray-900">
                {valueLabel(row[widget.metric])}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DashboardChart({
  rows,
  widget,
}: {
  rows: DashboardRow[];
  widget: DashboardWidget;
}) {
  if (widget.visualization === "pie") {
    return (
      <div className="h-72" aria-label={`${widget.title} pie chart`}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={rows}
              dataKey={widget.metric}
              nameKey={widget.dimension}
              innerRadius={52}
              outerRadius={92}
              paddingAngle={2}
            >
              {rows.map((_, index) => (
                <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip />
          </PieChart>
        </ResponsiveContainer>
      </div>
    );
  }

  const common = {
    data: rows,
    margin: { top: 12, right: 16, left: 0, bottom: 70 },
  };
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
      <XAxis
        dataKey={widget.dimension}
        angle={-35}
        textAnchor="end"
        interval={0}
        height={80}
        tick={{ fontSize: 11, fill: "#4b5563" }}
      />
      <YAxis tick={{ fontSize: 11, fill: "#4b5563" }} />
      <Tooltip />
    </>
  );

  return (
    <div className="h-80" aria-label={`${widget.title} ${widget.visualization} chart`}>
      <ResponsiveContainer width="100%" height="100%">
        {widget.visualization === "line" ? (
          <LineChart {...common}>
            {axes}
            <Line
              type="monotone"
              dataKey={widget.metric}
              stroke={CHART_COLORS[0]}
              strokeWidth={2}
              dot={{ r: 3 }}
            />
          </LineChart>
        ) : (
          <BarChart {...common}>
            {axes}
            <Bar dataKey={widget.metric} fill={CHART_COLORS[0]} radius={[4, 4, 0, 0]} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

function WidgetPreview({
  dataset,
  widget,
}: {
  dataset: DashboardDataset;
  widget: DashboardWidget;
}) {
  const rows = useMemo(() => prepareWidgetRows(dataset, widget), [dataset, widget]);
  if (!dataset.rows.length) {
    return (
      <div className="flex min-h-52 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 text-center text-sm text-gray-600">
        This aggregate is not available for this audit yet.
      </div>
    );
  }
  if (!rows.length) {
    return (
      <div className="flex min-h-52 items-center justify-center rounded-lg border border-dashed border-gray-300 bg-gray-50 px-6 text-center text-sm text-gray-600">
        No rows match the current filter.
      </div>
    );
  }
  return widget.visualization === "table" ? (
    <DashboardTable rows={rows} widget={widget} />
  ) : (
    <DashboardChart rows={rows} widget={widget} />
  );
}

function WidgetEditor({
  widget,
  dataset,
  index,
  total,
  onChange,
  onMove,
  onDuplicate,
  onRemove,
}: {
  widget: DashboardWidget;
  dataset: DashboardDataset;
  index: number;
  total: number;
  onChange: (widget: DashboardWidget) => void;
  onMove: (toIndex: number) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  const set = <K extends keyof DashboardWidget>(key: K, value: DashboardWidget[K]) =>
    onChange({ ...widget, [key]: value });

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-200 px-5 py-4">
        <span className="rounded-md bg-indigo-50 px-2 py-1 text-xs font-semibold text-indigo-700">
          {dataset.label}
        </span>
        <input
          className={`min-w-48 flex-1 rounded-md border px-2 py-1 text-base font-semibold text-gray-900 outline-none focus:ring-2 ${
            widget.title.trim()
              ? "border-transparent hover:border-gray-200 focus:border-indigo-500 focus:ring-indigo-100"
              : "border-red-400 focus:border-red-500 focus:ring-red-100"
          }`}
          value={widget.title}
          aria-label="Widget title"
          aria-invalid={!widget.title.trim()}
          maxLength={120}
          onChange={(event) => set("title", event.target.value)}
        />
        <div className="flex items-center gap-1">
          <button
            type="button"
            className="btn-ghost p-2"
            aria-label={`Move ${widget.title} up`}
            disabled={index === 0}
            onClick={() => onMove(index - 1)}
          >
            <ArrowUp className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="btn-ghost p-2"
            aria-label={`Move ${widget.title} down`}
            disabled={index === total - 1}
            onClick={() => onMove(index + 1)}
          >
            <ArrowDown className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="btn-ghost p-2"
            aria-label={`Duplicate ${widget.title}`}
            onClick={onDuplicate}
          >
            <Copy className="h-4 w-4" />
          </button>
          <button
            type="button"
            className="btn-ghost p-2 text-red-700 hover:bg-red-50"
            aria-label={`Remove ${widget.title}`}
            onClick={onRemove}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="grid gap-6 p-5 xl:grid-cols-[15rem_minmax(0,1fr)]">
        <div className="space-y-4">
          <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
            View
            <select
              className="input-field mt-1 px-3 py-2 text-sm normal-case"
              value={widget.visualization}
              onChange={(event) =>
                set("visualization", event.target.value as DashboardVisualization)
              }
            >
              {dataset.visualizations.map((visualization) => (
                <option key={visualization} value={visualization}>
                  {formatFieldLabel(visualization)}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
            Dimension
            <select
              className="input-field mt-1 px-3 py-2 text-sm normal-case"
              value={widget.dimension}
              onChange={(event) => set("dimension", event.target.value)}
            >
              {dataset.dimension_fields.map((field) => (
                <option key={field} value={field}>
                  {formatFieldLabel(field)}
                </option>
              ))}
            </select>
          </label>
          <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
            Metric
            <select
              className="input-field mt-1 px-3 py-2 text-sm normal-case"
              value={widget.metric}
              onChange={(event) => set("metric", event.target.value)}
            >
              {dataset.metric_fields.map((field) => (
                <option key={field} value={field}>
                  {formatFieldLabel(field)}
                </option>
              ))}
            </select>
          </label>
          {widget.dataset === "prompts" ? (
            <>
              <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
                Topic
                <select
                  className="input-field mt-1 px-3 py-2 text-sm normal-case"
                  value={widget.filter_topic}
                  onChange={(event) => set("filter_topic", event.target.value)}
                >
                  <option value="">All topics</option>
                  {(dataset.filter_options?.topics ?? []).map((topic) => (
                    <option key={topic} value={topic}>
                      {topic}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
                Channel
                <select
                  className="input-field mt-1 px-3 py-2 text-sm normal-case"
                  value={widget.filter_channel}
                  onChange={(event) => {
                    const next = event.target.value as DashboardFilterChannel;
                    onChange({
                      ...widget,
                      filter_channel: next,
                      filter_platform:
                        next &&
                        widget.filter_platform &&
                        ((next === "chatbot" &&
                          !["gemini", "openai", "claude"].includes(widget.filter_platform)) ||
                          (next === "ai_overview" && widget.filter_platform !== "google_aio"))
                          ? ""
                          : widget.filter_platform,
                    });
                  }}
                >
                  <option value="">{channelFilterLabel("")}</option>
                  {(dataset.filter_options?.channels ?? ["chatbot", "ai_overview"]).map(
                    (channel) => (
                      <option key={channel} value={channel}>
                        {channelFilterLabel(channel)}
                      </option>
                    ),
                  )}
                </select>
              </label>
              <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
                Platform
                <select
                  className="input-field mt-1 px-3 py-2 text-sm normal-case"
                  value={widget.filter_platform}
                  onChange={(event) => set("filter_platform", event.target.value)}
                >
                  <option value="">All platforms</option>
                  {(dataset.filter_options?.platforms ?? [])
                    .filter((platform) => {
                      if (widget.filter_channel === "chatbot") {
                        return ["gemini", "openai", "claude"].includes(platform);
                      }
                      if (widget.filter_channel === "ai_overview") {
                        return platform === "google_aio";
                      }
                      return true;
                    })
                    .map((platform) => (
                      <option key={platform} value={platform}>
                        {platformLabel(platform)}
                      </option>
                    ))}
                </select>
              </label>
            </>
          ) : null}
          <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
            Search
            <input
              className="input-field mt-1 px-3 py-2 text-sm normal-case"
              value={widget.filter_text}
              placeholder="Search rows"
              onChange={(event) => set("filter_text", event.target.value)}
            />
          </label>
          <div className="grid grid-cols-2 gap-3">
            <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
              Sort
              <select
                className="input-field mt-1 px-3 py-2 text-sm normal-case"
                value={widget.sort_direction}
                onChange={(event) =>
                  set("sort_direction", event.target.value as "asc" | "desc")
                }
              >
                <option value="desc">Highest first</option>
                <option value="asc">Lowest first</option>
              </select>
            </label>
            <label className="block text-xs font-semibold uppercase tracking-wide text-gray-500">
              Rows
              <input
                className="input-field mt-1 px-3 py-2 text-sm normal-case"
                type="number"
                min={1}
                max={100}
                value={widget.limit}
                onChange={(event) =>
                  set("limit", Math.min(100, Math.max(1, Number(event.target.value) || 1)))
                }
              />
            </label>
          </div>
        </div>
        <div className="min-w-0">
          <WidgetPreview dataset={dataset} widget={widget} />
          <p className="mt-2 text-right text-xs text-gray-500">
            {dataset.row_count.toLocaleString()} source rows
          </p>
        </div>
      </div>
    </section>
  );
}

export function WorkshopDashboard({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [config, setConfig] = useState<DashboardConfig | null>(null);
  const [datasets, setDatasets] = useState<DashboardDataset[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [generatedAt, setGeneratedAt] = useState("");
  const editRevisionRef = useRef(0);
  const auditGenerationRef = useRef(0);

  useEffect(() => {
    auditGenerationRef.current += 1;
    let active = true;
    setLoading(true);
    setSaving(false);
    setError(null);
    setNotice(null);
    Promise.all([
      fetchWorkshopDashboard(auditDirOrSlug),
      fetchWorkshopDashboardData(auditDirOrSlug),
    ])
      .then(([savedConfig, data]) => {
        if (!active) return;
        setConfig(normalizeConfig(savedConfig));
        setDatasets(data.datasets);
        setGeneratedAt(data.generated_at);
        editRevisionRef.current = 0;
        setDirty(false);
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "Could not load dashboard");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [auditDirOrSlug]);

  const datasetById = useMemo(
    () => new Map(datasets.map((dataset) => [dataset.id, dataset])),
    [datasets],
  );

  const changeConfig = (next: DashboardConfig) => {
    editRevisionRef.current += 1;
    setConfig(next);
    setDirty(true);
    setNotice(null);
  };

  const save = async () => {
    if (!config || saving) return;
    const revisionAtSave = editRevisionRef.current;
    const auditGenerationAtSave = auditGenerationRef.current;
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const saved = await saveWorkshopDashboard(auditDirOrSlug, config);
      if (auditGenerationRef.current !== auditGenerationAtSave) return;
      if (editRevisionRef.current === revisionAtSave) {
        setConfig(saved);
        setDirty(false);
        setNotice("Dashboard saved");
      } else {
        setNotice("Earlier changes saved. Save again to include your latest edits.");
      }
    } catch (reason) {
      if (auditGenerationRef.current !== auditGenerationAtSave) return;
      setError(reason instanceof Error ? reason.message : "Could not save dashboard");
    } finally {
      if (auditGenerationRef.current === auditGenerationAtSave) {
        setSaving(false);
      }
    }
  };

  if (loading) {
    return (
      <div className="space-y-4" aria-label="Loading dashboard builder">
        <div className="h-24 animate-pulse rounded-xl bg-white/70" />
        <div className="h-80 animate-pulse rounded-xl bg-white/70" />
      </div>
    );
  }

  if (error && !config) {
    return <div className="alert-error">{error}</div>;
  }

  if (!config) return null;
  const canSave =
    Boolean(config.title.trim()) &&
    config.widgets.every((widget) => Boolean(widget.title.trim()));

  return (
    <div className="space-y-6">
      <header className="flex flex-col gap-4 border-b border-gray-300 pb-6 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-indigo-700">
            Workshop
          </p>
          <input
            className="w-full max-w-2xl border-0 bg-transparent p-0 text-3xl font-semibold tracking-tight text-gray-900 outline-none focus:ring-0"
            value={config.title}
            aria-label="Dashboard title"
            maxLength={120}
            onChange={(event) => changeConfig({ ...config, title: event.target.value })}
          />
          <p className="mt-2 max-w-2xl text-sm text-gray-600">
            Build a client-ready view from approved audit aggregates. Data stays scoped to this
            audit and raw response text is not included.
          </p>
        </div>
        <button
          type="button"
          className="btn-primary self-start px-5 py-2.5 md:self-auto"
          disabled={!dirty || saving || !canSave}
          onClick={() => void save()}
        >
          <Save className="h-4 w-4" />
          {saving ? "Saving…" : dirty ? "Save dashboard" : "Saved"}
        </button>
      </header>

      {error ? <div className="alert-error mb-0">{error}</div> : null}
      {notice ? <div className="alert-success mb-0">{notice}</div> : null}

      <div className="grid items-start gap-6 lg:grid-cols-[15rem_minmax(0,1fr)]">
        <aside className="rounded-xl border border-gray-200 bg-white p-4 lg:sticky lg:top-4">
          <div className="mb-4 flex items-center gap-2">
            <Database className="h-4 w-4 text-indigo-700" />
            <h2 className="text-sm font-semibold text-gray-900">Add dataset</h2>
          </div>
          <div className="space-y-2">
            {datasets.map((dataset) => (
              <button
                key={dataset.id}
                type="button"
                className="group w-full rounded-lg border border-gray-200 p-3 text-left transition-colors hover:border-indigo-300 hover:bg-indigo-50 focus:outline-none focus:ring-2 focus:ring-indigo-400"
                disabled={config.widgets.length >= 12}
                onClick={() =>
                  changeConfig({ ...config, widgets: [...config.widgets, createWidget(dataset)] })
                }
              >
                <span className="flex items-center justify-between gap-2 text-sm font-medium text-gray-900">
                  {dataset.label}
                  <Plus className="h-4 w-4 text-gray-400 group-hover:text-indigo-700" />
                </span>
                <span className="mt-1 block text-xs leading-5 text-gray-500">
                  {dataset.row_count.toLocaleString()} rows
                </span>
              </button>
            ))}
          </div>
          <p className="mt-4 border-t border-gray-100 pt-4 text-xs leading-5 text-gray-500">
            Up to 12 widgets. Use the arrows on each widget to set report order.
          </p>
        </aside>

        <main className="min-w-0 space-y-5">
          {config.widgets.length === 0 ? (
            <div className="flex min-h-72 flex-col items-center justify-center rounded-xl border border-dashed border-gray-300 bg-white/60 px-8 text-center">
              <Table2 className="mb-3 h-8 w-8 text-gray-400" />
              <h2 className="font-semibold text-gray-900">Start with an audit dataset</h2>
              <p className="mt-1 max-w-md text-sm text-gray-600">
                Choose a dataset to create a table or chart. You can change its fields and view
                before saving.
              </p>
            </div>
          ) : (
            config.widgets.map((widget, index) => {
              const dataset = datasetById.get(widget.dataset);
              if (!dataset) return null;
              return (
                <WidgetEditor
                  key={widget.id}
                  widget={widget}
                  dataset={dataset}
                  index={index}
                  total={config.widgets.length}
                  onChange={(nextWidget) =>
                    changeConfig({
                      ...config,
                      widgets: config.widgets.map((item) =>
                        item.id === widget.id ? nextWidget : item,
                      ),
                    })
                  }
                  onMove={(toIndex) =>
                    changeConfig({
                      ...config,
                      widgets: moveWidget(config.widgets, index, toIndex),
                    })
                  }
                  onDuplicate={() =>
                    changeConfig({
                      ...config,
                      widgets: [
                        ...config.widgets.slice(0, index + 1),
                        duplicateWidget(widget),
                        ...config.widgets.slice(index + 1),
                      ].slice(0, 12),
                    })
                  }
                  onRemove={() =>
                    changeConfig({
                      ...config,
                      widgets: config.widgets.filter((item) => item.id !== widget.id),
                    })
                  }
                />
              );
            })
          )}
        </main>
      </div>

      <footer className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-gray-300 pt-4 text-xs text-gray-500">
        <span className="inline-flex items-center gap-1.5">
          <BarChart3 className="h-3.5 w-3.5" />
          Persisted audit aggregates
        </span>
        {generatedAt ? <span>Refreshed {new Date(generatedAt).toLocaleString()}</span> : null}
        {config.updated_at ? (
          <span>Saved {new Date(config.updated_at).toLocaleString()}</span>
        ) : (
          <span>Using the starter layout</span>
        )}
      </footer>
    </div>
  );
}
