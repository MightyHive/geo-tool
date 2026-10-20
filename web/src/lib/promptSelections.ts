import type { ProductServiceRow } from "../types";
import { isCustomPromptsCategory } from "./customPrompts";

export interface PromptSelection {
  product: string;
  prompt: string;
  included: boolean;
  tags: string[];
  isCustom: boolean;
  customTopic: boolean;
  /** Buyer persona this prompt was written for (persona prompts only). */
  persona?: string;
}

export type PromptGroupBy = "topic" | "persona";
export const NO_PERSONA_GROUP = "No persona (topic prompts)";

export function buildSelections(rows: ProductServiceRow[]): PromptSelection[] {
  const out: PromptSelection[] = [];
  for (const row of rows) {
    const product = row.product_or_service.trim();
    if (!product) continue;
    for (const p of row.prompts) {
      const prompt = String(p).trim();
      if (!prompt) continue;
      const persona = row.prompt_personas?.[prompt]?.trim();
      out.push({
        product,
        prompt,
        included: true,
        tags: row.prompt_tags?.[prompt] ?? [],
        isCustom: row.is_custom_topic === true
          || isCustomPromptsCategory(product)
          || (row.custom_prompts ?? []).includes(prompt),
        customTopic: row.is_custom_topic === true || isCustomPromptsCategory(product),
        ...(persona ? { persona } : {}),
      });
    }
  }
  return out;
}

export function selectionsToRows(selections: PromptSelection[]): ProductServiceRow[] {
  const byProduct = new Map<string, PromptSelection[]>();
  for (const s of selections) {
    if (!s.included) continue;
    const list = byProduct.get(s.product) ?? [];
    list.push(s);
    byProduct.set(s.product, list);
  }
  return Array.from(byProduct.entries()).map(([product_or_service, items]) => {
    const prompts = items.map((item) => item.prompt);
    return {
      product_or_service,
      prompts,
      prompt_tags: Object.fromEntries(
        items.filter((item) => item.tags.length).map((item) => [item.prompt, item.tags]),
      ),
      prompt_personas: Object.fromEntries(
        items.filter((item) => item.persona).map((item) => [item.prompt, item.persona as string]),
      ),
      custom_prompts: items.filter((item) => item.isCustom).map((item) => item.prompt),
      is_custom_topic: items.some((item) => item.customTopic),
    };
  });
}

export function selectionGroupKey(selection: PromptSelection, groupBy: PromptGroupBy): string {
  if (groupBy === "persona") return selection.persona || NO_PERSONA_GROUP;
  return selection.product;
}

/** Included prompts split into topic vs persona budgets (custom prompts are separate). */
export function countIncludedByBudget(selections: PromptSelection[]): {
  topic: number;
  persona: number;
  custom: number;
} {
  let topic = 0;
  let persona = 0;
  let custom = 0;
  for (const s of selections) {
    if (!s.included) continue;
    if (s.isCustom) custom++;
    else if (s.persona) persona++;
    else topic++;
  }
  return { topic, persona, custom };
}
