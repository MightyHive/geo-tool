export interface AuthUser {
  email: string;
  name: string;
}

export type AuthMode = "iap" | "oauth" | "none";

export interface AuthStatus {
  mode: AuthMode;
  enabled: boolean;
  logged_in: boolean;
  user: AuthUser | null;
  login_url: string | null;
  logout_available?: boolean;
  iap_enforce?: boolean;
}

export interface AppConfig {
  app_env: string;
  app_env_label: string;
  report_sections: { id: string; label: string }[];
  auth?: {
    enabled: boolean;
    redirect_uri?: string | null;
    web_public_origin?: string;
  };
}

export type AuditRunStepStatus = "pending" | "active" | "done";

export interface AuditRunProgressStep {
  id: string;
  label: string;
  status: AuditRunStepStatus;
}

export interface AuditRunProgressPayload {
  percent: number;
  detail: string;
  current_step: string;
  steps: AuditRunProgressStep[];
}

export interface AuditRunStatusResponse {
  status: "running" | "done" | "error";
  audit_dir?: string;
  percent?: number;
  detail?: string;
  current_step?: string;
  steps?: AuditRunProgressStep[];
  overall_score?: number;
  error?: string;
}

export interface LocalAudit {
  id: string;
  audit_dir: string;
  base_url: string;
  brand_name?: string;
  favicon_url?: string;
  modified_at: string;
  overall_score?: number;
}

export interface AuditSummary {
  base_url?: string;
  overall_score?: number;
  audit_label?: string;
  [key: string]: unknown;
}

export interface ReportMeta {
  base_url: string;
  brand_name: string;
  industry: string;
  favicon_url?: string;
  overall_score: number | null;
  overall_label: string;
  score_tone: "green" | "blue" | "yellow" | "red";
  generated_at: string;
}

export interface OnboardingContext {
  brand_name_used?: string;
  brand_website_used?: string;
  industry_used?: string;
  geo_market_country?: string;
  geo_market_country_code?: string;
  products_and_services?: string[];
  products_and_services_rows?: { product_or_service: string; prompts: string[] }[];
  competitors_detail?: { competitor_website: string; competitor_brand: string }[];
  accepted_competitors?: string[];
  ga4_property_id?: string;
  crawl_urls?: string[];
  additional_crawl_markets?: { country: string; country_code: string }[];
}

export interface AuditDetail {
  audit_dir: string;
  summary: AuditSummary;
  has_report_html: boolean;
  report_meta?: ReportMeta | null;
  onboarding_context?: OnboardingContext | null;
}

export interface ArchiveRun {
  id: string;
  primary_url: string;
  site_key: string;
  audit_dir: string;
  created_at: string;
  overall_score: number;
  brand_name?: string;
  competitors?: string[];
}

export interface ArchiveResponse {
  runs: ArchiveRun[];
  auth_required: boolean;
  auth_enabled: boolean;
  user?: AuthUser | null;
}

export interface DomainOption {
  label: string;
  url: string;
}

export interface Ga4Account {
  id: string;
  name: string;
}

export interface Ga4Property {
  id: string;
  name: string;
  account: string;
  account_id: string;
}

export interface Ga4Status {
  configured: boolean;
  connected: boolean;
  redirect_uri: string | null;
  accounts: Ga4Account[];
  properties: Ga4Property[];
  selected_account_id: string;
  selected_property_id: string;
  ai_channel_names: string;
  error: string | null;
}

export interface ProductServiceRow {
  product_or_service: string;
  prompts: string[];
}

export interface CrawlMarket {
  country: string;
  country_code: string;
}

export interface VerifiedSite {
  canonical_url: string;
  hostname: string;
  favicon_url: string;
  warning?: string | null;
  bot_wall?: boolean;
  provider?: string | null;
  browser_verified?: boolean;
}

export interface ProbeSiteProtection {
  canonical_url: string;
  bot_wall: boolean;
  provider?: string | null;
  status_code?: number | null;
}

export interface CompetitorDetail {
  competitor_brand: string;
  competitor_website: string;
  favicon_url: string;
  included: boolean;
}

export interface PromptPerformanceCompetitor {
  competitor_brand: string;
  competitor_website: string;
}

export interface PromptPerformancePssRow {
  product_or_service: string;
  prompts: string[];
}

export interface CitationItem {
  url: string;
  domain: string;
  /** Page or video title (populated by video enrichment or AIO grounding). */
  title?: string;
  /** Thumbnail image URL for YouTube / TikTok citations. */
  thumbnail_url?: string;
  /** View count for YouTube / TikTok citations. */
  views?: number;
  /** "youtube" | "tiktok" | "web" — set by video enrichment. */
  platform?: string;
  /** True when the URL is a Vertex AI grounding redirect that could not be resolved. */
  unresolved_redirect?: boolean;
}

export interface TopCitedSite {
  domain: string;
  count: number;
  platforms: string[];
  example_url?: string;
  title?: string;
  thumbnail_url?: string;
  views?: number;
  platform?: string;
  brand_mentioned?: boolean;
  competitor_mentioned?: boolean;
  competitor_names?: string[];
  unresolved_redirect?: boolean;
}

export interface TopCitedUrl {
  url: string;
  domain: string;
  title?: string;
  thumbnail_url?: string;
  views?: number;
  platform?: string;
  content_type?: string;
  channel_type?: string;
  frequency: number;
  probe_platforms: string[];
  brand_mentioned: boolean;
  competitor_mentioned: boolean;
  competitor_names: string[];
  unresolved_redirect?: boolean;
}

export interface AioPromptResult {
  index: number;
  prompt: string;
  response?: string;
  citations: CitationItem[];
  error?: string | null;
}

export interface AioProbeResult {
  per_prompt: AioPromptResult[];
  top_cited_sites: TopCitedSite[];
  available: boolean;
  error?: string | null;
}

export interface MentionScores {
  brand_signal?: number;
  brand_name_hits?: number;
  product_line_hits?: number;
  primary_host_bonus?: number;
  competitors_combined_hits?: number;
  competitor_detail?: Record<string, number>;
}

export interface LiveProbePerPrompt {
  index?: number;
  prompt?: string;
  gemini_response?: string;
  openai_response?: string;
  claude_response?: string;
  google_aio_response?: string;
  error_gemini?: string;
  error_openai?: string;
  error_claude?: string;
  error_google_aio?: string;
  gemini_brand_mention_pct?: number;
  gemini_competitor_mention_pct?: number;
  openai_brand_mention_pct?: number;
  openai_competitor_mention_pct?: number;
  claude_brand_mention_pct?: number;
  claude_competitor_mention_pct?: number;
  google_aio_brand_mention_pct?: number;
  google_aio_competitor_mention_pct?: number;
  mention_scores_gemini?: MentionScores;
  mention_scores_openai?: MentionScores;
  mention_scores_claude?: MentionScores;
  mention_scores_google_aio?: MentionScores;
  citations_gemini?: CitationItem[];
  citations_openai?: CitationItem[];
  citations_claude?: CitationItem[];
  citations_google_aio?: CitationItem[];
}

export interface LiveProbeAggregate {
  gemini?: { brand_share_pct?: number; competitor_share_pct?: number };
  openai?: { brand_share_pct?: number; competitor_share_pct?: number };
  claude?: { brand_share_pct?: number; competitor_share_pct?: number };
  google_aio?: { brand_share_pct?: number; competitor_share_pct?: number };
}

export interface LiveProbeResult {
  per_prompt?: LiveProbePerPrompt[];
  aggregate?: LiveProbeAggregate;
  disclaimer?: string;
  reply_detected_brands?: { brand_name?: string; website_url?: string }[];
  reply_detected_brand_names?: string[];
  reply_detected_brands_error?: string;
  brand_match_tokens?: string[];
  brand_detected_spellings?: string[];
  product_line_aliases?: string[];
  /** Platforms omitted after fatal API errors (quota, auth, billing). */
  excluded_platforms?: string[];
  /** Platforms included in probes and UI. */
  active_platforms?: string[];
  /** Most frequently cited domains across all prompts and platforms. */
  top_cited_sites?: TopCitedSite[];
  /** Most frequently cited individual URLs across all prompts and platforms. */
  top_cited_urls?: TopCitedUrl[];
}

export interface CategorySentiment {
  category: string;
  sentiment: string;
  summary: string;
}

export interface PromptSentimentAnalysis {
  overall_sentiment: string;
  overall_summary: string;
  by_category: CategorySentiment[];
}

export interface PromptSentimentResponse {
  available: boolean;
  sentiment: PromptSentimentAnalysis | null;
  error: string | null;
  cached?: boolean;
}

export interface SovHistoryPoint {
  date: string;
  datetime?: string;
  audit_dir?: string;
  is_current?: boolean;
  brand_share_pct: number;
  competitor_avg_sov_pct: number;
}

export interface PromptPerformanceContext {
  brand_name: string;
  brand_site_url: string;
  use_pss: boolean;
  pss_rows: PromptPerformancePssRow[];
  /** Rows actually included in live probes (custom prompts always included). */
  probed_pss_rows?: PromptPerformancePssRow[];
  flat_prompts: string[];
  prompt_count: number;
  /** Total prompts stored on file (may exceed probed count for non-custom lines). */
  stored_prompt_count?: number;
  competitors: PromptPerformanceCompetitor[];
  primary_market: { country: string; country_id: string };
  category_labels: string[];
  industry: string;
  live_probe: LiveProbeResult | null;
  live_probe_in_progress?: boolean;
  aio_probe?: AioProbeResult | null;
  aio_probe_in_progress?: boolean;
  highlight: {
    brand: string;
    competitor_urls: string[];
    competitor_brands: string[];
    brand_match_tokens?: string[];
    brand_detected_spellings?: string[];
    product_line_aliases?: string[];
  };
  sov_history?: SovHistoryPoint[];
  sov_history_by_product?: Record<string, SovHistoryPoint[]>;
}
