export type DashboardDatasetId =
  | "scores"
  | "prompts"
  | "citations"
  | "competitors"
  | "findings";

export type DashboardVisualization = "table" | "bar" | "line" | "pie";
export type DashboardRow = Record<string, string | number | boolean | null>;
export type DashboardFilterChannel = "" | "chatbot" | "ai_overview";

export interface DashboardFilterOptions {
  topics: string[];
  platforms: string[];
  channels: Array<"chatbot" | "ai_overview">;
}

export interface DashboardDataset {
  id: DashboardDatasetId;
  label: string;
  description: string;
  dimension_fields: string[];
  metric_fields: string[];
  default_dimension: string;
  default_metric: string;
  visualizations: DashboardVisualization[];
  rows: DashboardRow[];
  row_count: number;
  filter_options?: DashboardFilterOptions;
}

export interface DashboardDataResponse {
  schema_version: 1;
  generated_at: string;
  provenance: {
    audit_id: string;
    source: string;
    replies_omitted: boolean;
  };
  datasets: DashboardDataset[];
}

export interface DashboardWidget {
  id: string;
  title: string;
  dataset: DashboardDatasetId;
  visualization: DashboardVisualization;
  dimension: string;
  metric: string;
  sort_direction: "asc" | "desc";
  limit: number;
  filter_text: string;
  filter_topic: string;
  filter_channel: DashboardFilterChannel;
  filter_platform: string;
}

export interface DashboardConfig {
  schema_version: 1;
  title: string;
  widgets: DashboardWidget[];
  updated_at: string | null;
  updated_by: string | null;
}

const AGGREGATE_DIMENSIONS = new Set(["topic", "platform"]);

const PLATFORM_LABELS: Record<string, string> = {
  gemini: "Gemini",
  openai: "ChatGPT",
  claude: "Claude",
  google_aio: "Google AI Overview",
};

const CHANNEL_LABELS: Record<DashboardFilterChannel, string> = {
  "": "All channels",
  chatbot: "Chatbots",
  ai_overview: "AI Overview",
};

const CHANNEL_ROW_VALUES: Record<"chatbot" | "ai_overview", string> = {
  chatbot: "Chatbot",
  ai_overview: "AI Overview",
};

export function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? formatFieldLabel(platform);
}

export function channelFilterLabel(channel: DashboardFilterChannel): string {
  return CHANNEL_LABELS[channel];
}

export function createWidget(dataset: DashboardDataset): DashboardWidget {
  return {
    id: `${dataset.id}-${crypto.randomUUID()}`,
    title: dataset.label,
    dataset: dataset.id,
    visualization: dataset.visualizations.includes("bar") ? "bar" : "table",
    dimension: dataset.default_dimension,
    metric: dataset.default_metric,
    sort_direction: "desc",
    limit: 10,
    filter_text: "",
    filter_topic: "",
    filter_channel: "",
    filter_platform: "",
  };
}

export function duplicateWidget(widget: DashboardWidget): DashboardWidget {
  return {
    ...widget,
    id: `${widget.dataset}-${crypto.randomUUID()}`,
    title: `${widget.title.slice(0, 115)} copy`,
  };
}

export function moveWidget(
  widgets: DashboardWidget[],
  fromIndex: number,
  toIndex: number,
): DashboardWidget[] {
  if (
    fromIndex < 0 ||
    fromIndex >= widgets.length ||
    toIndex < 0 ||
    toIndex >= widgets.length ||
    fromIndex === toIndex
  ) {
    return widgets;
  }
  const next = [...widgets];
  const [moved] = next.splice(fromIndex, 1);
  next.splice(toIndex, 0, moved);
  return next;
}

function sortValue(value: DashboardRow[string]): string | number {
  if (typeof value === "number") return value;
  return String(value ?? "").toLocaleLowerCase();
}

function mean(values: number[]): number | null {
  if (!values.length) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  return Math.round((total / values.length) * 100) / 100;
}

function matchesStructuredFilters(row: DashboardRow, widget: DashboardWidget): boolean {
  if (widget.filter_topic && String(row.topic ?? "") !== widget.filter_topic) {
    return false;
  }
  if (widget.filter_platform && String(row.platform ?? "") !== widget.filter_platform) {
    return false;
  }
  if (widget.filter_channel) {
    const expected = CHANNEL_ROW_VALUES[widget.filter_channel];
    if (String(row.channel ?? "") !== expected) return false;
  }
  return true;
}

function aggregateRows(
  rows: DashboardRow[],
  dimension: string,
): DashboardRow[] {
  const groups = new Map<string, DashboardRow[]>();
  for (const row of rows) {
    const key = String(row[dimension] ?? "").trim() || "Other";
    const bucket = groups.get(key) ?? [];
    bucket.push(row);
    groups.set(key, bucket);
  }

  return [...groups.entries()].map(([key, group]) => {
    const visibility = group
      .map((row) => row.visibility_pct)
      .filter((value): value is number => typeof value === "number");
    const positions = group
      .map((row) => row.avg_position)
      .filter((value): value is number => typeof value === "number");
    const responseCount = group.reduce(
      (sum, row) => sum + (typeof row.response_count === "number" ? row.response_count : 0),
      0,
    );
    return {
      [dimension]: key,
      visibility_pct: mean(visibility),
      avg_position: mean(positions),
      response_count: responseCount,
    };
  });
}

export function prepareWidgetRows(
  dataset: DashboardDataset,
  widget: DashboardWidget,
): DashboardRow[] {
  const needle = widget.filter_text.trim().toLocaleLowerCase();
  let filtered = dataset.rows.filter((row) => matchesStructuredFilters(row, widget));
  if (needle) {
    filtered = filtered.filter((row) =>
      Object.values(row).some((value) =>
        String(value ?? "").toLocaleLowerCase().includes(needle),
      ),
    );
  }

  const prepared =
    AGGREGATE_DIMENSIONS.has(widget.dimension) && widget.dataset === "prompts"
      ? aggregateRows(filtered, widget.dimension)
      : filtered;

  return [...prepared]
    .sort((left, right) => {
      const a = sortValue(left[widget.metric]);
      const b = sortValue(right[widget.metric]);
      const comparison =
        typeof a === "number" && typeof b === "number"
          ? a - b
          : String(a).localeCompare(String(b));
      return widget.sort_direction === "asc" ? comparison : -comparison;
    })
    .slice(0, widget.limit);
}

export function formatFieldLabel(field: string): string {
  if (field === "platform") return "Platform";
  if (field === "topic") return "Topic";
  if (field in PLATFORM_LABELS) return platformLabel(field);
  return field
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}
