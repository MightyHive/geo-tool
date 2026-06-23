"""Map create-report / crawl-site log lines to user-facing audit progress (staging UI)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Order matters for the progress bar.
PIPELINE_STEPS: tuple[tuple[str, str], ...] = (
    ("crawl", "Crawling your site"),
    ("competitors", "Analysing competitors"),
    ("ga4", "Pulling GA4 traffic"),
    ("report", "Building report and scores"),
    ("prompt_probes", "AI prompt probes (share of voice)"),
    ("sentiment", "Sentiment analysis"),
    ("finish", "Finishing up"),
)

_STEP_INDEX = {step_id: i for i, (step_id, _) in enumerate(PIPELINE_STEPS)}


@dataclass
class AuditProgressState:
    current_id: str = "crawl"
    completed: set[str] = field(default_factory=set)
    detail: str = "Starting audit…"

    def to_payload(self) -> dict[str, Any]:
        idx = _STEP_INDEX.get(self.current_id, 0)
        done = len(self.completed)
        total = len(PIPELINE_STEPS)
        # Active step counts as partial progress.
        percent = min(100, int(((done + 0.35) / total) * 100))
        if "finish" in self.completed:
            percent = 100
        steps = []
        for step_id, label in PIPELINE_STEPS:
            if step_id in self.completed:
                status = "done"
            elif step_id == self.current_id:
                status = "active"
            else:
                status = "pending"
            steps.append({"id": step_id, "label": label, "status": status})
        return {
            "percent": percent,
            "detail": self.detail,
            "current_step": self.current_id,
            "steps": steps,
        }


_RE_RUNNING_CRAWL = re.compile(r"Running:.*crawl-site\.py", re.I)
_RE_PAGES_SCANNED = re.compile(r"Pages scanned:\s*(\d+)", re.I)
_RE_COMPETITOR_CRAWL = re.compile(r"Competitor\s+\d+\s+\(https?://", re.I)
_RE_COMPARISON = re.compile(r"comparison\.(md|json):", re.I)
_RE_GA4 = re.compile(r"\[GA4\]|GA4 fetch|ga4_traffic", re.I)
_RE_REPORT_HTML = re.compile(r"Wrote\s+.+\breport\.html\b", re.I)


def apply_log_line(state: AuditProgressState, line: str) -> AuditProgressState:
    text = (line or "").strip()
    if not text:
        return state

    if _RE_RUNNING_CRAWL.search(text):
        state.current_id = "crawl"
        state.detail = "Crawling pages and technical setup"
        return state

    if _RE_PAGES_SCANNED.search(text):
        state.completed.add("crawl")
        m = _RE_PAGES_SCANNED.search(text)
        n = m.group(1) if m else ""
        state.current_id = "competitors"
        state.detail = f"Site crawl complete ({n} pages scanned)" if n else "Site crawl complete"
        return state

    if _RE_COMPETITOR_CRAWL.search(text) and "Skipping" not in text:
        state.completed.add("crawl")
        state.current_id = "competitors"
        state.detail = "Crawling competitor sites"
        return state

    if _RE_COMPARISON.search(text):
        state.completed.update({"crawl", "competitors"})
        state.current_id = "ga4"
        state.detail = "Competitor comparison ready"
        return state

    if _RE_GA4.search(text):
        state.completed.update({"crawl", "competitors"})
        state.current_id = "ga4"
        if "failed" in text.lower() or "warning" in text.lower():
            state.detail = "GA4 pull skipped or partial — continuing"
        elif "success" in text.lower():
            state.detail = "GA4 traffic data saved"
        else:
            state.detail = "Pulling GA4 traffic data"
        return state

    if _RE_REPORT_HTML.search(text):
        state.completed.update({"crawl", "competitors", "ga4", "report"})
        state.current_id = "prompt_probes"
        state.detail = "Report generated — preparing AI prompt probes"
        return state

    if "robots.txt:" in text or "llms.txt" in text:
        if state.current_id == "crawl":
            state.detail = "Checking robots.txt and llms.txt"
        return state

    if "json-ld" in text.lower() and state.current_id == "crawl":
        state.detail = "Scanning structured data on sample pages"
        return state

    return state


def advance_to_step(state: AuditProgressState, step_id: str, detail: str) -> AuditProgressState:
    """Mark earlier pipeline steps complete and set the active step (for post-crawl phases)."""
    idx = _STEP_INDEX.get(step_id, len(PIPELINE_STEPS) - 1)
    for i, (sid, _) in enumerate(PIPELINE_STEPS):
        if i < idx:
            state.completed.add(sid)
    state.completed.discard(step_id)
    state.current_id = step_id
    state.detail = detail
    return state


def complete_all_steps(state: AuditProgressState, *, detail: str = "Audit complete") -> AuditProgressState:
    state.completed.update(sid for sid, _ in PIPELINE_STEPS)
    state.current_id = "finish"
    state.detail = detail
    return state
