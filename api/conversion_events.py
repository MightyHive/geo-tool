"""Parse and format GA4 conversion event specs.

Primary UX / storage model::

    Field 1 — comma-separated GA4 event ids (e.g. ``purchase`` or
    ``purchase,generate_lead``)
    Field 2 — optional display label for the conversions series (single event
    or summed multi-event metric)

The stored string remains a single ``conversion_event_name`` value. When a
series label is set it is encoded on the first event only::

    purchase
    generate_lead
    generate_lead:Leads
    purchase:Purchases + leads,generate_lead

Legacy per-event labels (``event:Label`` / ``event=Label`` on multiple tokens)
are still accepted: event ids are kept, and distinct labels collapse into one
series label (joined with `` + `` when more than one distinct label exists).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_EVENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_MAX_EVENTS = 10
_MAX_EVENT_LEN = 40
_MAX_LABEL_LEN = 60
_MAX_SPEC_LEN = 500


@dataclass(frozen=True)
class ConversionEvent:
    event: str
    label: str | None = None

    @property
    def display_label(self) -> str:
        text = (self.label or "").strip()
        return text or self.event


class ConversionEventParseError(ValueError):
    """Invalid conversion event specification."""


def _validate_event_name(event_name: str) -> str:
    name = event_name.strip()
    if not name or len(name) > _MAX_EVENT_LEN or not _EVENT_RE.match(name):
        raise ConversionEventParseError(
            "Conversion event must start with a letter and use letters, numbers, underscores, or hyphens"
        )
    return name


def _validate_series_label(label: str | None) -> str | None:
    text = (label or "").strip()
    if not text:
        return None
    if "," in text:
        raise ConversionEventParseError("Conversion label must not contain commas")
    if len(text) > _MAX_LABEL_LEN:
        raise ConversionEventParseError(
            f"Conversion label must be at most {_MAX_LABEL_LEN} characters"
        )
    return text


def series_label_from_events(events: list[ConversionEvent]) -> str | None:
    """Single display label for the (possibly summed) conversions series."""
    labels: list[str] = []
    seen: set[str] = set()
    for item in events:
        text = (item.label or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        labels.append(text)
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    # Legacy multi per-event labels → one series name (avoid commas for re-serialize).
    return " + ".join(labels)


def apply_series_label(
    events: list[ConversionEvent], label: str | None
) -> list[ConversionEvent]:
    """Return events with the same series label on every item (or cleared)."""
    series = _validate_series_label(label)
    return [ConversionEvent(event=item.event, label=series) for item in events]


def parse_conversion_events(raw: str | None, *, default: str = "purchase") -> list[ConversionEvent]:
    """Parse a conversion-event spec into structured events with one series label."""
    text = (raw or "").strip()
    if not text:
        text = default
    if len(text) > _MAX_SPEC_LEN:
        raise ConversionEventParseError(
            f"Conversion event list must be at most {_MAX_SPEC_LEN} characters"
        )

    raw_events: list[ConversionEvent] = []
    seen: set[str] = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            event_part, label_part = token.split(":", 1)
        elif "=" in token:
            event_part, label_part = token.split("=", 1)
        else:
            event_part, label_part = token, ""
        event_name = _validate_event_name(event_part)
        label = label_part.strip() or None
        if label and len(label) > _MAX_LABEL_LEN:
            raise ConversionEventParseError(
                f"Conversion label must be at most {_MAX_LABEL_LEN} characters"
            )
        if event_name in seen:
            continue
        seen.add(event_name)
        raw_events.append(ConversionEvent(event=event_name, label=label))

    if not raw_events:
        raw_events = [ConversionEvent(event=default)]
    if len(raw_events) > _MAX_EVENTS:
        raise ConversionEventParseError(f"At most {_MAX_EVENTS} conversion events are allowed")

    series = series_label_from_events(raw_events)
    return apply_series_label(raw_events, series)


def serialize_conversion_events(events: list[ConversionEvent]) -> str:
    """Serialize events; series label is stored on the first event only."""
    if not events:
        return "purchase"
    series = series_label_from_events(events)
    names = [item.event for item in events]
    if series:
        return ",".join(
            [f"{names[0]}:{series}", *names[1:]]
        )
    return ",".join(names)


def conversion_event_names(events: list[ConversionEvent]) -> list[str]:
    return [item.event for item in events]


def event_names_input(events: list[ConversionEvent]) -> str:
    """Field-1 display: comma-separated event ids only."""
    return ",".join(conversion_event_names(events)) if events else "purchase"


def conversion_events_as_dicts(events: list[ConversionEvent]) -> list[dict[str, str]]:
    series = series_label_from_events(events)
    out: list[dict[str, str]] = []
    for item in events:
        row = {"event": item.event, "label": series or item.event}
        out.append(row)
    return out


def is_only_default_purchase(events: list[ConversionEvent]) -> bool:
    return len(events) == 1 and events[0].event == "purchase"


def format_conversion_heading(events: list[ConversionEvent]) -> str:
    """Column / chart heading for reports.

    Rules:
    - series label set → that label
    - single ``purchase`` + no label → ``Purchases``
    - single non-purchase + no label → event id
    - multi-event + no label → neutral ``Conversions`` (UI should prompt for a label)
    """
    series = series_label_from_events(events)
    if series:
        return series
    if is_only_default_purchase(events):
        return "Purchases"
    if len(events) == 1:
        return events[0].event
    return "Conversions"


def format_conversion_method_note(events: list[ConversionEvent]) -> str:
    series = series_label_from_events(events)
    if is_only_default_purchase(events) and not series:
        return ""
    if series:
        return f" (conversions = {series})"
    if len(events) == 1:
        return f" (conversions = {events[0].event})"
    return f" (conversions = {', '.join(item.event for item in events)})"


def build_conversion_event_spec(
    event_names_raw: str | None,
    series_label: str | None = "",
    *,
    default: str = "purchase",
) -> str:
    """Build a storage string from Field 1 (event ids) + Field 2 (series label).

    Prefer the explicit series label. If Field 2 is empty but Field 1 still
    contains legacy ``event:Label`` tokens, keep the migrated series label.
    """
    parsed = parse_conversion_events(event_names_raw, default=default)
    names_only = [ConversionEvent(event=item.event) for item in parsed]
    explicit = (series_label or "").strip()
    label = explicit or series_label_from_events(parsed)
    return serialize_conversion_events(apply_series_label(names_only, label))


def normalize_conversion_event_spec(raw: str | None, *, default: str = "purchase") -> str:
    """Validate and re-serialize a user-provided spec."""
    return serialize_conversion_events(parse_conversion_events(raw, default=default))
