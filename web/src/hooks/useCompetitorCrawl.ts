import { useCallback, useEffect, useRef, useState } from "react";
import {
  fetchCompetitorCrawlStatus,
  markCompetitorCrawlSeen,
  runCompetitorCrawl,
} from "../api/client";
import type { CompetitorCrawlStatusResponse } from "../types";

const IDLE_STATUS: CompetitorCrawlStatusResponse = {
  status: "idle",
  seen: true,
  has_comparison: false,
  archives: [],
};

export function useCompetitorCrawl(auditId: string) {
  const [status, setStatus] = useState<CompetitorCrawlStatusResponse>(IDLE_STATUS);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const prevStatusRef = useRef<string>("idle");
  const [justCompleted, setJustCompleted] = useState(false);

  const refresh = useCallback(async () => {
    if (!auditId) return;
    try {
      const next = await fetchCompetitorCrawlStatus(auditId);
      const prev = prevStatusRef.current;
      if (prev === "running" && next.status === "done") {
        setJustCompleted(true);
      }
      prevStatusRef.current = next.status;
      setStatus(next);
      if (next.status === "error") {
        setError(next.error || next.detail || "Competitor crawl failed");
      } else if (next.status === "running") {
        setError(null);
      }
    } catch {
      // Keep last known status when polling fails transiently.
    }
  }, [auditId]);

  useEffect(() => {
    setStatus(IDLE_STATUS);
    setError(null);
    setStarting(false);
    setJustCompleted(false);
    prevStatusRef.current = "idle";
    void refresh();
  }, [auditId, refresh]);

  useEffect(() => {
    if (!auditId) return;
    const shouldPoll = status.status === "running" || starting;
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      void refresh();
    }, 2500);
    return () => window.clearInterval(timer);
  }, [auditId, refresh, starting, status.status]);

  const start = useCallback(async () => {
    if (!auditId) return;
    setStarting(true);
    setError(null);
    setJustCompleted(false);
    try {
      await runCompetitorCrawl(auditId);
      prevStatusRef.current = "running";
      setStatus((prev) => ({
        ...prev,
        status: "running",
        seen: false,
        detail: "Starting competitor crawl…",
        percent: 0,
      }));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start competitor crawl");
    } finally {
      setStarting(false);
    }
  }, [auditId, refresh]);

  const markSeen = useCallback(async () => {
    if (!auditId) return;
    if (status.status !== "done" || status.seen) return;
    try {
      const next = await markCompetitorCrawlSeen(auditId);
      setStatus(next);
      setJustCompleted(false);
    } catch {
      setStatus((prev) => ({ ...prev, seen: true }));
      setJustCompleted(false);
    }
  }, [auditId, status.seen, status.status]);

  const clearJustCompleted = useCallback(() => {
    setJustCompleted(false);
  }, []);

  const busy = starting || status.status === "running";
  const showCompleteBadge =
    status.status === "done" && status.seen === false;

  return {
    status,
    error,
    busy,
    starting,
    showCompleteBadge,
    justCompleted,
    start,
    refresh,
    markSeen,
    clearJustCompleted,
  };
}
