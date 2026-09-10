import { describe, expect, it } from "vitest";
import { computeOverallSentiment } from "./sentimentCalc";

describe("surface sentiment", () => {
  it("uses positive mentions divided by all mentions in the selected surface", () => {
    const rows = [
      {
        runs: {
          gemini: [{ response: "Example is excellent.", error: "" }],
          openai: [{ response: "Example is poor.", error: "" }],
          google_aio: [{ response: "Example is excellent.", error: "" }],
        },
      },
    ];

    const chatbots = computeOverallSentiment(rows, ["example"], ["gemini", "openai", "claude"]);
    const overviews = computeOverallSentiment(rows, ["example"], ["google_aio"]);

    expect(chatbots.mentionedCount).toBe(2);
    expect(chatbots.positiveCount).toBe(1);
    expect(chatbots.scorePercent).toBe(50);
    expect(overviews.mentionedCount).toBe(1);
    expect(overviews.positiveCount).toBe(1);
    expect(overviews.scorePercent).toBe(100);
  });

  it("deduplicates repeated response payloads before calculating the denominator", () => {
    const rows = [
      {
        runs: {
          gemini: [
            { response: "Example is excellent.", error: "" },
            { response: "Example is excellent.", error: "" },
          ],
          google_aio: [
            { response: "Example is poor.", error: "" },
            { response: "Example is poor.", error: "" },
          ],
        },
      },
    ];

    const chatbots = computeOverallSentiment(rows, ["example"], ["gemini", "openai", "claude"]);
    const overviews = computeOverallSentiment(rows, ["example"], ["google_aio"]);

    expect(chatbots.mentionedCount).toBe(1);
    expect(chatbots.positiveCount).toBe(1);
    expect(overviews.mentionedCount).toBe(1);
    expect(overviews.positiveCount).toBe(0);
    expect(overviews.scorePercent).toBe(0);
  });

  it("uses platform sentiment metrics when slim rows omit response bodies", () => {
    const rows = [{
      replies_omitted: true,
      list_metrics: {
        platform_metrics: {
          gemini: {
            brand_mention_count: 2,
            sentiment_votes: { positive: 2, negative: 0, neutral: 0 },
          },
          google_aio: {
            brand_mention_count: 1,
            sentiment_votes: { positive: 0, negative: 1, neutral: 0 },
          },
        },
      },
    }];

    const chatbots = computeOverallSentiment(rows, ["example"], ["gemini", "openai", "claude"]);
    const overviews = computeOverallSentiment(rows, ["example"], ["google_aio"]);

    expect(chatbots).toMatchObject({ mentionedCount: 2, positiveCount: 2, scorePercent: 100 });
    expect(overviews).toMatchObject({ mentionedCount: 1, negativeCount: 1, scorePercent: 0 });
  });
});
