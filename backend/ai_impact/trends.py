"""Validate and normalize manually exported Google Trends CSV files."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_TERMS = 5


class TrendsUploadError(ValueError):
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__(" ".join(messages))


@dataclass(frozen=True)
class ParsedTrendsUpload:
    weekly: pd.DataFrame
    terms: list[str]
    start_date: str
    end_date: str
    week_count: int
    warnings: list[str]


def _term_from_header(header: str) -> str:
    term = re.sub(r":\s*\([^)]*\)\s*$", "", str(header)).strip()
    return term or "Search interest"


def _read_export(content: bytes) -> tuple[pd.DataFrame, str]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TrendsUploadError(["The file must be a UTF-8 CSV exported by Google Trends."]) from exc

    for skip_rows in range(5):
        try:
            frame = pd.read_csv(io.StringIO(text), skiprows=skip_rows)
        except Exception:
            continue
        date_column = next(
            (
                column
                for column in frame.columns
                if str(column).strip().lower() in {"week", "day", "date", "time"}
            ),
            None,
        )
        if date_column is not None:
            return frame, str(date_column).strip()
    raise TrendsUploadError(
        [
            "This does not look like an Interest over time CSV.",
            "Download the CSV from the Interest over time chart in Google Trends and upload it unchanged.",
        ]
    )


def parse_google_trends_upload(
    content: bytes,
    *,
    expected_start: str,
    expected_end: str,
) -> ParsedTrendsUpload:
    if not content:
        raise TrendsUploadError(["The uploaded file is empty."])
    if len(content) > MAX_UPLOAD_BYTES:
        raise TrendsUploadError(["The CSV is too large. The maximum upload size is 2 MB."])

    frame, grain = _read_export(content)
    if grain.lower() != "week":
        raise TrendsUploadError(
            [
                f"The export uses {grain.lower()}ly rows, but weekly rows are required.",
                "Set the full requested date range in Google Trends before downloading the CSV.",
            ]
        )

    date_column = next(
        column for column in frame.columns if str(column).strip().lower() == grain.lower()
    )
    metric_columns = [
        column
        for column in frame.columns
        if column != date_column and str(column).strip().lower() not in {"ispartial", "is partial"}
    ]
    if not metric_columns:
        raise TrendsUploadError(["The CSV has no search-interest columns."])
    if len(metric_columns) > MAX_TERMS:
        raise TrendsUploadError([f"Use no more than {MAX_TERMS} search terms in one export."])

    dates = pd.to_datetime(frame[date_column], errors="coerce")
    values = frame[metric_columns].copy()
    for column in metric_columns:
        values[column] = pd.to_numeric(
            values[column].astype(str).str.strip().str.replace("<1", "0.5", regex=False),
            errors="coerce",
        )

    valid_rows = dates.notna()
    dates = dates[valid_rows].reset_index(drop=True)
    values = values.loc[valid_rows].reset_index(drop=True)
    if len(dates) < 2:
        raise TrendsUploadError(["The CSV does not contain enough weekly observations."])

    sorted_dates = dates.sort_values().reset_index(drop=True)
    spacing = sorted_dates.diff().dropna().dt.days
    if spacing.empty or float(spacing.median()) not in {6.0, 7.0, 8.0}:
        raise TrendsUploadError(
            ["The date column is not weekly. Export the full requested range from Google Trends."]
        )

    expected_start_date = pd.Timestamp(expected_start).normalize()
    expected_end_date = pd.Timestamp(expected_end).normalize()
    actual_start = sorted_dates.min().normalize()
    actual_end = sorted_dates.max().normalize()
    problems: list[str] = []
    if actual_start > expected_start_date + pd.Timedelta(days=8):
        problems.append(
            f"The file starts on {actual_start.date()}, but it must cover {expected_start_date.date()}."
        )
    if actual_end < expected_end_date - pd.Timedelta(days=14):
        problems.append(
            f"The file ends on {actual_end.date()}, but it must cover data through approximately "
            f"{expected_end_date.date()}."
        )
    if problems:
        problems.append("Update the date range in Google Trends, download a new CSV, and try again.")
        raise TrendsUploadError(problems)

    terms = [_term_from_header(str(column)) for column in metric_columns]
    warnings: list[str] = []
    if values.isna().any().any():
        warnings.append("Some missing interest values were interpolated.")
    if any(values[column].nunique(dropna=True) <= 1 for column in metric_columns):
        warnings.append("At least one search term has little or no variation.")

    output = pd.DataFrame({"week": dates})
    output["week"] = output["week"] - pd.to_timedelta(
        (output["week"].dt.dayofweek + 1) % 7, unit="D"
    )
    for index, column in enumerate(metric_columns, start=1):
        series = values[column].astype(float).interpolate(limit_direction="both")
        output[f"trends_{index}"] = series
    output = output.groupby("week", as_index=False).mean(numeric_only=True).sort_values("week")
    output = output[
        (output["week"] >= expected_start_date - pd.Timedelta(days=7))
        & (output["week"] <= expected_end_date)
    ].reset_index(drop=True)

    if output.filter(like="trends_").replace([np.inf, -np.inf], np.nan).isna().all().any():
        raise TrendsUploadError(["At least one search-interest column contains no usable values."])

    return ParsedTrendsUpload(
        weekly=output,
        terms=terms,
        start_date=output["week"].min().date().isoformat(),
        end_date=output["week"].max().date().isoformat(),
        week_count=len(output),
        warnings=warnings,
    )


def default_expected_end() -> str:
    return date.today().isoformat()
