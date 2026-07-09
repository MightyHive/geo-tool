"""Shared paths for research scripts and exports."""

from __future__ import annotations

import sys
from pathlib import Path

RESEARCH_ROOT = Path(__file__).resolve().parent
REPO_ROOT = RESEARCH_ROOT.parent
BACKEND_ROOT = REPO_ROOT / "backend"

CONFIG_DIR = RESEARCH_ROOT / "config"
GA4_DIR = RESEARCH_ROOT / "ga4"
GA4_EXPORTS = GA4_DIR / "exports"
GA4_DAILY = GA4_EXPORTS / "daily"
GA4_WEEKLY = GA4_EXPORTS / "weekly"
GA4_PPC = GA4_EXPORTS / "ppc"
GA4_PROPERTIES = CONFIG_DIR / "ga4_ppc_properties.csv"
GA4_PROPERTIES_EXAMPLE = CONFIG_DIR / "ga4_ppc_properties.example.csv"

ANALYSIS_DIR = RESEARCH_ROOT / "analysis"
ANALYSIS_OUTPUTS = ANALYSIS_DIR / "outputs"
SEGMENT_DIAGNOSTICS = ANALYSIS_OUTPUTS / "segment_diagnostics"
SEO_COUNTERFACTUAL = ANALYSIS_OUTPUTS / "seo_counterfactual"
LEGACY_OUTPUTS = ANALYSIS_OUTPUTS / "legacy"

TRENDS_ROOT = RESEARCH_ROOT / "trends_manual"
OAUTH_TOKEN = RESEARCH_ROOT / ".ga4_oauth_token.json"


def ensure_backend_on_path() -> None:
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))


def ensure_research_on_path() -> None:
    if str(RESEARCH_ROOT) not in sys.path:
        sys.path.insert(0, str(RESEARCH_ROOT))
