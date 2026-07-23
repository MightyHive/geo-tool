/**
 * Align Competitor comparison criterion rows with OVERVIEW → Summary /
 * pillar Overview (same keys, labels, and finding-summary construction).
 */

import {
  groupContentQualityComponents,
  type ContentQualityScoreComponent,
} from "./contentQualityAreas";
import {
  groupTechnicalSetupComponents,
  type TechnicalSetupScoreComponent,
} from "./technicalSetupAreas";
import type { CompetitorPillarComponent } from "../types";

const AI_VISIBILITY_KEYS = new Set(["brand_visibility", "share_of_voice"]);
const TECHNICAL_OVERVIEW_KEYS = new Set([
  "crawler_access",
  "citability",
  "platform_readiness",
]);
const CONTENT_OVERVIEW_KEYS = new Set([
  "eeat",
  "structure_answerability",
  "schema_entity_markup",
  "brand_visibility_authority",
]);

function toScoreComponent(
  component: CompetitorPillarComponent,
): ContentQualityScoreComponent & TechnicalSetupScoreComponent {
  return {
    key: component.key,
    title: component.title,
    score: component.score,
    weight_pct: component.weight_pct,
    detail: component.detail ?? "",
    finding_summary: component.finding_summary,
    evidence_example: component.evidence_example ?? component.detail ?? "",
    strengths: component.strengths,
    improvements: component.improvements,
  };
}

function toCompetitorComponent(
  component: ContentQualityScoreComponent | TechnicalSetupScoreComponent,
): CompetitorPillarComponent {
  return {
    key: component.key,
    title: component.title,
    score: component.score,
    weight_pct: component.weight_pct,
    finding_summary: component.finding_summary,
    detail: component.detail,
    evidence_example: component.evidence_example,
    strengths: component.strengths,
    improvements: component.improvements,
  };
}

function alreadyOverviewShaped(
  components: CompetitorPillarComponent[],
  overviewKeys: Set<string>,
): boolean {
  if (!components.length) return false;
  return components.every((component) => overviewKeys.has(component.key));
}

/** Keep AI Visibility as Brand visibility + Share of voice performance. */
export function normalizeAiVisibilityCriteria(
  components: CompetitorPillarComponent[] | undefined,
): CompetitorPillarComponent[] {
  if (!components?.length) return [];
  if (alreadyOverviewShaped(components, AI_VISIBILITY_KEYS)) {
    return components.map((component) => ({
      ...component,
      title:
        component.key === "brand_visibility"
          ? "Brand visibility"
          : component.key === "share_of_voice"
            ? "Share of voice performance"
            : component.title,
    }));
  }
  const byKey = new Map(components.map((component) => [component.key, component]));
  const brand =
    byKey.get("brand_visibility")
    ?? byKey.get("brand_entity_visibility")
    ?? byKey.get("brand_visibility_authority");
  const sov = byKey.get("share_of_voice");
  const out: CompetitorPillarComponent[] = [];
  if (brand) {
    out.push({
      ...brand,
      key: "brand_visibility",
      title: "Brand visibility",
      weight_pct: brand.weight_pct || 60,
    });
  }
  if (sov) {
    out.push({
      ...sov,
      key: "share_of_voice",
      title: "Share of voice performance",
      weight_pct: sov.weight_pct || 40,
    });
  }
  return out.length ? out : components;
}

export function normalizeTechnicalSetupCriteria(
  components: CompetitorPillarComponent[] | undefined,
): CompetitorPillarComponent[] {
  if (!components?.length) return [];
  if (alreadyOverviewShaped(components, TECHNICAL_OVERVIEW_KEYS)) {
    return components.map((component) => ({
      ...component,
      title:
        component.key === "crawler_access"
          ? "Crawler access"
          : component.key === "citability"
            ? "Citability"
            : component.key === "platform_readiness"
              ? "Platform readiness"
              : component.title,
    }));
  }
  return groupTechnicalSetupComponents(components.map(toScoreComponent)).map(
    toCompetitorComponent,
  );
}

export function normalizeContentQualityCriteria(
  components: CompetitorPillarComponent[] | undefined,
): CompetitorPillarComponent[] {
  if (!components?.length) return [];
  if (alreadyOverviewShaped(components, CONTENT_OVERVIEW_KEYS)) {
    return components.map((component) => ({
      ...component,
      title:
        component.key === "eeat"
          ? "E-E-A-T Signals"
          : component.key === "structure_answerability"
            ? "Content Structure & Answerability"
            : component.key === "schema_entity_markup"
              ? "Schema & Entity Markup"
              : component.key === "brand_visibility_authority"
                ? "Brand Visibility & Authority"
                : component.title,
    }));
  }
  // Crawl payloads may expose schema under json_ld or brand authority under
  // brand_visibility / source_transparency_governance — remap before grouping.
  const remapped = components.map((component) => {
    if (component.key === "json_ld") {
      return {
        ...component,
        key: "schema_entity_markup",
        title: "Schema & Entity Markup",
      };
    }
    if (
      component.key === "brand_visibility"
      || component.key === "brand_entity_visibility"
      || component.key === "source_transparency_governance"
    ) {
      return {
        ...component,
        key: "brand_visibility_authority",
        title: "Brand Visibility & Authority",
      };
    }
    return component;
  });
  // Prefer the first remapped row when aliases collide on the same overview key.
  const deduped = remapped.filter((component, index) => {
    if (
      component.key === "schema_entity_markup"
      || component.key === "brand_visibility_authority"
    ) {
      return remapped.findIndex((row) => row.key === component.key) === index;
    }
    return true;
  });
  return groupContentQualityComponents(deduped.map(toScoreComponent)).map(
    toCompetitorComponent,
  );
}

export type ComparisonPillar = "ai_visibility" | "technical_setup" | "content_quality";

export function normalizeComparisonCriteria(
  pillar: ComparisonPillar,
  components: CompetitorPillarComponent[] | undefined,
): CompetitorPillarComponent[] {
  if (pillar === "ai_visibility") return normalizeAiVisibilityCriteria(components);
  if (pillar === "technical_setup") return normalizeTechnicalSetupCriteria(components);
  return normalizeContentQualityCriteria(components);
}
