/**
 * Parse GA4 conversion event specs.
 *
 * Primary UX / storage model:
 *   Field 1 — comma-separated GA4 event ids
 *   Field 2 — optional display label for the (single or summed) conversions series
 *
 * Stored as one string. Series label is encoded on the first event only:
 *   purchase
 *   generate_lead:Leads
 *   purchase:Purchases + leads,generate_lead
 *
 * Legacy per-event labels (event:Label / event=Label) are still parsed: event
 * ids are kept and distinct labels collapse into one series label.
 */

export type ConversionEvent = {
  event: string;
  label?: string;
};

const EVENT_RE = /^[A-Za-z][A-Za-z0-9_-]*$/;
const MAX_EVENTS = 10;
const MAX_EVENT_LEN = 40;
const MAX_LABEL_LEN = 60;
const MAX_SPEC_LEN = 500;

export class ConversionEventParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConversionEventParseError";
  }
}

function validateEventName(eventName: string): string {
  const name = eventName.trim();
  if (!name || name.length > MAX_EVENT_LEN || !EVENT_RE.test(name)) {
    throw new ConversionEventParseError(
      "Conversion event must start with a letter and use letters, numbers, underscores, or hyphens",
    );
  }
  return name;
}

function validateSeriesLabel(label: string | null | undefined): string | undefined {
  const text = (label || "").trim();
  if (!text) return undefined;
  if (text.includes(",")) {
    throw new ConversionEventParseError("Conversion label must not contain commas");
  }
  if (text.length > MAX_LABEL_LEN) {
    throw new ConversionEventParseError(
      `Conversion label must be at most ${MAX_LABEL_LEN} characters`,
    );
  }
  return text;
}

export function displayLabel(item: ConversionEvent): string {
  const text = (item.label || "").trim();
  return text || item.event;
}

export function seriesLabelFromEvents(events: ConversionEvent[]): string | undefined {
  const labels: string[] = [];
  const seen = new Set<string>();
  for (const item of events) {
    const text = (item.label || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    labels.push(text);
  }
  if (!labels.length) return undefined;
  if (labels.length === 1) return labels[0];
  return labels.join(" + ");
}

export function applySeriesLabel(
  events: ConversionEvent[],
  label: string | null | undefined,
): ConversionEvent[] {
  const series = validateSeriesLabel(label);
  return events.map((item) =>
    series ? { event: item.event, label: series } : { event: item.event },
  );
}

export function parseConversionEvents(
  raw: string | null | undefined,
  defaultEvent = "purchase",
): ConversionEvent[] {
  let text = (raw || "").trim();
  if (!text) text = defaultEvent;
  if (text.length > MAX_SPEC_LEN) {
    throw new ConversionEventParseError(
      `Conversion event list must be at most ${MAX_SPEC_LEN} characters`,
    );
  }

  const rawEvents: ConversionEvent[] = [];
  const seen = new Set<string>();
  for (const part of text.split(",")) {
    const token = part.trim();
    if (!token) continue;
    let eventPart: string;
    let labelPart = "";
    if (token.includes(":")) {
      const idx = token.indexOf(":");
      eventPart = token.slice(0, idx);
      labelPart = token.slice(idx + 1);
    } else if (token.includes("=")) {
      const idx = token.indexOf("=");
      eventPart = token.slice(0, idx);
      labelPart = token.slice(idx + 1);
    } else {
      eventPart = token;
    }
    const eventName = validateEventName(eventPart);
    const label = labelPart.trim() || undefined;
    if (label && label.length > MAX_LABEL_LEN) {
      throw new ConversionEventParseError(
        `Conversion label must be at most ${MAX_LABEL_LEN} characters`,
      );
    }
    if (seen.has(eventName)) continue;
    seen.add(eventName);
    rawEvents.push(label ? { event: eventName, label } : { event: eventName });
  }

  if (!rawEvents.length) rawEvents.push({ event: defaultEvent });
  if (rawEvents.length > MAX_EVENTS) {
    throw new ConversionEventParseError(
      `At most ${MAX_EVENTS} conversion events are allowed`,
    );
  }

  return applySeriesLabel(rawEvents, seriesLabelFromEvents(rawEvents));
}

export function serializeConversionEvents(events: ConversionEvent[]): string {
  if (!events.length) return "purchase";
  const series = seriesLabelFromEvents(events);
  const names = events.map((item) => item.event);
  if (series) return [names[0] + ":" + series, ...names.slice(1)].join(",");
  return names.join(",");
}

export function eventNamesInput(events: ConversionEvent[]): string {
  return events.length ? events.map((e) => e.event).join(",") : "purchase";
}

export function isOnlyDefaultPurchase(events: ConversionEvent[]): boolean {
  return events.length === 1 && events[0].event === "purchase";
}

export function formatConversionHeading(events: ConversionEvent[]): string {
  const series = seriesLabelFromEvents(events);
  if (series) return series;
  if (isOnlyDefaultPurchase(events)) return "Purchases";
  if (events.length === 1) return events[0].event;
  // Neutral fallback when multi-event has no label (UI should prompt to fill).
  return "Conversions";
}

export function formatConversionMethodNote(events: ConversionEvent[]): string {
  const series = seriesLabelFromEvents(events);
  if (isOnlyDefaultPurchase(events) && !series) return "";
  if (series) return ` (conversions = ${series})`;
  if (events.length === 1) return ` (conversions = ${events[0].event})`;
  return ` (conversions = ${events.map((e) => e.event).join(", ")})`;
}

export function buildConversionEventSpec(
  eventNamesRaw: string,
  seriesLabel = "",
): string {
  const parsed = parseConversionEvents(eventNamesRaw);
  const namesOnly = parsed.map((item) => ({ event: item.event }));
  const explicit = seriesLabel.trim();
  const label = explicit || seriesLabelFromEvents(parsed);
  return serializeConversionEvents(applySeriesLabel(namesOnly, label));
}

export function tryParseConversionEvents(
  raw: string,
): { ok: true; events: ConversionEvent[] } | { ok: false; error: string } {
  try {
    return { ok: true, events: parseConversionEvents(raw) };
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Invalid conversion events",
    };
  }
}

export function isValidConversionEventSpec(raw: string): boolean {
  return tryParseConversionEvents(raw).ok;
}

/** True when multi-event sum needs an explicit series label. */
export function requiresSeriesLabel(events: ConversionEvent[]): boolean {
  return events.length > 1;
}

export type ConversionSpecValidation =
  | { ok: true; events: ConversionEvent[] }
  | { ok: false; error: string; events: ConversionEvent[] };

/**
 * Wizard / dashboard gate for conversion Field 1 + Field 2.
 *
 * - Bare `purchase` or any single event with an empty label → ok
 * - Multiple events without a non-whitespace series label → not ok
 * - Invalid event tokens → not ok
 */
export function validateConversionSpec(
  eventsInput: string,
  seriesLabel = "",
): ConversionSpecValidation {
  const raw = eventsInput.trim() || "purchase";
  const parsed = tryParseConversionEvents(raw);
  if (!parsed.ok) {
    return { ok: false, error: parsed.error, events: [] };
  }
  if (requiresSeriesLabel(parsed.events) && !seriesLabel.trim()) {
    return {
      ok: false,
      error: "Enter a display name for the summed conversion events.",
      events: parsed.events,
    };
  }
  try {
    // Reject labels that cannot be stored (e.g. commas) before enabling Continue.
    buildConversionEventSpec(raw, seriesLabel);
  } catch (e) {
    return {
      ok: false,
      error: e instanceof Error ? e.message : "Invalid conversion events",
      events: parsed.events,
    };
  }
  return { ok: true, events: parsed.events };
}

/** Whether the GA4 connect-step Continue control should be enabled. */
export function canContinueGa4Connect(opts: {
  connected: boolean;
  saving?: boolean;
  accountId: string;
  propertyId: string;
  eventsInput: string;
  seriesLabel?: string;
}): { ok: true } | { ok: false; error: string } {
  if (!opts.connected) {
    return { ok: false, error: "Sign in with Google for Analytics first." };
  }
  if (opts.saving) {
    return { ok: false, error: "Saving GA4 selection…" };
  }
  if (!opts.accountId.trim()) {
    return { ok: false, error: "Select a GA4 account." };
  }
  if (!opts.propertyId.trim()) {
    return { ok: false, error: "Select a GA4 property." };
  }
  const conversion = validateConversionSpec(
    opts.eventsInput,
    opts.seriesLabel ?? "",
  );
  if (!conversion.ok) {
    return { ok: false, error: conversion.error };
  }
  return { ok: true };
}
