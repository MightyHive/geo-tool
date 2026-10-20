/**
 * Topics — AI visibility metrics compared across product/service topics.
 */

import { Layers3 } from "lucide-react";
import { filterContextByTopic, listProbeTopics } from "../lib/promptCategoryGrouping";
import { topicCitationVisibilityPoints } from "./CitationVisibilityScatter";
import { DimensionVisibilitySection, type DimensionConfig } from "./DimensionVisibilitySection";

const TOPICS_CONFIG: DimensionConfig = {
  title: "Topics",
  description:
    "Compare AI visibility and share of voice across product and service topics from your prompts.",
  dimensionLabel: "Topic",
  scatterSubtitle: "Your brand by product or service topic",
  emptyIcon: Layers3,
  emptyMessage: "No topic-tagged probe data yet. Run prompts to compare visibility by topic.",
  list: listProbeTopics,
  filter: filterContextByTopic,
  points: topicCitationVisibilityPoints,
};

export function TopicsVisibilitySection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  return <DimensionVisibilitySection auditDirOrSlug={auditDirOrSlug} config={TOPICS_CONFIG} />;
}
