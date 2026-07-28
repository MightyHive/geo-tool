/**
 * Reddit Insights — Reddit posts cited by AI platforms, grouped by the
 * subreddit encoded in each post URL.
 */

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, ChevronDown, ChevronRight, AlertCircle, X } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchRedditInsights } from "../api/client";
import type { RedditPost } from "../types";
import { PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { ViewportOverlay } from "./ViewportOverlay";

// ── Platform chips ───────────────────────────────────────────────────────────

function PlatformChips({ platforms }: { platforms: string[] }) {
  if (!platforms.length) return <span className="text-xs text-gray-300">—</span>;
  return (
    <div className="flex flex-wrap gap-1">
      {platforms.map((p) => {
        const meta = PLATFORM_META[p];
        const label = meta?.label ?? p;
        return (
          <span
            key={p}
            className="inline-flex items-center gap-1 text-[11px] font-medium bg-gray-50 border border-gray-200 text-gray-600 rounded-full px-2 py-0.5"
          >
            <PlatformLogo platform={p} size={12} />
            {label}
          </span>
        );
      })}
    </div>
  );
}

// ── Topic accordion ──────────────────────────────────────────────────────────

function TopicCell({ topics }: { topics: { topic: string; prompts: string[] }[] | undefined }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!topics?.length) return <span className="text-xs text-gray-300">—</span>;
  return (
    <div className="space-y-1 text-xs">
      {topics.map(({ topic, prompts }) => (
        <div key={topic}>
          <button
            onClick={() => setOpen(open === topic ? null : topic)}
            className="flex items-center gap-1 font-medium text-gray-700 hover:text-indigo-600 transition-colors"
          >
            {open === topic ? (
              <ChevronDown className="w-3 h-3 shrink-0" />
            ) : (
              <ChevronRight className="w-3 h-3 shrink-0" />
            )}
            <span className="text-left">{topic}</span>
            <span className="text-gray-400 font-normal ml-0.5">({prompts.length})</span>
          </button>
          {open === topic && (
            <ul className="ml-4 mt-1 space-y-0.5 border-l border-gray-100 pl-2">
              {prompts.map((pr, j) => (
                <li key={j} className="text-[10px] text-gray-400 italic leading-snug">
                  {pr}
                </li>
              ))}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Reddit URL metadata ──────────────────────────────────────────────────────

function subredditFromUrl(url: string): string | undefined {
  try {
    const segments = new URL(url).pathname.split("/").filter(Boolean);
    const rIndex = segments.findIndex((segment) => segment.toLowerCase() === "r");
    if (rIndex >= 0 && segments[rIndex + 1]) {
      return decodeURIComponent(segments[rIndex + 1]);
    }
  } catch {
    // Invalid citation URLs are rendered without a subreddit.
  }
  return undefined;
}

// ── Post detail overlay ──────────────────────────────────────────────────────

function PostDetailOverlay({ post, onClose }: { post: RedditPost; onClose: () => void }) {
  const title = post.reddit_title || post.title || post.url;
  const linkUrl = post.post_url || post.url;
  const details = post.citation_details ?? [];

  return (
    <ViewportOverlay onClose={onClose} labelledBy="reddit-post-detail-title">
      <div className="bg-white rounded-2xl w-full max-w-3xl max-h-[min(88vh,900px)] flex flex-col shadow-2xl overflow-hidden">
        <div className="flex items-start gap-3 px-6 py-4 border-b border-gray-100 bg-gray-50">
          <img src="/logos/reddit.png" alt="" className="w-6 h-6 object-contain shrink-0" />
          <div className="flex-1 min-w-0">
            <h3 id="reddit-post-detail-title" className="text-sm font-bold text-[#0d0d0d] leading-snug">
              {title}
            </h3>
            <a
              href={linkUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 text-[11px] text-blue-600 hover:underline truncate block"
            >
              View post on Reddit
              <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-middle" />
            </a>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close post details"
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-3">
            Prompts that cited this post ({details.length})
          </p>
          {details.length > 0 ? (
            <div className="space-y-3">
              {details.map((detail, index) => {
                const platformLabel = PLATFORM_META[detail.platform]?.label ?? detail.platform;
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
                          This platform supplied the post as structured grounding data without an inline text position.
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

// ── Summary stats ─────────────────────────────────────────────────────────────

function SummaryStats({ posts }: { posts: RedditPost[] }) {
  const brandMentioned = posts.filter((p) => p.brand_mentioned).length;
  const subreddits = new Set(
    posts.map((post) => subredditFromUrl(post.post_url || post.url)).filter(Boolean),
  ).size;

  const cards = [
    { label: "Reddit posts cited", value: posts.length.toString() },
    { label: "Subreddits", value: subreddits.toString() },
    ...(brandMentioned > 0 ? [{ label: "Brand mentioned", value: `${brandMentioned} / ${posts.length}` }] : []),
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
      {cards.map((c) => (
        <div key={c.label} className="bg-white rounded-xl border border-gray-200 px-4 py-3 text-center">
          <p className="text-xl font-bold text-[#0d0d0d]">{c.value}</p>
          <p className="text-xs text-gray-400 mt-0.5">{c.label}</p>
        </div>
      ))}
    </div>
  );
}

// ── Table ─────────────────────────────────────────────────────────────────────

const cols = [
  { label: "Post", width: "w-72" },
  { label: "Subreddit", width: "w-40" },
  { label: "Topics", width: "w-52" },
  { label: "Platforms", width: "w-36" },
  { label: "Citations", width: "w-20 text-right" },
  { label: "Brand", width: "w-20 text-center" },
];

function PostsTable({ posts, onPostClick }: { posts: RedditPost[]; onPostClick: (post: RedditPost) => void }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-gray-200 bg-white">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            {cols.map((c) => (
              <th
                key={c.label}
                className={`px-4 py-2.5 text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide whitespace-nowrap ${c.width}`}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">
          {posts.map((post, i) => {
            const title = post.reddit_title || post.title || post.url;
            const linkUrl = post.post_url || post.url;
            const subreddit = subredditFromUrl(linkUrl);
            const platforms = post.platforms ?? (post.platform ? [post.platform] : []);

            return (
              <tr key={i} className="align-top hover:bg-gray-50/50 transition-colors">
                {/* Post */}
                <td className="px-4 py-3">
                  <div className="flex items-start gap-2.5">
                    <img src="/logos/reddit.png" alt="Reddit" className="mt-0.5 w-6 h-6 object-contain shrink-0" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                    <div className="min-w-0">
                      <button
                        type="button"
                        onClick={() => onPostClick(post)}
                        aria-haspopup="dialog"
                        className="text-left text-xs font-semibold text-[#0d0d0d] hover:text-blue-600 leading-snug block line-clamp-2"
                      >
                        {title}
                      </button>
                      <a
                        href={linkUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 mt-1 text-[10px] text-gray-400 hover:text-blue-600"
                      >
                        Open post
                        <ExternalLink className="w-2.5 h-2.5" />
                      </a>
                    </div>
                  </div>
                </td>

                {/* Subreddit */}
                <td className="px-4 py-3">
                  {subreddit ? (
                    <a
                      href={`https://www.reddit.com/r/${encodeURIComponent(subreddit)}/`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-block text-[11px] font-semibold text-orange-600 bg-orange-50 border border-orange-100 rounded-full px-2 py-0.5 hover:bg-orange-100 transition-colors"
                    >
                      {subreddit}
                    </a>
                  ) : (
                    <span className="text-xs text-gray-300">—</span>
                  )}
                </td>

                {/* Topics */}
                <td className="px-4 py-3">
                  <TopicCell topics={post.topics_referencing} />
                </td>

                {/* Platforms */}
                <td className="px-4 py-3">
                  <PlatformChips platforms={platforms} />
                </td>

                {/* Citations */}
                <td className="px-4 py-3 text-right">
                  {(post.citation_count ?? 0) > 0 ? (
                    <span className="text-xs font-medium text-indigo-700">{post.citation_count}</span>
                  ) : (
                    <span className="text-xs text-gray-300">—</span>
                  )}
                </td>

                {/* Brand Mentioned */}
                <td className="px-4 py-3 text-center">
                  {post.brand_mentioned ? (
                    <span className="inline-block text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5">Yes</span>
                  ) : (
                    <span className="inline-block text-xs text-gray-400 bg-gray-50 border border-gray-200 rounded-full px-2 py-0.5">No</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function RedditInsightsSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [data, setData] = useState<{ posts: RedditPost[]; total: number; brand_name: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPost, setSelectedPost] = useState<RedditPost | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    fetchRedditInsights(auditDirOrSlug)
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

  const posts = data?.posts ?? [];
  const brandName = data?.brand_name ?? "your brand";

  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center gap-2.5 mb-1">
          <img src="/logos/reddit.png" alt="Reddit" className="w-7 h-7 object-contain" />
          <h2 className="text-xl font-bold text-[#0d0d0d]">Reddit Citations</h2>
        </div>
        <p className="text-sm text-gray-500">
          Reddit posts cited by AI platforms, organised by subreddit and topic. Select a post title to inspect its citation context.
        </p>
      </div>

      {posts.length === 0 ? (
        <div className="bg-white rounded-2xl border border-gray-200 p-8 text-center">
          <div className="flex justify-center mb-3">
            <img src="/logos/reddit.png" alt="Reddit" className="w-12 h-12 object-contain opacity-40" />
          </div>
          <p className="text-sm font-semibold text-[#0d0d0d] mb-1">No Reddit posts found</p>
          <p className="text-xs text-gray-400">
            Run probes from the Prompts section. If AI platforms cite Reddit posts for your topics,
            they will appear here.
          </p>
        </div>
      ) : (
        <>
          <SummaryStats posts={posts} />
          <PostsTable posts={posts} onPostClick={setSelectedPost} />
          <p className="text-[11px] text-gray-300 text-center">
            Subreddits are identified from post URLs · Brand: "{brandName}"
          </p>
        </>
      )}
      {selectedPost && (
        <PostDetailOverlay post={selectedPost} onClose={() => setSelectedPost(null)} />
      )}
    </div>
  );
}
