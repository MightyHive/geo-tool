import { describe, expect, it } from "vitest";
import {
  canRegenerateSections,
  shouldHydrateOutline,
} from "./ContentOutlineGeneratorSection";

describe("content outline editor state", () => {
  it("preserves dirty edits when polling refreshes the selected topic", () => {
    expect(shouldHydrateOutline({
      hydratedTopic: "Low visibility",
      hydratedVersion: "v1",
      nextTopic: "Low visibility",
      nextVersion: "v2",
      dirty: true,
    })).toBe(false);
  });

  it("hydrates a different selected topic even after editing", () => {
    expect(shouldHydrateOutline({
      hydratedTopic: "First",
      hydratedVersion: "v1",
      nextTopic: "Second",
      nextVersion: "v1",
      dirty: true,
    })).toBe(true);
  });

  it("requires at least one non-empty section heading", () => {
    expect(canRegenerateSections([])).toBe(false);
    expect(canRegenerateSections([{ heading: "Overview" }, { heading: "  " }])).toBe(false);
    expect(canRegenerateSections([{ heading: "Overview" }, { heading: "Evidence" }])).toBe(true);
  });
});
