"""UK retail festive window: Black Friday → Twelfth Night (5 Jan)."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

TWELFTH_NIGHT_DAY = 5


def us_thanksgiving(year: int) -> date:
    nov1 = date(year, 11, 1)
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thu + timedelta(days=21)


def black_friday(year: int) -> date:
    return us_thanksgiving(year) + timedelta(days=1)


def bf_to_twelfth_night_dates(year: int) -> set[date]:
    """Black Friday (year) through Twelfth Night 5 Jan (year+1)."""
    start = black_friday(year)
    end = date(year + 1, 1, TWELFTH_NIGHT_DAY)
    out: set[date] = set()
    d = start
    while d <= end:
        out.add(d)
        d += timedelta(days=1)
    return out


def festive_mask_bf_to_twelfth_night(weeks: pd.Series) -> pd.Series:
    """True if Sunday-start week overlaps BF → Twelfth Night for any year."""
    weeks = pd.to_datetime(weeks)
    if weeks.empty:
        return pd.Series([], dtype=bool)
    year_min = int(weeks.min().year) - 1
    year_max = int(weeks.max().year) + 1
    festive: set[date] = set()
    for y in range(year_min, year_max + 1):
        festive |= bf_to_twelfth_night_dates(y)
    flags = []
    for w in weeks:
        w_date = w.date()
        week_days = {w_date + timedelta(days=i) for i in range(7)}
        flags.append(bool(week_days & festive))
    return pd.Series(flags, index=weeks.index)
