import { describe, expect, it } from "vitest";

import {
  PAGE_PROMPT_LOCALE_KEY,
  pageAuditPromptContext,
  pageLiveProbe,
} from "./pageAuditPromptContext";
import type { PageAuditDetail } from "../types";

function pageAudit(overrides: Partial<PageAuditDetail> = {}): PageAuditDetail {
  return {
    id: "page-1",
    url: "https://example.com/guides/brakes",
    status: "done",
    title: "Brake guide",
    parent_brand_name: "Example",
    parent_base_url: "https://www.example.com",
    parent_competitors: [
      { competitor_brand: "Rival", competitor_website: "https://rival.com" },
    ],
    page_prompts: { prompts: ["How do I change brake pads?"] },
    ...overrides,
  };
}

describe("pageAuditPromptContext", () => {
  it("maps live probe rows, topic label, and brand tokens", () => {
    const audit = pageAudit({
      page_probe: {
        live_probe: {
          per_prompt: [{ prompt: "How do I change brake pads?", gemini_response: "Use quality pads." }],
          brand_match_tokens: ["example"],
        },
      },
    });
    const { ctx, live, topicLabel } = pageAuditPromptContext(audit);
    expect(topicLabel).toBe("Brake guide");
    expect(PAGE_PROMPT_LOCALE_KEY).toBe("page");
    expect(live?.per_prompt).toHaveLength(1);
    expect(ctx.use_pss).toBe(false);
    expect(ctx.category_labels).toEqual(["Brake guide"]);
    expect(ctx.brand_name).toBe("Example");
    expect(ctx.highlight.brand_match_tokens).toEqual(["example"]);
    expect(ctx.competitors).toEqual([
      { competitor_brand: "Rival", competitor_website: "https://rival.com" },
    ]);
  });

  it("returns no live probe when the page has not been probed", () => {
    const { live } = pageAuditPromptContext(pageAudit({ page_probe: null }));
    expect(live).toBeNull();
    expect(pageLiveProbe(pageAudit({ page_probe: { prompt_metrics: { score: 10 } } }))).toBeNull();
  });

  it("falls back to This page when the audit has no title", () => {
    const { topicLabel, ctx } = pageAuditPromptContext(
      pageAudit({
        title: "",
        page_probe: { per_prompt: [{ prompt: "hello" }] },
      }),
    );
    expect(topicLabel).toBe("This page");
    expect(ctx.category_labels).toEqual(["This page"]);
    expect(ctx.live_probe?.per_prompt).toHaveLength(1);
  });
});
