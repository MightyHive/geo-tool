"""PDF generation for GEO audit reports — screenshot-per-section via Playwright + Pillow."""
from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

PANEL_IDS = [
    "summary",
    "ga4-traffic",
    "recommendations",
    "competitors",
    "ai-visibility",
    "technical",
    "content",
    "samples",
]

_SANITIZE = re.compile(r"[^\w\-.]")


def _sanitize_filename(name: str) -> str:
    name = _SANITIZE.sub("-", name)
    return name.strip("-") or "geo-report"


def _pil_image_from_png(png_bytes: bytes):  # type: ignore[return]
    from PIL import Image  # type: ignore[import-untyped]

    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def _screenshots_for_report(report_html_path: Path, pw) -> list:  # type: ignore[return]
    """Return one PIL image per tab panel in report.html."""
    from playwright.sync_api import Browser  # noqa: F401

    browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
    images = []
    try:
        page = browser.new_page()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.emulate_media(media="screen")
        page.goto(report_html_path.as_uri(), wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        for panel_id in PANEL_IDS:
            # Activate the panel via the tab button
            activated = page.evaluate(
                """(id) => {
                    const btn = document.querySelector('[data-tab="' + id + '"]');
                    if (btn) { btn.click(); return true; }
                    // Fallback: show panel directly
                    const panel = document.querySelector('[data-tab-panel="' + id + '"]');
                    if (!panel) return false;
                    document.querySelectorAll('[data-tab-panel]').forEach(p => p.hidden = true);
                    panel.hidden = false;
                    return true;
                }""",
                panel_id,
            )
            if not activated:
                continue
            page.wait_for_timeout(900)
            png = page.screenshot(full_page=True)
            images.append(_pil_image_from_png(png))
    finally:
        browser.close()

    return images


def _screenshot_for_prompt_performance(audit_dir: Path, pw) -> list:  # type: ignore[return]
    """Return a list of PIL images for the prompt performance HTML page."""
    from api.html_service import build_prompt_performance_section, _section_divider

    pp_html = build_prompt_performance_section(audit_dir)
    full_html = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #f5f4f2;
    color: #1a1a1a;
    padding: 40px;
  }
  .page-header {
    background: #0d0d0d;
    color: #fff;
    padding: 24px 40px;
    margin: -40px -40px 32px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .page-title {
    font-size: 18px;
    font-weight: 700;
    letter-spacing: -.01em;
  }
  .section-label {
    background: rgba(255,255,255,.12);
    color: rgba(255,255,255,.8);
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: .08em;
  }
  .content { max-width: 960px; margin: 0 auto; }
</style>
</head>
<body>
<div class="page-header">
  <span class="page-title">Prompt Performance</span>
  <span class="section-label">AI Share of Voice</span>
</div>
<div class="content">""" + pp_html + """</div>
</body>
</html>"""

    with tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(full_html)
        tmp_path = Path(f.name)

    try:
        browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
        images = []
        try:
            page = browser.new_page()
            page.set_viewport_size({"width": 1440, "height": 900})
            page.emulate_media(media="screen")
            page.goto(tmp_path.as_uri(), wait_until="domcontentloaded")
            # Expand all pp-cards so everything is visible in the PDF
            page.evaluate(
                """() => {
                    document.querySelectorAll('.pp-card').forEach(c => c.style.display = '');
                    var btn = document.getElementById('pp-btn');
                    if (btn) btn.style.display = 'none';
                    var ct = document.getElementById('pp-count');
                    if (ct) ct.textContent = 'All prompts';
                }"""
            )
            page.wait_for_timeout(800)
            png = page.screenshot(full_page=True)
            images.append(_pil_image_from_png(png))
        finally:
            browser.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return images


def _images_to_pdf_bytes(images: list) -> bytes:
    """Combine PIL images into a single PDF."""
    if not images:
        raise ValueError("No images to combine into PDF")
    buf = io.BytesIO()
    images[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=150,
    )
    return buf.getvalue()


def generate_report_pdf(report_html_path: Path) -> bytes:
    """Generate a multi-page PDF by screenshotting each tab + prompt performance section."""
    from playwright.sync_api import sync_playwright

    audit_dir = report_html_path.parent

    with sync_playwright() as pw:
        images = _screenshots_for_report(report_html_path, pw)
        pp_images = _screenshot_for_prompt_performance(audit_dir, pw)

    all_images = images + pp_images
    if not all_images:
        raise RuntimeError("No pages were captured for PDF")

    return _images_to_pdf_bytes(all_images)


def pdf_filename_for_audit(audit_dir: Path) -> str:
    safe = _sanitize_filename(audit_dir.name)
    return f"geo-report-{safe}.pdf"
