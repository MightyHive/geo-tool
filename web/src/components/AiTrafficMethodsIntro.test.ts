import { describe, expect, it } from "vitest";
import { AI_TRAFFIC_METHODS_LEAD, AI_TRAFFIC_METHODS_TITLE } from "./AiTrafficMethodsIntro";

describe("AI traffic methods intro", () => {
  it("distinguishes counted chatbot visits from modelled SEO/Direct impact", () => {
    expect(AI_TRAFFIC_METHODS_TITLE).toMatch(/two ways/i);
    expect(AI_TRAFFIC_METHODS_LEAD.toLowerCase()).toContain("not be added together");
  });
});
