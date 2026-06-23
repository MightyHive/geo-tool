import type { CompetitorDetail, ProductServiceRow, VerifiedSite } from "../types";

const STORAGE_KEY = "geo_audit_wizard_draft";
const AUDIT_RUN_KEY = "geo_audit_run_active";
const FRESH_INTENT_KEY = "geo_audit_wizard_fresh_intent";

export type SitePreviewPhase = "form" | "loading" | "confirmed";

export interface WizardDraft {
  brandName: string;
  brandWebsite: string;
  industry: string;
  marketCountry: string;
  marketCountryCode: string;
  previewUnlocked: boolean;
  sitePreviewPhase: SitePreviewPhase;
  verifiedSite: VerifiedSite | null;
  productRows: ProductServiceRow[];
  selectedProducts: string[];
  competitorDetails: CompetitorDetail[];
  ga4PropertyId?: string;
  ga4AiChannels?: string;
  wizardStep?: number;
  promptsReady?: boolean;
}

export interface AuditRunDraft {
  active: boolean;
  auditDir: string;
  brandName?: string;
  brandWebsite?: string;
}

export function loadWizardDraft(): Partial<WizardDraft> | null {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as Partial<WizardDraft>;
  } catch {
    return null;
  }
}

export function saveWizardDraft(draft: WizardDraft): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(draft));
  } catch {
    /* quota / private mode */
  }
}

/** Call before navigating to New audit so the wizard resets even without ?fresh=1 in the URL. */
export function markWizardFreshIntent(): void {
  try {
    sessionStorage.setItem(FRESH_INTENT_KEY, "1");
  } catch {
    /* quota / private mode */
  }
}

export function consumeWizardFreshIntent(): boolean {
  try {
    const v = sessionStorage.getItem(FRESH_INTENT_KEY);
    if (v !== "1") return false;
    sessionStorage.removeItem(FRESH_INTENT_KEY);
    return true;
  } catch {
    return false;
  }
}

/** Clear wizard fields when explicitly starting a fresh setup (?fresh=1). */
export function clearWizardDraft(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* quota / private mode */
  }
}

export function loadAuditRunDraft(): AuditRunDraft | null {
  try {
    const raw = sessionStorage.getItem(AUDIT_RUN_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw) as AuditRunDraft;
    return data?.active && data.auditDir ? data : null;
  } catch {
    return null;
  }
}

export function saveAuditRunDraft(draft: AuditRunDraft): void {
  try {
    sessionStorage.setItem(AUDIT_RUN_KEY, JSON.stringify(draft));
  } catch {
    /* quota / private mode */
  }
}

export function clearAuditRunDraft(): void {
  try {
    sessionStorage.removeItem(AUDIT_RUN_KEY);
  } catch {
    /* quota / private mode */
  }
}
