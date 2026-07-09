"""Parse Google Trends multiTimeline CSV exports (weekly or daily)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pandas as pd


def explore_url_range(
    date_start: str,
    date_end: str,
    *,
    geo: str,
    hl: str,
    q_term: str,
) -> str:
    """Google Trends explore URL for one search term."""
    date_param = f"{date_start.strip()} {date_end.strip()}"
    return (
        "https://trends.google.com/trends/explore"
        f"?date={quote(date_param)}&geo={quote(geo)}&q={quote(q_term, safe=',')}&hl={quote(hl)}"
    )


def load_trends_csv(path: str) -> pd.DataFrame:
    """Load a Google Trends multiTimeline CSV; return DataFrame indexed by date."""
    for skip in (0, 1, 2, 3):
        try:
            df = pd.read_csv(path, skiprows=skip)
            date_col = next(
                (
                    c
                    for c in df.columns
                    if c.strip().lower() in ("day", "week", "date", "time")
                ),
                None,
            )
            if date_col is None:
                continue
            df = df.rename(columns={date_col: "date"})
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"]).set_index("date")
            for col in df.columns:
                df[col] = (
                    df[col]
                    .astype(str)
                    .str.replace("<1", "0.5", regex=False)
                    .pipe(pd.to_numeric, errors="coerce")
                )
            df.columns = [c.split(":")[0].strip() for c in df.columns]
            return df
        except Exception:
            continue
    raise ValueError(f"Could not parse Trends CSV: {path}")


def is_valid_trends_csv(path) -> bool:
    try:
        df = load_trends_csv(str(path))
        return not df.empty and len(df.columns) >= 1
    except Exception:
        return False


def _parse_metric_header(raw: str) -> tuple[str, str]:
    """``good food: (United Kingdom)`` → (term, region label)."""
    text = raw.strip()
    if ":" in text:
        term, rest = text.split(":", 1)
        region = rest.strip().strip("()").strip()
        return term.strip(), region or "United Kingdom"
    return text, "United Kingdom"


def read_csv_grain_and_terms(path: str | Path) -> tuple[str, list[tuple[str, str]]]:
    """Return grain (``Week`` or ``Day``) and ``(term, region)`` for each metric column."""
    path = Path(path)
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:10]:
        stripped = line.strip().lstrip("\ufeff")
        lower = stripped.lower()
        if lower.startswith("week,") or lower.startswith("day,"):
            grain = stripped.split(",", 1)[0].strip()
            cols = [c.strip() for c in stripped.split(",", 1)[1].split(",") if c.strip()]
            if not cols:
                raise ValueError(f"No metric columns in header: {path}")
            return grain, [_parse_metric_header(c) for c in cols]
    raise ValueError(f"Could not find Week/Day header row in {path}")


def term_slug(term: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")
    return slug or "term"


def write_single_term_csv(
    path: str | Path,
    *,
    term: str,
    region: str,
    grain: str,
    dest: Path,
) -> None:
    """Extract one metric column from a multiTimeline export into a single-term CSV."""
    df = load_trends_csv(str(path))
    if term not in df.columns:
        raise ValueError(f"Term {term!r} not in {list(df.columns)}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = df[[term]].sort_index().reset_index()
    date_col = out.columns[0]
    header_metric = f"{term}: ({region})"
    with dest.open("w", encoding="utf-8", newline="") as f:
        f.write("Category: All categories\n\n")
        f.write(f"{grain},{header_metric}\n")
        for _, row in out.iterrows():
            d = row[date_col]
            if hasattr(d, "strftime"):
                d_str = d.strftime("%Y-%m-%d")
            else:
                d_str = str(d)[:10]
            val = row[term]
            if pd.isna(val):
                val_str = ""
            elif float(val).is_integer():
                val_str = str(int(val))
            else:
                val_str = str(val)
            f.write(f"{d_str},{val_str}\n")

