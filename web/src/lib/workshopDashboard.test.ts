import { describe, expect, it } from "vitest";
import {
  createWidget,
  duplicateWidget,
  formatFieldLabel,
  moveWidget,
  prepareWidgetRows,
  type DashboardDataset,
  type DashboardWidget,
} from "./workshopDashboard";

const baseWidget: DashboardWidget = {
  id: "scores",
  title: "Scores",
  dataset: "scores",
  visualization: "bar",
  dimension: "pillar",
  metric: "score",
  sort_direction: "desc",
  limit: 2,
  filter_text: "",
  filter_topic: "",
  filter_channel: "",
  filter_platform: "",
};

const scoresDataset: DashboardDataset = {
  id: "scores",
  label: "Audit scores",
  description: "Scores",
  dimension_fields: ["pillar"],
  metric_fields: ["score"],
  default_dimension: "pillar",
  default_metric: "score",
  visualizations: ["table", "bar", "line", "pie"],
  rows: [
    { pillar: "Overall", score: 72 },
    { pillar: "Technical setup", score: 84 },
    { pillar: "Content quality", score: 65 },
  ],
  row_count: 3,
};

const promptsDataset: DashboardDataset = {
  id: "prompts",
  label: "Prompt performance",
  description: "Prompts",
  dimension_fields: ["prompt", "topic", "platform", "locale", "sentiment"],
  metric_fields: ["visibility_pct", "avg_position", "response_count"],
  default_dimension: "prompt",
  default_metric: "visibility_pct",
  visualizations: ["table", "bar", "line"],
  filter_options: {
    topics: ["Brakes", "Batteries"],
    platforms: ["gemini", "openai", "google_aio"],
    channels: ["chatbot", "ai_overview"],
  },
  rows: [
    {
      prompt: "Best brake pads",
      topic: "Brakes",
      platform: "gemini",
      channel: "Chatbot",
      locale: "uk-en",
      visibility_pct: 50,
      avg_position: 2,
      response_count: 2,
      sentiment: "positive",
    },
    {
      prompt: "Best brake pads",
      topic: "Brakes",
      platform: "openai",
      channel: "Chatbot",
      locale: "uk-en",
      visibility_pct: 100,
      avg_position: 1,
      response_count: 1,
      sentiment: "positive",
    },
    {
      prompt: "Best batteries",
      topic: "Batteries",
      platform: "google_aio",
      channel: "AI Overview",
      locale: "uk-en",
      visibility_pct: 25,
      avg_position: 4,
      response_count: 1,
      sentiment: "neutral",
    },
  ],
  row_count: 3,
};

describe("workshop dashboard helpers", () => {
  it("sorts, filters and limits preview rows", () => {
    expect(prepareWidgetRows(scoresDataset, baseWidget).map((row) => row.pillar)).toEqual([
      "Technical setup",
      "Overall",
    ]);
    expect(
      prepareWidgetRows(scoresDataset, { ...baseWidget, filter_text: "content" }),
    ).toEqual([{ pillar: "Content quality", score: 65 }]);
  });

  it("filters prompt rows by topic, channel and platform", () => {
    const widget: DashboardWidget = {
      ...createWidget(promptsDataset),
      filter_topic: "Brakes",
      filter_channel: "chatbot",
      filter_platform: "openai",
      limit: 10,
    };
    expect(prepareWidgetRows(promptsDataset, widget)).toEqual([
      expect.objectContaining({
        prompt: "Best brake pads",
        platform: "openai",
        visibility_pct: 100,
      }),
    ]);
  });

  it("aggregates prompt rows by topic and platform dimensions", () => {
    const byTopic = prepareWidgetRows(promptsDataset, {
      ...createWidget(promptsDataset),
      dimension: "topic",
      metric: "visibility_pct",
      limit: 10,
    });
    expect(byTopic).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ topic: "Brakes", visibility_pct: 75, response_count: 3 }),
        expect.objectContaining({ topic: "Batteries", visibility_pct: 25, response_count: 1 }),
      ]),
    );

    const byPlatform = prepareWidgetRows(promptsDataset, {
      ...createWidget(promptsDataset),
      dimension: "platform",
      metric: "response_count",
      sort_direction: "desc",
      limit: 10,
    });
    expect(byPlatform[0]).toEqual(
      expect.objectContaining({ platform: "gemini", response_count: 2 }),
    );
  });

  it("moves widgets without mutating the source", () => {
    const second = { ...baseWidget, id: "second" };
    const source = [baseWidget, second];
    const moved = moveWidget(source, 1, 0);
    expect(moved.map((item) => item.id)).toEqual(["second", "scores"]);
    expect(source.map((item) => item.id)).toEqual(["scores", "second"]);
  });

  it("formats API field names for controls", () => {
    expect(formatFieldLabel("ai_visibility")).toBe("Ai Visibility");
    expect(formatFieldLabel("topic")).toBe("Topic");
    expect(formatFieldLabel("google_aio")).toBe("Google AI Overview");
  });

  it("keeps duplicated widget titles within the API limit", () => {
    expect(duplicateWidget({ ...baseWidget, title: "A".repeat(120) }).title).toHaveLength(120);
  });
});
