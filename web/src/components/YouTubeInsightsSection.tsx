/**
 * YouTube Insights — rich table view of YouTube videos cited by AI platforms.
 * Enriched via YouTube Data API v3 (videos.list with statistics + contentDetails + snippet).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ExternalLink,
  Eye,
  ThumbsUp,
  MessageSquare,
  AlertCircle,
  Info,
  ChevronDown,
  ChevronRight,
  X,
} from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchYouTubeInsights } from "../api/client";
import type { YouTubeVideo } from "../types";
import { PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { ViewportOverlay } from "./ViewportOverlay";

// ── Helpers ────────────────────────────────────────────────────────────────

function fmt(n: number | undefined): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return n.toLocaleString();
}

function fmtDate(iso: string | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString("en-GB", {
      day: "numeric", month: "short", year: "numeric",
    });
  } catch {
    return "—";
  }
}


// ── Topic accordion cell ────────────────────────────────────────────────────

function TopicCell({ topics }: { topics: { topic: string; prompts: string[] }[] }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  if (!topics?.length) return <span className="text-xs text-gray-300">—</span>;

  const toggle = (t: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(t) ? next.delete(t) : next.add(t);
      return next;
    });

  return (
    <div className="space-y-1 max-w-[220px]">
      {topics.map(({ topic, prompts }) => (
        <div key={topic}>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              toggle(topic);
            }}
            className="flex items-center gap-1 text-[11px] font-semibold text-gray-700 hover:text-blue-600 text-left"
          >
            {expanded.has(topic) ? (
              <ChevronDown className="w-3 h-3 shrink-0" />
            ) : (
              <ChevronRight className="w-3 h-3 shrink-0" />
            )}
            <span className="truncate max-w-[180px]">{topic}</span>
            <span className="text-[10px] text-gray-400 shrink-0">({prompts.length})</span>
          </button>
          {expanded.has(topic) && (
            <ul className="ml-4 mt-0.5 space-y-0.5">
              {prompts.map((p, i) => (
                <li key={i} className="text-[10px] text-gray-500 leading-snug line-clamp-2">
                  {p}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Platform chips ──────────────────────────────────────────────────────────

function PlatformChips({ platforms }: { platforms: string[] }) {
  if (!platforms?.length) return <span className="text-xs text-gray-300">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {platforms.map((p) => (
        <span
          key={p}
          className="inline-flex items-center gap-1 text-[10px] font-medium px-1.5 py-0.5 rounded bg-gray-50 border border-gray-100 text-gray-500"
          title={PLATFORM_META[p as keyof typeof PLATFORM_META]?.label ?? p}
        >
          <PlatformLogo platform={p} size={12} />
          <span>{PLATFORM_META[p as keyof typeof PLATFORM_META]?.label ?? p}</span>
        </span>
      ))}
    </div>
  );
}

// ── Thumbnail cell ──────────────────────────────────────────────────────────

function ThumbnailCell({ video }: { video: YouTubeVideo }) {
  const thumbnail = video.thumbnail_url;
  const duration = video.duration;
  return (
    <a
      href={video.url}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(event) => event.stopPropagation()}
      className="relative block w-24 aspect-video rounded-lg overflow-hidden bg-gray-100 shrink-0 group"
    >
      {thumbnail ? (
        <img
          src={thumbnail}
          alt=""
          className="w-full h-full object-cover group-hover:brightness-90 transition"
          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center">
          <img src="/logos/youtube.png" alt="YouTube" className="w-6 h-6 object-contain opacity-50" />
        </div>
      )}
      {/* Play button overlay */}
      <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition">
        <div className="w-7 h-7 rounded-full bg-red-600 flex items-center justify-center shadow">
          <svg className="w-3.5 h-3.5 text-white ml-0.5" viewBox="0 0 24 24" fill="currentColor">
            <path d="M8 5v14l11-7z" />
          </svg>
        </div>
      </div>
      {/* Duration badge */}
      {duration && (
        <span className="absolute bottom-1 right-1 bg-black/75 text-white text-[9px] font-bold px-1 rounded">
          {duration}
        </span>
      )}
    </a>
  );
}

// ── Video detail overlay ────────────────────────────────────────────────────

function VideoDetailOverlay({ video, onClose }: { video: YouTubeVideo; onClose: () => void }) {
  const title = video.yt_title || video.title || video.url;
  const details = video.citation_details ?? [];

  return (
    <ViewportOverlay onClose={onClose} labelledBy="youtube-video-detail-title">
      <div className="bg-white rounded-2xl w-full max-w-3xl max-h-[min(88vh,900px)] flex flex-col shadow-2xl overflow-hidden">
        <div className="flex items-start gap-3 px-6 py-4 border-b border-gray-100 bg-gray-50">
          {video.thumbnail_url ? (
            <img
              src={video.thumbnail_url}
              alt=""
              className="w-16 aspect-video rounded-md object-cover shrink-0 bg-gray-100"
            />
          ) : (
            <img src="/logos/youtube.png" alt="" className="w-6 h-6 object-contain shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <h3 id="youtube-video-detail-title" className="text-sm font-bold text-[#0d0d0d] leading-snug">
              {title}
            </h3>
            {video.channel_title ? (
              <p className="mt-0.5 text-[11px] text-gray-500">{video.channel_title}</p>
            ) : null}
            <a
              href={video.url}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 text-[11px] text-blue-600 hover:underline truncate block"
            >
              Watch on YouTube
              <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-middle" />
            </a>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close video details"
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-3">
            Prompts that cited this video ({details.length})
          </p>
          {details.length > 0 ? (
            <div className="space-y-3">
              {details.map((detail, index) => {
                const platformLabel = PLATFORM_META[detail.platform as keyof typeof PLATFORM_META]?.label
                  ?? detail.platform;
                return (
                  <section key={`${detail.prompt}-${detail.platform}-${index}`} className="rounded-xl border border-gray-200 overflow-hidden">
                    <div className="px-4 py-3 bg-gray-50 border-b border-gray-100">
                      <div className="flex items-center gap-2 mb-1.5">
                        {detail.topic && (
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-stone-100 text-stone-500">
                            {detail.topic}
                          </span>
                        )}
                        <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-gray-500">
                          <PlatformLogo platform={detail.platform} size={12} />
                          {platformLabel}
                        </span>
                      </div>
                      <p className="text-sm font-semibold text-[#0d0d0d] leading-snug">{detail.prompt}</p>
                    </div>
                    <div className="px-4 py-3">
                      <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1.5">
                        {detail.citation_text_is_exact ? "Text containing the citation" : "Response associated with the citation"}
                      </p>
                      {detail.citation_text ? (
                        <p className="text-xs text-gray-600 leading-relaxed whitespace-pre-wrap">{detail.citation_text}</p>
                      ) : (
                        <p className="text-xs text-gray-400 italic">No response text was stored for this citation.</p>
                      )}
                      {!detail.citation_text_is_exact && detail.citation_text && (
                        <p className="text-[10px] text-gray-400 mt-2">
                          This platform supplied the video as structured grounding data without an inline text position.
                        </p>
                      )}
                    </div>
                  </section>
                );
              })}
            </div>
          ) : (
            <p className="text-xs text-gray-400 italic">
              Prompt-level detail is not available for citations sourced only from aggregate data.
            </p>
          )}
        </div>
      </div>
    </ViewportOverlay>
  );
}

// ── Summary stats ───────────────────────────────────────────────────────────

function SummaryStats({ videos }: { videos: YouTubeVideo[] }) {
  const enriched = videos.filter((v) => v.enriched);
  const totalViews = enriched.reduce((s, v) => s + (v.view_count ?? v.views ?? 0), 0);
  const totalLikes = enriched.reduce((s, v) => s + (v.like_count ?? 0), 0);
  const totalComments = enriched.reduce((s, v) => s + (v.comment_count ?? 0), 0);
  const brandMentioned = videos.filter((v) => v.brand_mentioned).length;

  const cards = [
    { label: "Videos cited", value: videos.length.toString() },
    ...(brandMentioned > 0 ? [{ label: "Brand mentioned", value: `${brandMentioned} / ${videos.length}` }] : []),
    ...(totalViews > 0 ? [{ label: "Total views", value: fmt(totalViews) }] : []),
    ...(totalLikes > 0 ? [{ label: "Total likes", value: fmt(totalLikes) }] : []),
    ...(totalComments > 0 ? [{ label: "Total comments", value: fmt(totalComments) }] : []),
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-white rounded-xl border border-gray-200 px-4 py-3 text-center">
          <p className="text-xl font-bold text-[#0d0d0d]">{c.value}</p>
          <p className="text-xs text-gray-400 mt-0.5">{c.label}</p>
        </div>
      ))}
    </div>
  );
}

// ── Main table ──────────────────────────────────────────────────────────────

function VideoTable({
  videos,
  reviewedPromptCount,
  onVideoClick,
}: {
  videos: YouTubeVideo[];
  reviewedPromptCount: number;
  onVideoClick: (video: YouTubeVideo) => void;
}) {
  const [channelFilter, setChannelFilter] = useState("all");
  const channels = useMemo(
    () =>
      Array.from(
        new Set(videos.map((video) => video.channel_title?.trim()).filter(Boolean) as string[]),
      ).sort((a, b) => a.localeCompare(b)),
    [videos],
  );
  const filteredVideos = useMemo(
    () =>
      channelFilter === "all"
        ? videos
        : videos.filter((video) => video.channel_title?.trim() === channelFilter),
    [channelFilter, videos],
  );
  const cols = [
    { label: "Video", width: "w-64" },
    { label: "Channel", width: "w-28" },
    { label: "Published", width: "w-24" },
    { label: "Duration", width: "w-16" },
    { label: "Topics", width: "w-44" },
    { label: "Platforms", width: "w-28" },
    { label: "Citations", width: "w-16 text-right" },
    { label: "Citation %", width: "w-20 text-right" },
    { label: "Brand", width: "w-16 text-center" },
    { label: "Views", width: "w-16 text-right" },
    { label: "Likes", width: "w-14 text-right" },
    { label: "Comments", width: "w-16 text-right" },
  ];

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <label className="block min-w-56 text-xs font-semibold text-gray-700">
          Channel
          <select
            className="mt-1 block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-xs font-normal text-gray-800 focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-200"
            value={channelFilter}
            onChange={(event) => setChannelFilter(event.target.value)}
          >
            <option value="all">All channels</option>
            {channels.map((channel) => (
              <option key={channel} value={channel}>
                {channel}
              </option>
            ))}
          </select>
        </label>
        <span className="text-xs text-gray-400">
          Showing {filteredVideos.length} of {videos.length} videos
        </span>
      </div>
      <div className="w-full overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1120px] table-fixed text-xs">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              {cols.map((c) => (
                <th
                  key={c.label}
                  className={`px-2.5 py-2.5 text-left text-[9px] font-semibold uppercase tracking-wide text-gray-500 ${c.width}`}
                >
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {filteredVideos.map((video, i) => {
              const platforms = video.platforms ?? (video.platform ? [video.platform] : []);
              const views = video.view_count ?? video.views;
              const topics = video.topics_referencing ?? [];
              return (
                <tr key={i} className="hover:bg-gray-50/60 transition-colors align-top">
                  {/* Preview + title */}
                  <td className="px-2.5 py-3">
                    <div className="flex items-start gap-2.5">
                      <ThumbnailCell video={video} />
                      <div className="min-w-0">
                        <button
                          type="button"
                          onClick={() => onVideoClick(video)}
                          aria-haspopup="dialog"
                          className="text-left text-[11px] font-semibold leading-snug text-[#0d0d0d] hover:text-blue-600"
                        >
                          <span className="line-clamp-4">
                            {video.yt_title || video.title || (
                              <span className="font-normal text-gray-400">No title available</span>
                            )}
                          </span>
                        </button>
                        <a
                          href={video.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          onClick={(event) => event.stopPropagation()}
                          className="inline-flex items-center gap-1 mt-1 text-[10px] text-gray-400 hover:text-blue-600"
                        >
                          Open video
                          <ExternalLink className="w-2.5 h-2.5" />
                        </a>
                      </div>
                    </div>
                  </td>

                  {/* Channel */}
                  <td className="px-2.5 py-3">
                    <span className="line-clamp-3 text-[11px] font-medium text-gray-700">
                      {video.channel_title || "—"}
                    </span>
                  </td>

                  {/* Published */}
                  <td className="px-2.5 py-3">
                    <span className="text-xs text-gray-500">{fmtDate(video.published_at)}</span>
                  </td>

                  {/* Duration */}
                  <td className="px-2.5 py-3">
                    <span className="text-xs font-mono text-gray-600">
                      {video.duration || "—"}
                    </span>
                  </td>

                  {/* Topics */}
                  <td className="px-2.5 py-3">
                    <TopicCell topics={topics} />
                  </td>

                  {/* Platforms */}
                  <td className="px-2.5 py-3">
                    <PlatformChips platforms={platforms} />
                  </td>

                  {/* Citations */}
                  <td className="px-2.5 py-3 text-right">
                    {(video.citation_count ?? 0) > 0 ? (
                      <span className="text-xs font-medium text-indigo-700">
                        {video.citation_count}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>

                  {/* Citation percentage */}
                  <td
                    className="px-2.5 py-3 text-right"
                    title={
                      reviewedPromptCount > 0
                        ? `${video.citing_prompt_count ?? 0} of ${reviewedPromptCount} reviewed prompts`
                        : undefined
                    }
                  >
                    {video.citation_percentage != null ? (
                      <span className="text-xs font-semibold text-indigo-700">
                        {video.citation_percentage.toFixed(1)}%
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>

                  {/* Brand Mentioned */}
                  <td className="px-2.5 py-3 text-center">
                    {video.brand_mentioned ? (
                      <span className="inline-block text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">Yes</span>
                    ) : (
                      <span className="inline-block text-xs text-gray-400 bg-gray-50 border border-gray-200 rounded-full px-2 py-0.5">No</span>
                    )}
                  </td>

                  {/* Views */}
                  <td className="px-2.5 py-3 text-right">
                    {views != null ? (
                      <div className="flex items-center justify-end gap-1 text-xs text-gray-600">
                        <Eye className="w-3 h-3 text-gray-300" />
                        {fmt(views)}
                      </div>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>

                  {/* Likes */}
                  <td className="px-2.5 py-3 text-right">
                    {video.like_count != null ? (
                      <div className="flex items-center justify-end gap-1 text-xs text-gray-600">
                        <ThumbsUp className="w-3 h-3 text-gray-300" />
                        {fmt(video.like_count)}
                      </div>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>

                  {/* Comments */}
                  <td className="px-2.5 py-3 text-right">
                    {video.comment_count != null ? (
                      <div className="flex items-center justify-end gap-1 text-xs text-gray-600">
                        <MessageSquare className="w-3 h-3 text-gray-300" />
                        {fmt(video.comment_count)}
                      </div>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ── Main export ─────────────────────────────────────────────────────────────

export function YouTubeInsightsSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [data, setData] = useState<{
    videos: YouTubeVideo[];
    total: number;
    reviewed_prompt_count: number;
    brand_name: string;
    api_available: boolean;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<YouTubeVideo | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchYouTubeInsights(auditDirOrSlug)
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <PageLoading />;
  }

  if (error) {
    return (
      <div className="flex items-center gap-3 text-amber-600 bg-amber-50 rounded-xl p-4">
        <AlertCircle className="w-5 h-5 shrink-0" />
        <p className="text-sm">{error}</p>
      </div>
    );
  }

  const videos = data?.videos ?? [];
  const apiAvailable = data?.api_available ?? false;

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <img src="/logos/youtube.png" alt="YouTube" className="w-7 h-7 object-contain" />
          <h2 className="text-xl font-bold text-[#0d0d0d]">YouTube Citations</h2>
        </div>
        <p className="text-sm text-gray-500">
          YouTube videos cited by AI platforms in their responses, enriched with statistics
          from YouTube Data API v3.
        </p>
      </div>

      {!apiAvailable && (
        <div className="flex items-start gap-2 text-blue-700 bg-blue-50 rounded-xl px-4 py-3">
          <Info className="w-4 h-4 shrink-0 mt-0.5" />
          <p className="text-xs leading-relaxed">
            <span className="font-semibold">YouTube API key not configured.</span>{" "}
            Add <code className="bg-blue-100 px-1 rounded">YOUTUBE_API_KEY</code> or{" "}
            <code className="bg-blue-100 px-1 rounded">GOOGLE_API_KEY</code> as an environment
            variable to pull views, likes, comments, and duration from the YouTube Data API.
            Videos found in citations are shown without statistics.
          </p>
        </div>
      )}

      {videos.length === 0 ? (
        <div className="bg-white rounded-2xl border border-gray-200 p-8 text-center">
          <div className="flex justify-center mb-3">
            <img src="/logos/youtube.png" alt="YouTube" className="w-12 h-12 object-contain opacity-40" />
          </div>
          <p className="text-sm font-semibold text-[#0d0d0d] mb-1">No YouTube videos found</p>
          <p className="text-xs text-gray-400">
            Run probes from the Prompts section. If AI platforms cite YouTube videos for your
            topics, they will appear here.
          </p>
        </div>
      ) : (
        <>
          <SummaryStats videos={videos} />
          <VideoTable
            videos={videos}
            reviewedPromptCount={data?.reviewed_prompt_count ?? 0}
            onVideoClick={setSelectedVideo}
          />
          <p className="text-[11px] text-gray-300 text-center">
            {apiAvailable
              ? "Click a video title for prompt details. Statistics via YouTube Data API v3."
              : "Click a video title for prompt details. Add YOUTUBE_API_KEY to enable full statistics."}
          </p>
          {selectedVideo && (
            <VideoDetailOverlay video={selectedVideo} onClose={() => setSelectedVideo(null)} />
          )}
        </>
      )}
    </div>
  );
}
