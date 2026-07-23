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
  report_sections: { id: string; label: string; group?: string }[];
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

export interface ProbeProgressSummary {
  status?: string;
  market_count?: number;
  locale_index?: number;
  locale_label?: string;
  completed_calls?: number;
  planned_calls?: number;
  locale_completed_calls?: number;
  locale_planned_calls?: number;
  prompt_index?: number;
  prompt_total?: number;
  elapsed_seconds?: number | null;
  /** Remaining wall-clock estimate (max locale remaining × ~7s). */
  eta_seconds?: number | null;
  /** Total wall-clock estimate at start (max locale planned × ~7s). */
  eta_total_seconds?: number | null;
  fanout?: boolean;
}

export interface AuditRunProgressPayload {
  percent: number;
  detail: string;
  current_step: string;
  steps: AuditRunProgressStep[];
  market_count?: number;
  probe_progress?: ProbeProgressSummary | null;
}

export interface AuditRunStatusResponse {
  status: "running" | "done" | "error";
  audit_dir?: string;
  percent?: number;
  detail?: string;
  current_step?: string;
  steps?: AuditRunProgressStep[];
  market_count?: number;
  probe_progress?: ProbeProgressSummary | null;
  overall_score?: number;
  error?: string;
  /** True while crawl, probes, CQ, and/or sentiment for this run are still active. */
  still_running?: boolean;
  pipeline_phase?: string;
  pipeline_components?: Record<string, boolean>;
}

export interface CompetitorCrawlArchive {
  archive_id: string;
  archived_at?: string;
  from_status?: {
    status?: string;
    finished_at?: string;
    started_at?: string;
    crawled?: string[];
    competitor_count?: number;
  };
}

export interface CompetitorCrawlStatusResponse {
  status: "idle" | "running" | "done" | "error";
  audit_dir?: string;
  job_type?: string;
  percent?: number;
  detail?: string;
  current_step?: string;
  started_at?: string;
  finished_at?: string;
  updated_at?: string;
  competitor_count?: number;
  crawled?: string[];
  skipped?: Array<{ url: string; reason: string }>;
  archived_previous_id?: string | null;
  error?: string;
  seen?: boolean;
  has_comparison?: boolean;
  archives?: CompetitorCrawlArchive[];
}

export interface CompetitorPillarComponent {
  key: string;
  title: string;
  score: number;
  weight_pct: number;
  finding_summary: string;
  detail?: string;
  evidence_example?: string;
  strengths?: string[];
  improvements?: string[];
  /** False when finding blurb was stripped as brand-misattributed or placeholder. */
  verified?: boolean;
}

export interface CompetitorPillarRationale {
  summary?: string;
  strengths?: string[];
  improvements?: string[];
  /** Criterion findings for this row (competitor, or brand on the primary row). */
  components?: CompetitorPillarComponent[];
  /** Primary-brand criterion findings shown inside competitor expand rows. */
  brand_components?: CompetitorPillarComponent[];
  /** prompt_visibility when matched to live probes; crawl_fallback otherwise. */
  source?: "prompt_visibility" | "crawl_fallback" | string;
  matched_entity_key?: string;
  crawl_components?: CompetitorPillarComponent[];
  /** True when at least one competitor component has a verified finding blurb. */
  has_verified_findings?: boolean;
}

export interface CompetitorComparisonRow {
  name: string;
  url: string;
  is_primary: boolean;
  overall: number;
  ai_visibility: number;
  technical_setup: number;
  content_quality: number;
  favicon_url?: string;
  ai_visibility_rationale?: CompetitorPillarRationale;
  technical_setup_rationale?: CompetitorPillarRationale;
  content_quality_rationale?: CompetitorPillarRationale;
}

export interface CompetitorComparisonResponse {
  rows: CompetitorComparisonRow[];
  has_comparison: boolean;
}

export interface LocalAudit {
  id: string;
  audit_dir: string;
  base_url: string;
  brand_name?: string;
  favicon_url?: string;
  modified_at: string;
  overall_score?: number;
  /** True until the full audit pipeline (crawl + probes + CQ + sentiment) is idle. */
  still_running?: boolean;
  pipeline_phase?: string;
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

export interface PromptLocaleConfig {
  country: string;
  country_code: string;
  language: string;
  language_name: string;
  key: string;
  label: string;
}

export interface OnboardingContext {
  brand_name_used?: string;
  brand_website_used?: string;
  industry_used?: string;
  geo_market_country?: string;
  geo_market_country_code?: string;
  /** Extra market+language pairs for prompt probes (primary market+en always implied). */
  prompt_locales?: PromptLocaleConfig[];
  products_and_services?: string[];
  products_and_services_rows?: ProductServiceRow[];
  competitors_detail?: { competitor_website: string; competitor_brand: string }[];
  accepted_competitors?: string[];
  ga4_property_id?: string;
  crawl_urls?: string[];
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
  conversion_event_name: string;
  conversion_events?: Array<{ event: string; label: string }>;
  error: string | null;
}

export interface Ga4TopPage {
  url: string;
  total_pageviews: number;
}

export interface Ga4TopPagesResponse {
  pages: Ga4TopPage[];
  metric: "screenPageViews";
  date_range: "last_90_days";
  limit: number;
}

export interface ProductServiceRow {
  product_or_service: string;
  prompts: string[];
  prompt_tags?: Record<string, string[]>;
  custom_prompts?: string[];
  is_custom_topic?: boolean;
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
  prompt_tags?: Record<string, string[]>;
  custom_prompts?: string[];
  is_custom_topic?: boolean;
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
  brand_cited?: boolean;
  competitor_cited?: boolean;
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

export interface SingleRunData {
  run_index: number;
  /** May be omitted on slim list payloads; see `has_response`. */
  response?: string;
  citations: CitationItem[];
  mention_scores: MentionScores;
  error?: string;
  /** True when a reply existed but the body was stripped from the slim payload. */
  has_response?: boolean;
}

export interface LiveProbePerPrompt {
  index?: number;
  run_index?: number;
  prompt?: string;
  /** Stable id for detail fetches when replies are omitted from list payloads. */
  prompt_id?: string;
  /** True when reply bodies were stripped from this list row. */
  replies_omitted?: boolean;
  has_response_gemini?: boolean;
  has_response_openai?: boolean;
  has_response_claude?: boolean;
  has_response_google_aio?: boolean;
  /** Precomputed list-view metrics (avoids needing reply bodies). */
  list_metrics?: {
    platforms_responded?: string[];
    visibility_pct?: number;
    sentiment?: "positive" | "negative" | "neutral" | string;
    sentiment_votes?: Record<string, number>;
    avg_position?: number | null;
    competitors_mentioned?: string[];
    citation_domains?: string[];
    response_count?: number;
    brand_mention_count?: number;
  };
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
  /** Per-run results keyed by platform. Present when num_runs > 1. */
  runs?: {
    gemini?: SingleRunData[];
    openai?: SingleRunData[];
    claude?: SingleRunData[];
    google_aio?: SingleRunData[];
  };
  _locale_key?: string;
}

export interface PlatformDailySummary {
  brand_visibility: number;
  avg_competitor_visibility: number;
  competitor_detail?: Record<string, number>;
  competitor_visibility?: Record<string, number>;
  response_count?: number;
  brand_mentioned_count?: number;
  positive_brand_mention_count?: number;
  sentiment_score?: number | null;
}

export interface ProbeHistoryEntry {
  date: string;
  created_at?: string;
  summary: {
    gemini?: PlatformDailySummary;
    openai?: PlatformDailySummary;
    google_aio?: PlatformDailySummary;
    claude?: PlatformDailySummary;
    top_cited_domains?: { domain: string; frequency: number }[];
  };
}

export interface ProbeHistoryResponse {
  entries: ProbeHistoryEntry[];
  total: number;
}

export interface ScoreHistoryCompetitorPoint {
  name: string;
  website?: string;
  overall?: number | null;
  ai_visibility?: number | null;
  technical_setup?: number | null;
  content_structure?: number | null;
}

export interface ScoreHistoryEntry {
  date: string;
  created_at?: string;
  source?: string;
  overall?: number | null;
  ai_visibility?: number | null;
  technical_setup?: number | null;
  content_structure?: number | null;
  competitors?: ScoreHistoryCompetitorPoint[];
}

export interface ScoreHistoryResponse {
  entries: ScoreHistoryEntry[];
  count?: number;
}

export interface CitationHistoryRow {
  date: string;
  domain: string;
  frequency: number;
}

export interface CitationHistoryResponse {
  rows: CitationHistoryRow[];
  dates: string[];
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
  /** Precomputed keyword sentiment when reply bodies are omitted. */
  keyword_sentiment?: {
    mentioned_count: number;
    positive_count: number;
    negative_count: number;
    score_percent: number | null;
    label: string;
  };
  replies_omitted?: boolean;
  prompt_count?: number;
  metrics_only?: boolean;
}

export interface CategorySentiment {
  category: string;
  sentiment: string;
  summary: string;
}

export interface PerPromptSentiment {
  prompt_id: string;
  sentiment: string;
  summary: string;
}

export interface PromptSentimentAnalysis {
  overall_sentiment: string;
  overall_summary: string;
  by_category: CategorySentiment[];
  /** Gemini qualitative label per probed prompt (prompt_id keyed). */
  by_prompt?: PerPromptSentiment[];
}

export interface PromptSentimentResponse {
  available: boolean;
  sentiment: PromptSentimentAnalysis | null;
  error: string | null;
  cached?: boolean;
  status?: string;
  job?: {
    audit_id?: string;
    status?: string;
    request_id?: string;
    execution?: string;
    error?: string | null;
  };
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
  prompt_locales?: PromptLocaleConfig[];
  default_locale_key?: string;
  locale_probes?: Record<
    string,
    {
      locale?: PromptLocaleConfig;
      live_probe?: LiveProbeResult | null;
      prompts_probed?: string[];
      source_prompts?: string[];
    }
  >;
  locale_spread?: Array<{
    key: string;
    label: string;
    country?: string;
    country_code?: string;
    language?: string;
    language_name?: string;
    brand_share_pct: number | null;
    prompt_count?: number;
  }>;
  category_labels: string[];
  industry: string;
  live_probe: LiveProbeResult | null;
  live_probe_in_progress?: boolean;
  /** Live probe fan-out / progress summary while a run is in flight. */
  probe_progress?: ProbeProgressSummary | null;
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
  /** Server-side overall visibility metrics (from persisted slim file). */
  overall_metrics?: {
    score?: number;
    visibility_pct?: number;
    sov_pct?: number;
    sov_performance_score?: number;
    sov_rank?: number | null;
    competitor_count?: number;
    top_competitor_sov_pct?: number;
    average_competitor_sov_pct?: number;
    visible_prompt_count?: number;
    prompt_count?: number;
    per_platform?: Record<string, {
      response_count?: number;
      visible_response_count?: number;
      visibility_pct?: number;
      brand_hits?: number;
      competitor_hits?: number;
      sov_pct?: number;
    }>;
  };
  metrics_from_cache?: boolean;
  replies_omitted?: boolean;
}

export interface RedditPost {
  url: string;
  domain: string;
  title?: string;
  reddit_title?: string;
  subreddit?: string;
  subreddit_name?: string;
  brand_sentiment?: "positive" | "negative" | "neutral" | "mixed";
  post_url?: string;
  /** @deprecated use platforms[] */
  platform?: string;
  /** @deprecated use topics_referencing[] */
  prompt?: string;
  platforms?: string[];
  topics_referencing?: { topic: string; prompts: string[] }[];
  citation_details?: {
    prompt: string;
    topic: string;
    platform: string;
    citation_text: string;
    citation_text_is_exact: boolean;
  }[];
  citation_count?: number;
  brand_mentioned?: boolean;
  thumbnail_url?: string;
  enriched?: boolean;
}

export interface YouTubeVideo {
  url: string;
  domain: string;
  video_id?: string;
  title?: string;
  yt_title?: string;
  channel_title?: string;
  published_at?: string;
  thumbnail_url?: string;
  view_count?: number;
  views?: number;
  like_count?: number;
  comment_count?: number;
  duration?: string;
  duration_seconds?: number;
  brand_sentiment?: "positive" | "negative" | "neutral" | "mixed";
  platforms?: string[];
  /** Platform from old single-citation format */
  platform?: string;
  prompt?: string;
  topics_referencing?: { topic: string; prompts: string[] }[];
  citation_details?: {
    prompt: string;
    topic: string;
    platform: string;
    citation_text: string;
    citation_text_is_exact: boolean;
  }[];
  citation_count?: number;
  citing_prompt_count?: number;
  citation_percentage?: number | null;
  brand_mentioned?: boolean;
  enriched?: boolean;
}
