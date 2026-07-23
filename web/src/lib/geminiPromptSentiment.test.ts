import { describe, expect, it } from "vitest";
import {
  buildGeminiPromptSentimentMap,
  dominantGeminiSentiment,
  geminiSentimentForPrompt,
  normalizeGeminiSentimentLabel,
} from "./geminiPromptSentiment";

describe("geminiPromptSentiment", () => {
  it("normalizes Gemini labels", () => {
    expect(normalizeGeminiSentimentLabel("positive")).toBe("Positive");
    expect(normalizeGeminiSentimentLabel("Mixed")).toBe("Mixed");
    expect(normalizeGeminiSentimentLabel("nope")).toBeNull();
  });

  it("looks up by prompt_id", () => {
    const map = buildGeminiPromptSentimentMap({
      overall_sentiment: "Positive",
      overall_summary: "ok",
      by_category: [],
      by_prompt: [
        { prompt_id: "0:best-cream", sentiment: "Positive", summary: "Recommended" },
        { prompt_id: "1:serum", sentiment: "Neutral", summary: "Not mentioned" },
      ],
    });
    expect(geminiSentimentForPrompt({ prompt_id: "0:best-cream" }, map)).toBe("Positive");
    expect(geminiSentimentForPrompt({ prompt_id: "missing" }, map)).toBeNull();
  });

  it("picks a dominant label", () => {
    expect(dominantGeminiSentiment(["Positive", "Positive", "Neutral"])).toBe("Positive");
    expect(dominantGeminiSentiment([null, undefined])).toBeNull();
  });
});
