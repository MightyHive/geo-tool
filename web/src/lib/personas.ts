import type {
  Persona,
  PersonaPromptSuggestion,
  ProductServiceRow,
  PromptPerformanceContext,
} from "../types";
import { accumulateBrandVisibilityHits } from "./brandVisibilityRows";
import { isCustomPromptsCategory } from "./customPrompts";
import { filterContextByPersona, listProbePersonas } from "./promptCategoryGrouping";
import { computeVisibilityMetrics } from "./visibilityMetrics";

/** Mirrors api/prompt_selection.py budgets. */
export const MAX_TOPIC_PROBE_PROMPTS = 25;
export const MAX_PERSONA_PROBE_PROMPTS = 25;
export const MAX_PERSONAS = 8;
export const PERSONA_TAG_PREFIX = "Persona: ";

export function personaTag(name: string): string {
  return `${PERSONA_TAG_PREFIX}${name.trim()}`;
}

export function isPersonaTag(tag: string): boolean {
  return tag.startsWith(PERSONA_TAG_PREFIX);
}

/** Trim names/descriptions, drop blanks and duplicate names (case-insensitive). */
export function cleanPersonas(personas: Persona[] | null | undefined): Persona[] {
  const seen = new Set<string>();
  const out: Persona[] = [];
  for (const p of personas ?? []) {
    const name = (p.name || "").trim();
    if (!name || seen.has(name.toLowerCase())) continue;
    seen.add(name.toLowerCase());
    out.push({ name, description: (p.description || "").trim() });
  }
  return out.slice(0, MAX_PERSONAS);
}

/** Remove every persona prompt (and its persona tag) so persona prompts can be regenerated. */
export function stripPersonaPrompts(rows: ProductServiceRow[]): ProductServiceRow[] {
  return rows.map((row) => {
    const personaPrompts = new Set(Object.keys(row.prompt_personas ?? {}));
    if (!personaPrompts.size) return row;
    const prompts = row.prompts.filter((p) => !personaPrompts.has(p));
    const promptTags = Object.fromEntries(
      Object.entries(row.prompt_tags ?? {}).filter(([p]) => !personaPrompts.has(p)),
    );
    return { ...row, prompts, prompt_tags: promptTags, prompt_personas: {} };
  });
}

/**
 * Merge Gemini persona prompts into the topic row each prompt was mapped to.
 * Records the persona in `prompt_personas` and adds a `Persona: <name>` tag.
 * Prompts mapped to an unknown topic land in the first non-custom row.
 */
export function mergePersonaPrompts(
  rows: ProductServiceRow[],
  suggestions: PersonaPromptSuggestion[],
): ProductServiceRow[] {
  const next = stripPersonaPrompts(rows).map((row) => ({
    ...row,
    prompts: [...row.prompts],
    prompt_tags: { ...(row.prompt_tags ?? {}) },
    prompt_personas: { ...(row.prompt_personas ?? {}) },
  }));
  const topicIndex = new Map<string, number>();
  next.forEach((row, i) => {
    const label = row.product_or_service.trim().toLowerCase();
    if (label && !isCustomPromptsCategory(label) && !row.is_custom_topic && !topicIndex.has(label)) {
      topicIndex.set(label, i);
    }
  });
  const fallbackIndex = topicIndex.size ? [...topicIndex.values()][0] : -1;
  if (fallbackIndex < 0) return next;

  const allPrompts = new Set(next.flatMap((row) => row.prompts.map((p) => p.toLowerCase())));
  for (const persona of suggestions) {
    const name = persona.name.trim();
    if (!name) continue;
    for (const item of persona.prompts) {
      const prompt = item.prompt.trim();
      if (!prompt || allPrompts.has(prompt.toLowerCase())) continue;
      const idx = topicIndex.get(item.product_or_service.trim().toLowerCase()) ?? fallbackIndex;
      const row = next[idx];
      row.prompts.push(prompt);
      row.prompt_personas[prompt] = name;
      row.prompt_tags[prompt] = [personaTag(name)];
      allPrompts.add(prompt.toLowerCase());
    }
  }
  return next;
}

export interface PersonaPerformance {
  name: string;
  description: string;
  promptCount: number;
  responseCount: number;
  visibilityPct: number;
  citationRatePct: number;
  citationCount: number;
  sovPct: number;
  topCompetitor: string | null;
}

/** Per-persona visibility, citation rate, SOV and most-mentioned competitor. */
export function computePersonaPerformance(
  ctx: PromptPerformanceContext | null | undefined,
): PersonaPerformance[] {
  if (!ctx) return [];
  const descriptions = new Map((ctx.personas ?? []).map((p) => [p.name, p.description] as const));
  const out: PersonaPerformance[] = [];
  for (const name of listProbePersonas(ctx)) {
    const filtered = filterContextByPersona(ctx, name);
    const metrics = filtered ? computeVisibilityMetrics(filtered) : null;
    if (!filtered || !metrics) continue;
    const competitors = accumulateBrandVisibilityHits(filtered)
      .filter((row) => !row.isOwnBrand)
      .map((row) => ({
        name: row.name,
        hits: Object.values(row.platformHits).reduce((sum, v) => sum + v, 0),
      }))
      .filter((row) => row.hits > 0)
      .sort((a, b) => b.hits - a.hits || a.name.localeCompare(b.name));
    out.push({
      name,
      description: descriptions.get(name) ?? "",
      promptCount: filtered.live_probe?.per_prompt?.length ?? 0,
      responseCount: metrics.promptCount,
      visibilityPct: metrics.visibilityPct,
      citationRatePct: metrics.citationRatePct,
      citationCount: metrics.citationCount,
      sovPct: metrics.sovPct,
      topCompetitor: competitors[0]?.name ?? null,
    });
  }
  return out;
}
