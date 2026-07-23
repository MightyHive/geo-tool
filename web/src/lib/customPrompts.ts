import type { ProductServiceRow } from "../types";

/** Reserved product/service label for user-submitted prompts in the wizard and dashboard. */
export const CUSTOM_PROMPTS_LABEL = "Custom prompts";

export function isCustomPromptsCategory(label: string): boolean {
  return label.trim().toLowerCase() === CUSTOM_PROMPTS_LABEL.toLowerCase();
}

export function mergeCustomPromptsRow(
  rows: ProductServiceRow[],
  prompts: string[],
): ProductServiceRow[] {
  const rest = rows.filter((r) => !isCustomPromptsCategory(r.product_or_service));
  const cleaned = prompts.map((p) => p.trim()).filter(Boolean);
  if (!cleaned.length) return rest;
  return [...rest, { product_or_service: CUSTOM_PROMPTS_LABEL, prompts: cleaned }];
}

export function withoutCustomPromptsRow(rows: ProductServiceRow[]): ProductServiceRow[] {
  return rows.filter((r) => !isCustomPromptsCategory(r.product_or_service));
}

/** Drop Custom prompts from sentiment cards when none were probed. */
export function filterSentimentCategories<T extends { category: string }>(
  categories: T[],
  probedRows: ProductServiceRow[] | undefined,
): T[] {
  const rows = probedRows ?? [];
  const hasCustom = rows.some(
    (r) => isCustomPromptsCategory(r.product_or_service) && (r.prompts?.length ?? 0) > 0,
  );
  if (hasCustom) return categories;
  return categories.filter((c) => !isCustomPromptsCategory(c.category));
}

export interface CustomPromptCsvRow {
  prompt: string;
  category: string;
  tags: string[];
}

function splitCsvLine(line: string): string[] {
  const cells: string[] = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        current += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }
    if (ch === "," && !inQuotes) {
      cells.push(current);
      current = "";
      continue;
    }
    current += ch;
  }
  cells.push(current);
  return cells.map((cell) => cell.trim());
}

function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replace(/^\ufeff/, "");
}

/**
 * Parse a CSV with required headers Prompt, Category, Tags.
 * Tags must be comma-separated within the Tags cell (quote the cell if needed).
 */
export function parseCustomPromptsCsv(text: string): {
  rows: CustomPromptCsvRow[];
  error?: string;
} {
  const raw = (text || "").replace(/^\ufeff/, "").trim();
  if (!raw) return { rows: [], error: "CSV file is empty." };

  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
  if (lines.length < 2) {
    return { rows: [], error: "CSV needs a header row and at least one data row." };
  }

  const headers = splitCsvLine(lines[0]).map(normalizeHeader);
  const promptIdx = headers.indexOf("prompt");
  const categoryIdx = headers.indexOf("category");
  const tagsIdx = headers.indexOf("tags");
  if (promptIdx < 0 || categoryIdx < 0 || tagsIdx < 0) {
    return {
      rows: [],
      error: "CSV must include columns: Prompt, Category, Tags.",
    };
  }

  const rows: CustomPromptCsvRow[] = [];
  for (let i = 1; i < lines.length; i++) {
    const cells = splitCsvLine(lines[i]);
    const prompt = (cells[promptIdx] || "").trim();
    if (!prompt) continue;
    const category = (cells[categoryIdx] || "").trim() || CUSTOM_PROMPTS_LABEL;
    const tagsRaw = (cells[tagsIdx] || "").trim();
    const tags = tagsRaw
      ? tagsRaw.split(",").map((tag) => tag.trim()).filter(Boolean)
      : [];
    rows.push({ prompt, category, tags });
  }

  if (!rows.length) {
    return { rows: [], error: "No valid prompt rows found in the CSV." };
  }
  return { rows };
}
