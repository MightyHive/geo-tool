/**
 * Unit tests for conversion event parsing (Vitest-compatible; also runnable via node assert).
 */
import { describe, expect, it } from "vitest";
import {
  buildConversionEventSpec,
  canContinueGa4Connect,
  eventNamesInput,
  formatConversionHeading,
  parseConversionEvents,
  serializeConversionEvents,
  seriesLabelFromEvents,
  tryParseConversionEvents,
  validateConversionSpec,
} from "./conversionEvents";

describe("conversionEvents", () => {
  it("keeps single purchase backward compatible", () => {
    const events = parseConversionEvents("purchase");
    expect(events).toEqual([{ event: "purchase" }]);
    expect(formatConversionHeading(events)).toBe("Purchases");
    expect(serializeConversionEvents(events)).toBe("purchase");
  });

  it("collapses legacy per-event labels into one series label", () => {
    const events = parseConversionEvents("purchase:Purchases, generate_lead=Leads");
    expect(events.map((e) => e.event)).toEqual(["purchase", "generate_lead"]);
    expect(seriesLabelFromEvents(events)).toBe("Purchases + Leads");
    expect(formatConversionHeading(events)).toBe("Purchases + Leads");
    expect(serializeConversionEvents(events)).toBe(
      "purchase:Purchases + Leads,generate_lead",
    );
    expect(eventNamesInput(events)).toBe("purchase,generate_lead");
  });

  it("builds spec from event ids + series label", () => {
    expect(buildConversionEventSpec("purchase,generate_lead", "All conversions")).toBe(
      "purchase:All conversions,generate_lead",
    );
    const events = parseConversionEvents(
      buildConversionEventSpec("generate_lead", "Sign-ups"),
    );
    expect(formatConversionHeading(events)).toBe("Sign-ups");
  });

  it("uses neutral heading for multi-event without label", () => {
    const events = parseConversionEvents("purchase,generate_lead");
    expect(formatConversionHeading(events)).toBe("Conversions");
  });

  it("uses event id for single non-purchase without label", () => {
    expect(formatConversionHeading(parseConversionEvents("generate_lead"))).toBe(
      "generate_lead",
    );
  });

  it("rejects invalid tokens", () => {
    expect(tryParseConversionEvents("bad event").ok).toBe(false);
  });

  it("accepts hyphenated GA4 event names", () => {
    const events = parseConversionEvents(
      "appointment-confirmation-replace,appointment-confirmation-repair",
    );
    expect(events.map((e) => e.event)).toEqual([
      "appointment-confirmation-replace",
      "appointment-confirmation-repair",
    ]);
    expect(
      buildConversionEventSpec(
        "appointment-confirmation-replace,appointment-confirmation-repair",
        "appointment_confirmation",
      ),
    ).toBe(
      "appointment-confirmation-replace:appointment_confirmation,appointment-confirmation-repair",
    );
  });

  it("validateConversionSpec allows purchase / single event with empty label", () => {
    expect(validateConversionSpec("purchase", "").ok).toBe(true);
    expect(validateConversionSpec("purchase", "   ").ok).toBe(true);
    expect(validateConversionSpec("", "").ok).toBe(true);
    expect(validateConversionSpec("generate_lead", "").ok).toBe(true);
    expect(validateConversionSpec("purchase:Purchases", "").ok).toBe(true);
  });

  it("validateConversionSpec requires series label for multi-event", () => {
    const missing = validateConversionSpec("purchase,generate_lead", "");
    expect(missing.ok).toBe(false);
    if (!missing.ok) {
      expect(missing.error).toMatch(/display name/i);
    }
    expect(validateConversionSpec("purchase,generate_lead", "   ").ok).toBe(false);
    expect(validateConversionSpec("purchase,generate_lead", "All conversions").ok).toBe(
      true,
    );
    expect(
      validateConversionSpec(
        "appointment-confirmation-replace,appointment-confirmation-repair",
        "appointment_confirmation",
      ).ok,
    ).toBe(true);
  });

  it("canContinueGa4Connect enables for default purchase when account+property set", () => {
    expect(
      canContinueGa4Connect({
        connected: true,
        accountId: "acc-1",
        propertyId: "prop-1",
        eventsInput: "purchase",
        seriesLabel: "",
      }).ok,
    ).toBe(true);
    expect(
      canContinueGa4Connect({
        connected: true,
        accountId: "acc-1",
        propertyId: "prop-1",
        eventsInput: "purchase,generate_lead",
        seriesLabel: "",
      }).ok,
    ).toBe(false);
    expect(
      canContinueGa4Connect({
        connected: true,
        accountId: "",
        propertyId: "prop-1",
        eventsInput: "purchase",
        seriesLabel: "",
      }).ok,
    ).toBe(false);
    expect(
      canContinueGa4Connect({
        connected: true,
        accountId: "acc-1",
        propertyId: "prop-1",
        eventsInput:
          "appointment-confirmation-replace,appointment-confirmation-repair",
        seriesLabel: "appointment_confirmation",
      }).ok,
    ).toBe(true);
  });
});
