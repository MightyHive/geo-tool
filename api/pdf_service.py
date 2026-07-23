"""PDF generation for GEO audit reports — one section per page via Playwright."""

from __future__ import annotations

import io
import re
import tempfile
from pathlib import Path

_SANITIZE = re.compile(r"[^\w\-.]")


def _sanitize_filename(name: str) -> str:
    name = _SANITIZE.sub("-", name)
    return name.strip("-") or "geo-report"


def _pil_image_from_png(png_bytes: bytes):  # type: ignore[return]
    from PIL import Image  # type: ignore[import-untyped]

    return Image.open(io.BytesIO(png_bytes)).convert("RGB")


def _screenshot_html_on_page(page, html_path: Path, *, chunk_tall: bool = True) -> list:  # type: ignore[return]
    """Screenshot one HTML document into one or more PIL images."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.emulate_media(media="screen")
    page.goto(html_path.as_uri(), wait_until="domcontentloaded")
    try:
        page.evaluate("() => (document.fonts && document.fonts.ready) || Promise.resolve()")
    except Exception:
        pass
    page.wait_for_timeout(500)

    metrics = page.evaluate(
        """() => ({
            height: Math.max(
                document.body.scrollHeight,
                document.documentElement.scrollHeight
            ),
            width: Math.max(
                document.body.scrollWidth,
                document.documentElement.scrollWidth,
                1440
            )
        })"""
    )
    total_h = int(metrics.get("height") or 900)
    width = int(metrics.get("width") or 1440)
    images = []
    if not chunk_tall or total_h <= 1800:
        png = page.screenshot(full_page=True)
        images.append(_pil_image_from_png(png))
        return images

    chunk = 1600
    y = 0
    while y < total_h:
        page.set_viewport_size({"width": width, "height": min(chunk, total_h - y)})
        page.evaluate(f"() => window.scrollTo(0, {y})")
        page.wait_for_timeout(120)
        png = page.screenshot()
        images.append(_pil_image_from_png(png))
        y += chunk
    return images


def _images_to_pdf_bytes(images: list) -> bytes:
    if not images:
        raise RuntimeError("No pages were captured for PDF")
    buf = io.BytesIO()
    first, rest = images[0], images[1:]
    first.save(
        buf,
        format="PDF",
        save_all=True,
        append_images=rest,
        resolution=150,
    )
    return buf.getvalue()


def generate_report_pdf(report_html_path: Path) -> bytes:
    """Full-report PDF (``report_html_path`` only locates the audit directory)."""
    return generate_audit_pdf(report_html_path.parent)


def generate_audit_pdf(audit_dir: Path) -> bytes:
    """
    Full-report PDF with each report section starting on its own page.
    Screenshots one standalone HTML document per section, then merges pages.
    Prefers precomputed ``exports/sections/*.html`` when fresh.
    """
    from playwright.sync_api import sync_playwright

    from api.export_builders import FULL_REPORT_SECTIONS, iter_section_export_html
    from api.export_precompute import precomputed_html_is_fresh, section_html_path

    use_precomputed = precomputed_html_is_fresh(audit_dir)
    section_docs: list[tuple[str, str, str]] = []
    if use_precomputed:
        for section_id, label in FULL_REPORT_SECTIONS:
            path = section_html_path(audit_dir, section_id)
            if not path.is_file():
                use_precomputed = False
                break
            section_docs.append((section_id, label, path.read_text(encoding="utf-8")))
    if not use_precomputed:
        section_docs = iter_section_export_html(audit_dir)
    if not section_docs:
        raise RuntimeError("No sections available for PDF export")

    images: list = []
    tmp_paths: list[Path] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
            try:
                page = browser.new_page()
                for _section_id, _label, html in section_docs:
                    with tempfile.NamedTemporaryFile(
                        suffix=".html", mode="w", encoding="utf-8", delete=False
                    ) as tmp:
                        tmp.write(html)
                        path = Path(tmp.name)
                    tmp_paths.append(path)
                    images.extend(_screenshot_html_on_page(page, path, chunk_tall=True))
            finally:
                browser.close()
    finally:
        for path in tmp_paths:
            path.unlink(missing_ok=True)

    return _images_to_pdf_bytes(images)


def generate_section_pdf(audit_dir: Path, section: str) -> bytes:
    """Single-section PDF from the report-styled export HTML."""
    from playwright.sync_api import sync_playwright

    from api.export_precompute import read_precomputed_section_html
    from api.html_service import generate_section_html, resolve_export_section

    target = resolve_export_section(section)
    if not target:
        raise ValueError(f"Section is not available for download: {section}")

    section_html = read_precomputed_section_html(audit_dir, target)
    if section_html is None:
        _slug, section_html = generate_section_html(audit_dir, section)
    with tempfile.NamedTemporaryFile(
        suffix=".html", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(section_html)
        tmp_path = Path(tmp.name)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--no-sandbox", "--disable-setuid-sandbox"])
            try:
                page = browser.new_page()
                images = _screenshot_html_on_page(page, tmp_path, chunk_tall=True)
            finally:
                browser.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return _images_to_pdf_bytes(images)


def pdf_filename_for_audit(audit_dir: Path, *, section: str | None = None) -> str:
    slug = _sanitize_filename(audit_dir.name)
    if section:
        sec = _sanitize_filename(section)
        return f"geo-report-{slug}-{sec}.pdf"
    return f"geo-report-{slug}.pdf"
